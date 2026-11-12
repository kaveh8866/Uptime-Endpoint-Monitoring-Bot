# syntax=docker/dockerfile:1
# =============================================================================
# Uptime Endpoint Monitoring Bot — worker image
# =============================================================================
# Deliberately boring: python:3.12-slim, one dependency (PyYAML), no build
# toolchain, no shell utilities. Everything the bot needs is in the standard
# library, which keeps the image small enough for a 1 OCPU Always Free ARM VM
# and the attack surface close to zero.
#
#   src/      monitoring loop, probes, storage, notifiers (baked in)
#   configs/  endpoints + thresholds, mounted read-only at runtime so the watch
#             list can change without rebuilding the image
#   /data     SQLite state and history (named volume, writable by `bot`)

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    ENDPOINTS_FILE=/app/configs/endpoints.yaml \
    THRESHOLDS_FILE=/app/configs/thresholds.yaml \
    STATE_DB_PATH=/data/uptime.db

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r /app/requirements.txt

COPY src/ /app/src/
COPY configs/ /app/configs/

# Non-root, and the only writable path is the state volume.
RUN groupadd --system bot \
 && useradd --system --gid bot --home-dir /home/bot --create-home bot \
 && mkdir -p /data \
 && chown -R bot:bot /data

USER bot

# Graceful shutdown is handled by the loop itself (SIGTERM sets a stop event).
STOPSIGNAL SIGTERM
CMD ["python", "-m", "bot.main"]
