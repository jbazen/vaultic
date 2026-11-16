#!/bin/bash
# Vaultic — Oracle Cloud A1 (Ubuntu) one-time server setup
# Run as: bash server-setup.sh
set -e

echo "=== Vaultic Server Setup ==="

# ── System packages ───────────────────────────────────────────────────────────
sudo apt-get update -q
sudo apt-get install -y -q \
    python3.12 python3.12-venv python3-pip \
    nodejs npm \
    nginx \
    git \
    curl \
    ufw

# ── Clone repo ────────────────────────────────────────────────────────────────
cd ~
if [ ! -d "vaultic" ]; then
    git clone https://github.com/jbazen/vaultic.git vaultic
fi
cd vaultic

# ── Python virtualenv ─────────────────────────────────────────────────────────
# .venv in the repo: the deploy and deploy/start.sh use this path
python3.12 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

# ── Frontend build ────────────────────────────────────────────────────────────
cd ui
npm ci --silent
npm run build
cd ..

# ── .env file — fill in values after setup ───────────────────────────────────
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo ">>> IMPORTANT: edit ~/vaultic/.env with your real values before starting <<<"
fi

# ── data directory ────────────────────────────────────────────────────────────
mkdir -p data

# ── systemd service ───────────────────────────────────────────────────────────
# The unit lives in the repo (deploy/vaultic-api.service) and every deploy
# installs it again: it runs deploy/start.sh, which wraps uvicorn in Litestream.
sudo cp deploy/vaultic-api.service /etc/systemd/system/vaultic-api.service
sudo systemctl daemon-reload
sudo systemctl enable vaultic-api.service

# ── nginx config ──────────────────────────────────────────────────────────────
# The config lives in the repo (deploy/vaultic-nginx.conf) and every deploy
# installs it the same way (#167): a copy in sites-available, symlinked from
# sites-enabled. It needs the Cloudflare Origin Certificate in
# /etc/ssl/cloudflare/ (cert.pem, key.pem), so nginx is only reloaded once
# that is in place.
sudo cp deploy/vaultic-nginx.conf /etc/nginx/sites-available/vaultic
sudo ln -sfn /etc/nginx/sites-available/vaultic /etc/nginx/sites-enabled/vaultic
sudo rm -f /etc/nginx/sites-enabled/default
if sudo test -f /etc/ssl/cloudflare/cert.pem && sudo test -f /etc/ssl/cloudflare/key.pem; then
    sudo nginx -t && sudo systemctl reload nginx
else
    echo ">>> nginx not reloaded: put the Cloudflare Origin Certificate in /etc/ssl/cloudflare/ first <<<"
fi

# ── Firewall ──────────────────────────────────────────────────────────────────
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw --force enable

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit ~/vaultic/.env with your real values"
echo "  2. Put the Cloudflare Origin Certificate in /etc/ssl/cloudflare/ (cert.pem, key.pem),"
echo "     then: sudo nginx -t && sudo systemctl reload nginx"
echo "  3. sudo systemctl start vaultic-api.service"
echo "  4. sudo systemctl status vaultic-api.service   (check it's running)"
echo ""
echo "GitHub Actions secrets to add at github.com/jbazen/vaultic/settings/secrets:"
echo "  SERVER_HOST    = <your Oracle Cloud public IP>"
echo "  SSH_PRIVATE_KEY = <paste your Oracle Cloud SSH private key>"
