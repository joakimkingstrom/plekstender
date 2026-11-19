"""Song previews: 40-second AAC clips cut from the Plex file, and how quiet each intro is.

The file is read from Plex directly (its media part, with the user's own token), which is
not a playback: Plex records no session and counts no play. ffmpeg reads it from a pipe,
so the token never appears on a command line. The same pass measures leading silence with
`silencedetect`, so clips can start where the music does and games can avoid silent
intros. Clips are cached under DATA_DIR/previews.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.config import Settings
from plekstender.db.models import Track, TrackIntro
from plekstender.plex.server import PlexServerClient
from plekstender.plex.tv import PlexError

log = logging.getLogger(__name__)

CLIP_SECONDS = 40
# Quieter than this counts as silence (most masters' quiet passages are well above it).
SILENCE_DB = -40
SILENCE_MIN_SECONDS = 0.5
# An intro this long without music makes a clip (or a game round) pointless.
QUIET_INTRO_MS = 8000
# Files are read through a pipe; formats that need random access (MP4 with the index at the
# end) are downloaded first, up to this size.
MAX_DOWNLOAD = 400 * 1024 * 1024
FFMPEG_TIMEOUT = 90
MAX_CACHE_BYTES = 600 * 1024 * 1024
CONCURRENCY = 2

_semaphore = asyncio.Semaphore(CONCURRENCY)
_locks: dict[tuple[int, int], asyncio.Lock] = {}
_SILENCE = re.compile(r"silence_(start|end): (-?[\d.]+)")


class PreviewError(Exception):
    """The clip couldn't be made (no file, Plex unreachable, unreadable audio)."""


@dataclass
class Clip:
    path: Path
    intro_ms: int  # leading silence: where the music starts

    @property
    def quiet_intro(self) -> bool:
        return self.intro_ms >= QUIET_INTRO_MS


def ffmpeg_exe(settings: Settings) -> str:
    if settings.ffmpeg_path:
        return settings.ffmpeg_path
    import imageio_ffmpeg

    return str(imageio_ffmpeg.get_ffmpeg_exe())


def previews_dir(settings: Settings) -> Path:
    return settings.data_dir / "previews"


def clip_path(settings: Settings, server_id: int, rating_key: int) -> Path:
    return previews_dir(settings) / str(server_id) / f"{rating_key}.m4a"


def leading_silence_ms(log_text: str) -> int:
    """From ffmpeg's silencedetect output: how long the clip is silent from its start."""
    start: float | None = None
    for kind, value in _SILENCE.findall(log_text):
        if kind == "start":
            start = float(value)
        elif start is not None and start <= 0.05:
            return round(float(value) * 1000)
        else:
            return 0
    # Silent from the start to the end of the clip.
    return CLIP_SECONDS * 1000 if start is not None and start <= 0.05 else 0


def _command(exe: str, source: str, target: Path) -> list[str]:
    return [
        exe,
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "info",
        "-i",
        source,
        "-t",
        str(CLIP_SECONDS),
        "-map",
        "0:a:0",
        "-vn",
        "-af",
        f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN_SECONDS}",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        "-y",
        str(target),
    ]


async def _run_piped(
    exe: str, client: PlexServerClient, part_key: str, target: Path
) -> tuple[int, str]:
    """ffmpeg reading the file from a pipe while it streams from Plex."""
    proc = await asyncio.create_subprocess_exec(
        *_command(exe, "pipe:0", target),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin is not None and proc.stderr is not None
    stderr = asyncio.create_task(proc.stderr.read())

    async def feed() -> None:
        assert proc.stdin is not None
        try:
            async with client.stream_part(part_key) as resp:
                if resp.status_code != 200:
                    raise PreviewError(f"Plex returned HTTP {resp.status_code} for the file.")
                async for chunk in resp.aiter_bytes(256 * 1024):
                    proc.stdin.write(chunk)
                    await proc.stdin.drain()
        except BrokenPipeError, ConnectionResetError:
            pass  # ffmpeg has what it needs (-t) and stopped reading
        finally:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                proc.stdin.close()

    feeder = asyncio.create_task(feed())
    try:
        code = await asyncio.wait_for(proc.wait(), FFMPEG_TIMEOUT)
    except TimeoutError:
        proc.kill()
        raise PreviewError("Making the preview took too long.") from None
    finally:
        if not feeder.done():
            feeder.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await feeder
    if feeder.done() and not feeder.cancelled() and (exc := feeder.exception()):
        if isinstance(exc, PreviewError):
            raise exc
        if isinstance(exc, (httpx.HTTPError, PlexError)):
            raise PreviewError("Plex couldn't be reached for the file.") from exc
    return code, (await stderr).decode(errors="replace")


async def _run_downloaded(
    exe: str, client: PlexServerClient, part_key: str, target: Path
) -> tuple[int, str]:
    """ffmpeg on a downloaded copy (for files that can't be read from a pipe)."""
    fd, name = tempfile.mkstemp(suffix=Path(part_key).suffix[:8], dir=target.parent)
    try:
        size = 0
        with os.fdopen(fd, "wb") as out:
            async with client.stream_part(part_key) as resp:
                if resp.status_code != 200:
                    raise PreviewError(f"Plex returned HTTP {resp.status_code} for the file.")
                async for chunk in resp.aiter_bytes(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD:
                        raise PreviewError("The file is too large to preview.")
                    out.write(chunk)
        proc = await asyncio.create_subprocess_exec(
            *_command(exe, name, target),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), FFMPEG_TIMEOUT)
        except TimeoutError:
            proc.kill()
            raise PreviewError("Making the preview took too long.") from None
        return proc.returncode or 0, err.decode(errors="replace")
    except (httpx.HTTPError, PlexError) as exc:
        raise PreviewError("Plex couldn't be reached for the file.") from exc
    finally:
        await asyncio.to_thread(Path(name).unlink, missing_ok=True)


async def make_clip(
    settings: Settings, client: PlexServerClient, part_key: str, target: Path
) -> int:
    """Cut the clip to `target`; returns the intro's leading silence in ms."""
    exe = ffmpeg_exe(settings)
    await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
    partial = target.with_suffix(".part.m4a")
    async with _semaphore:
        code, log_text = await _run_piped(exe, client, part_key, partial)
        if code != 0 or not partial.exists() or partial.stat().st_size == 0:
            log.debug("piped preview failed (%s), downloading: %s", code, log_text[-500:])
            code, log_text = await _run_downloaded(exe, client, part_key, partial)
        if code != 0 or not partial.exists() or partial.stat().st_size == 0:
            partial.unlink(missing_ok=True)
            log.warning("preview of %s failed: %s", part_key, log_text[-800:])
            raise PreviewError("This song's file couldn't be read.")
    partial.replace(target)
    return leading_silence_ms(log_text)


async def get_clip(
    db: AsyncSession,
    settings: Settings,
    client: PlexServerClient,
    track: Track,
) -> Clip:
    """The track's cached clip, made (and its intro measured) the first time."""
    path = clip_path(settings, track.server_id, track.rating_key)
    async with _locks.setdefault((track.server_id, track.rating_key), asyncio.Lock()):
        intro = await db.get(TrackIntro, (track.server_id, track.rating_key))
        if intro is not None and await asyncio.to_thread(path.exists):
            return Clip(path, intro.leading_silence_ms)
        if not track.part_key:
            raise PreviewError("Plex has no file for this song.")
        silence = await make_clip(settings, client, track.part_key, path)
        stmt = insert(TrackIntro).values(
            server_id=track.server_id,
            rating_key=track.rating_key,
            leading_silence_ms=silence,
            analysed_at=dt.datetime.now(dt.UTC),
        )
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=[TrackIntro.server_id, TrackIntro.rating_key],
                set_={
                    "leading_silence_ms": stmt.excluded.leading_silence_ms,
                    "analysed_at": stmt.excluded.analysed_at,
                },
            )
        )
        await db.commit()
        return Clip(path, silence)


def trim_cache(settings: Settings, max_bytes: int = MAX_CACHE_BYTES) -> int:
    """Remove the least recently made clips beyond `max_bytes`. Returns files removed."""
    root = previews_dir(settings)
    if not root.exists():
        return 0
    files = sorted((p for p in root.rglob("*.m4a") if p.is_file()), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    removed = 0
    for path in files:
        if total <= max_bytes:
            break
        total -= path.stat().st_size
        path.unlink(missing_ok=True)
        removed += 1
    return removed
