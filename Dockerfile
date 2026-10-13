# opal_listener API + listener runtime. Multi-arch (amd64 / arm64 Raspberry Pi 5).
FROM python:3.12-slim-bookworm

ARG EXTRAS=""
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libportaudio2 alsa-utils curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 --groups audio opal

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY opal_listener ./opal_listener
RUN pip install ".${EXTRAS:+[$EXTRAS]}" && mkdir -p /data && chown opal:opal /data

ENV OPAL_LISTENER_DATA_DIR=/data \
    OPAL_LISTENER_VOSK_MODEL_PATH=/models/vosk-model-small-en-us-0.15 \
    OPAL_LISTENER_HOST=0.0.0.0 \
    OPAL_LISTENER_PORT=8000

USER opal
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1
CMD ["opal-listener-server"]
