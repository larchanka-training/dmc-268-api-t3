#!/usr/bin/env bash
# One-time, idempotent server preparation. Run as root by
# .github/workflows/bootstrap.yml over the provider's password login; every
# later connection uses the key-only `deploy` user it creates.
set -euo pipefail

: "${DEPLOY_PUBKEY:?DEPLOY_PUBKEY must be set}"

if [ "$(id -u)" -ne 0 ]; then
  echo "bootstrap must run as root (connected as $(id -un))" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq curl ca-certificates
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

if ! id deploy >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash deploy
fi
# "*" is never a valid hash: no password login, key login still works.
usermod --password '*' deploy
# Membership in the docker group is root-equivalent; deploy exists only for CD.
usermod -aG docker deploy

install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
touch /home/deploy/.ssh/authorized_keys
grep -qxF "$DEPLOY_PUBKEY" /home/deploy/.ssh/authorized_keys \
  || printf '%s\n' "$DEPLOY_PUBKEY" >> /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys

install -d -m 750 -o deploy -g deploy /opt/dmc268

# systemd-resolved answers LLMNR on public port 5355; other teams' VPSs had it open.
if systemctl is-active --quiet systemd-resolved; then
  install -d /etc/systemd/resolved.conf.d
  printf '[Resolve]\nLLMNR=no\nMulticastDNS=no\n' > /etc/systemd/resolved.conf.d/dmc268.conf
  systemctl restart systemd-resolved
fi

echo "bootstrap complete: docker $(docker version --format '{{.Server.Version}}'), user deploy ready"
