#!/usr/bin/env bash
# Pull the newest code from GitHub and rebuild. Take a backup first (deploy/backup.sh) if you changed anything important.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.tls.yml ps
