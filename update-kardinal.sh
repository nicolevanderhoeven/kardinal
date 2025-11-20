#!/bin/bash
# Auto-update script for Kardinal container
# This script pulls the latest image and restarts the container if a new image is available
# Usage: Run via cron or manually

set -e

COMPOSE_FILE="/srv/kardinal/docker-compose.registry.yml"
LOG_FILE="/var/log/kardinal-update.log"

# Function to log messages
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "Starting Kardinal update check..."

# Change to the directory containing the compose file
cd "$(dirname "$COMPOSE_FILE")" || exit 1

# Pull the latest image
log "Pulling latest image..."
if docker-compose -f "$(basename "$COMPOSE_FILE")" pull; then
    log "Image pull completed"
    
    # Stop and remove old container if it exists (handles ContainerConfig errors)
    log "Stopping and removing old container..."
    docker-compose -f "$(basename "$COMPOSE_FILE")" down 2>/dev/null || true
    
    # Start the container with the new image
    log "Starting container with new image..."
    if docker-compose -f "$(basename "$COMPOSE_FILE")" up -d; then
        log "Container updated successfully"
    else
        log "ERROR: Failed to update container"
        exit 1
    fi
else
    log "ERROR: Failed to pull image"
    exit 1
fi

log "Update check completed"

