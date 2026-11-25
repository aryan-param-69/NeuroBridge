#!/usr/bin/env bash
# Create a user inside the running API container. The password is typed hidden and never put on the command line.
# Usage:  bash deploy/create_admin.sh you@gmail.com "Your Name" [role]      (role defaults to admin)
# Use a real-looking e-mail domain: reserved ones such as .local or .test are rejected.
set -euo pipefail
EMAIL="${1:?usage: bash deploy/create_admin.sh EMAIL \"Full Name\" [role]}"
NAME="${2:?usage: bash deploy/create_admin.sh EMAIL \"Full Name\" [role]}"
ROLE="${3:-admin}"
cd "$(dirname "$0")/.."
read -r -s -p "Password (12+ characters, upper and lower case, a digit): " PW; echo
export NB_USER_PASSWORD="$PW"
docker compose -f docker-compose.yml -f docker-compose.tls.yml exec -T -e NB_USER_PASSWORD api \
  uv run --no-dev --frozen python -m scripts.create_user "$EMAIL" "$NAME" "$ROLE"
