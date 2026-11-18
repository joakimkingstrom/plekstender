# Plekstender

A self-hosted helper app for Plex & Plexamp music libraries: rate songs, albums and artists quickly, replace artist photos with logos, and build playlists that are smarter than "artist title contains…" filters.

> **Status:** early development. Sign-in, server connection, library sync and browsing, and Last.fm history import work; see [plan.md](plan.md) for the roadmap and [app-features.md](app-features.md) for the feature list.

Plekstender isn't affiliated with or endorsed by Plex.

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

### Who can sign in

On the Admin page, choose one of:

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

## License

MIT
