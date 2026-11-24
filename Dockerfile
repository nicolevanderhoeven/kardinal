# Base image pinned by digest so a rebuild cannot silently pick up a different
# (or tampered) image. Dependabot (.github/dependabot.yml) proposes digest bumps
# weekly. To update by hand: docker buildx imagetools inspect python:3.12-slim
FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30

# No .pyc files (the root filesystem is read-only in the compose files) and
# unbuffered logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies from the hash-locked file: pip refuses anything that is
# not in it or does not match its hash. Regenerate it after editing
# requirements.txt (see the header of requirements.txt).
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Copy application files
COPY app.py gunicorn.conf.py ./
COPY templates/ ./templates/
COPY static/ ./static/

# Create non-root user for security
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

# Expose port
EXPOSE 5000

# OpenTelemetry defaults. Inert unless a deployment turns tracing on: with no
# exporter and no endpoint the SDK does nothing and nothing is sent anywhere.
# To enable, set OTEL_TRACES_EXPORTER=otlp and OTEL_EXPORTER_OTLP_ENDPOINT
# (see docker-compose.registry.yml).
ENV OTEL_SERVICE_NAME=kardinal \
    OTEL_TRACES_EXPORTER=none \
    OTEL_METRICS_EXPORTER=none \
    OTEL_LOGS_EXPORTER=none \
    OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf

# /health from inside the container (requests from loopback get the detailed
# answer; see app.py). Python is used because the slim image has no curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=4)"]

# Run with Gunicorn (settings in gunicorn.conf.py), wrapped by OpenTelemetry
# auto-instrumentation
CMD ["opentelemetry-instrument", "gunicorn", "-c", "gunicorn.conf.py", "app:app"]
