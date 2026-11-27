# syntax=docker/dockerfile:1
ARG TARGETARCH

# --- frontend build ---------------------------------------------------------------
# Static files, the same for every CPU: built natively even for multi-arch images.
FROM --platform=$BUILDPLATFORM node:26-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# The version the image is published as (empty for a local build): the page compares it with
# the server's to offer a reload after an update.
ARG PLEKSTENDER_VERSION=""
RUN VITE_APP_VERSION="$PLEKSTENDER_VERSION" npm run build

# --- fpcalc (Chromaprint) for identifying files by sound ----------------------------
# The official static builds (a couple of MB), checked against their published digests.
FROM scratch AS fpcalc-amd64
ADD --checksum=sha256:c968ad4a0821592a79b158fb2e1f46cbe0a22a0a1dbc0743c67d68e75d15d2bb \
    https://github.com/acoustid/chromaprint/releases/download/v1.6.2/chromaprint-fpcalc-1.6.2-linux-x86_64.tar.gz /fpcalc.tar.gz
FROM scratch AS fpcalc-arm64
ADD --checksum=sha256:3aa1498126ed272d805bc9d081c77965c9e1e1a0891bb112c13b56ef2ad47bad \
    https://github.com/acoustid/chromaprint/releases/download/v1.6.2/chromaprint-fpcalc-1.6.2-linux-arm64.tar.gz /fpcalc.tar.gz
FROM fpcalc-${TARGETARCH} AS fpcalc

# --- python runtime ---------------------------------------------------------------
FROM python:3.15-slim AS app
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
RUN --mount=from=fpcalc,source=/fpcalc.tar.gz,target=/tmp/fpcalc.tar.gz \
    tar -xzf /tmp/fpcalc.tar.gz -C /usr/local/bin --strip-components=1 --wildcards '*/fpcalc' \
    && fpcalc -version

COPY --from=web /web/dist /app/static

RUN useradd --system --uid 1000 --no-create-home --home-dir /app plekstender \
    && mkdir -p /data && chown plekstender /data

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STATIC_DIR=/app/static \
    DATA_DIR=/data \
    PORT=8080
EXPOSE 8080

# Last, so a new version doesn't invalidate the layers above. Set by the publish workflow;
# left empty, the app reports its package version marked -dev. In a file, not the
# environment: a deployment's own PLEKSTENDER_VERSION (the image tag it pulls, say "latest")
# must not change which version the image says it is.
ARG PLEKSTENDER_VERSION=""
ARG PLEKSTENDER_COMMIT=""
RUN printf '{"version": "%s", "commit": "%s"}\n' "$PLEKSTENDER_VERSION" "$PLEKSTENDER_COMMIT" \
    > /app/build.json
USER plekstender

# The same image runs every service; compose picks the command (web | worker | migrate).
CMD ["plekstender", "web"]
