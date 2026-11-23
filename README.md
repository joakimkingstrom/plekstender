<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="branding/github/readme-header-dark.png">
    <img src="branding/github/readme-header-light.png" alt="Plekstender" width="450">
  </picture>
</h1>

A self-hosted helper app for Plex & Plexamp music libraries: rate songs, albums and artists quickly, keep a listening history with stats, replace artist photos with logos, and build playlists that are smarter than "artist title contains…" filters. Several people can use one instance, each with their own Plex account.

Plekstender isn't affiliated with or endorsed by Plex.

![artist page](screenshots/SCR-20261122-bucl.png)

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

![artist image tool](screenshots/SCR-20261122-bvqi.png)

![bulk tag editor using musicbrainz metadata](screenshots/SCR-20261122-bxrk.png)

![rating tool](screenshots/SCR-20261122-bumj.png)

![upcoming gigs](screenshots/SCR-20261122-byfd.png)

![playlist creator/editor](screenshots/SCR-20261122-cawg.png)

![discover tool](screenshots/SCR-20261122-bvcx.png)

## Running with Docker

```bash
cp .env.example .env
# Fill in APP_URL, APP_SECRET_KEY and POSTGRES_PASSWORD (see comments in the file)
docker compose up -d --build
```

Open `APP_URL` and sign in with Plex. The first account to sign in becomes the instance admin. To make sure that's you, it has to enter the **setup code** the web app writes to its log while nobody has signed in (`docker compose logs frontend`; a new code at each start until the instance is claimed). `INITIAL_ADMIN` can also limit it to your Plex username or email. Then connect a Plex server and choose its music library.

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

- **fanart.tv:** artist logos need a free API key (Settings → Services, or `FANART_API_KEY`).
- **Album covers:** Deezer, iTunes and the Cover Art Archive need nothing; Tidal needs a free app's client ID and secret from [developer.tidal.com](https://developer.tidal.com) (Settings → Services → Artwork or Playlists). The same app reads public Tidal playlists for importing.
- **Last.fm import and scrobbling:** import needs an API key, scrobbling to Last.fm needs its shared secret too (Settings → Scrobbling, or `LASTFM_API_KEY` and `LASTFM_SHARED_SECRET`). From 1 December 2026, Last.fm only allows this with a Last.fm Pro subscriber's key.
- **Identifying files by sound:** needs a free AcoustID application key (register one at [acoustid.org/new-application](https://acoustid.org/new-application); Settings → Services, or `ACOUSTID_API_KEY`). The Docker image includes Chromaprint's `fpcalc`; elsewhere, install it or set `FPCALC_PATH`.
- **Gigs (upcoming):** needs a JamBase Data API key (the free Developer plan at [data.jambase.com](https://data.jambase.com) is for non-commercial use; Settings → Services, or `JAMBASE_API_KEY`). It has 1,000 requests a month and bills requests past that, so Plekstender stops at `JAMBASE_MONTHLY_CAP` (default 900) and spreads each person's daily refresh over the month. Places are searched with Open-Meteo's free geocoding (only the typed text is sent).
- **Gigs, UK listings:** Data Thistle (a free account at [api.datathistle.com](https://api.datathistle.com)): paste its access and refresh tokens in Settings → Services; Plekstender renews them itself. 1,000 requests a month (`DATATHISTLE_DAILY_CAP`, default 30 a day); listings are looked up when someone opens Live and kept a day at most, as its terms ask.
- **More scrobble services:** besides ListenBrainz, each person can send their Plex plays to Last.fm, Maloja, Koito, Multi-Scrobbler (or any ListenBrainz-compatible server) and Libre.fm, under Settings → Scrobbling. Last.fm is connected by signing in on last.fm.
- **Gigs from setlist.fm:** Gigs → Been to can import the concerts people marked as attended on setlist.fm with an admin's free, non-commercial API key (Settings → Services, or `SETLISTFM_API_KEY`). Venues are placed on the gig map with OpenStreetMap's Nominatim (one request a second, results shared).
- **Gigs, second source:** a free Ticketmaster Discovery API key (an app at [developer.ticketmaster.com](https://developer.ticketmaster.com); Settings → Services, or `TICKETMASTER_API_KEY`) adds Ticketmaster's shows; shows both sources find are listed once. Starred shows are in a private calendar feed (Gigs → Upcoming → Your calendar) that calendar apps fetch without signing in, so `APP_URL` must be reachable from them.
- **Image search:** start with `--profile search` to run SearXNG next to Plekstender, or point `SEARXNG_URL` at your own.
- **Artist folders:** to save `artist.jpg` next to your music, mount the music folder read-write into the **worker** container only (see `compose.override.example.yaml`; the web app never touches music files) and set it up in Settings → Music files.

### Who can sign in

On Settings → Admin, choose one of:

- **Approved servers** (default): people with access to a Plex server you've approved, such as friends you share your server with.
- **Allowlist**: only the Plex usernames or emails you list.
- **Open**: any Plex account.

Plex tokens are stored encrypted with a key derived from `APP_SECRET_KEY` and are never sent to the browser.

## Development

Requirements: Python 3.14+ with [uv](https://docs.astral.sh/uv/), Node 24+, and a Postgres database.

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

More for developers in [`docs/`](docs): [architecture](docs/architecture.md), [how the features work](docs/how-it-works.md) and [ideas](docs/ideas.md).

### Versions and releases

Every image built from `main` gets the next version number (`0.1.<commits>`), shown on Home → Status.
To make a release, run **Actions → Release**: it tags the newest published build (`v0.1.214`),
writes the release notes from the commits since the last release, and moves the image tag `stable`
to it. For 0.2 or 1.0, change the version in `backend/pyproject.toml` first.

## Credits

- IP geolocation by [DB-IP](https://db-ip.com) (IP to City Lite, CC BY 4.0), downloaded monthly to the server; lookups never leave it.
- Country shapes from [Natural Earth](https://www.naturalearthdata.com) (public domain); rebuild with `frontend/scripts/world-map.mjs`.
- Artist logos from [fanart.tv](https://fanart.tv), artist IDs and releases from [MusicBrainz](https://musicbrainz.org) (CC0), release covers from the [Cover Art Archive](https://coverartarchive.org), fingerprints by [Chromaprint](https://acoustid.org/chromaprint) and matched by [AcoustID](https://acoustid.org), fonts from [Google Fonts](https://fonts.google.com). Images keep their own terms; logo packs record where each one came from.

## License

MIT
