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
python3.12 -m venv ~/vaultic-venv
source ~/vaultic-venv/bin/activate
pip install -q -r requirements.txt
deactivate

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
sudo tee /etc/systemd/system/vaultic-api.service > /dev/null <<'EOF'
[Unit]
Description=Vaultic API (FastAPI/uvicorn)
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/vaultic
ExecStart=/home/ubuntu/vaultic-venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
EnvironmentFile=/home/ubuntu/vaultic/.env

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable vaultic-api.service

# ── nginx config ──────────────────────────────────────────────────────────────
# Serves the built React UI as static files.
# Proxies /api/* to uvicorn on localhost:8000.
# Replace YOUR_DOMAIN with your actual domain (or use the server IP temporarily).
sudo tee /etc/nginx/sites-available/vaultic > /dev/null <<'EOF'
server {
    listen 80;
    server_name YOUR_DOMAIN;

    root /home/ubuntu/vaultic/ui/dist;
    index index.html;

    # Serve frontend — fallback to index.html for client-side routing
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Proxy API requests to uvicorn
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/vaultic /etc/nginx/sites-enabled/vaultic
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx

# ── Firewall ──────────────────────────────────────────────────────────────────
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw --force enable

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit ~/vaultic/.env with your real values"
echo "  2. Edit /etc/nginx/sites-available/vaultic — replace YOUR_DOMAIN"
echo "  3. sudo systemctl start vaultic-api.service"
echo "  4. sudo systemctl status vaultic-api.service   (check it's running)"
echo "  5. Set up SSL with: sudo apt install certbot python3-certbot-nginx && sudo certbot --nginx"
echo ""
echo "GitHub Actions secrets to add at github.com/jbazen/vaultic/settings/secrets:"
echo "  SERVER_HOST    = <your Oracle Cloud public IP>"
echo "  SSH_PRIVATE_KEY = <paste your Oracle Cloud SSH private key>"
