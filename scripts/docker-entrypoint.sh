#!/bin/sh
# Runs as root (image default), fixes ownership on the mounted volumes if
# needed, then drops to the unprivileged `appuser` before exec'ing the real
# command. This makes the non-root container self-healing across:
#   - fresh named volumes (already appuser-owned from the image, no-op check)
#   - volumes previously written by an older root-user image (one-time chown)
#   - host bind-mounts owned by an arbitrary host uid
set -e

APP_UID=1000
APP_GID=1000

fix_ownership() {
    dir="$1"
    mkdir -p "$dir"
    owner_uid="$(stat -c '%u' "$dir" 2>/dev/null || echo "")"
    if [ "$owner_uid" != "$APP_UID" ]; then
        echo "[entrypoint] fixing ownership of $dir (uid $owner_uid -> $APP_UID)"
        chown -R "$APP_UID:$APP_GID" "$dir"
    fi
}

fix_ownership /app/data
fix_ownership /app/manga-image-translator/models

exec gosu appuser "$@"
