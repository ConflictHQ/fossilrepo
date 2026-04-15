#!/bin/bash
# fossilrepo ECS entrypoint — runs as root, supervisord manages all processes.
set -euo pipefail

# Ensure SSH host keys exist (persistent across restarts via EFS volume)
if [ ! -f /etc/ssh/ssh_host_ed25519_key ]; then
    ssh-keygen -A
fi

# Ensure data dirs exist with correct permissions
FOSSIL_REPOS_DIR="${FOSSIL_REPOS_DIR:-/data/repos}"
mkdir -p "$FOSSIL_REPOS_DIR" /data/trash /data/ssh
touch /data/ssh/authorized_keys
chmod 600 /data/ssh/authorized_keys
chown -R fossil:fossil /data/ssh
chown -R app:app "$FOSSIL_REPOS_DIR" /data/trash

# Compute JUPYTERHUB_SERVICE_PREFIX if not already set.
if [ -z "${JUPYTERHUB_SERVICE_PREFIX:-}" ] && [ -n "${JUPYTERHUB_USER:-}" ]; then
    SERVER="${JUPYTERHUB_SERVER_NAME:-}"
    if [ -n "$SERVER" ]; then
        export JUPYTERHUB_SERVICE_PREFIX="/user/${JUPYTERHUB_USER}/${SERVER}/"
    else
        export JUPYTERHUB_SERVICE_PREFIX="/user/${JUPYTERHUB_USER}/"
    fi
fi

# Run Django migrations as app user
gosu app python manage.py migrate --noinput

# Ensure the auto-auth user exists in Django before the first request arrives.
# AUTO_AUTH_USERNAME is the canonical source; JUPYTERHUB_USER is the fallback
# for when the spawner sets it separately.  ensure_user is idempotent.
_SEED_USER="${AUTO_AUTH_USERNAME:-${JUPYTERHUB_USER:-}}"
if [ -n "$_SEED_USER" ]; then
    gosu app python manage.py ensure_user \
        --username "$_SEED_USER" \
        --group "Administrators"
fi

gosu app python manage.py seed_content

exec supervisord -c /etc/supervisor/conf.d/fossilrepo.conf
