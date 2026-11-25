# Free online deployment (Oracle Cloud Always Free + DuckDNS + Caddy)

Goal: log in, upload and view EEG from anywhere, at no cost. Everything runs on one free ARM server with Docker.
**Demo data only:** the server is on the public internet. Use fictional patients and public EEG files, never real patient data.
**Untested:** these files and scripts have never run on a real server (Docker is not installed on the development machine). Expect to fix a
few small things on the first deploy; send the error output and it can be fixed.
Free-tier limits and the web consoles change: if a screen does not look like the description, follow the provider's current page.

## Part A: Oracle account and server (about 30-60 minutes)
1. Sign up at oracle.com/cloud/free. A card is needed for identity verification; stay on "Always Free" resources and you are not charged.
   The **home region cannot be changed later**: choose one close to you with free ARM capacity (for example Mumbai or Hyderabad).
2. Console, **Compute, Instances, Create instance**:
   - Image: Canonical Ubuntu 22.04 or 24.04 (the **aarch64 / ARM** build).
   - Shape: **VM.Standard.A1.Flex**. Use 2 OCPUs and 12 GB RAM (the free allowance is up to 4 OCPUs and 24 GB in total, so this leaves room).
   - Boot volume: 100 GB (the free allowance is 200 GB in total).
   - Networking: give it a **public IPv4 address**.
   - SSH keys: let Oracle generate a key pair and **download the private key** now (you cannot download it again). Keep it safe.
   - If creation fails with "Out of host capacity", retry later, try another availability domain, or use 1 OCPU / 6 GB.
3. Open the web ports: **Networking, Virtual cloud networks, your VCN, Security lists, Default**, then **Add ingress rules**: source `0.0.0.0/0`,
   TCP, destination ports `80` and `443` (two rules or one with `80,443`). Port 22 for SSH is already there.
4. Note the instance's **public IP address**.

## Part B: free web address (5 minutes)
1. Go to duckdns.org and sign in (GitHub or Google).
2. Create a name, for example `neurotrace-demo` (you get `neurotrace-demo.duckdns.org`). Put the server's public IP in the "current ip" box and update.

## Part C: set up the server (about 30 minutes)
1. Connect (PowerShell on your laptop; fix the key path and IP):
   ```powershell
   ssh -i C:\path\to\your-private-key.key ubuntu@PUBLIC_IP
   ```
2. On the server, download your code (use your repository's address):
   ```bash
   sudo apt-get update && sudo apt-get install -y git
   git clone https://github.com/YOUR_USER/YOUR_REPO.git neurotrace && cd neurotrace
   ```
3. Install Docker and open the server firewall (read the script first if you like: `cat deploy/setup_server.sh`):
   ```bash
   bash deploy/setup_server.sh
   ```
   Then log out (`exit`) and connect again so the docker group applies.
4. Create the settings file with generated secrets:
   ```bash
   cd neurotrace
   bash deploy/make_env.sh neurotrace-demo.duckdns.org
   cat .env
   ```
   **Copy `.env` to a safe place off the server** (a password manager or your laptop). If `ENCRYPTION_KEY` is lost, stored identifiers and original EEG files cannot be read.
5. Build and start everything (the first build takes several minutes on the free server):
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d --build
   docker compose -f docker-compose.yml -f docker-compose.tls.yml ps
   ```
   All of mongo, api, frontend and proxy should be "running" (api "healthy" after a minute).
6. Create the admin account (use a real-looking e-mail domain, and a strong password: 12+ characters, upper and lower case, a digit):
   ```bash
   bash deploy/create_admin.sh you@gmail.com "Your Name"
   ```
7. Open `https://neurotrace-demo.duckdns.org` on your laptop and phone, and log in.
   Caddy gets the HTTPS certificate on the first visit (needs ports 80 and 443 and the DuckDNS name pointing at the server).

## Part D: test it from outside
Log in from your phone on mobile data (not your home Wi-Fi). Create a fictional patient, upload a public EDF file (for example an EEGMMIDB `S001R04.edf`),
view it, run processing, approve the quality check, open **AI analysis**, run the model, and export a report.
Also check `https://YOUR_NAME.duckdns.org/api/v1/health/ready`: it must say `"status":"ready"`.

## Part E: keep it healthy
- **Backups** (every night at 02:30; run `crontab -e` and add the line, fixing the folder name):
  ```
  30 2 * * * cd /home/ubuntu/neurotrace && bash deploy/backup.sh >> backups/backup.log 2>&1
  ```
  Copy a backup to your laptop now and then (`scp -i KEY -r ubuntu@IP:neurotrace/backups/nb-backup-... .`). Backups never contain the keys.
- **Uptime:** make a free UptimeRobot account and add an HTTPS monitor for `/api/v1/health/ready`. Pings also keep the server from looking idle
  (Oracle can reclaim Always Free servers that stay almost completely idle for a long time).
- **Update the app** after you push changes: `bash deploy/update.sh`.
- **See logs:** `docker compose -f docker-compose.yml -f docker-compose.tls.yml logs -f api` (and `proxy`, `mongo`).

## Troubleshooting
| Symptom | Likely cause |
|---|---|
| Browser cannot connect | Oracle ingress rules for 80/443 missing, or the server firewall step was skipped (re-run `deploy/setup_server.sh`) |
| Certificate error | DuckDNS name does not point at the server IP yet, or ports 80/443 are blocked; see `logs proxy` |
| `api` keeps restarting | Check `logs api`: production refuses weak or missing secrets; a wrong `.env` shows up here |
| AI analysis fails | `logs api`; the model files must be in `ml/models` (they come with the git clone) |
| Out of memory while processing | Lower `MAX_PROCESS_BYTES` in `.env` and restart; or give the server more RAM |
| Login says "too many requests" | Per-address rate limit (20 per minute); wait a minute. If everyone shares one address, `TRUSTED_PROXIES` is wrong (it must be 2) |

## Safety notes
- Fictional patients and public EEG only. This is research software, not a medical device, and it has had no security review.
- No second factor yet: use a long, unique admin password. Rate limiting, lockout and HTTPS are on.
- Keep the SSH private key and `.env` private. Do not commit them.
- Delete the instance and the DuckDNS name when you no longer need them.
