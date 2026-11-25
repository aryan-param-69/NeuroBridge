# Operations guide (security and ops basics)

What each safeguard does, how to use it, and what it does **not** do. Commands run from `backend/` unless noted.

## 1. Health checks
- `GET /api/v1/health` : liveness (the process is up).
- `GET /api/v1/health/ready` : readiness. Returns 200 when the database answers, the storage folder is writable and the default model file exists;
  otherwise 503 with `{"database"|"storage"|"model": "ok"|"failed"}` only (no internal details). The Docker healthcheck uses this.
- Every response carries `X-Request-ID` (echoes a valid client value, otherwise a new one): quote it when reporting a problem.

## 2. Rate limiting
- Per client address, sliding one-minute window: login and token refresh 20 per minute (`RATE_LIMIT_AUTH_PER_MINUTE`), all other API calls
  1,200 per minute (`RATE_LIMIT_API_PER_MINUTE`). Over the limit: HTTP 429 with `Retry-After`. Health checks are never limited.
- This is in addition to the per-account lockout (5 wrong passwords, 15 minutes).
- Behind reverse proxies set `TRUSTED_PROXIES` to the number of proxies (1 = nginx in docker-compose, 2 = Caddy + nginx). The client address is
  then read from the right-hand side of `X-Forwarded-For`, so a client cannot choose its own address. A wrong value either lumps all users into
  one address (too high a limit hit) or lets a client dodge the limit.
- Limits: counters are per process and reset on restart. With several API workers each counts separately. Use a shared store (Redis) if scaled out.

## 3. Backup and restore
```powershell
uv run python -m scripts.backup --out ../backups
uv run python -m scripts.restore --backup ../backups/nb-backup-<timestamp> --db neurobridge_restored
```
- A backup folder holds each collection as compressed Extended JSON, `files.tar.gz` (the storage folder) and `MANIFEST.json` with a SHA-256 for every file.
- Restore verifies every checksum first, refuses a non-empty target database unless `--force`, checks document counts, rebuilds indexes, and
  extracts files only into the folder you name (`--files-to`).
- **Keys are never in a backup.** Store `SECRET_KEY`, `ENCRYPTION_KEY`, `HMAC_KEY` (and any `ENCRYPTION_KEY_PREVIOUS`) in a separate secret store;
  without them encrypted identifiers and original EEG files cannot be read after a restore.
- De-identified copies and processed signals are not encrypted by the application: keep backups on an encrypted disk or volume.
- Practice a restore into a scratch database before you need it. A backup that has never been restored is unproven.
- The audit-log hash chain is restored as stored; after a restore, run the chain verification (admin audit endpoint, `verify_chain`) to confirm it is intact.

## 4. Key rotation (ENCRYPTION_KEY)
1. Generate a key: `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
2. Set it as `ENCRYPTION_KEY`; move the old value into `ENCRYPTION_KEY_PREVIOUS`; restart. Nothing breaks: old data still decrypts.
3. Take a backup, then `uv run python -m scripts.rotate_keys` (dry run) and `uv run python -m scripts.rotate_keys --apply`.
4. Re-run the dry run until it reports 0 remaining, then remove the old key from `ENCRYPTION_KEY_PREVIOUS`.
Covers patient identifiers and original EEG files. Not covered: `HMAC_KEY` (search tokens must be recomputed) and `SECRET_KEY` (changing it logs everyone out).

## 5. HTTPS
`docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d --build` puts Caddy in front (automatic certificate for `DOMAIN`).
Set `ENVIRONMENT=production`, `COOKIE_SECURE=true`, `CORS_ORIGINS=https://<DOMAIN>`, `TRUSTED_PROXIES=2` and real secrets in `.env`;
in production the API refuses to start with weak or missing secrets, without `COOKIE_SECURE`, or with a database URL that has no credentials.
**Not tested here:** Docker was not available on the development machine, so the compose files and this TLS setup have never been run. Try them on a
staging host first.

## 6. Dependency checks (last run for this version)
- Backend: `pip-audit` on the locked runtime dependencies: no known vulnerabilities.
- Frontend: `npm audit --omit=dev`: 0 vulnerabilities.
Re-run before each release: `uv export --frozen --no-dev --no-hashes --no-emit-project > req.txt; uvx pip-audit -r req.txt --no-deps` and `npm audit --omit=dev`.

## 7. What is still not done (be honest in the viva)
- No second factor (removed for the prototype); no per-user session list; no Redis-backed shared rate limiting; no insert-only database role for the audit log.
- No formal security or clinical review, no penetration test, no regulatory assessment. This is research software, not a medical device.
- Docker/TLS untested. Backups are manual: schedule them (Windows Task Scheduler or cron) and monitor that they run.
