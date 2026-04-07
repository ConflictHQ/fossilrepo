#!/bin/bash
# fossilrepo entrypoint — starts sshd + gunicorn.
#
# sshd runs in the background for Fossil SSH access.
# gunicorn runs in the foreground as the main process.

set -euo pipefail

# Ensure SSH host keys exist (persistent across restarts via volume)
if [ ! -f /etc/ssh/ssh_host_ed25519_key ]; then
    ssh-keygen -A
fi

# Ensure SSH data dir exists and has correct permissions
mkdir -p /data/ssh
touch /data/ssh/authorized_keys
chmod 600 /data/ssh/authorized_keys
chown -R fossil:fossil /data/ssh

# Ensure fossil user can read repos
chown -R fossil:fossil /data/repos

# Start sshd in the background (non-detach mode with -D would block)
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

# Run gunicorn in the foreground
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 3 \
    --timeout 120
