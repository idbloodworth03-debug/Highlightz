#!/bin/bash
# Highlightz - Server Setup Script
# Run this once on a fresh Ubuntu 22.04 DigitalOcean Droplet as root
#
# Usage:
#   With GitHub:  GITHUB_REPO=https://github.com/YOUR_USERNAME/highlightz bash <(curl -s URL_TO_THIS_SCRIPT)
#   Already cloned: bash /opt/highlightz/deploy/setup.sh

set -e

echo ""
echo "========================================="
echo "  Highlightz Server Setup"
echo "========================================="
echo ""

APP_DIR="/opt/highlightz"

# ── Clone from GitHub if GITHUB_REPO is set ───────────────────────────────────
if [ -n "$GITHUB_REPO" ]; then
    echo "[0/7] Cloning from GitHub..."
    apt-get install -y -qq git
    if [ -d "$APP_DIR/.git" ]; then
        cd "$APP_DIR" && git pull
    else
        rm -rf "$APP_DIR"
        git clone "$GITHUB_REPO" "$APP_DIR"
    fi
    echo "      Done."
fi

# ── System packages ───────────────────────────────────────────────────────────
echo "[1/7] Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    ffmpeg \
    redis-server \
    nginx \
    certbot python3-certbot-nginx \
    git curl wget unzip \
    supervisor

echo "      Done."

# ── Service user ──────────────────────────────────────────────────────────────
echo "[2/7] Setting up app directory and service user..."
APP_DIR="/opt/highlightz"
# Dedicated unprivileged system user — the bot must NOT run as root.
if ! id highlightz >/dev/null 2>&1; then
    useradd --system --create-home --home-dir /home/highlightz --shell /usr/sbin/nologin highlightz
fi
mkdir -p "$APP_DIR/clips/profiles"
mkdir -p "$APP_DIR/clips/clips"
mkdir -p "$APP_DIR/clips/tmp"
mkdir -p "$APP_DIR/clips/segments"
echo "      Done."

# ── Python virtualenv ─────────────────────────────────────────────────────────
echo "[3/7] Creating Python virtual environment..."
cd "$APP_DIR"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
pip install streamlink -q
deactivate
echo "      Done."

# ── Redis ─────────────────────────────────────────────────────────────────────
echo "[4/7] Configuring Redis..."
systemctl enable redis-server
systemctl start redis-server
echo "      Done."

# ── Systemd service ───────────────────────────────────────────────────────────
echo "[5/7] Installing systemd service..."
cp "$APP_DIR/deploy/highlightz.service" /etc/systemd/system/highlightz.service
systemctl daemon-reload
systemctl enable highlightz
echo "      Done."

# ── Nginx ─────────────────────────────────────────────────────────────────────
echo "[6/7] Configuring Nginx..."
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/highlightz
ln -sf /etc/nginx/sites-available/highlightz /etc/nginx/sites-enabled/highlightz
rm -f /etc/nginx/sites-enabled/default
nginx -t -q && systemctl restart nginx
echo "      Done."

# ── .env check ────────────────────────────────────────────────────────────────
echo "[7/7] Checking .env..."
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/deploy/env.production" "$APP_DIR/.env"
    # Auto-generate a strong session secret so the app never ships with a default.
    GENERATED_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s|^DASHBOARD_SECRET_KEY=.*|DASHBOARD_SECRET_KEY=${GENERATED_KEY}|" "$APP_DIR/.env"
    echo ""
    echo "  !! .env file created from template (with a generated DASHBOARD_SECRET_KEY)."
    echo "  !! Edit /opt/highlightz/.env: set DASHBOARD_PASSWORD and your API keys before starting."
    echo ""
else
    echo "      .env already exists, skipping."
fi

# Secrets file must not be world-readable, and the service user owns the app.
chmod 600 "$APP_DIR/.env"
chown highlightz:highlightz "$APP_DIR/.env"
chown -R highlightz:highlightz "$APP_DIR/clips"

echo ""
echo "========================================="
echo "  Setup complete!"
echo ""
echo "  Next steps:"
echo "  1. Edit your config:  nano /opt/highlightz/.env"
echo "  2. Start the bot:     systemctl start highlightz"
echo "  3. Check it running:  systemctl status highlightz"
echo "  4. View logs:         journalctl -u highlightz -f"
echo ""
echo "  Then set up your domain SSL:"
echo "  certbot --nginx -d yourdomain.com"
echo "========================================="
