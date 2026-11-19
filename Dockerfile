# syntax=docker/dockerfile:1

# --- frontend build ---------------------------------------------------------------
# Static files, the same for every CPU: built natively even for multi-arch images.
FROM --platform=$BUILDPLATFORM node:24-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- python runtime ---------------------------------------------------------------
FROM python:3.14-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# Dependencies first (cached unless the lockfile changes), then the project itself.
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY backend/README.md backend/alembic.ini ./
COPY backend/src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev
# ffmpeg for song previews comes with the imageio-ffmpeg wheel: fail the build if it can't run.
RUN /app/.venv/bin/python -c "import imageio_ffmpeg, subprocess; \
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-version'], check=True, capture_output=True)"

COPY --from=web /web/dist /app/static

RUN useradd --system --uid 1000 --no-create-home --home-dir /app plekstender \
    && mkdir -p /data && chown plekstender /data
USER plekstender

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STATIC_DIR=/app/static \
    DATA_DIR=/data \
    PORT=8080
EXPOSE 8080

# The same image runs every service; compose picks the command (web | worker | migrate).
CMD ["plekstender", "web"]
