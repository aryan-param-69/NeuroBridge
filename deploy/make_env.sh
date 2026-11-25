#!/usr/bin/env bash
# Create the production .env with freshly generated secrets.
# Usage:  bash deploy/make_env.sh yourname.duckdns.org
# Refuses to overwrite an existing .env. Keep a copy of the generated .env somewhere safe and OFF the server
# (a password manager): losing ENCRYPTION_KEY makes stored identifiers and original EEG files unreadable.
set -euo pipefail
DOMAIN="${1:?usage: bash deploy/make_env.sh yourname.duckdns.org}"
cd "$(dirname "$0")/.."
[ -e .env ] && { echo ".env already exists: not overwriting it."; exit 1; }
command -v openssl >/dev/null || { echo "openssl is required"; exit 1; }

# Fernet key = 32 random bytes, URL-safe base64
FERNET=$(openssl rand -base64 32 | tr '+/' '-_')
umask 077
cat > .env <<EOF
ENVIRONMENT=production
SECRET_KEY=$(openssl rand -hex 32)
ENCRYPTION_KEY=${FERNET}
HMAC_KEY=$(openssl rand -hex 32)
MONGO_PASSWORD=$(openssl rand -hex 16)
MONGO_DB=neurobridge
COOKIE_SECURE=true
CORS_ORIGINS=https://${DOMAIN}
DOMAIN=${DOMAIN}
TRUSTED_PROXIES=2
MAX_PROCESS_BYTES=1073741824
ENCRYPTION_KEY_PREVIOUS=
EOF
echo ".env created (permissions: owner only). Domain: ${DOMAIN}"
echo "NOW copy it to a safe place off this server: the keys cannot be recovered if lost."
