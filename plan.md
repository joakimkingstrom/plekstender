# RoadieFM — Implementation Plan

Companion to [app-features.md](app-features.md). This document covers *how* the app gets built; the feature doc covers *what* it does.

## Decisions

| Topic | Decision |
|---|---|
| Name | **RoadieFM** (no "Plex" in the name, per Plex's trademark guidelines; "for Plex & Plexamp" is fine in descriptions). Logo is being designed separately. |
| Stack | FastAPI (Python) backend + React/Vite/TypeScript frontend |
| Users | Multi-user: anyone with a Plex account can sign in and connect a server they have access to. Must be safe to expose publicly. |
| Listening history | RoadieFM keeps its own per-user listening history (Plex plays + imported history) and shows stats on it (Phase 2). Inspired by Koito, but built natively: Koito is single-user, Go + SQLite, with its own catalogue. |
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
plex-helper-app/                     # repo (app name: RoadieFM)
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
│   └── src/roadiefm/
│       ├── cli.py                   # roadiefm web | worker | migrate | healthcheck | openapi
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
- *Listened*: `view_count > 0`, or listened to according to RoadieFM's history (Phase 2), which includes imported Last.fm plays
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

RoadieFM keeps every user's plays in its own `listens` table. Stats, the ratings "Listened" pool and play-count weighting all read from it, so years of Last.fm history count without writing anything into Plex.

**Where plays come from**
- **Plex play history** (main source). The worker polls `/status/sessions/history/all` every few minutes per server with a `viewedAt>` watermark, and backfills all existing music history the first time. Plex records a play once a track is ~90% played, so these are finished listens.
  - The server owner's token sees every account's plays; `accountID` is mapped to RoadieFM users (the owner is account 1, others by plex.tv account ID). Plays by accounts that don't use RoadieFM are not stored.
  - If the owner doesn't use RoadieFM, try the shared user's own token (to be confirmed by the spike). Last resort: derive plays from `view_count` increases during incremental sync, timed by `last_viewed_at` (approximate; marked as such).
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
- New Plex listens go into an outbox and are sent in batches as `single` listens: names, `duration_ms`, MBIDs when known (recording/release/artist), `media_player` from the Plex device when known, `submission_client: "roadie.fm"`. Retries with backoff, honours `X-RateLimit-*` headers; a rejected token pauses scrobbling and shows a warning.
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
- [ ] **Before 1 Dec:** `listens` + `listen_imports`, Last.fm API key setting, per-user Last.fm import job with progress UI, matching to the mirror, simple history list
- [ ] Spike: `plex_probe.py --history` (fields, filters, account IDs, what a shared user's token can read)
  - First run (owner token): history is readable (45k entries of all types). The `type=10` filter returns **0** entries, so music plays must be selected by `librarySectionID` instead (probe updated, needs a re-run). `/accounts` lists 12 accounts; the owner's plex.tv ID isn't among them, confirming the owner is account `1` and other accounts use their plex.tv IDs.
- [ ] Plex play capture: history polling + backfill, account mapping, cross-source duplicates
- [ ] ListenBrainz scrobbling: token, outbox, batched submit, retries
- [ ] Stats pages and per-item listen stats
- [ ] ListenBrainz import (API + export file), history export, delete all

### Phase 3 — Ratings
- [ ] Queue builders (3 pools × 3 strategies) with tests for distribution and artist interleaving
- [ ] Mobile swipe UI + undo
- [ ] Desktop songs / albums / artists layouts + layout toggle

### Phase 4 — Artwork
- [ ] Port transforms/text logos/fonts with tests
- [ ] Sources: fanart.tv (+ MusicBrainz fallback), Plex posters, upload, SearXNG
- [ ] Editor UI, status tracking, bulk apply job
- [ ] Folder save with path mapping
- [ ] Export/import zip with matching preview

### Phase 5 — Smart playlists
- [ ] Definition schema + builder service + preview endpoint
- [ ] Builder UI
- [ ] Scheduled refresh

### Phase 6 — Polish
- [ ] PWA install, offline shell
- [ ] M3U playlist exports
- [ ] Docs, screenshots, published images (GHCR)

## Later

### Preview playback
Fetch the track file directly from Plex (`/library/parts/…`) and trim it to 30–60 s with ffmpeg on the server (`-t 45 -c:a libopus`), streamed to an `<audio>` element. Plex counts plays from client timeline/scrobble reports, so direct file access should not affect stats (to be confirmed in a Phase 0 spike). Useful inside the playlist builder.

### Writing imported plays back to Plex
RoadieFM's own history makes this optional: the ratings queues and stats already count imported plays. If Plex's own play counts should include them too:
- **API mode:** `/:/scrobble` per play. Raises play counts, but every play gets the current timestamp, which distorts "recently played".
- **Database mode:** write `metadata_item_views` / `metadata_item_settings` in Plex's SQLite database with the real timestamps. Requires Plex to be stopped, a backup first and the database file mounted. Unsupported by Plex, so opt-in with explicit warnings.
- Either way, skip plays Plex already has (matched listens with `source = plex` are the overlap).

## Open questions
- Does the derived popularity (top 3 tracks per album, top 10 songs per artist) rank artists sensibly on the real library? Tune it on Settings → Library using the preview.
- Plex history timestamps: is `viewedAt` really the end of the track, and does the account mapping hold for shared users? (`plex_probe.py --history`)
- Should playlists be shareable between users of the same server (e.g. "copy definition")?
- Licence of shared logo packs: fanart.tv images come with their own terms; the export should keep `source` per image so people can decide what they share.
