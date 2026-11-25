#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu server (written for Oracle Cloud Always Free, also fine on other Ubuntu hosts).
# Usage:  bash deploy/setup_server.sh
# Installs Docker, opens ports 80 and 443 in the server's own firewall and keeps the rules after a reboot.
# Not run by the project's tests: check the output and read the script before you run it.
set -euo pipefail

echo "== 1/3 Docker =="
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"

echo "== 2/3 Firewall on the server itself =="
# Oracle's Ubuntu images ship iptables rules that reject everything except SSH, so opening ports in the Oracle web console is not enough.
# Insert our ACCEPT rules just before the first REJECT rule (or at the top if there is none).
N=$(sudo iptables -L INPUT --line-numbers -n | awk '/REJECT/ {print $1; exit}')
N=${N:-1}
for PORT in 80 443; do
  sudo iptables -C INPUT -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null || sudo iptables -I INPUT "$N" -p tcp --dport "$PORT" -j ACCEPT
done
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent >/dev/null
sudo netfilter-persistent save

echo "== 3/3 Automatic security updates =="
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y unattended-upgrades >/dev/null

echo
echo "Done. Log out and log in again (so the docker group applies), then continue with docs/DEPLOY_FREE_ORACLE.md step C4."
