#!/bin/bash
# Auto-update script for Kardinal container
# This script pulls the latest image and restarts the container if a new image is available
# Usage: Run via cron or manually

COMPOSE_FILE="/srv/kardinal/docker-compose.registry.yml"
LOG_FILE="/var/log/kardinal-update.log"
COMPOSE_CMD="docker-compose -f $(basename "$COMPOSE_FILE")"

# Function to log messages
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Function to force cleanup containers
force_cleanup() {
    log "Performing force cleanup..."
    # Stop containers
    $COMPOSE_CMD stop 2>/dev/null || true
    # Remove containers forcefully
    $COMPOSE_CMD rm -f 2>/dev/null || true
    # Also try docker-compose down as backup
    $COMPOSE_CMD down 2>/dev/null || true
    # Force remove the container by name if it still exists
    docker rm -f kardinal 2>/dev/null || true
    log "Force cleanup completed"
}

log "Starting Kardinal update check..."

# Change to the directory containing the compose file
cd "$(dirname "$COMPOSE_FILE")" || {
    log "ERROR: Failed to change to directory $(dirname "$COMPOSE_FILE")"
    exit 1
}

# Force cleanup before starting to ensure clean state
force_cleanup

# Pull the latest image
log "Pulling latest image..."
if $COMPOSE_CMD pull; then
    log "Image pull completed"
    
    # Start the container with the new image
    log "Starting container with new image..."
    if $COMPOSE_CMD up -d; then
        log "Container updated successfully"
    else
        log "ERROR: Failed to start container, attempting recovery..."
        # Attempt recovery by force cleaning and retrying once
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
else
    log "ERROR: Failed to pull image"
    exit 1
fi

log "Update check completed"

