# One image for every Python process: the API, the poller, the scorer and the cron jobs.
# Build from the repo root: docker build -f infra/python.Dockerfile .
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /bin/uv

# libgomp1: LightGBM's OpenMP runtime. curl: container health checks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# supercronic runs the nightly jobs with the container's environment (plain cron drops it)
ARG TARGETARCH=amd64
ARG SUPERCRONIC=v0.2.33
RUN case "$TARGETARCH" in \
      amd64) sha=71b0d58cc53f6bd72cf2f293e09e294b79c666d8 ;; \
      arm64) sha=e0f0c06ebc5627e43b25475711e694450489ab00 ;; \
      *) echo "unsupported arch $TARGETARCH" && exit 1 ;; \
    esac \
    && curl -fsSLo /usr/local/bin/supercronic \
       "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC}/supercronic-linux-${TARGETARCH}" \
    && echo "${sha}  /usr/local/bin/supercronic" | sha1sum -c - \
    && chmod +x /usr/local/bin/supercronic

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /app
# dependencies first, so a code change does not reinstall LightGBM
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY common ./common
COPY ingest ./ingest
COPY pipeline ./pipeline
COPY ml ./ml
COPY api ./api
RUN uv sync --frozen --no-dev
COPY infra/crontab /etc/bustrust.crontab

RUN useradd --system --uid 10001 bustrust && mkdir -p /data && chown bustrust /data
USER bustrust

# Local archives live on the shared /data volume unless pointed at s3:// in .env
ENV BT_RAW_ARCHIVE=/data/raw \
    BT_STATIC_ARCHIVE=/data/static \
    BT_PARQUET_DIR=/data/parquet \
    BT_MODEL_DIR=/data/models

CMD ["bustrust", "--help"]
