#!/bin/bash
# sync_to_fossil.sh — Commit current code into the existing .fossil repo.
#
# Opens a temporary checkout, rsyncs the working tree in, commits changes.
# NEVER replaces or reimports the .fossil file — preserves all tickets,
# wiki, forum, and other Fossil-native artifacts.
#
# Usage: ./scripts/sync_to_fossil.sh ["commit message"]
# Run from inside the container or via:
#   docker compose exec backend bash scripts/sync_to_fossil.sh "message"

set -euo pipefail

REPO="/data/repos/fossilrepo.fossil"
WORKDIR="/tmp/fossil-checkout-$$"
MESSAGE="${1:-Sync from working tree}"

export USER="${USER:-ragelink}"

if [ ! -f "$REPO" ]; then
    echo "Error: $REPO not found" >&2
    exit 1
fi

echo "=== Committing to Fossil ==="

# Create temp checkout
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

fossil open "$REPO" --workdir "$WORKDIR" 2>/dev/null
fossil update trunk 2>/dev/null || true

# Sync code from /app — use tar to copy with exclusions (rsync not available)
cd /app
tar cf - \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.ruff_cache' \
    --exclude='node_modules' \
    --exclude='assets' \
    --exclude='.env' \
    --exclude='repos' \
    --exclude='.fslckout' \
    --exclude='_FOSSIL_' \
    --exclude='*.fossil' \
    --exclude='.claude' \
    . | (cd "$WORKDIR" && tar xf -)
cd "$WORKDIR"

# Register new/deleted files
fossil addremove 2>/dev/null || true

# Commit if there are changes
CHANGES=$(fossil changes 2>/dev/null | wc -l | tr -d ' ')
if [ "$CHANGES" -gt 0 ]; then
    fossil commit -m "$MESSAGE" --no-warnings 2>&1 | tail -3
    echo "Committed $CHANGES changed files."
else
    echo "No changes to commit."
fi

# Cleanup
fossil close --force 2>/dev/null || true
cd /
rm -rf "$WORKDIR"

echo "=== Status ==="
echo "Checkins: $(fossil sql -R "$REPO" "SELECT count(*) FROM event WHERE type='ci';" | tr -d "' ")"
echo "Wiki: $(fossil wiki list -R "$REPO" | wc -l) pages"
echo "Tickets: $(fossil sql -R "$REPO" "SELECT count(*) FROM ticket;" | tr -d "' ")"
