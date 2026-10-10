FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# tzdata is required: slim images ship no IANA timezone database, and
# schedule validation depends on it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# ---------------------------------------------------------------------------
# Port selection.
#
# Render assigns a port through the PORT environment variable and expects the
# process to bind exactly that one. Docker Compose and local runs do not set
# PORT, so it defaults to 8000 and the previous behaviour is unchanged.
#
# The HEALTHCHECK and CMD both read the same expression, otherwise the health
# probe would keep testing the wrong port.
# ---------------------------------------------------------------------------

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT:-8000}/api/health" || exit 1

# Shell form so ${PORT:-8000} expands, and `exec` so uvicorn becomes PID 1 and
# receives SIGTERM directly for a graceful shutdown.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"