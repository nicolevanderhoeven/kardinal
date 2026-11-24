#!/bin/bash
# Example: pinned, verified update for the Kardinal container.
#
# Unlike update-kardinal.sh (which follows the mutable :latest tag), this deploys
# exactly the image named in an env file, by content digest, so a malicious or
# broken push to :latest does nothing until the owner changes that file on
# purpose (a two-line change, e.g. monthly). It also checks the result and rolls
# back to the last image that was healthy.
#
# Setup (on the server):
#   1. cp update-kardinal-pinned.sh /srv/kardinal/ && chmod 700 /srv/kardinal/update-kardinal-pinned.sh
#   2. echo 'KARDINAL_IMAGE=nvanderhoeven/kardinal@sha256:<64 hex digits>' > /srv/kardinal/kardinal.env
#      (the digest is shown in the "Image digest" summary of each GitHub Actions run, or:
#       docker buildx imagetools inspect nvanderhoeven/kardinal:latest)
#   3. cron: 0 2 * * * /srv/kardinal/update-kardinal-pinned.sh
#      (replaces the update-kardinal.sh entry)
#   To release a new build: put its digest in kardinal.env; the next run (or a manual run)
#   deploys it.
#
# It needs docker-compose.registry.yml with
#   image: ${KARDINAL_IMAGE:-nvanderhoeven/kardinal:latest}
# and an image with a HEALTHCHECK (current builds have one).
#
# What it checks before and after changing anything:
#   - the env file holds one well-formed image@sha256:<digest> reference
#     (the file is parsed, never sourced as shell);
#   - the compose file does not mount the Docker socket or run privileged;
#   - the pulled image really carries that digest;
#   - the new container reports healthy within HEALTH_WAIT_SECONDS, otherwise the
#     previous known-good image is restored.
# A failed pull or failed check leaves the running container untouched (or restores it).
# Paths can be overridden from the environment (used by the tests).

KARDINAL_DIR="${KARDINAL_DIR:-/srv/kardinal}"
COMPOSE_FILE="${COMPOSE_FILE:-$KARDINAL_DIR/docker-compose.registry.yml}"
ENV_FILE="${ENV_FILE:-$KARDINAL_DIR/kardinal.env}"
LAST_GOOD_FILE="${LAST_GOOD_FILE:-$KARDINAL_DIR/kardinal.last-good}"
LOG_FILE="${LOG_FILE:-/var/log/kardinal-update.log}"
CONTAINER_NAME="${CONTAINER_NAME:-kardinal}"
HEALTH_WAIT_SECONDS="${HEALTH_WAIT_SECONDS:-90}"

IMAGE_RE='^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$'

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

if command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD="docker-compose -f $(basename "$COMPOSE_FILE")"
else
    COMPOSE_CMD="docker compose -f $(basename "$COMPOSE_FILE")"
fi

# Bring the container up on a given image reference (compose substitutes KARDINAL_IMAGE).
deploy() {
    KARDINAL_IMAGE="$1" $COMPOSE_CMD up -d
}

# Wait until the container is healthy. Returns 0 if so, 1 on timeout or unhealthy.
wait_healthy() {
    local waited=0 status
    while [ "$waited" -lt "$HEALTH_WAIT_SECONDS" ]; do
        status="$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER_NAME" 2>/dev/null || true)"
        case "$status" in
            healthy) return 0 ;;
            unhealthy) return 1 ;;
        esac
        sleep 3
        waited=$((waited + 3))
    done
    return 1
}

log "Starting pinned Kardinal update check..."

if [ ! -r "$ENV_FILE" ]; then
    log "ERROR: $ENV_FILE not found; nothing to deploy"
    exit 1
fi
IMAGE="$(sed -n 's/^KARDINAL_IMAGE=//p' "$ENV_FILE" | tail -n 1 | tr -d "\"'[:space:]")"
if ! printf '%s' "$IMAGE" | grep -Eq "$IMAGE_RE"; then
    log "ERROR: KARDINAL_IMAGE in $ENV_FILE must look like name@sha256:<64 hex digits>; refusing to run"
    exit 1
fi
WANT_DIGEST="${IMAGE#*@}"

cd "$(dirname "$COMPOSE_FILE")" || {
    log "ERROR: Failed to change to directory $(dirname "$COMPOSE_FILE")"
    exit 1
}
if [ ! -r "$(basename "$COMPOSE_FILE")" ]; then
    log "ERROR: Cannot read $COMPOSE_FILE"
    exit 1
fi
if ! grep -q 'KARDINAL_IMAGE' "$(basename "$COMPOSE_FILE")"; then
    log "ERROR: $COMPOSE_FILE does not use \${KARDINAL_IMAGE}; it would ignore the pinned image; refusing to run"
    exit 1
fi
if grep -Eq 'docker\.sock|privileged:[[:space:]]*true' "$(basename "$COMPOSE_FILE")"; then
    log "ERROR: $COMPOSE_FILE mounts the Docker socket or runs privileged; refusing to run"
    exit 1
fi

log "Pulling pinned image $IMAGE..."
if ! docker pull "$IMAGE"; then
    log "ERROR: Failed to pull $IMAGE; leaving the running container untouched"
    exit 1
fi

# Content-addressed pull already guarantees this; checking it keeps the script honest.
if ! docker image inspect --format '{{json .RepoDigests}}' "$IMAGE" 2>/dev/null | grep -q "$WANT_DIGEST"; then
    log "ERROR: Pulled image does not carry digest $WANT_DIGEST; leaving the running container untouched"
    exit 1
fi
NEW_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null || true)"
RUNNING_ID="$(docker inspect --format '{{.Image}}' "$CONTAINER_NAME" 2>/dev/null || true)"
CONTAINER_RUNNING="$(docker inspect --format '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || true)"
log "Verified $IMAGE (image ID ${NEW_ID:0:19})"

if [ -n "$NEW_ID" ] && [ "$RUNNING_ID" = "$NEW_ID" ] && [ "$CONTAINER_RUNNING" = "true" ]; then
    log "Pinned image already running; nothing to do"
    exit 0
fi

PREVIOUS=""
[ -r "$LAST_GOOD_FILE" ] && PREVIOUS="$(head -n 1 "$LAST_GOOD_FILE" | tr -d "\"'[:space:]")"

log "Deploying $IMAGE..."
if deploy "$IMAGE" && wait_healthy; then
    echo "$IMAGE" > "$LAST_GOOD_FILE"
    log "Container healthy on $IMAGE"
    log "Update check completed"
    exit 0
fi

log "ERROR: New container did not become healthy"
if [ -n "$PREVIOUS" ] && [ "$PREVIOUS" != "$IMAGE" ] && printf '%s' "$PREVIOUS" | grep -Eq "$IMAGE_RE"; then
    log "Rolling back to last known-good image $PREVIOUS..."
    if deploy "$PREVIOUS" && wait_healthy; then
        log "Rolled back; $PREVIOUS is healthy. Fix or replace the digest in $ENV_FILE."
    else
        log "ERROR: Rollback to $PREVIOUS also failed; manual intervention required"
    fi
else
    log "No earlier known-good image recorded; manual intervention required"
fi
exit 1
