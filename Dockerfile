FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py .
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

# Run with Gunicorn, wrapped by OpenTelemetry auto-instrumentation
CMD ["opentelemetry-instrument", "gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "--access-logfile", "-", "--error-logfile", "-", "app:app"]

