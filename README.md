# Plekstender

A self-hosted helper app for Plex & Plexamp music libraries: rate songs, albums and artists quickly, keep a listening history with stats, replace artist photos with logos, and build playlists that are smarter than "artist title contains…" filters. Several people can use one instance, each with their own Plex account.

Plekstender isn't affiliated with or endorsed by Plex.

## What it does

- **Library:** browse artists, albums and songs from your Plex servers, with your own ratings and plays. Releases are grouped into albums, EPs, singles, compilations and live albums.
- **Rate:** swipe cards on a phone (right for 2½–5 stars, left for ½–2, down to skip) or rate 15 songs at a time on a computer. Choose songs you've played, unrated ones or everything, by genre, shuffled or most played first. Ratings are saved to Plex with your own account, with undo. Turn on **Play songs** to hear each card as you rate it (for albums and artists, one of their most popular songs). Or play **Guess the intro**: a song plays with the card hidden, and you have 30 seconds to name the song, album or artist before rating it.
- **History:** every Plex play, plus your Last.fm and ListenBrainz history, with each play counted once. Top artists, albums and songs per period, listening activity, streaks, most skipped songs, and a map of where you listen (world, continent and country).
- **Scrobbling:** sends new Plex plays to ListenBrainz, and can send your whole history there (leaving out what ListenBrainz already has).
- **Artwork:** replace artist photos with logos from fanart.tv, Plex, an image search, an upload or a text logo in a Google font; fit, zoom and recolour them; optionally save `artist.jpg` into each artist's folder; share logo packs between servers.
- **Previews:** 40-second clips of any song (cut from the file by ffmpeg, starting where the music does), in Rate and the playlist builder.
- **Playlists:** start from artists, songs or a genre, add similar and sonically similar artists, keep only what you rate highly or each artist's best-known songs, and save the result to Plex. Playlists can refresh themselves daily, weekly or monthly, and download as M3U8.
- **Music files** (server owners, with the music folder mounted on the worker): scan your files' tags and see them on album pages next to Plex's metadata, differences highlighted. Once turned on (Settings → Music files): edit tags, write Plex's metadata (and, if you like, your ratings) into the files for the whole library or an artist, or pick an album's release on MusicBrainz (as in Picard) and write its tags and IDs. Files with missing tags can be identified by how they sound (AcoustID), and files can be renamed and moved into folders from a template (with a dry run first). Every change is previewed first, recorded, and can be undone.
- **Exports:** your library with ratings and plays (CSV / JSON), and your listening history in ListenBrainz's format.

See [plan.md](plan.md) for the roadmap and [app-features.md](app-features.md) for the original feature list.

## Running with Docker

```bash
cp .env.example .env
# Fill in APP_URL, APP_SECRET_KEY and POSTGRES_PASSWORD (see comments in the file)
docker compose up -d --build
```

Open `APP_URL` and sign in with Plex. The first account to sign in becomes the instance admin (set `INITIAL_ADMIN` to your Plex username or email if the instance is reachable from the internet before then). Then connect a Plex server and choose its music library.

Services:

| Service | What it does |
|---|---|
| `db` | Postgres 18 |
| `db-init` | Applies database migrations, then exits |
| `frontend` | The web app and its API (port 8080) |
| `worker` | Background jobs |
| `searxng` | Optional (`--profile search`): image search for artist logos, internal only |
| `traefik` | Optional (`--profile traefik`): HTTPS reverse proxy with Let's Encrypt |

Already running Traefik? Copy `compose.override.example.yaml` to `compose.override.yaml` and adjust.

### Using the published image

Images for amd64 and arm64 are published to `ghcr.io/joakimkingstrom/plekstender` after each successful CI run on `main` (`latest`, `sha-<commit>`, and version tags). To run one instead of building locally, add this to `.env`:

```bash
PLEKSTENDER_IMAGE=ghcr.io/joakimkingstrom/plekstender:latest
PLEKSTENDER_PULL_POLICY=always
```

New GitHub packages are private: make the package public on GitHub, or `docker login ghcr.io` on the host first.

### On a phone

Open your Plekstender address in Safari and choose **Share → Add to Home Screen** (Chrome: **⋮ → Add to Home screen**). Opened from its icon, it runs full screen like an app and starts on the Rate page (Settings → Appearance changes that). The app's shell is kept for offline use; ratings and data need a connection.

### Optional settings

- **fanart.tv:** artist logos need a free API key (Settings → Connections, or `FANART_API_KEY`).
- **Last.fm import:** needs an API key (Settings → Connections, or `LASTFM_API_KEY`). From 1 December 2026, Last.fm only allows this with a Last.fm Pro subscriber's key.
- **Identifying files by sound:** needs a free AcoustID application key (register one at [acoustid.org/new-application](https://acoustid.org/new-application); Settings → Connections, or `ACOUSTID_API_KEY`). The Docker image includes Chromaprint's `fpcalc`; elsewhere, install it or set `FPCALC_PATH`.
- **Live (upcoming shows):** needs a JamBase Data API key (the free Developer plan at [data.jambase.com](https://data.jambase.com) is for non-commercial use; Settings → Connections, or `JAMBASE_API_KEY`). It has 1,000 requests a month and bills requests past that, so Plekstender stops at `JAMBASE_MONTHLY_CAP` (default 900) and spreads each person's daily refresh over the month. Places are searched with Open-Meteo's free geocoding (only the typed text is sent).
- **Image search:** start with `--profile search` to run SearXNG next to Plekstender, or point `SEARXNG_URL` at your own.
- **Artist folders:** to save `artist.jpg` next to your music, mount the music folder read-write into the **worker** container only (see `compose.override.example.yaml`; the web app never touches music files) and set it up in Settings → Music files.

### Who can sign in

On Settings → Admin, choose one of:

- **Approved servers** (default): people with access to a Plex server you've approved, such as friends you share your server with.
- **Allowlist**: only the Plex usernames or emails you list.
- **Open**: any Plex account.

Plex tokens are stored encrypted with a key derived from `APP_SECRET_KEY` and are never sent to the browser.

## Development

Requirements: Python 3.14 with [uv](https://docs.astral.sh/uv/), Node 24+, and a Postgres database.

```bash
# Backend (http://localhost:8080)
cd backend
uv sync
export APP_SECRET_KEY=$(openssl rand -hex 32) APP_URL=http://localhost:5173 \
       DATABASE_URL=postgresql://plekstender:plekstender@localhost:5432/plekstender
uv run plekstender migrate
uv run plekstender web          # API
uv run plekstender worker       # background jobs (separate terminal)

# Frontend (http://localhost:5173, proxies /api to the backend)
cd frontend
npm install
npm run dev
```

Checks:

```bash
cd backend && uv run ruff check . && uv run mypy && \
  TEST_DATABASE_URL=postgresql://…/plekstender_test uv run pytest   # wipes that database
cd frontend && npm run lint && npm run typecheck && npm test
```

After changing API routes or schemas, regenerate the frontend types with `npm run gen:api`.
New database migrations: `cd backend && uv run alembic revision --autogenerate -m "…"`.

## Credits

- IP geolocation by [DB-IP](https://db-ip.com) (IP to City Lite, CC BY 4.0), downloaded monthly to the server; lookups never leave it.
- Country shapes from [Natural Earth](https://www.naturalearthdata.com) (public domain); rebuild with `frontend/scripts/world-map.mjs`.
- Artist logos from [fanart.tv](https://fanart.tv), artist IDs and releases from [MusicBrainz](https://musicbrainz.org) (CC0), release covers from the [Cover Art Archive](https://coverartarchive.org), fingerprints by [Chromaprint](https://acoustid.org/chromaprint) and matched by [AcoustID](https://acoustid.org), fonts from [Google Fonts](https://fonts.google.com). Images keep their own terms; logo packs record where each one came from.

## License

MIT
