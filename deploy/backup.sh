#!/usr/bin/env bash
# Back up the database and files into ./backups (checksummed), and delete backups older than 7 days.
# Usage:  bash deploy/backup.sh          (put it in cron: see docs/DEPLOY_FREE_ORACLE.md)
# Encryption keys are NOT in the backup: they live in .env, which you keep off the server too.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups && chmod 777 backups   # the API runs as a different user inside the container
docker compose -f docker-compose.yml -f docker-compose.tls.yml exec -T api \
  uv run --no-dev --frozen python -m scripts.backup --out /srv/backups
find backups -maxdepth 1 -name 'nb-backup-*' -mtime +7 -exec rm -rf {} +
echo "backups kept: $(ls -1 backups | wc -l)"
