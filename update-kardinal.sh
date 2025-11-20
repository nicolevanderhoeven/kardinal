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
    
    # Check if container needs to be updated
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

