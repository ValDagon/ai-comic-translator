#!/bin/bash
# GCP VM metadata startup script (paste into Console → VM → Automation → Startup script).
# Installs Docker; clone .env and `docker compose up` you run once over SSH.

set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y ca-certificates curl git

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

source /etc/os-release
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  ${VERSION_CODENAME} stable" >/etc/apt/sources.list.d/docker.list

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

systemctl enable docker
systemctl start docker

APP_USER="${SUDO_USER:-ubuntu}"
if id "$APP_USER" &>/dev/null; then
  usermod -aG docker "$APP_USER"
fi

mkdir -p /opt
chown "$APP_USER:$APP_USER" /opt 2>/dev/null || true

echo "Docker ready. SSH in as $APP_USER, clone the repo to /opt/ai-comic-translator, configure .env, then:"
echo "  cd /opt/ai-comic-translator && docker compose up -d --build"
