#!/usr/bin/env bash
# Nightly backup into a local directory (e.g. a mounted external disk or a synced folder):
# database dump + artifact files (content-addressed, so copies are incremental).
# Cron example: 30 3 * * * /opt/proofxiv/deploy/backup.sh /mnt/backup/proofxiv
set -euo pipefail
TARGET="$(realpath -m "${1:?usage: backup.sh <local backup dir>}")"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$TARGET/db" "$TARGET/data"
cd "$(dirname "$0")"
docker compose exec -T postgres pg_dump -U proofxiv -Fc proofxiv >"$TARGET/db/proofxiv-$STAMP.dump"
docker run --rm -v proofxiv_data:/data:ro -v "$TARGET/data":/backup alpine cp -a -u /data/. /backup/
find "$TARGET/db" -name 'proofxiv-*.dump' -mtime +30 -delete
echo "backup $STAMP done"
