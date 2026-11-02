#!/bin/bash
# Auto-update script for Kardinal container
# Pulls the latest image first and only recreates the container when the image
# has actually changed. A failed pull leaves the running container untouched.
# Usage: Run via cron or manually

COMPOSE_FILE="/srv/kardinal/docker-compose.registry.yml"
LOG_FILE="/var/log/kardinal-update.log"
CONTAINER_NAME="kardinal"

# Prefer the standalone docker-compose binary (what the server uses today),
# fall back to the Compose v2 plugin.
if command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD="docker-compose -f $(basename "$COMPOSE_FILE")"
else
    COMPOSE_CMD="docker compose -f $(basename "$COMPOSE_FILE")"
fi

# Function to log messages
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Function to force cleanup the container (recovery path only)
force_cleanup() {
    log "Performing force cleanup..."
    $COMPOSE_CMD down 2>/dev/null || true
    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
    log "Force cleanup completed"
}

log "Starting Kardinal update check..."

# Change to the directory containing the compose file
cd "$(dirname "$COMPOSE_FILE")" || {
    log "ERROR: Failed to change to directory $(dirname "$COMPOSE_FILE")"
    exit 1
}

# Image the compose file runs, and the image ID the container is running now
IMAGE="$(sed -n 's/^[[:space:]]*image:[[:space:]]*//p' "$(basename "$COMPOSE_FILE")" | head -n 1 | tr -d "\"'" | sed 's/[[:space:]]*#.*//; s/[[:space:]]*$//')"
if [ -z "$IMAGE" ]; then
    log "ERROR: Could not determine the image from $COMPOSE_FILE"
    exit 1
fi
RUNNING_ID="$(docker inspect --format '{{.Image}}' "$CONTAINER_NAME" 2>/dev/null || true)"

# Pull first, while the current container keeps serving traffic
log "Pulling latest image ($IMAGE)..."
if ! $COMPOSE_CMD pull; then
    log "ERROR: Failed to pull image; leaving the running container untouched"
    exit 1
fi

NEW_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null || true)"
if [ -z "$NEW_ID" ]; then
    log "ERROR: Pulled image $IMAGE not found locally; leaving the running container untouched"
    exit 1
fi

CONTAINER_RUNNING="$(docker inspect --format '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || true)"
if [ "$RUNNING_ID" = "$NEW_ID" ] && [ "$CONTAINER_RUNNING" = "true" ]; then
    log "Image unchanged (${NEW_ID:0:19}) and container running; nothing to do"
    log "Update check completed"
    exit 0
fi

# New image (or container missing/stopped): recreate. Compose replaces the
# container in place, so downtime is limited to the swap itself.
log "Image changed or container not running; recreating container..."
if $COMPOSE_CMD up -d; then
    log "Container updated successfully"
else
    log "ERROR: Failed to start container, attempting recovery..."
    force_cleanup
    sleep 2
    if $COMPOSE_CMD up -d; then
        log "Container started successfully after recovery"
    else
        log "ERROR: Failed to update container after recovery attempt"
        log "Container may be in an inconsistent state. Manual intervention may be required."
        exit 1
    fi
fi

log "Update check completed"
