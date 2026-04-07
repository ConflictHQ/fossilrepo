#!/bin/bash
# fossilrepo entrypoint — starts sshd as root, drops to app user for gunicorn.
#
# sshd needs root for port binding and key access.
# gunicorn runs as the unprivileged 'app' user.

set -euo pipefail

# Ensure SSH host keys exist (persistent across restarts via volume)
if [ ! -f /etc/ssh/ssh_host_ed25519_key ]; then
    ssh-keygen -A
fi

# Ensure data dirs exist with correct permissions
mkdir -p /data/ssh /data/repos /data/trash
touch /data/ssh/authorized_keys
chmod 600 /data/ssh/authorized_keys
chown -R fossil:fossil /data/ssh
chown -R app:app /data/repos /data/trash
# fossil user needs read access to repos for SSH sync
chmod -R g+r /data/repos

# Start sshd in the background (runs as root)
/usr/sbin/sshd -p 2222 -e &
SSHD_PID=$!
echo "sshd started (PID $SSHD_PID) on port 2222"

# Trap signals to clean up sshd
cleanup() {
    echo "Shutting down sshd..."
    kill "$SSHD_PID" 2>/dev/null || true
    wait "$SSHD_PID" 2>/dev/null || true
}
trap cleanup EXIT TERM INT

# Drop to non-root 'app' user for gunicorn
exec gosu app gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 3 \
    --timeout 120
