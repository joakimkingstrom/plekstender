# Plekstender — Implementation Plan

Companion to [app-features.md](app-features.md). This document covers *how* the app gets built; the feature doc covers *what* it does.

## Decisions

| Topic | Decision |
|---|---|
| Name | **Plekstender** (renamed from RoadieFM on 18 Nov 2026). "for Plex & Plexamp" is fine in descriptions; Plex's trademark guidelines discourage "Plex" in app names. Logo: the guitar pick in `branding/`. |
| Stack | FastAPI (Python) backend + React/Vite/TypeScript frontend |
| Users | Multi-user: anyone with a Plex account can sign in and connect a server they have access to. Must be safe to expose publicly. |
| Listening history | Plekstender keeps its own per-user listening history (Plex plays + imported history) and shows stats on it (Phase 2). Inspired by Koito, but built natively: Koito is single-user, Go + SQLite, with its own catalogue. |
| Last.fm | One-off history import only, no scrobbling to Last.fm. From 1 Dec 2026 a Last.fm API key only works if its owner has Last.fm Pro, so the importer ships first. |
| ListenBrainz | New plays are scrobbled to ListenBrainz (opt-in per user, with their own token). ListenBrainz history can also be imported. |
| Image search | SearXNG as an optional, internal-only service (compose profile), behind a pluggable provider interface. |

## Architecture

```
            ┌──────────── traefik (optional, profile) ────────────┐
            │                                                      │
browser ──► frontend  (FastAPI: /api + built React SPA)            │
               │  ▲                                                │
               ▼  │                                                │
              db (Postgres) ◄── worker (same image, job runner)    │
               ▲                    │                              │
           db-init (migrations)     ├──► Plex servers (users')     │
                                    ├──► fanart.tv / MusicBrainz   │
                                    └──► searxng (internal only)   │
```

### Services (compose.yaml)

| Service | Image | Role |
|---|---|---|
| `db` | `postgres:18` | Primary store. Healthcheck with `pg_isready`. |
| `db-init` | app image | Runs `alembic upgrade head`, then exits. Others depend on `service_completed_successfully`. |
| `frontend` | app image | FastAPI (uvicorn) serving `/api/*` and the static SPA build. Only service with an exposed port. |
| `worker` | app image | Procrastinate worker (Postgres-backed queue, supports periodic tasks; no Redis needed). |
| `searxng` | `searxng/searxng` | Profile `search`. No published port, internal network only. |
| `traefik` | `traefik:v3` | Profile `traefik`. For people without an existing reverse proxy. Labels on `frontend` also work with an existing Traefik. |

One Docker image is built from the repo (multi-stage: Node builds the SPA → Python runtime image copies `dist/`). `frontend`, `worker` and `db-init` differ only in `command`.

Networks: `internal` (db, worker, frontend, searxng — `internal: true` where possible) and `web` (frontend, traefik).

Container hardening (as in other stacks): non-root user, `read_only: true` + `tmpfs: /tmp`, `cap_drop: ALL`, `no-new-privileges`, healthchecks on every long-running service.

### Repo layout

```
plex-helper-app/                     # repo (app name: Plekstender)
├── compose.yaml                     # db, db-init, frontend, worker (+ searxng, traefik profiles)
├── compose.override.example.yaml    # running behind an existing Traefik
├── .env.example
├── Dockerfile                       # multi-stage: web build + python runtime, one image
├── searxng/settings.yml
├── backend/
│   ├── pyproject.toml / uv.lock     # uv-managed, Python 3.14
│   ├── alembic.ini                  # for `alembic revision --autogenerate` in development
│   ├── scripts/spikes/              # Phase 0 probes to run against a real server
│   ├── tests/
│   └── src/plekstender/
│       ├── cli.py                   # plekstender web | worker | migrate | healthcheck | openapi
│       ├── config.py                # pydantic-settings
│       ├── migrate.py               # Alembic + procrastinate schema management
│       ├── migrations/              # Alembic revisions
│       ├── worker.py                # procrastinate app + periodic tasks
│       ├── api/                     # FastAPI app, middleware, routes, schemas
│       ├── auth/                    # access policy, sessions
│       ├── db/                      # SQLAlchemy 2.0 models, instance settings
│       ├── plex/                    # plex.tv + Plex server clients, connection picker
│       ├── security/                # token encryption, signing
│       │   (later phases)
│       ├── sync/                    # library mirror
│       ├── ratings/                 # queue builders, rating writes
│       ├── artwork/                 # port of plex-artist-logos
│       ├── playlists/               # definitions, builder, scheduler
│       ├── export/                  # metadata + artwork export/import
│       └── search/                  # image search providers (searxng, brave, none)
└── frontend/
    ├── package.json                 # Vite 8, React 19, TypeScript 7, oxlint, vitest
    └── src/
        ├── api/                     # openapi-fetch client, generated schema.d.ts, query hooks
        ├── auth/                    # Plex sign-in flow
        ├── components/              # layout, UI primitives
        └── pages/
```

Frontend libraries: React Router, TanStack Query, Tailwind v4, `openapi-fetch` with types generated by `openapi-typescript` (`npm run gen:api`; run via `npx` because it needs TypeScript 5 while the app uses 7). Later: `@use-gesture/react` + `framer-motion` (swipe), `vite-plugin-pwa` (installable on mobile).

## Core idea: a local mirror of the Plex library

Almost every feature (weighted rating queues, "top 10 artists in genre X", popularity percentiles, similar-artist expansion, metadata backup) is a database query once the library is mirrored locally, and slow or impossible against the Plex API. So **library sync is built first** and the metadata-backup feature is a by-product.

- Full sync: walk the music section(s) for artists, albums and tracks, including genres, moods, styles, popularity, MBIDs and similar artists.
- Incremental sync: periodic job using `updatedAt` (metadata) and `lastViewedAt` / `lastRatedAt` (personal state) greater than the last sync; manual "sync now" button.
- Per-user state (ratings, play counts, last played) is per Plex account. It is synced with *each user's* token into a separate table.
- Ratings written in the app go to Plex first, then the mirror is updated (Plex stays the source of truth).

### Data model (first cut)

- `users`: Plex account id, username, thumb, `is_instance_admin`
- `sessions`: server-side sessions (id, user_id, expires_at, user agent)
- `plex_servers`: machine identifier, name, owner account id
- `user_servers`: user ↔ server, encrypted access token, `is_owner`, preferred connection URI, selected music section(s)
- `server_settings`: library path mapping (Plex path → container path), capability flags
- `artists`, `albums`, `tracks`: keyed by `(server_id, rating_key)`; title, sort title, parent keys, MBID/guid, year, duration, summary, popularity (tracks: Plex `ratingCount`; albums/artists: derived from their tracks), thumb path, `raw jsonb`
- `tags` + `item_tags`: genres/moods/styles (normalised, per server)
- `similar_artists`: `(server_id, artist_key, similar_name, similar_key nullable, source)`
- `user_item_state`: `(user_id, server_id, item_type, rating_key)` → user_rating, view_count, last_viewed_at
- `rating_events`: append-only log (enables undo and history)
- `artist_artwork`: status (todo/custom/done), source, transform settings jsonb, image hash, updated_by
- `playlists`: owner, server, Plex playlist key, name, `definition jsonb`, schedule, last build result
- `sync_runs`: job bookkeeping and progress for the UI
- `listens`: one row per play per user: time, artist/album/track names as played, MBIDs, duration, source + source reference, matched `(server_id, track rating_key)` (nullable), `duplicate_of`, ListenBrainz submission status
- `listen_imports`: import jobs (Last.fm, ListenBrainz) with resumable cursor, progress and counts
- `user_integrations`: per-user Last.fm username, encrypted ListenBrainz token, scrobbling on/off

## Authentication, multi-user & security

### Sign-in
1. Plex PIN flow: `POST plex.tv/api/v2/pins` → redirect to `app.plex.tv/auth#?clientID=…&code=…&forwardUrl=…` → poll/confirm the PIN → user token.
2. `GET plex.tv/api/v2/resources?includeHttps=1` → list servers (with per-server access tokens) → user picks a server and music library.
3. Connection picker: the backend tests the server's connections in order (local → remote → relay) **from the backend's point of view** and stores the first that responds.

### Instance access control
- The first user to sign in becomes the instance admin.
- `ACCESS_MODE` setting: `open` (any Plex user), `servers` (only users who have access to admin-approved servers, e.g. friends sharing your server), `allowlist` (explicit Plex usernames). Default: `servers`.

### Security requirements
- **Tokens never reach the browser.** Plex and plex.tv tokens are encrypted at rest (AES-GCM, key from `APP_SECRET_KEY`) and only used by the backend.
- **Image proxy only for allowed Plex paths.** `/api/servers/{id}/image?path=/library/metadata/123/thumb/…` checks session ↔ server, accepts only `/library/…` and `/photo/:/transcode` paths, and resizes through Plex's transcoder. No "fetch any URL" endpoint.
- **Safe outbound fetcher** for fanart.tv, search results and user-provided URLs: http(s) only, DNS resolution checked against private/loopback/link-local ranges (blocks requests into the internal network, i.e. SSRF), max size (e.g. 15 MB), content-type sniffing with Pillow, timeouts everywhere.
- **Uploads:** size limit, re-encoded through Pillow (strips anything that isn't an image), stored under random names.
- **Authorization:** every request is scoped to a `(user, server)` pair the user owns a token for, and writes use *that user's* token, so Plex enforces its own permissions. Server-wide actions (artist images, folder writes) require `is_owner`, i.e. the server admin.
- **Sessions:** httpOnly, `Secure`, `SameSite=Lax` cookie; state-changing requests also require a custom header (CSRF protection).
- **Rate limits:** sign-in endpoints, image search, bulk jobs (per user, per server).
- **Tests:** per-user isolation tests (user A cannot read or modify user B's playlists, ratings or servers).

### Capability flags per server
Some features only work when the app runs next to the Plex server:
- `can_write_artwork_to_folders`: requires the music share mounted into the container plus a path mapping.
- `is_owner`: required for changing artist posters.

The UI hides or disables features based on these flags rather than failing.

## Features

### 1. Ratings

**Scale:** Plex `userRating` is 0–10 (half stars = 1 unit). Write via `PUT /:/rate?key=…&identifier=com.plexapp.plugins.library&rating=…`.

**Item pools** (songs / albums / artists × 3 modes):
- *Listened*: `view_count > 0`, or listened to according to Plekstender's history (Phase 2), which includes imported Last.fm plays
- *Unrated*: `user_rating IS NULL`
- *Everything*: all items; already-rated ones carry a "Rated ★3.5" badge plus a skip action

**Ordering strategies** (for *Listened*):
- *Weighted random*: `ORDER BY -ln(random()) / weight` where `weight = (view_count + 1) ^ α` (weighted sampling in one query; α ≈ 0.7 by default, tunable).
- *Most played* and *Most popular*, with artists interleaved:
  1. Rank each artist's eligible items by the metric (plays or popularity).
  2. Build the queue in batches: draw an artist with weight `artist_metric ^ β`, take that artist's next item, repeat. Optionally cap consecutive items from the same artist (e.g. no artist twice within 5).
  3. Result: popular items come first, but the artist order is mixed.
- Queues are generated server-side in pages (e.g. 50), with a cursor that stores the seed and the already-served keys.

**Mobile (one card at a time):**
- Card: album cover (songs, albums) or artist image (artists).
- Swipe left maps distance to 2.0 → 0.5; swipe right maps to 2.5 → 5.0; half-star steps; live star readout and colour while dragging; haptic tick per step (`navigator.vibrate` where supported).
- Swipe down = skip. Undo button (uses `rating_events`).
- Toggle to switch to the desktop layout.

**Desktop:**
- *Songs*: grid sized to the viewport using `ResizeObserver` (row count = available height / row height), no scrolling, "next page" when the page is done; keyboard shortcuts (1–5, shift = half star).
- *Albums*: cover, title, artist, review, tracklist with per-track star widgets. Hovering the cover shows a 5-star overlay; the horizontal click position sets the rating (rounded to half stars).
- *Artists*: artist image, biography, most popular albums (each with stars), top 5 songs (each with stars).

### 2. Artist artwork (port of plex-artist-logos)

Port `logic.py` mostly as-is (transform pipeline, text logo generation, font download), rewritten as a service module with tests. The Jinja UI is rebuilt in React.

**Sources (pluggable `ArtworkSource` interface):**
- fanart.tv (`hdmusiclogo` + `musiclogo`, **sorted by `likes`**). If there's no MBID on the Plex artist, look it up on MusicBrainz by name (rate limited to 1 req/s, proper User-Agent).
- Existing Plex posters for the artist.
- Image search ("<artist> logo"), via SearXNG or another provider (see below).
- Upload.
- Text generator with Google Fonts.

**Editor:** current vs. preview, invert / greyscale / white mask / tint / contrast / zoom / default-size, status (todo / custom / done), bulk "apply most-liked fanart logo" as a background job with progress.

**Save targets:** Plex poster upload; optionally also `artist.jpg` in the artist folder (`can_write_artwork_to_folders`). The folder path comes from a track's file path → artist directory → path mapping.

**Export / import:**
- Export: a zip containing `manifest.json` (`version`, entries with `mbid`, `artist_name`, `normalised_name`, `file`, `sha256`, `source`) plus the images. Runs as a background job; download link when done.
- Import: upload the zip → match by MBID, then normalised name (fuzzy, via `rapidfuzz`) → preview table (matched / ambiguous / unmatched, with existing-vs-incoming thumbnails) → apply the selection as a job.
- Because entries are keyed by MBID, logo packs can be shared between different servers.

**Fixes for bugs found in the current app:**
- Artist names containing `'` break inline `onclick` handlers (e.g. "Guns N' Roses").
- `bulk_apply_fanart` doesn't update `.status`.
- fanart logos aren't sorted by likes, so "most popular" means "first returned".
- Plex URLs including `X-Plex-Token` are returned to the browser; `/proxy_image` will fetch any URL.
- The Plex connection is made once at import time and never retried.
- The index page walks the filesystem for every artist on every load; status is keyed by a sanitised title, so different artists can collide.
- Missing timeouts on several `requests.get` calls.

### 3. Image search (SearXNG)

- `ImageSearchProvider` interface; implementations: `searxng` (default when `SEARXNG_URL` is set), `brave` (API key), `none`.
- SearXNG config shipped in the repo (`searxng/settings.yml`):
  - JSON output format enabled (`search.formats: [html, json]`)
  - only image engines enabled (bing, duckduckgo and qwant images; google images ships disabled because Google blocks most server IPs)
  - limiter off (it's not publicly reachable), `server.secret_key` from env
  - no published port and no Traefik labels: only `frontend`/`worker` can reach it
- Results go through the safe outbound fetcher before being shown (proxied thumbnails, so the user's browser never connects to random hosts).
- Requests are cached (`image_search_cache` table, ~30 days) and rate limited, so bulk use doesn't get the instance's IP blocked by upstream engines.

### 4. Smart playlists (static, auto-refreshing)

**Definition (stored as JSON):**
```jsonc
{
  "seeds": [{"type": "artist", "key": "123"}, {"type": "track", "key": "456"}],
  "expand": {"similar_artists": true, "sonically_similar": true, "max_similar": 15},
  "filters": {
    "op": "AND",
    "rules": [
      {"field": "user_rating", "op": ">=", "value": 7, "include_unrated": true},
      {"field": "popularity_pct_within_artist", "op": "top", "value": 30}
    ]
  },
  "limit": 500
}
```

**Builder flow (UI):**
1. Pick artist(s) or song(s) directly, **or** pick a genre/mood → show the top 10 artists for it (by plays and popularity in the mirror) → select one or more.
2. Suggested similar artists/songs (Plex `similar` + sonically-similar endpoints for artists, albums and tracks) shown as chips to accept or reject (Spotify "add similar songs" style).
3. Filters: rating threshold (± unrated), top N% per artist by popularity, combined with AND/OR (nested groups later if needed).
4. Live preview: track count, artist distribution, sample tracks.
5. Save → the worker creates the Plex playlist under the user's account.

**Refresh:** manual button, or a schedule (daily / weekly / monthly / cron) run by the worker; rebuilding replaces the playlist items in place, so the Plex playlist key stays the same and Plexamp pins keep working.

### 5. Metadata backup & export

- The mirror *is* the backup; scheduled sync keeps it current.
- Export formats (jobs → downloadable files):
  - JSON (lossless dump incl. `raw` Plex payloads)
  - CSV (tracks with ratings/plays/MBIDs, readable by spreadsheets and most importers)
  - M3U8 playlists (with path mapping options)
  - ListenBrainz-compatible listen history (for plays with known timestamps)
- Later: write ratings into file tags (ID3 `POPM`, Vorbis `RATING`/`FMPS_RATING`) so other players can read them.

### 6. Listening history & stats

Plekstender keeps every user's plays in its own `listens` table. Stats, the ratings "Listened" pool and play-count weighting all read from it, so years of Last.fm history count without writing anything into Plex.

**Where plays come from**
- **Plex play history** (main source). The worker polls `/status/sessions/history/all` every few minutes per server with a `viewedAt>` watermark, and backfills all existing music history the first time. Plex records a play once a track is ~90% played, so these are finished listens.
  - The server owner's token sees every account's plays; `accountID` is mapped to Plekstender users (the owner is account 1, others by plex.tv account ID). Plays by accounts that don't use Plekstender are not stored.
  - If the owner doesn't use Plekstender, try the shared user's own token (to be confirmed by the spike). Last resort: derive plays from `view_count` increases during incremental sync, timed by `last_viewed_at` (approximate; marked as such).
  - Plex's `viewedAt` is when the track finished; Last.fm and ListenBrainz use the start time. Stored as start time (`viewedAt − duration`), to be checked against real data.
- **Last.fm import** (one-off per user, see below).
- **ListenBrainz import**: through the API (`/1/user/{name}/listens`, free) or an uploaded ListenBrainz export. This is also the route after 1 Dec for anyone who moved their Last.fm history into ListenBrainz with its own importer.

**Matching to the library.** Each listen keeps the names it was played under, so plays of music that isn't (or is no longer) in the library still count. Matching tries recording MBID → normalised artist + title (+ album to pick the release) and otherwise leaves the listen unmatched. A re-match job runs after syncs, so newly added albums pick up their old plays. Same song on several releases: prefer the album from the listen, otherwise the original album.

**Duplicates across sources.** Plexamp plays that were also scrobbled to Last.fm appear twice. Two listens by the same user of the same normalised artist + title within *duration + 5 minutes* of each other are one play: the Plex one is kept and the other gets `duplicate_of`, so stats skip it.

**Last.fm import**
- Instance API key from `LASTFM_API_KEY` or Settings (admin, encrypted). From 1 Dec 2026 the key's owner needs Last.fm Pro; without it the import page says so and points to ListenBrainz import.
- The user enters their Last.fm username (checked with `user.getInfo`, which also gives the total to show progress). The job pages backwards through `user.getRecentTracks` (200 per page, `to=` cursor, ≤5 requests/s), saving the cursor so it resumes after restarts. ~100k scrobbles ≈ 500 requests, a few minutes. Running it again only fetches newer scrobbles.
- Profiles with hidden recent listening can't be read with an API key alone; the job fails with that explanation.
- Any public username can be entered. That only affects the importer's own stats, so it isn't verified.

**Scrobbling to ListenBrainz**
- Per user, opt-in: paste the user token from listenbrainz.org/settings, checked with `/1/validate-token` and stored encrypted (purpose `user.listenbrainz_token`).
- New Plex listens go into an outbox and are sent in batches as `single` listens: names, `duration_ms`, MBIDs when known (recording/release/artist), `media_player` from the Plex device when known, `submission_client: "Plekstender"`. Retries with backoff, honours `X-RateLimit-*` headers; a rejected token pauses scrobbling and shows a warning.
- Only plays after scrobbling was turned on are sent. Imported Last.fm history isn't sent (ListenBrainz has its own Last.fm importer); sending it as `import` listens can be an explicit option later.
- Warn that scrobbling from another tool too (e.g. multi-scrobbler) causes doubles.
- Later: "playing now" from `/status/sessions`.

**Stats (per user)**
- Overview for a period (7 days, 30 days, year, all time, custom): listens, listening time, top artists / albums / tracks with change vs the previous period.
- Activity: listens per day / week / month, weekday × hour heatmap, streaks.
- Artist, album and track pages get "your listens": count, first and last listen, plays over time.
- History: list of listens with source badges and unmatched plays, deletable.
- Day boundaries use the user's time zone (from the browser, stored per user).
- Plain aggregates over `(user_id, listened_at)` indexes; daily rollups only if it gets slow.
- Later: year in review.

**Privacy.** Listens are only visible to the user who made them, not to other users or admins. Each user can export their history (ListenBrainz JSONL) and delete all of it.

## Phasing

### Phase 0 — Skeleton & spikes
- [x] Repo scaffold (backend, frontend, Dockerfile, compose with all services and profiles, `.env.example`)
- [x] Alembic setup, `db-init` flow (also installs/upgrades procrastinate's schema), Procrastinate worker with heartbeat + expired-session cleanup tasks
- [x] Plex PIN sign-in (popup, with full-page redirect fallback for mobile), server picker, connection picker, encrypted token storage, sessions
- [x] Instance access modes + first-user admin (optionally pinned with `INITIAL_ADMIN`), admin page
- [x] CI: lint (ruff, oxlint), type check (mypy strict, tsc), tests (pytest against Postgres, vitest), API-types drift check, image build
- [x] Verify the Docker image and compose stack on a Docker host (docker-02: image builds, db-init migrates, web app and worker healthy)
- [ ] Spikes (scripts in `backend/scripts/spikes/`, to be run against a real server):
  - [x] What popularity fields Plex exposes on tracks / albums / artists (`plex_probe.py`)
  - [x] Similar and sonically-similar endpoints (`plex_probe.py`)
  - [x] Confirm that fetching a track file directly does not create a play (`plex_probe.py --stream-test`)
  - [x] SearXNG image result quality for "<artist> logo" (`searxng_probe.py`)

#### Spike results (Plex Media Server 1.43, Plex Pass, ~1.3k artists / 7.3k albums / 98k tracks)

| Question | Finding | Consequence |
|---|---|---|
| Popularity fields | `ratingCount` (Last.fm listener count) exists **only on tracks**, and 97% of tracks have it. Albums and artists have no popularity field. | Store track popularity in the mirror and **derive** album and artist popularity from their tracks (e.g. sum of the top 3 tracks for albums, top 10 for artists; settle the formula in Phase 1). |
| Duplicate recordings | Different tracks share identical `ratingCount` values: likely the same recording on several releases (compilations, remasters). | Rating queues and playlists should group a recording's duplicates (artist + normalised title), preferring the original album, so the same song doesn't show up twice. |
| Genres / moods / styles | On artists and albums (e.g. 10 moods on one artist), **not on tracks**. | Track-level genre and mood filters inherit from the album, falling back to the artist. |
| Similar artists | Artist metadata includes a `Similar` list (30 for the sampled artist). | Used for "add similar artists" in playlists. |
| Artist page hubs | `artist.mostpopulartracks` (10 tracks) exists. | Not needed (popularity is in the mirror), but useful as a cross-check. |
| Sonic similarity | `/library/metadata/{id}/nearest` works for artists, albums and tracks. | Use it when available; servers without Plex Pass or sonic analysis fall back to `Similar` plus shared genres and styles. |
| Critic ratings | Albums have `rating` (critic score) for about two-thirds of the sample. | Show it next to the album review in the album rating view. |
| Existing ratings | Most sampled artists, albums and tracks already have a `userRating`. | The "Everything" rating mode, with already-rated items labelled, matters. |
| Preview playback | Downloading the first 512 KB of a track file changed neither `viewCount`, `lastViewedAt` nor play history. | Previews through direct file access don't affect stats. Re-check once the real preview (ffmpeg reading ~45 s) exists. |
| Image search: engines | From a home server, Google Images returned "access denied" on every query and SearXNG suspended it. Bing, DuckDuckGo and Qwant images each worked, together returning ~150 results per artist, with many duplicates across engines. | Google is off in the shipped `settings.yml`. Deduplicate results by image URL. |
| Image search: quality | For well-known artists (Radiohead, The Beatles) the top results are real logos from logo sites, often large PNGs. For smaller artists (Nathaniel Rateliff, St. Paul & The Broken Bones) real logos are mixed with merch (TeePublic, Redbubble, Etsy, Amazon), tour posters and ticket-site banners. | Search is a source of **candidates to pick from**, never used by the bulk "apply automatically" action. Rank candidates: PNG/SVG and "logo" in the title first; merch and ticket sites last. fanart.tv stays the first choice. |

### Phase 1 — Library mirror
- [x] Full + incremental sync jobs with progress UI
- [x] Per-user state sync
- [x] Basic library browser (needed by later features anyway)
- [x] JSON/CSV export
- [ ] Check the popularity ranking and a first full sync against the real library

#### How Phase 1 works

- **Shared metadata, personal visibility.** Artists, albums, tracks, tags and similar artists are stored once per server. Each user's own sync records `user_item_state` rows for every item *their* token can list, and every library query joins on them. A shared user restricted to part of a library only sees that part, even if the owner's sync stored the rest.
- **Who may delete.** Only a server owner's full sync removes metadata for items that are gone; other users' full syncs only drop their own visibility rows.
- **When syncs run.** A full sync is queued when a library is first chosen. After that the worker checks every 5 minutes and queues an incremental sync every 30 minutes and a full one every 7 days per connection (both adjustable on Settings → Library), plus "Sync changes" / "Full resync" buttons. It waits a full interval after any attempt, so a failing server isn't retried on every check. Syncs for the same server never overlap.
- **Incremental sync** fetches items with `updatedAt`, `lastViewedAt` or `lastRatedAt` after the previous sync (minus 5 minutes), and re-reads all personal state if a server rejects those filters. Deletions are only detected by full syncs.
- **Details** (genres, moods, styles, countries, similar artists) come from batched `/library/metadata/{k1,k2,...}` requests, 50 items each, falling back to one item per request.
- **Popularity:** album = sum of its top 3 tracks; artist = sum of its top 10 songs, with releases of the same song (normalised title) counted once. Admins can change both numbers on Settings → Library, with a live preview of the resulting top artists; saving re-scores every server in a background job.
- **Exports** stream straight from the database: `tracks.csv` and `library.json` (lossless, with tags, similar artists and personal state).
- **Benchmark** against a generated 101k-track library on a laptop: full sync ~56 s and ~400 Plex requests; incremental sync 0.2 s and ~10 requests.

### Phase 2 — Listening history
Ordered by the Last.fm deadline (1 Dec 2026):
- [x] **Before 1 Dec:** `listens` + `listen_imports`, Last.fm API key (Settings → Listening or `LASTFM_API_KEY`), per-user Last.fm import job with progress UI, resume after failures, History page, delete one / imported / all listens
- [ ] Link listens to library items (artist / album / track pages, "not in library" in stats)
- [x] Spike: `plex_probe.py --history` (owner token; a shared user's token not tested yet)
  - **41,746 music plays back to 30 Jun 2021** in library 3 (of 45k history entries in total). Library 7 has none.
  - Filtering: `type=10` returns nothing and `metadataType=10` is ignored, but `librarySectionID` and `viewedAt>` both work, so capture polls per music library with a `viewedAt` watermark. `accountID=1` returned the full total; either every play is the owner's or the filter is ignored, so filter client-side.
  - Every entry has `historyKey` (dedupe key), `ratingKey` (all 50 sampled still exist), `viewedAt`, `accountID`, `deviceID` and album / album-artist titles. There's no `originalTitle`, so compilation tracks only name the album artist: take the track artist from the mirror via `ratingKey`, falling back to `grandparentTitle`.
  - Accounts: the owner is account `1` (their plex.tv ID isn't listed); 11 other accounts use plex.tv IDs.
  - Consecutive plays are ~4 minutes apart (median), which doesn't settle whether `viewedAt` marks the start or the end of a play. Keep start = `viewedAt − duration` and check against Last.fm duplicates once both are imported.
- [x] Plex play capture: history polling + backfill, account mapping, cross-source duplicates
  - Every 5 minutes the worker reads each server's history per music library (oldest first, from 10 minutes before the newest play seen; the first read backfills everything). It waits until the connection's libraries have had a full sync, since names and durations come from the mirror.
  - Only the owner's connection is read when the owner uses Plekstender (account `1` = owner, others by plex.tv ID); plays by accounts that haven't connected the server are skipped. Without the owner, each user's own connection is read and only their own plays are kept. A shared user's token reading history is still unverified on a real server; failures show on Settings → Listening.
  - Each Plex play is stored once (`user_id, server_id, historyKey`). After a capture or a Last.fm import, imported listens of the same song within its duration + 5 minutes of a Plex play are paired one-to-one (nearest first) and marked `duplicate_of`; stats skip them.
- [x] ListenBrainz scrobbling: token, outbox, batched submit, retries
  - Settings → Listening: paste the user token (checked with `/1/validate-token`, stored encrypted). Plex plays that start after connecting are sent a few minutes after capture, oldest first, 100 per request (`single` for one, `import` for several), with duration, album and `submission_client: "Plekstender"`. A listens-id watermark tracks what's been handled.
  - A listen ListenBrainz refuses (400) is found by resending the batch one by one and skipped. Other failures stay pending and are retried every 15 minutes; a rejected token pauses scrobbling until a new token for the same account is pasted (a different account starts over).
- [x] Stats pages: History → Overview with periods (7 days, 30 days, 12 months, each year since the first listen, all time) in the viewer's time zone; totals with change vs the previous period, new artists, listening streaks; listens over time; top artists / albums / songs with rank changes, linked to the library (and its artwork) where a match exists; weekday × hour heatmap. Duplicates are skipped and listens without a length count as 3:35. ~0.2 s for a year and <1 s for all time on 280k listens.
- [x] Per-item listen stats: artist and album pages show "Your listens" (count, first and last listen, listens per year, an artist's most played songs) and each track row shows your listens of that song. Matched by normalised names (albums also by their track artists, for compilations); remasters count as the same song.
- [x] ListenBrainz import, history export, delete
  - Import: a ListenBrainz username's public listens via `/1/user/{name}/listens` (1,000 per request, newest first with a saved `max_ts` cursor, ~1 request/s, honours 429 `X-RateLimit-Reset-In`); resumable, and a repeat import stops at the previous one's end. ListenBrainz suggests its export API for full histories; paging is fine at this size (~280 requests for 280k listens). Importing an uploaded export file is not done.
  - Duplicates: a second pass pairs ListenBrainz listens with Last.fm scrobbles of the same song within 2 minutes (e.g. history copied by ListenBrainz's Last.fm importer). Plex > Last.fm > ListenBrainz.
  - Export: Settings → Listening → "Download all listens" streams ListenBrainz JSON lines, oldest first, duplicates left out. Delete per source (Last.fm, ListenBrainz) or everything.

### Phase 3 — Ratings
- [x] Queue builders (3 pools × 3 strategies) with tests for distribution and artist interleaving
  - `POST /api/servers/{id}/rate/queue` {kind, pool, order, exclude, limit}: pools *listened* (Plex plays or listens in the history), *unrated*, *all*; orders *weighted* (random, weight `(plays+1)^0.7 × (1 + ln(1 + popularity/1000))`), *plays* and *popular* (top 400 candidates, then artists drawn at random weighted by `(best remaining + 1)^0.6`, no artist twice within 5). Songs released more than once appear once (earliest release; a rating on any release counts). ~0.3 s per page of songs on a 98k-track library.
  - Listen counts per item (`user_item_state.listens`): the history matched to the library by normalised names, refreshed after captures, imports, syncs and deletes. Mirror rows get `artists.name_key`, `albums.title_norm`, `tracks.artist_key` (backfilled by the migration).
  - `POST /ratings` writes to Plex with the user's own token (`PUT /:/rate`, -1 clears), then the mirror, and logs a `rating_events` row; `POST /ratings/undo` steps back one change at a time.
- [x] Mobile swipe UI + undo
  - Rate → Cards: one card at a time (album cover or artist image). Swipe right for 2½–5 stars, left for 2–½ (further = higher / lower, a vibration tick per half star where supported), down to skip; a live star readout while dragging. Stars below the card for tapping or keyboard. Undo steps back through your ratings and brings a swiped card back.
- [x] Desktop songs / albums / artists layouts + layout toggle
  - Lists layout: songs fill the window without scrolling (keys 1–5, Shift for halves, ↑↓ between rows, "Next N songs"); albums show the cover (click across it: left edge 1 star, right edge 5), review and every song with stars; artists show the image (same click rating), biography, six most popular albums and five most popular songs, all rateable. The Cards / Lists toggle works on any screen size; choices are remembered per browser.

### Phase 4 — Artwork ✓
- [x] Port transforms/text logos/fonts with tests
  - `artwork/images.py`: crop to the visible logo, white / greyscale / invert / contrast, fit into 80% of a 1000 px square (or zoom), background colour, tint. Untrusted bytes are decoded by Pillow (PNG, JPEG, WebP, GIF, BMP; 40 MP cap) and re-encoded. `artwork/text.py`: the 44 Google Fonts from plex-artist-logos, downloaded once into `DATA_DIR/fonts` (TrueType), 1–4 rows, case, colour.
- [x] Sources: fanart.tv (+ MusicBrainz fallback), Plex posters, upload, SearXNG
  - fanart.tv HD + SD logos sorted by likes (key from `FANART_API_KEY` or Settings, checked on save); missing MBIDs are looked up on MusicBrainz by exact normalised name (≤1 request/s) and stored. Plex posters via `/library/metadata/{id}/posters`, read through Plex's photo transcoder. SearXNG results ranked (logo / PNG hints first, merch and ticket sites last), cached 30 days. Uploads are re-encoded to PNG under `DATA_DIR/uploads` and removed after a day.
  - Outside images only through `artwork/fetch.py`: http(s) on ports 80/443, every resolved address must be public, redirects re-checked, 15 MB cap. The browser only gets signed (HMAC, 24 h) references, so the image proxy can't be pointed elsewhere.
  - Applying uploads the 1000 px JPEG to Plex (`POST /library/metadata/{id}/posters`), records source / settings / hash in `artist_artwork`, and refreshes the mirror's thumb. Artwork endpoints are owner-only.
- [x] Editor UI and status tracking
  - Artwork page (server owners): artist list with search and To do / Custom / Done filters; per artist, candidates from fanart.tv, Plex, Search, Upload or Text, adjustments (fit or zoom, contrast, invert, greyscale, all white, tint, background), a live preview next to the current image, and "Save to Plex". "Edit current" re-works the image Plex has. fanart.tv key on Settings → Library.
- [x] Bulk apply job: "Apply fanart.tv logos to all To do" (or chosen artists) runs in the worker (`artwork_jobs`, one per server at a time, resumes after a restart), applies each artist's most-liked logo with the default transform and reports applied / no logo / not on MusicBrainz / failed.
- [x] Folder save with path mapping: per server (owner, Artwork page → "Save to artist folders"), Plex's music path is mapped to where the music is mounted in the containers. The artist folder is the common parent of its album folders (one album: its parent); `artist.jpg` is only written when that folder exists, its name matches the artist and (after resolving symlinks) it lies inside the mapped folder. A folder problem never blocks the Plex upload; it's reported with the result. Mount example in `compose.override.example.yaml`.
- [x] Export/import zip with matching preview
  - Export (job): every artist marked Done or Custom, their current Plex image (1000 px JPEG) plus `manifest.json` (`version`, entries with `mbid`, `artist_name`, `normalised_name`, `file`, `sha256`, `source`); downloadable for 7 days.
  - Import: upload the zip (up to 500 MB, read in place, never extracted; entries over 15 MB ignored) → matched by MBID, then by normalised name (exact; fuzzy matching wasn't needed so far) → preview with the pack's thumbnails, matched ones ticked → applied as a job (source "import", status Done, also saved to artist folders when on).

### Phase 5 — Feedback round 1 (from `.todo.md`, 19 Nov 2026) ✓
Requests after using Phases 1–4 on the real library. Notes in italics are how they'd be done, or what needs checking first.

**General**
- [x] Site-native tooltips instead of the browser's `title` tooltips.
  *A small Tooltip component (shown on hover and keyboard focus, positioned to stay on screen); replace every `title=` in the UI.*
- [x] Site-native dropdowns instead of the browser's `<select>`.
  *A Select / listbox component with full keyboard support (arrows, type-ahead, Esc) and grouped options (fonts); replace every `<select>`.*
- [x] Site-native date picker instead of the browser's `<input type="date">` (21 Nov): a button with the date, opening a calendar in the site's colours (Monday first, today ringed, the chosen day in the accent); the title goes to months, then years, for dates far back; keyboard as a calendar grid (arrows, Page Up / Down, Home / End, Enter, Esc); min and max respected. Used for adding a gig and Rewind's own dates.
- [x] Everything else that still looks like a browser default: checkboxes, range sliders, file pickers, colour pickers, number inputs, scrollbars.
  *Styled replacements sharing the same tokens; file pickers become a button with drag-and-drop.*
- [ ] Choose the accent colour (the yellow/orange) yourself.
  *Per user, saved to the account so it follows them across devices: a few presets plus a custom colour. The darker and lighter variants and the chart colour are derived from it, and the chart colour is adjusted until it keeps at least 3:1 contrast in both themes.*
- [x] Clicking album covers, artwork, artist images etc. opens a larger version in a lightbox.
  *A shared Lightbox (Esc / click outside to close, arrow keys where there's a set, focus kept inside); full-size images through the existing image proxy.*

**History**
- [x] Top artists / albums / songs by time spent listening as well as by number of listens (long songs in some genres).
  *Listening time = listens × track length. Last.fm and ListenBrainz listens have no length, so take it from the matched library track (or the song's median Plex length), falling back to 3:35 and marking those as estimates. A "By listens / By time" switch on each top list.*
- [x] Make the "When you listen" heatmap fill the whole width of its card.
- [x] Most skipped songs / artists (albums dropped 21 Nov: an album being skipped is hard to tell; counts show as "3 skips", plays in a tooltip): plays where less than a set share of the track was heard (default 80%, adjustable per user).
  - *Changed (21 Nov): a short play is a skip only when another song started on the same player within 30 s of it stopping, so pausing, or stopping with nothing after, isn't one. A track picked up again where it stopped (within 30 minutes, from at least 0:15, on the same player) reopens the same play instead of starting a new one. Worked out when stats are asked for, so older plays follow the new rule too.*
  *Check first: Plex's history only records finished plays (Plex counts a play at ~90%), so stopped plays need a session watcher that polls `/status/sessions` every ~10–15 s and records how far each track got. That watcher would also give "playing now" for ListenBrainz and the client details below. Until then, Plex's own `skipCount` (already in the mirror) can power a first version.*
- [x] Clicking a chart opens the details behind it, e.g. the 2025 bar on an artist page lists those listens.
  *A drill-down panel (site-native popup) showing the listens for that period plus the item; for the overview charts the same panel filtered by day, week or month, or by weekday and hour for the heatmap.*
- [x] Period buttons: hide the horizontal scrollbar, and put "All time" first.
- [x] Listens tab: show the client and IP / location for each listen.
  *Check first: Plex history only has a `deviceID` (the client name comes from `/devices`), not an IP address. The IP and LAN/WAN come from live sessions (`Player.address`, `Session.location`), i.e. the session watcher above, so only plays captured live would have them. Imported scrobbles have neither. Decided (19 Nov): add a location using a local IP database, so IP addresses never leave the server: DB-IP's free "IP to City Lite" (`.mmdb`, CC BY 4.0, no account or key; credit "IP geolocation by DB-IP" where locations are shown), downloaded monthly by the worker into `DATA_DIR/geoip` and read with `maxminddb`. LAN addresses show as "Local network".*

*Done (19 Nov): the session watcher (`history/sessions.py`) runs as a worker job every minute and polls `/status/sessions` every 10 s through the same connections as play capture (the owner's when available), mapping accounts the same way. Each track seen gets a `play_sessions` row (furthest position, length, player, IP, LAN/WAN, location); a row ends when the track is no longer listed. "Most skipped" on History counts ended plays heard below the user's threshold (Settings → Listening, 10–95%, default 80%). When play capture stores a Plex listen, the nearest session of the same track (±15 min) gives it its client, IP, network and location. Locations come from DB-IP's city database, checked hourly and downloaded when missing or a month old (`DATA_DIR/geoip`), looked up locally; LAN addresses show as "Local network". Only plays seen live have this data.*

**Rate**
- [x] Skip a song in the Lists view.
- [x] When every song on the page is rated or skipped, show the next page automatically.
- [x] Lists view: always 15 songs per page (it sometimes shows 10, sometimes 15).
  *Cause: the row count is worked out from the window height when the page first renders, so it depends on screen size and on what had loaded by then. Fix it at 15 and fit the row height to the window instead (with a minimum, scrolling only on very small screens).*
- [x] Genre filter under Songs / Albums / Artists.
  *Songs inherit genres from their album, falling back to the artist (see the Phase 0 spike). The queue query gets an optional tag filter; the picker lists the genres present in the chosen pool.*

**Library**
- [x] Rate artists, albums and songs in the library views too (artist list, artist page, album page, track lists).
  *Reuse StarRating and the ratings API (same undo log).*
- [x] Hide the horizontal scrollbar of the genre chips under the artist search.
- [x] Artist page: group releases by type (albums, compilations, singles and EPs, live, …) instead of one list.
  *Spike (19 Nov, 500 of 7,278 albums): every album has a `Format` tag — Album 493, EP 4, Single 3 — and some a `Subformat` — Compilation 15, Live 7, Soundtrack 4. Done: both are stored as tags (kinds `format` / `subformat`) by the album detail sync; an album's type is its Subformat if set, else its Format, else Album; the artist page shows a section per type (Albums, EPs, Singles, Live, Compilations, Soundtracks, then any other). Existing albums get their types on the next full sync.*

**Artwork**
- [x] Change an artist's status by clicking its status dot in the list (cycles To do → Custom → Done).

### Phase 6 — Smart playlists ✓
- [x] Definition schema + builder service + preview endpoint (artists, always-included
      songs, min rating ± unrated, each artist's top N% by popularity, AND/OR, limit,
      shuffled or most popular; songs released more than once count once)
- [x] Builder helpers: library search, a genre's / mood's top 10 artists, similar artists
      (Plex's list, matched to the library) and sonic similarity (`/nearest`, best effort)
- [x] Builder UI (live preview) and playlists page (status, rebuild, delete ± in Plex)
- [x] Written to Plex as a regular playlist under the user's own account; rebuilds replace
      the songs in place (same key), or recreate it if it was deleted in Plex
- [x] Scheduled refresh (daily / weekly / monthly; checked every 10 minutes)

### Phase 7 — Feedback round 2 (from `.todo.md`, 19 Nov 2026) ✓

**General**
- [x] Send all listens to ListenBrainz, not just new Plex plays. (Checked in ListenBrainz's
  source: it ignores a listen only when user, time *and* `recording_msid` - a hash of the
  names - match, so before each batch the job reads the user's listens on ListenBrainz around
  those times and leaves out the same song within a minute.)
  *A "Send my whole history" action on Settings → Listening: every listen (all sources, duplicates left out) in `import` batches of up to 1,000 as a resumable job, with progress. Check first: whether ListenBrainz ignores listens it already has (same time and song). If it doesn't, compare against the user's listens on ListenBrainz for each time range before sending, so history copied there by ListenBrainz's own Last.fm importer isn't doubled.*

**History**
- [x] A map of where you listen: listens and top artists / songs per country, with three zoom levels (world, continent, country). (History → Map. Server location comes from its public address on plex.tv; no manual override yet.)
  *Only plays seen live by the session watcher have a location (imported scrobbles don't), so the map starts from when the watcher was added. Store the country and continent codes per listen (from the same DB-IP lookup) next to the "City, Country" text. Draw an SVG choropleth from Natural Earth's public-domain country shapes (TopoJSON, ~100 KB) with d3-geo; clicking a continent zooms in, then a country; a side panel lists that area's listens and top artists / songs. LAN plays count towards the server's own location (set once in Settings, or taken from the server's public IP).*

**Artwork**
- [x] Status order To do → Done → Custom (dot, filter chips, editor).
- [x] Choose which fonts the text logo offers (Settings → Library: add by Google Fonts name, checked first; remove; restore defaults).
  *An admin list of Google Fonts names, starting from today's curated 44; adding a name checks it exists by asking Google Fonts for it; the font picker shows the instance's list.*
- [x] Search results showed broken images. Cause: the signed image references weren't escaped in the thumbnail links, so a `&` in a search thumbnail's URL (e.g. Bing's) cut the reference short and its signature failed. They're escaped now, image hosts are asked for PNG / JPEG / WebP / GIF first (AVIF is read too), and a thumbnail host that refuses falls back to the full image.

**Settings**
- [x] Move the Last.fm, ListenBrainz and fanart.tv keys and accounts to a "Connections" tab.
- [x] An "Export / import" tab: the library export (with ratings and plays: tracks.csv, library.json), the listening history download, and the logo pack export / import.
  - Split into parts like Services (Settings → Export & import → History, Library, Gigs, Artist logos; logos for owners only), each with its imports first. The gigs import / export card moved here from Gigs → Been to, which links to it.
- [x] Move Admin from the main menu to a Settings tab (`/admin` redirects there).

**General (cont.)**
- [x] Clicking the logo spins the pick once (skipped with reduced motion).

### Phase 8 — Polish

**Feedback round 3 (19 Nov 2026)**
- [x] Segmented buttons (Songs / Albums / Artists, Cards / Lists) the same height as the dropdowns.
- [x] "Save to artist folders" and the text logo fonts on a new Settings -> Artwork tab; font groups flow in columns (no gaps).
- [x] Listening tab: the Last.fm / ListenBrainz imports move to Export / import (imports left, exports right); the rest uses both columns.
- [x] Phones open on Rate (Settings -> Appearance; per account), and the app can be added to the home screen to run full screen (web app manifest). On phones the Rate page drops its title, folds the filters into "Options", sizes the card to the screen and shows the swipe hint only until the first swipe.
- [x] The rating shown while swiping stays put instead of moving with the card.
- [x] The lightbox blurs what's behind it.
- [x] PWA install, offline shell (service worker keeps the app shell; an offline banner, and a "can't be reached" screen instead of signing you out)
- [x] M3U8 playlist exports (each playlist's last build: Plex's file paths or relative to the music folder)
- [x] Docs and published images (GHCR: amd64 + arm64 after CI passes on main; compose can use them via PLEKSTENDER_IMAGE)
- [ ] Screenshots for the README (from a real library)

### Phase 9 — Previews and "Guess the intro" (from `.todo.md`, 19 Nov 2026) ✓

**Previews** (also the base for the game)
- [x] `GET /api/servers/{id}/tracks/{key}/preview`: a 40-second AAC clip (m4a) cut by ffmpeg straight from the Plex file (`/library/parts/…`, the user's own token, never a Plex transcode session, so no "now playing" entry and no play counted). ffmpeg comes from the `imageio-ffmpeg` wheel (static binary, amd64 + arm64), overridable with `FFMPEG_PATH`.
- [x] The same pass measures the intro with `silencedetect`: leading silence and quiet start are stored per track (`track_intros`), so clips can start where the music does.
- [x] Clips are cached under `DATA_DIR/previews` (a few hundred MB at most, oldest removed first) and served with byte ranges (iOS Safari needs them).
- [x] Play buttons: playlist builder preview list, Rate cards and the Lists layout.
- [x] "Play songs" while rating cards: each card's song plays as it comes up (albums and artists: one of their top 3 songs, preferring ones without a silent intro); silence at the start is skipped. The next card's song is prepared ahead.

**Guess the intro** (a mode of the card view in Rate)
- [x] Toggle "Guess the intro" on the card view. The card's artwork, artist and title are hidden and the song starts playing: the song itself, an album's or artist's popular songs (one of the top 3 by popularity, at random).
- [x] 30 seconds per round. The artwork unblurs gradually over the round; then the artist is revealed (at 18 s), and finally the song's or album's title (at 26 s; not in artist mode, where the artist is the answer).
- [x] Guessing: a search field. Artists (and albums in album mode) are suggested from 3 letters. Songs: either pick the artist first and then search their songs, or search songs directly, shown from 5 letters (or 80% of the title for short titles).
- [x] Right answer or time up: everything is revealed, and the card can be rated as usual (swipe or stars). A wrong pick costs 3 seconds.
- [x] Score: points for the seconds left, a streak, and the best streak per mode (kept per account).
- [x] Avoided: songs with very short titles (under 4 letters), and songs whose intro is silent or near-silent for more than 8 seconds (known after their first clip; skipped automatically in game mode).
- [x] The next round's clip is prepared while the current one plays.

**Guess the intro, round 2 (feedback, 19 Nov 2026)**
- [x] Song and album modes: naming the right artist (picking them, or a wrong song or album of theirs) earns 3 points, even when the round is lost; with feedback on, a wrong artist is said (and costs 3 s).
- [x] Suggestions close on a tap elsewhere (the artwork stays visible); no pictures in album and artist modes.
- [x] The answer's title only shows when the round is over; album and artist modes show the playing song's title two thirds in.
- [x] After a round: add the artist's top 5 songs to the game playlist in Plex (made when missing; songs already in it skipped).
- [x] Settings → Guess the intro (per account): guesses per round (default 3), round length, right-artist feedback, the playlist, and well-known / mixed / deep cuts (the cards' order, and which songs play for albums and artists).
- [x] On a phone (21 Nov): the game opens with how it works (what to name, seconds and guesses, the clues, the points) and a Start button; the first song is prepared while it's read, and the tap lets iPhones play it. *Fixed with it: iOS refused to play the first song after a slow load ("Something went wrong"); the unlock tap's silent play could be cut short by the song, and a blocked play now asks "Tap to play the song" instead of failing. Clips no longer hold a database connection while they're made (a few at once could run the pool dry: the occasional 500), and a failed round is tried once more.*

**Phones and previews (19 Nov 2026)**
- [x] The first time the app is opened on a phone (outside the installed app), a popup suggests adding it to the home screen, with the steps for iOS (Safari) or Android (Chrome, with its own Install button when offered).
- [x] Rate cards: a card's play button keeps playing (every next card's song too) until stop is tapped; the same switch as "Play songs".

**Feedback round 4 (19 Nov 2026)**
- [x] Settings: the Servers and Admin tabs are one ("Servers & admin" for admins).
- [x] Settings → Artwork: the fonts first.
- [x] Clicking the logo plays the brand's spinning pick (the loader-spin animation, drawn on the logo: transparent and theme-aware, unlike the GIF's white background).
- [x] iOS no longer zooms in when a text field is tapped (16px text in fields on touch screens); the guess field is full width, with "Give up" below it.

**Guess the intro fixes**
- [x] The card behind is blurred in the game (it was the next answer, visible while swiping).
- [x] Any version of a song or album counts: compilation copies (credited to "Various Artists" in Plex, with the performer on the track), remasters, single edits, live versions and deluxe editions.

**Fonts (19 Nov 2026)**
- [x] Settings → Artwork: browse Google Fonts (its public catalogue, kept a week under /data; most used first, by name and group), preview every font (offered and about to be added) in a line of text drawn by the server, with your own preview text, and add fonts with any capitals: the exact name and the group come from the catalogue (no group to choose).

**Scores**
- [x] History → Scores: everyone's Guess the intro scores on the instance (all modes or one), the most points first, with best streaks, the latest game (rounds without a 30-minute break) and how many they guessed. Finished rounds are kept from now on.

**Your ratings**
- [x] History → Ratings: everything you've rated (here or in Plex), the most recently rated first, by day; filter by songs, albums and artists; change a rating in place.
- [x] Undo per item on History → Ratings: puts back the rating from before (repeatable), and when it was set. Only for ratings made in Plekstender and not changed in Plex since.

### Phase 10 — Music files and the tag editor (from `.todo.md`; needs the music folder)

**Music folder on the worker only**
- [x] Only the worker mounts the music folder (read-write); the web container never touches music files. "Save to artist folders" becomes a background job (the rendered image is handed over through `/data`), and the folder check runs in the worker.

**Tag editor** (server owners; off until an admin turns it on)

*Principles.* Every change to a file is recorded before it's made, grouped into change sets ("Applied MusicBrainz release … to 12 files", "Renamed 240 files"). Nothing is written without a preview of the changes. All file work happens in worker jobs, inside the mounted folder only (paths resolved and checked), one job per folder at a time. Plex is asked to rescan the affected folders afterwards.

*Data.* `media_files` (path, size, mtime, format, duration, linked Plex track), `tag_snapshots` (each file's tags the first time Plekstender touches it, embedded pictures stored once by hash under `/data`), `change_sets` (who, when, what kind, status) and `tag_changes` (file, field, old value, new value, change set; also `path` for moves).

*History and undo.* Per file, album and artist: a timeline of change sets and their changes. Undo a whole change set, or cherry-pick changes: undo creates a new change set that writes the old values back. If a file changed since (the value on disk isn't the one recorded), that change is flagged instead of overwritten. An artist's history lists every change set that touched their files, with pick-and-choose undo.

*Stages.*
- [x] 10a. Scan and view: read tags with `mutagen` (FLAC/Vorbis, MP3 ID3v2.3/2.4, MP4/M4A, Opus/Ogg, WAV/AIFF), match files to Plex tracks, show tags per album with differences to Plex highlighted. Read-only. (Settings → Music files: the folder mapping, artist.jpg, scans; album pages: "Tags in the files" for server owners. Rescans read only changed files; symlinks aren't followed.)
- [x] 10b. Manual editing (per file or across an album) with history and undo. (Settings → Music files: "Allow editing tags", off by default. Album pages: Edit tags (album-wide fields, per-song track/title/artist, Fill in from Plex), a review of every change before it's written, and the tag history with undo per edit or per change; artist pages show their files' history.)
- [x] 10c. Write Plex's metadata to the files: titles, artists, album, album artist, track/disc numbers, year and genres, for the whole library (Settings → Music files) or one artist (their page). Pick the tags, preview the changes (counts per tag plus samples), write as one edit in batches (Plex rescans the folders, or the whole section for big runs); the history shows progress and undoes the whole run.
- [x] 10c+. Write your ratings to the files: "Your ratings" in the same panel (off by default), the requesting person's own song ratings. POPM in MP3s (Windows Media Player / MusicBee byte values with half stars; other players' play counts kept), RATING 0–100 in FLAC, Ogg and M4A. Previewed and undone the same way.
- [x] 10d. MusicBrainz: search releases (album and artist, or paste a release link), pick one like in Picard (cover, date, country, format, label, track count), files matched to tracks by number, title and length (change any match), preview per file, apply as one edit. Writes MusicBrainz IDs (recording, track, release, artist, album artist, release group), original date and release country. One request per second, answers kept a day, covers kept under `/data`. The tag reader now reads every field it writes (the next scan re-reads each file once).
- [x] 10e. Identify unknown files by fingerprint: Chromaprint (`fpcalc`, the official static build in the Docker image) + AcoustID (an application key, Settings → Connections or `ACOUSTID_API_KEY`). Settings → Music files → Unknown files: files missing tags, not in Plex, or without a MusicBrainz ID; identify them (worker job, up to 5,000 at a time; fingerprints kept per file until it changes), pick each file's match (recording on a release, or just the song), preview and write as one edit. Album pages: the MusicBrainz dialog suggests the releases the album's files sound like, and the matcher uses the identified recordings. Settings → Music files also shows every edit's history.
- [x] 10f. Rename and move files into a folder structure from a template (default, Plex's layout: `{albumartist}/{album}/[{disc}-][{track:02} - ]{title}`; `[…]` left out when a field in it is empty, `{disc}` empty for one-disc albums), per album or artist. The dry run (worker) lists every move, songs that would clash or find something in the way, and what goes along: files named like the song (lyrics) are renamed with it, and a folder's cover, .cue, .nfo and subfolders move when all its songs go to one folder. Moves are recorded as edits (undo moves back), never overwrite (link + unlink), remove emptied folders, and Plex rescans whole sections. "Allow changing files" covers moving too.
  - Later: an album's parent folder (CD1/CD2 layouts) and library-wide renames.
- [x] 10g. `.nfo` files (Kodi-style artist.nfo / album.nfo, as written by Kodi, MediaElch, tinyMediaManager, Lidarr): read by the scan (only when changed; files that aren't this kind of XML, like scene release notes, are skipped, and XML declaring entities is refused). Album pages: "From album.nfo" with its MusicBrainz release (compared with the files'), date, label, genres / styles / moods and review, and "Use this release…" opening the MusicBrainz dialog on it. Artist pages: "From artist.nfo" with the biography, dates, genres and the MusicBrainz ID compared with Plex's.
  - Later (with Phase 13): writing an .nfo's biography, genres or styles into Plex.
- [x] 10h. Artwork in folders and files: the scan describes each song's embedded cover and every cover / folder / front / album / artist image (size, dimensions, SHA-1; thumbnails under `/data` for pages). Album pages: "Artwork in the files" shows the folder images and the songs' covers side by side (oversized ones flagged); embed one cover (a folder image or Plex's poster) in every song, resized for players (baseline JPEG, 600 / 1000 / 1500 px, ~500 KB); save a cover.jpg from the songs' cover or Plex's poster. Settings → Music files: songs without covers, folders without cover.jpg, oversized covers and "Shrink N covers" library-wide. Every replaced picture is kept by SHA-1 under `/data`: undo puts it back (or removes a file it created).
  - Later: artist.jpg actions here (Artwork page already saves them), and embedding covers library-wide where songs have none.

*Open questions.* Undo for files Plekstender didn't create the snapshot for (scan first, always). Large libraries: scan incrementally by mtime. Ratings are written into files only when asked for.

### Phase 11 — Live (from `.todo.md`)
A **Live** tab: upcoming shows near you by the artists you listen to most.
- [x] 11a. The Live tab, JamBase and the budget. Each person sets a place (a city from Open-Meteo's geocoding, or "use my location", rounded to about a kilometre), a distance (25–1,000 km) and a dial for the artists to follow: the top X% of the artists they've played (default 10%; no upper limit since 20 Nov: following many artists just means a refresh costs more and the daily schedule refreshes it less often), across their servers, merged by MusicBrainz ID or name. Ranked by all-time plays (Plex's count or the listen history, whichever is higher) or by the listens of the past N months / years. The worker looks the shows up (a job per person, one at a time), listed by month with date, venue, distance, the people's artists on the bill (linked to their pages) and support acts, cancelled / postponed shown, and the ticket link exactly as JamBase gives it (or its JamBase page); filter by artist and by date (next 30 days / 3 months). "Powered by JamBase" is linked under the list. Admins set the key on Settings → Connections (checked with `/quota`, which is free) and see this month's requests and JamBase's own count.
  - What JamBase's documentation (`data.jambase.com/llms-full.txt`, `openapi.json`) changed: the key (`jbd_…`) goes in `Authorization: Bearer`, not a query parameter; about 20 artists a request (`artistId=musicbrainz:{mbid}|…`, `artistName=…|…` for the rest; the two can't be combined); `geoRadiusUnits=km`; the Developer plan sees six months ahead and doesn't return performers' MusicBrainz IDs (`expandExternalIdentifiers` needs a paid plan), so performers are matched to artists by name (normalised, plus a close-match fallback; a name search's look-alikes such as tribute acts are dropped). MusicBrainz IDs JamBase doesn't know fail the whole request (a 400 naming each one, under `code` / `message`): they're left out and the batch retried once, those artists are searched by name, and the IDs are remembered for 90 days.
  - Budget: every request is counted before it's sent and stops at `JAMBASE_MONTHLY_CAP` (900); `/quota` is checked before each run and a run stops if JamBase has fewer requests left than it needs. The daily schedule (05:20 UTC) refreshes the people whose shows are oldest, within today's share of what's left of the month; changing the place or the artists refreshes after a short pause; "Refresh" by hand at most once an hour. A run cut short keeps the last list; a complete run drops shows no longer found.
- [ ] JamBase per-artist IDs (`/v3/artists/id/musicbrainz:{mbid}`, free on every plan but counted) if name matching turns out to miss performers.
- [x] 11b. Ticketmaster's Discovery API as a second source (free Consumer Key, 5,000 requests a day, 5 a second; admins set it on Settings → Connections, checked with one small search). Not searched per artist: all music events near the person (`classificationName=music`, `geoPoint` geohash, `radius`/`unit=km`, `locale=*`, 200 a page) are read in 30-day windows over the same six months, a window with more than 1,000 events (the deep-paging limit) split in halves, at most 80 requests a refresh and `TICKETMASTER_DAILY_CAP` (4,000) a day; line-ups matched against all the followed artists by attraction MusicBrainz ID or exact normalised name. Each source keeps its own rows, errors and stale-show cleanup.
- [x] 11c. Calendar links (iCal): each person can add up to 5 (Live → Calendar links; `webcal://` works), e.g. their Songkick calendar export. Checked by reading once; stored encrypted (they may hold private tokens), shown by site and the calendar's own name. Read with the other sources (fetched like any user-given address: public hosts only, redirects re-checked, 5 MB at most); every upcoming event is listed (it's the person's own calendar), the billing split into acts ("A, B and C at Venue (date)"; the whole billing tried first, for names like "Florence and the Machine") and matched to their library's artists; LOCATION gives venue and city, GEO the distance, UTC times shown in the calendar's own time zone. Merged with JamBase / Ticketmaster shows (same day, venue and a shared act); a link that can't be read keeps its last shows and says why; removing one removes its shows (starred ones stay).
- [x] DICE shows come through JamBase (DICE has no public API of its own); "Find … on Songkick" (an artist search) when the list is filtered to one artist.
- [x] Shows found by several sources appear once: same day, a shared artist, and the same venue (names match, one within the other or mostly the same words, or within 1.5 km). JamBase's row is shown first (its data is credited); every source's ticket link is offered ("Also on Ticketmaster"); "Powered by JamBase" and "Shows also from Ticketmaster" under the list.
- [x] Starred shows: star any show (all its sources' rows) to keep it through refreshes and setting changes, filter by "★ Starred", download one as an .ics file, and subscribe to a private calendar feed (`/api/live/calendar/{token}.ics`, iCalendar with floating local times, all-day for shows without a time, cancelled shows marked, starred shows kept 60 days after; the address is the secret, can be replaced, and needs no sign-in so calendar apps can fetch it). "Forget my place" removes the starred shows too. Starred shows have "I've got a ticket" (a 🎟 badge; marking it stars the show, unstarring forgets it): in the calendar those are CONFIRMED and the rest TENTATIVE, with "You have a ticket." in the description.
- [x] 11d. Data Thistle (UK listings, small local events the others miss). The admin pastes the access and refresh tokens from their free account (Settings → Connections; the access token is checked with `GET /v1/ping`, expiry dates read from the JWTs, both stored encrypted). A daily worker job renews them when the access token has under 5 days left (`POST https://auth.datathistle.com/v1/refresh` with the refresh token as Bearer → `accessToken` + `refreshToken`, both stored); a failed renewal is shown on Connections (and the old token used while it works). Lookups: `GET /v1/events?tags=music&lat&lon&distance` (miles), the next 4 weeks, 20 a page nearest first, at most 10 pages; one show per place and date (places past the distance left out); the booking link as the ticket link. Events have no performers, so their names are matched against all the library's artists (the whole name, then its parts: "Portishead: Dummy Live" → Portishead); others are dropped. Budget: `DATATHISTLE_DAILY_CAP` (30) a day, and none when Data Thistle's `X-RateLimit-Remaining` (sent with every answer, shown on Connections) says the month is used up. Their terms: listings kept a day at most (older rows hidden and removed, starred ones too), so it's looked up when someone is looking (opening Live when the last lookup is over 20 hours old, a refresh, changed settings) and in the daily schedule only for people who starred one of its shows; its logo (bundled: the page doesn't load outside images) and "Add or update an event" under the list.
- [x] A venue filter next to the artist one (venues of the shows the other filters allow, with their town and counts), and ticket prices where a source has them: Ticketmaster's `priceRanges`, Data Thistle's performance tickets (a price of 0, or no price and "free", is free), JamBase's `priceSpecification` if a paid plan sends it (the free Developer plan doesn't). Shown as "Free", "£12" or "£35–£75"; a show found by several sources takes the first price there is.
- [x] New shows: a "New" filter and badge for shows first found in the past week (a show several sources found counts from its earliest sighting). Only a fingerprint is kept per show (`live_seen`: SHA-256 of "source:id" and the date first seen, a year and a bit), so Data Thistle's listings are recognised when fetched again after they've expired, without keeping them. The first lookup after setting up or changing the settings, and a source's first lookup, only note what's there (nothing is new); shows listed before this existed count as seen a month ago.
- [x] Gigs on artist pages: a library artist's page lists their upcoming gigs (the Live list's rows: star, ticket, calendar), and artists with an upcoming gig get a ticket badge on their picture (artist page and library grid). "Gigs" is the word for events, concerts and shows everywhere on the site.
- [ ] Bandsintown optionally, if they approve an app ID.

### Phase 11½ — Gigs you went to (20 Nov 2026)
History → Gigs: the gigs each person went to ("gigs" is the site's word for events, concerts and shows).
- [x] 11½a. Gigs by hand and from Live: date, artists (one a line, the headliner first; linked to the library by name), festival or tour name, venue, town, country, notes, a link. Past Live gigs that were starred or had a ticket are offered under "Did you go?" ("I went" records it with its place and coordinates; "Didn't go" unstars it). Listed by year with year and artist filters; edit and delete.
- [x] 11½b. Import and export. No service found has a public API for *writing* attended gigs: setlist.fm's API is read-only (`GET /rest/1.0/user/{userId}/attended`, `x-api-key`, free for non-commercial use); Songkick's API (with a gigography endpoint) isn't open to hobby projects; Concert Archives couldn't be checked. So imports: setlist.fm (an admin's key on Connections, each person's setlist.fm username; one setlist per artist per night, joined into one gig per date and venue), CSV (a header row with a date and an artist column at least; many column spellings and date formats; support acts split at commas) and calendar files (past events; "A and B at Venue" split into acts). Re-importing updates instead of duplicating (each gig keeps its source's reference), and a night already added another way (same day, venue and headliner) is skipped. Exports: CSV, JSON and a calendar file, to take elsewhere.
- [x] 11½c. Places: each venue looked up once (OpenStreetMap's Nominatim: "venue, town" in the gig's country, else the town; one request a second; results shared in `gig_places`, places not found tried again after a month), so gigs can be mapped at the venue. A worker job places someone's gigs after they're added or changed, and an hourly sweep catches the rest. Gigs from Live keep the coordinates their source gave.
  - Fixed (21 Nov): gigs whose venue wasn't found sat at the town's centre, and the map grouped pins by position, so they all showed under the first one's venue. Now a town-only place is its own grey pin ("These venues weren't found…", each gig with its venue), and a spot only names a venue when all its gigs share it. Venues are also searched as named places in the town (`amenity` + `city`) before falling back to the town; places found only as their town before then are looked up once more (and a re-lookup now reads the new row, not the one already loaded). The gig form has "Place on the map": what's known, and a search on OpenStreetMap (`GET /api/gigs/places`, on request) to pick it by hand (`place_precision = "manual"`, kept until the venue, town or country change or "Look it up again"); a pick goes to the person's other gigs at that venue too.
- [x] 11½d. Stats on the Gigs tab: gigs, artists seen, venues and the busiest year; gigs a year (every year from the first to the last); gigs in each month and by season (meteorological seasons, flipped for gigs south of the equator); most seen artists (linked to the library) and most visited venues.
- [x] 11½e. The gig map (Gigs tab, "Where"): MapLibre GL (6.16, loaded only on that tab) on OpenFreeMap's vector tiles (OpenMapTiles schema, free, no key), with its own plain style in the site's colours (land, water, a few road tiers, country borders, place names; street names from zoom 14), light and dark, zooming from the world down to a street. 3D-looking red pushpins (SVG: glossy head, highlight, steel needle, soft shadow): one per venue, a gig a pin up to three and a number beyond; nearby venues grouped by zoom (supercluster), a group's pins zoom in on click, a venue's pins list its gigs. The CSP allows the tile host and blob workers; MapLibre's worker file is bundled and pointed to with `setWorkerUrl` (it otherwise looks next to its own, bundled, file).
- [x] 11½f. Adding a gig is a popup (21 Nov): first a setlist.fm link, or a search of setlist.fm by artist, year and town (`/search/setlists`, 20 a page, newest first; each result with its tour, venue, town and how many songs); picking one fills in the whole night as a link does. "Add it by hand" goes straight to the form. Editing a gig opens the same form in a popup.
- [x] 11½d. Setlists and "seen live" (21 Nov): pasting a setlist.fm setlist link into the gig form fills it in (`GET /setlist/{id}`, then `/search/setlists?date&venueId` for the rest of that night's bill; headliners taken to be the longest sets; needs the admin's setlist.fm key). Each artist's songs are kept with the gig (`gigs.setlists`: name, cover, tape), from the link and from setlist.fm imports. Songs in the library played at a gig you went to are marked "Seen live" ("Seen live 2×") on album and artist pages: matched by the album's or song's artist and the normalised title, so remasters count; songs played from tape don't.
  - Library → Stats → "Seen live": the share of the library's songs seen live as a number (most people's is small), from how many gigs' setlists, and up to 10 artists with the highest share of their songs seen live (at least 5 songs in the library; ties by the number seen).
  - Also the share of the library's artists seen live: anyone on the bill of a gig the person went to (setlist or not).
- [x] 11½e. Artist pages list the gigs you've been to where they played (by library link, or by name for gigs from before they were in the library), each with that artist's setlist. *Fixed with it: the upcoming-gigs lookups on artist pages compared the user's server link ID with the Plex server's ID, which only matched when the two happened to be equal.*

### Phase 12 — More services (from `.todo.md`)
- [x] 12a. More scrobble services (Settings → Connections → "More scrobble services": the ones added, compactly, and "Add a scrobble service" picked from a list, showing only that service's fields): ListenBrainz-compatible servers (Maloja and Koito at `…/apis/listenbrainz`, Multi-Scrobbler at its root, or any other; a trailing `/1` is dropped; the token checked with `validate-token` where the server has it, else taken on trust) and Libre.fm or another GNU FM server (Last.fm's 2.0 API: signed in once with `auth.getMobileSession`, `authToken = md5(lower(username) + md5(password))`; only the session key is kept; `track.scrobble` 50 at a time). Each sends the new Plex plays from when it's added, with the same loop as ListenBrainz (refused plays skipped, a rejected token or session pauses that one; adding it again resumes it where it stopped). Tokens and session keys stored encrypted.
  - Last.fm too (Settings → Scrobbling): needs the instance's API key and its shared secret (an admin's, on the Scrobbling tab, which is where the Last.fm key card moved from Services → History; `LASTFM_SHARED_SECRET` works too; the secret is checked with a signed `auth.getToken`). Each person connects with Last.fm's web sign-in (`last.fm/api/auth/?api_key&cb`, back to `/api/scrobblers/lastfm/callback` with a `state` that's an HMAC of their user ID and the time, valid an hour, so a sign-in can't be slipped to someone else); the token is swapped for a session key with `auth.getSession`. No password passes through Plekstender. A refused key or signature leaves plays waiting (not skipped, not paused); a withdrawn session pauses it with "Connect again".
- [x] 12b. Import playlists from files (Playlists → "Import a playlist"): CSV (Exportify's and TuneMyMusic's columns, and most others: title, artist(s), album, a duration in ms or m:ss, the playlist's name), M3U / M3U8 (`#EXTINF` "Artist - Title", else "Artist - Title" or …/Artist/Album/NN - Title file names) and JSPF (ListenBrainz). Songs matched to the library on the server: the same normalised title by one of their artists (or the album artist), preferring the same album and the closest length; else a lone same-title song within 4 s (shown as "likely"). The review lists the matches (each can be left out) and the songs not found; the playlist is made in Plex under the user's account, songs in order.
- [x] 12c. Spotify links: a public playlist's link (open.spotify.com/playlist/… or spotify:playlist:…) read with Spotify's Web API (an admin's app on Settings → Connections, checked by signing in; client credentials, no one's Spotify account involved; 100 songs a page, local files and podcasts left out), then matched and reviewed like a file. Spotify doesn't let new apps read its own editorial or algorithmic playlists: those are explained when refused.
- [x] 12d. Tidal links: a public or unlisted playlist's link (tidal.com/playlist/…, tidal.com/browse/playlist/…, listen.tidal.com/playlist/…) read with Tidal's API v2 and the same free app used for album covers (client credentials). The playlist's items are paged by cursor (videos left out), then the tracks are looked up 20 at a time with their artists and albums; a track's version ("Live", "Remastered 2009") is added to its title. Matched and reviewed like a file. Private playlists are refused with an explanation.

### Phase 12½ — Navigation and Settings layout (20 Nov 2026)
The menus had grown one page at a time; this tidies them before Phase 13 adds more.
- [x] Main menu: Home, Library, History, Gigs, Settings (five, so the phone's bottom bar has room).
- [x] Library page with tabs: Library (the artists and albums), Playlists, Rate, Artwork (server owners). Artist and album pages stay under /library. Old addresses (/rate, /artwork, /playlists…) lead to the new ones.
- [x] Gigs (was Live) with tabs: Upcoming (the Live page) and Been to (the gig history, moved out of History, with its stats and map). /live and /history/gigs lead there.
  - Been to filters (21 Nov): artist, year (a dropdown now), country, city, venue, and "Venue not found" (gigs not pinned at their venue: town only, not found or not looked up yet; picked by hand counts as found). Picking a city or venue picks where it is too; changing one clears the ones below. Each bill is shown biggest artist first (the library's popularity score; artists not in the library last, in the order given), and gigs on the same day are ordered by their biggest artist. The map keeps every pin but moves to frame the gigs the filters pick out (one place up close, several so they all fit; back to everything when cleared), and is no longer rebuilt on each filter change.
  - A place picked by hand no longer moves the other gigs at the same venue by itself: when there are others (same venue, town and country, ignoring case and accents; `GET /api/gigs/same-venue`), saving asks "Yes, all n gigs" or "Just this one" (`place_others`). Renaming a gig's venue (or changing its town or country) asks the same about the other gigs at the old venue (`rename_others`: they take the new venue, town and country and are looked up on the map again); with a place picked as well, that question comes first and the renamed gigs count towards the place question.
  - Live: a gig from a calendar link is labelled with the site its page is on (Songkick, Bandsintown, else the site's address) instead of "In your calendar link".
- [x] History: "Listens" becomes "Scrobbles" (/history/scrobbles; /history/listens leads there).
- [x] Settings in a grouped sidebar (a section picker on phones):
  - You: Servers · Scrobbling (ListenBrainz and the other services) · Listening history (Plex plays, imports, skips, deleting) · Appearance · Guess the intro · Export & import.
  - Library (server owners): Music files.
  - Admin: Users & access · Library tuning · Text logo fonts · Services (every key and app, grouped by what it's for: Gigs — JamBase, Ticketmaster, Data Thistle, setlist.fm; History — Last.fm; Playlists — Spotify; Artwork — fanart.tv; Music files — AcoustID).
  - The old Connections page leads to Scrobbling.
  - Services and Text logo fonts open sub-pages underneath them in the sidebar (and in the phone's picker): Services → Gigs · History · Playlists · Artwork · Identifying files; Text logo fonts → Offered · Add from Google Fonts. /settings/services and /settings/fonts open the first; /settings/artwork leads to the fonts.
- [x] Home's Status card lists every connection, with a coloured dot (working, needs a look, not working, not set up): yours (each Plex server — red when its last sync failed; ListenBrainz and the other scrobble services; calendar links) and this Plekstender's (JamBase and Ticketmaster with their usage, amber from 80 % of the limit; Data Thistle's tokens and remaining requests; setlist.fm, Last.fm, Spotify, fanart.tv, AcoustID; SearXNG, checked with a 2-second ping kept a minute). Ones needing a look have a Fix link; services not set up share one line. Usage numbers and links to admin pages are for admins.

### Phase 13 — Genres, styles and moods; tag editing in bulk (from `.todo.md`, 19 Nov 2026)
Plex gives albums one or two broad genres (often "Pop/Rock") but many **styles** ("Art Rock", "Prog-Rock") and **moods** ("Witty", "Laid-Back/Mellow") from its own metadata. Plekstender already mirrors them (`tags`, kinds genre / style / mood / country); they just aren't used yet.
- [x] Every genre, style, mood and country chip links to its page (/library/tags/:id): the artists with it (on themselves or one of their albums) and the albums with it, sorted by your plays, your rating, year or name (the first 240 of each); "Often found with": the genres, styles and moods most often on the same artists and albums; "Make a playlist" opens the builder with its top 10 artists, the tag as a rule and its name. Chips show their kind: genres in the accent colour, styles outlined, moods soft and italic.
- [x] Library: a Genres / Styles / Moods switch above the filter chips (the 40 most common each, counted by artist, an artist counting through their albums' tags), with a link to the chosen one's page.
- [x] Playlists: styles as a source alongside genres and moods; a rule "Only with" these genres, styles or moods (the song's album or artist has one; always applies, whatever "Combine" says; chosen songs are always kept).
- [x] Rate: choose by genre, style or mood (artists match through their albums' tags too).
- [x] Tag editor: "More fields" when editing an album's tags: original date, release country, label, catalogue number, barcode and the MusicBrainz release, release group and album artist IDs for the whole album; disc number and composer per song. Written as Picard writes them (ID3: label in TPUB, catalogue number and barcode in TXXX; MP4: freeform atoms, the composer in ©wrt; Vorbis: LABEL, CATALOGNUMBER, BARCODE). The scan's reader moved to version 5, so the next scan reads every file once more to pick up labels, catalogue numbers and barcodes. *Chosen from the plan's candidates since the `.todo.md` note breaks off; comments were left out (ID3 keeps them in COMM frames with a language and description, which don't fit one text box).*
- [x] Library → **Tag editing** tab (server owners): albums with scanned files and what's off about them (files that differ from Plex and in which fields, without a MusicBrainz release ID, missing a title / artist / album / track number, unreadable), filtered by kind with counts and searched by album or artist, 50 a page; files not in Plex are counted with a link to identify them (Settings → Music files). Select several (or a page) and:
  - Look up on MusicBrainz: one album at a time (MusicBrainz's 1 request a second), each album's most likely release from the release ID already in its files, else identified by sound (most files), else a search (score 80+, the same number of tracks preferred). A *clear match* (every file matched, as many tracks as files, and the files' release ID, 80 %+ of the files by sound, or a search score of 95+) can be accepted with the others; the rest are accepted or skipped one by one, or picked by hand on the album page.
  - Write Plex's metadata: each album compared first (changes and fields), then written.
  - Rename: the album pages' template (remembered per browser), a dry run per album (moves, what can't move, where they go), then moved.
  Each album is its own edit in the history, undone from its page. Artists aren't listed separately: an artist's albums are.

### Library → Discover (21 Nov 2026)
- [x] Artists to add (owners' tab): artists not in the library that Plex lists as "similar" to the library's artists, ranked by how many library artists list them, then by the person's own listens to them (scrobbles count even for artists not in the library). Each with the library artists it's like (most played first, linked) and a Last.fm link. Hide ones you don't want (`hidden_suggestions`, per person, on every server); a Hidden view brings them back.

### Phase 14 — Library stats (from `.todo.md`)
Library → Stats (a tab next to Library; Tag editing is its own tab since Phase 13). Counts only what the person's Plex account can see.
- [x] Where artists are from: the listens map's world map counting artists, zooming by continent; a country (on the map or in the top-12 row under it) lists its artists, most played first. The country is Plex's (its country tag, names mapped to ISO codes, old names like "Soviet Union" or "England" included), else MusicBrainz's: the worker looks up artists Plex has no country for, hourly, 300 a run at one every two seconds (half MusicBrainz's limit, leaving room for the tag editor), by MusicBrainz ID, else by name (only an exact, 100-score match whose namesakes agree). The artist's country, else their area's or begin area's. Kept in `artist_areas` (shared by servers; "not found" retried after 90 days). The note under the map says how many are placed and how many are still to be looked up.
- [x] The library over time: running totals of tracks, albums or artists at the end of each month (every month from the first to the last), by when they were added to Plex, as a line over a soft area with a crosshair tooltip. Totals as three tiles above.
- [x] Music by decade: albums per decade; click a decade for its years, a year for its months and albums (from the release date; albums with only a year are counted as "?"). Albums without a year are left out and counted in the note.
- [x] Cover colours: each album cover's main colour, worked out by the worker every 15 minutes (up to 2,000 covers a run, six at a time, from Plex's 64 px thumbnail with the owner's connection; a changed cover is done again; one that can't be read is skipped until it changes). The main colour: the biggest of six colour clusters, unless a clearly colourful one is at least 60 % as big (a red logo on a black sleeve stays black). Shown as a strip of up to 360 cover colours in spectrum order, families (red, orange, yellow, green, teal, blue, purple, pink, white, grey, black) as segments sized by their albums; click one for its covers (up to 400), each with its colour under it. Kept in `album_colours`.
- [x] Word clouds of the 100 most common words in song, album and artist names: each name counts a word once, a song released more than once counts once, words in brackets ("(2011 Remaster)", "[feat. …]") are left out, and so are stop words in English, Swedish, German, French, Spanish, Italian, Dutch and Portuguese plus title clutter ("remastered", "live", "edit"…). Sized on a square-root scale and laid out as a cloud (21 Nov; first version: wrapped rows): the biggest words first, near the middle, each next one spiralling out from the centre until it finds room, about one in six smaller ones turned on its side; the same library always gives the same cloud, and it lays itself out again for the screen's width (taller on a phone). Apostrophes written as accents or backticks ("I´ll") count as apostrophes, so contractions don't split into "ll".

### Phase 15 — A fuller Home page (from `.todo.md`)
- [x] Remove "Coming up".
- [x] Your listening at a glance: this week's listens (against the week before), the last 30 days' listens, artists and time, the current and longest streak, and a column chart of the last 30 days; top 5 artists and albums of the last 30 days with bars; the latest 6 ratings. A short note instead when there's no history yet.
- [x] Recently added albums or artists (12, newest first; `GET /library/recent`).
- [x] Gigs coming up: the gigs you've got tickets for ("Gigs you've got tickets for", the next 4, and how many more), or when there are none the next 4 from the Gigs page, or a prompt to set a place there. (21 Nov: tickets first.)
- The library sync, Servers and Status cards stay below.

### Phase 16 — Rewind (from `.todo.md`)
A "wrapped"-style story for any period you choose (a year, a month, a custom range), opened as a full-screen popup within the site, far more colourful than the rest of Plekstender: each card takes its colours from the album covers on it.
- [x] History → Rewind (also linked from Home's "Your listening"): this year so far, last year, this month, last month, the last 30 days, or any dates (up to ten years). `GET /api/rewind?start&end&tz&server_id`.
- [x] The story: a full-screen dialog of cards with progress bars; tap the right two thirds (or →, Space, "Next") to go on, the left third (or ←, "Back") to go back, Esc or × to close. Each card's background is a gradient between two of the period's album cover colours (from Library → Stats' cover colours; vivid fallbacks until they're known), white text; cards slide in unless reduced motion is on.
- [x] Cards: the period in numbers (listens, time, days with music out of the period's days); top artist (big picture) and top 5 artists, albums and songs; new obsessions (artists and albums first heard in the period and played at least 10 times in it); new releases you played most (released in the period by the library's release date, or its year when the period is whole years); when you listen (busiest weekday and hour, with labelled bars for both); your sound (top styles, the top style of each part of the day and each weekday: an artist's listens split over their own and their albums' styles in Plex; genres only when none of the artists played has a style, since most of a library is "Pop/Rock"); on repeat (songs played two or more times in a row: "3 times in a row", or "2 to 5 times in a row on three different occasions" when it happened more than once); Guess the intro (rounds, points, accuracy, best streak, days played); time in Plekstender; and a closing card.
- [x] Music (21 Nov, optional, off until the Music button in the story's top bar is tapped; remembered on the device): the opening card plays your top song, then each card its artist's, album's or song's clip (top artist, top album, top songs, new obsessions, new releases, the longest repeat; songs found in the library by album and title, `track_key`); cards without their own carry the last one on. *Also: tall cards scroll from the top (they were centred, so their tops couldn't be reached on a phone), tighter sizes on phones, and New obsessions labels its artists and albums.*
- [x] Time in Plekstender: recorded from now on, per person and day (`app_time`): each minute the page is visible and was used in the last two minutes counts (`POST /api/app-time`).
- [ ] Shareable cards: each story card as an image (generated graphics in the style of the period's covers), to save or share. *Not built yet: drawing each card on a canvas (covers fetched through the image proxy) is its own piece of work.*

### Phase 17 — Notifications (20 Nov 2026)
Tell people when something they rely on stops working, before they notice it themselves.
- [x] A bell in the header with a count of new problems; its list shows open problems first (newest first, red or amber dot, the detail, how long ago, a Fix link to where it's fixed), then the ones fixed in the last 3 days (marked fixed). Opening it marks them read; polled every 2 minutes.
- [x] What raises one: the worker looks at every connection's status every 5 minutes (the same view as Home's Status card, so whatever turns a dot amber or red there): a Plex server whose last sync failed, ListenBrainz or another scrobble service failing or rejecting its token, a calendar link that can't be read, a Data Thistle renewal failing or its tokens running out, JamBase / Ticketmaster from 80 % of their limit (with the numbers), SearXNG not answering. Once per problem (`notifications.key`): repeats update it, getting worse (amber to red) makes it unread again, and it's resolved by itself when the status is back to working. *Rather than each worker job raising its own, one check reads the statuses: simpler, and the bell and Status card can't disagree. Not covered: setlist.fm has no usage numbers to warn about; a refused key shows up as the feature failing, not as a notification.*
- [x] Who sees what: each person their own (servers, scrobble services, calendar links); the instance's (service keys, quotas, SearXNG) only admins.
- [x] Each person can dismiss theirs (it comes back only if fixed and then broken again); an admin can hide an instance one for every admin.
- [ ] Later: email or push (the app is installable) for the serious ones, per person.
- [x] New gigs in the bell (21 Nov): after a gig refresh finds gigs for the first time (not a source's first lookup), one notification per person, "n new gigs" with the first few artists (soonest first) and a link to Gigs → Upcoming → New (`/gigs?show=new`). It counts what the New filter lists (merged across sources), is updated after each refresh, unread again when more are found, and removed when none are new (after a week). Level `info`: an amber dot and "Show" instead of "Fix".
- [x] Top 10 news (21 Nov): an artist, album or song entering its all-time top 10 ("Radiohead is in your top 10 artists", "#5 of all time, with 1,234 listens."), or a new #1 ("… is your new #1 song", "Taking over from …"), linked to History's all-time overview. Each top 10 is kept as last seen (`top_seen`, per kind) and worked out again, with the notification check every 5 minutes, when there are newer listens. The first look only notes them; entries count once there are ten; more than three entering at once (an import) make one notification; they go after two weeks.

### Phase 18 — Profile (20 Nov 2026)
- [x] A profile page (/profile, from the avatar in the header): name, picture and email from Plex, when you joined, admin badge; scrobbles, ratings and gigs been to (each linking to its page) and how far back your history goes; your servers (yours or shared with you).
- [x] Personal preferences in one place: the accent colour and what the app opens on a phone (moved from Settings → Appearance, which now leads here); the theme on the account (dark or light on every device, or per device as before: the header's ☀ / ☾ then saves to the account too when it has a theme); achievements on or off (Phase 19); for admins, whether to get the instance's notifications (Phase 17).
- [x] Settings → Appearance moved here (Settings keeps servers, services and admin).

### Phase 19 — Achievements (20 Nov 2026)
Small, silly rewards. Every one has a name from music lore, a badge, and a humorous message; unlocking one pops up a short celebration (confetti / a spinning record; calmer with reduced motion).
- [x] 32 to start with (`plekstender/achievements.py`, wording to be polished), each a threshold on one of the person's own numbers:
  - The library (what their account can see): Roadie, Session Musician, Headliner, Golden God, Hall of Fame (100 / 250 / 500 / 1,000 / 2,500 artists); Deep Cut, B-Side, Crate Digger, Music Hoarder, Platinum Record, These Go to Eleven (10,000 / 25,000 / 50,000 / 100,000 / 250,000 / 1,000,000 tracks).
  - Rating: One-Hit Wonder (a first rating), Liner Notes Reader (1,000 songs), Air Guitar / Stage Diver / Encore / Critic's Choice (10 / 25 / 50 / 100 % of songs rated: "Even the hidden track. Even the 12-minute one.").
  - Guess the intro: Sound Check (a first round), Backstage Pass (100 rounds), Brown M&Ms (ten right in a row: "Every detail of the rider, checked.").
  - Gigs: Front Row (a first), Groupie (25), Band-Aid (50), Tour Bus (10 venues), World Tour (3 countries), Residency (12 in a year).
  - Connections: Patch Bay (a scrobble service), Daisy Chain (three).
  - Listening: Heavy Rotation and Gold Record (10,000 / 100,000 scrobbles), On Tour (30 days in a row), Deep Diver (1,000 artists heard).
- [x] Profile → Achievements: the collected ones as trading cards (name, a record in the colour of its kind, the kind, its message as flavour text, what it was for, the date and its number in the set, e.g. 07/32) in a messy pile, newest on top. The scroll wheel first scrolls the page until the whole pile is in view (and stops there), then flips through the cards, then lets the page scroll on past the last one (or the first, going up); a swipe, the arrow keys or ‹ › flip too, and a click on the last card gathers the pile up again. The rest aren't listed, only counted: "You've collected N of M cards". (21 Nov: replaced the first version's list of unlocked and locked ones with progress bars.)
- [x] Unlocked when the numbers change: worked out whenever the app asks (it checks every 3 minutes while open, and the profile asks when opened), each once per person (`user_achievements`), kept even if a number goes down later. New ones pop up at the bottom of the screen (a spinning record badge and confetti, both still with reduced motion) with what they're for ("✓ 100 artists in your library") and their message, one after another, until "Nice!".
- [x] Each person can turn them off (Profile: no pop-ups, no list); an admin can turn them off for everyone (Settings → Users & access).
- [ ] Badges drawn per achievement (today: a record-label badge in the accent colour with a symbol per kind).

## Later

### Writing imported plays back to Plex
Plekstender's own history makes this optional: the ratings queues and stats already count imported plays. If Plex's own play counts should include them too:
- **API mode:** `/:/scrobble` per play. Raises play counts, but every play gets the current timestamp, which distorts "recently played".
- **Database mode:** write `metadata_item_views` / `metadata_item_settings` in Plex's SQLite database with the real timestamps. Requires Plex to be stopped, a backup first and the database file mounted. Unsupported by Plex, so opt-in with explicit warnings.
- Either way, skip plays Plex already has (matched listens with `source = plex` are the overlap).

## Open questions
- Live shows (Phase 11): JamBase as the main source (free non-commercial plan, searches many artists by MusicBrainz ID in one request, includes DICE; to confirm: whether the free plan needs a payment card, given that overage is billed); Ticketmaster's Discovery API (free key, 5,000 requests a day) as a second source; Songkick through your own calendar link (iCal; confirm the link's form when building); DICE through JamBase (DICE has no public API; scraping it is against its protections and likely its terms). Not used: Integuru's unofficial Songkick API (scrapes songkick.com with your login cookies, only your tracked-artists calendar, no licence, paid hosting). Data Thistle: yes (you're in the UK; good for small venues, but no performer field and a tight quota); tokens renewed through its Auth API (`/v1/refresh`). Bandsintown needs an approved app ID; setlist.fm only has past shows (could show what an artist played recently).
- Tag editor fields (Phase 13): the `.todo.md` note is cut off; built with the likely candidates (see Phase 13). Anything missing?
- Does the derived popularity (top 3 tracks per album, top 10 songs per artist) rank artists sensibly on the real library? Tune it on Settings → Library using the preview.
- Plex history timestamps: is `viewedAt` really the end of the track, and does the account mapping hold for shared users? (`plex_probe.py --history`)
- Should playlists be shareable between users of the same server (e.g. "copy definition")?
- Licence of shared logo packs: fanart.tv images come with their own terms; the export should keep `source` per image so people can decide what they share.

### Polish (21 Nov)
- [x] Home → Latest ratings uses the site's star icons (half stars too), not "★★½".
- [x] Home → Status: a summary ("All working" / "1 needs a look" / "1 not working"), then tiles grouped as Plekstender, Yours and Services on this Plekstender; each with its account or server underneath, a meter for services with a request limit (admins), and "Fix" where it needs one. The API gives `subtitle` and `usage` instead of names like "Plex: Home".
- [x] A new achievement pops up as its trading card, flipping over from its back in front of the page (with confetti and a shine; just appears with reduced motion).
- [x] Gigs → Upcoming: a "🎟 Got tickets" filter (`/api/live/shows?tickets=true`).

### Album covers (21 Nov)
- [x] Library → Artwork gets sub-tabs: Artist images and **Album covers** (server owners).
- [x] Every cover in Plex is measured by the worker (only the start of each image is read; every 10 minutes, and right away when the tab finds unmeasured ones; again when a cover changes), in `album_covers`.
- [x] The list shows what most needs a better cover: none (or unreadable) first, then the smallest. Low resolution means under 600 px on the shorter side. Filters: needs a better one, no cover, low resolution, all; search by album or artist. "It's fine as it is" keeps an album out of these lists.
- [x] Sources, all open (no Apple developer account): Deezer's public search (1000 px), Apple's iTunes search (asked for at 3000 px), the Cover Art Archive (by Plex's MBID, else the release group found on MusicBrainz by title and artist, kept), Tidal (a free developer app's client ID and secret, set by an admin under Settings → Services → Artwork; search, then the albums' cover art with each file's size), fanart.tv album covers (the instance key), Plex's own posters, web search (SearXNG, own words allowed; square, big images ranked first) and uploads. Results for the same album and artist come first (a title inside the other counts: "OKNOTOK" / "OKNOTOK 1997 2017").
- [x] Preview with both sizes, crop to a square, at most 3000 px (JPEG), saved to Plex as the album's poster; its size is recorded straight away.
- [ ] Later: Tidal's animated covers (its artworks include VIDEO ones), cover.jpg in album folders, Tidal playlist import.

- [x] **Apply best match** (one album, or every album listed as a background artwork job, kind `covers`, ~3.5 s an album for iTunes' limit): the biggest cover of this very album (same title, remaster/edition notes aside, and same artist; the archive's front image by MBID) on Deezer, iTunes, Tidal and the Cover Art Archive that is at least 1000 x 1000 and near square (at most 5% longer one way, cropped). Sizes not given are read from the start of each image (`fetch.image_size`). Not applied when Plex's cover is already as big. Cover Art Archive and fanart.tv results now show their size the same way. So do iTunes results (asked for at 3000 px, Apple sends its original when smaller). While an artwork job runs (best covers, fanart.tv logos, logo export or import), its owner sees it in the notification bell: an amber dot on the bell while it runs, and at the top of the list its progress bar and a link; when done, what it did (counted as new until the list is opened; dismissable). Jobs that finished before the visit aren't shown. Which sources best matches may come from is chosen when starting a run for every album (remembered on the account, `best_cover_sources`, and used by the one-album button too); the source albums open on is a preference too (`cover_source`; a star on the tab: filled for the default, ☆ on the open or hovered tab to make it the default). Only sources usable here are offered, as tabs and for best matches (`GET …/artwork/albums/sources`: the open ones always, Tidal and fanart.tv once set up, web search while SearXNG answers); a default that isn't usable falls back to the first one, and best-match choices for sources not set up are kept for when they are. On wide screens the preview sits next to the covers found and stays in view while scrolling them, with the current cover above the new one (both bigger); on narrower screens it's below them, side by side.
