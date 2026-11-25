#!/usr/bin/env bash
# Reset a user's password: deletes the account and creates it again (same e-mail, name and role) with a NEW password.
# Also clears a lockout caused by wrong passwords. Run it ON THE SERVER, inside the project folder.
# Usage:  bash deploy/reset_user.sh you@gmail.com "Your Name" [role]      (role defaults to admin)
# Only use it for accounts that have no data you need: records created by the user keep their author id, which will no longer match.
set -euo pipefail
EMAIL="${1:?usage: bash deploy/reset_user.sh EMAIL \"Full Name\" [role]}"
NAME="${2:?usage: bash deploy/reset_user.sh EMAIL \"Full Name\" [role]}"
ROLE="${3:-admin}"
cd "$(dirname "$0")/.."
C="docker compose -f docker-compose.yml -f docker-compose.tls.yml"
read -r -p "This deletes the account $EMAIL and creates it again as $ROLE. Continue? (yes/no) " ANSWER
[ "$ANSWER" = "yes" ] || { echo "cancelled"; exit 1; }
$C exec -T -e TARGET="$EMAIL" mongo sh -c \
  'mongosh -u neurobridge -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin neurobridge --quiet --eval "printjson(db.users.deleteOne({email: process.env.TARGET.toLowerCase()}))"'
bash deploy/create_admin.sh "$EMAIL" "$NAME" "$ROLE"
