# =============================================================================
# Dockerfile for the SparkMe / Candor interview web app (Render, Cloud Run, etc.)
# Runs the Flask backend + frontend together as a single persistent container.
# =============================================================================
FROM python:3.10-slim

WORKDIR /app

# System deps: gcc/python3-dev for building a few wheels, ffmpeg for audio (TTS).
# NOTE: PyAudio / portaudio are intentionally NOT installed — microphone capture
# happens in the browser, not on the server, so the server only needs to accept
# uploaded audio. This keeps the image smaller and the build reliable.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

# Working dirs for logs + interview data live under /var/data (mounted to a
# persistent disk on Render). The repo's data/configs/*.json topic plans stay
# in the image at /app/data/configs and are NOT masked, because the disk mounts
# at /var/data, not /app/data.
RUN mkdir -p /var/data/logs /var/data/data /app/logs && \
    chmod -R 777 /var/data /app/logs

ENV PYTHONUNBUFFERED=1
ENV LOGS_DIR=/var/data/logs
ENV DATA_DIR=/var/data/data
ENV PORT=8080

EXPOSE 8080

# IMPORTANT: exactly ONE worker. Interview sessions live in-memory in the
# process (active_sessions dict + per-session asyncio loops), so multiple
# workers would break session affinity. Concurrency is handled via threads.
CMD exec gunicorn --bind :$PORT \
    --workers 1 \
    --threads 8 \
    --timeout 600 \
    --worker-class gthread \
    --access-logfile - \
    --error-logfile - \
    src.main_flask:app
