#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/s4081588-a11y/cloud-computing-EC2.git}"
BRANCH="${BRANCH:-main}"

AWS_REGION="${AWS_REGION:-us-east-1}"
USERS_TABLE_NAME="${USERS_TABLE_NAME:-music_shared_users}"
MUSIC_TABLE_NAME="${MUSIC_TABLE_NAME:-music_shared_songs}"
SUBSCRIPTIONS_TABLE_NAME="${SUBSCRIPTIONS_TABLE_NAME:-music_shared_subscriptions}"
S3_BUCKET_NAME="${S3_BUCKET_NAME:-music-shared-private-covers-351543164084-us-east-1}"

PORT=8000
EC2_USER=ubuntu

APP_ROOT="/opt/music-app"
BACKEND="$APP_ROOT/backend"
FRONTEND="$APP_ROOT/frontend"

export DEBIAN_FRONTEND=noninteractive

echo "Installing dependencies..."
apt-get update -y
apt-get install -y git python3 python3-venv python3-pip nginx curl

echo "Cloning repo..."
rm -rf "$APP_ROOT"
git clone --branch "$BRANCH" "$REPO_URL" "$APP_ROOT"

chown -R ubuntu:ubuntu "$APP_ROOT"

echo "Setting up backend..."
sudo -u ubuntu python3 -m venv "$BACKEND/.venv"
sudo -u ubuntu "$BACKEND/.venv/bin/pip" install --upgrade pip
sudo -u ubuntu "$BACKEND/.venv/bin/pip" install -r "$BACKEND/requirements.txt"

# -------------------------
# PUBLIC IP 
# -------------------------
echo "Getting public IP..."
PUBLIC_IP=$(curl -s http://checkip.amazonaws.com || echo "localhost")

echo "Configuring frontend..."
cat > "$FRONTEND/config.js" <<EOF
window.APP_CONFIG = {
  ARCHITECTURE: "EC2",
  API_BASE_URL: "http://$PUBLIC_IP",
  ALLOW_HTTP_API: true,
  APP_TITLE: "MusicCloud EC2"
};
EOF

echo "Creating env file..."
cat >/etc/music-ec2.env <<EOF
AWS_REGION=$AWS_REGION
USERS_TABLE_NAME=$USERS_TABLE_NAME
MUSIC_TABLE_NAME=$MUSIC_TABLE_NAME
SUBSCRIPTIONS_TABLE_NAME=$SUBSCRIPTIONS_TABLE_NAME
S3_BUCKET_NAME=$S3_BUCKET_NAME
PORT=$PORT
EOF

chmod 600 /etc/music-ec2.env

echo "Creating systemd service..."
cat >/etc/systemd/system/music-ec2-api.service <<EOF
[Unit]
Description=Music EC2 API
After=network.target

[Service]
User=ubuntu
WorkingDirectory=$BACKEND
EnvironmentFile=/etc/music-ec2.env
ExecStart=$BACKEND/.venv/bin/gunicorn --bind 127.0.0.1:$PORT --workers 2 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "Configuring nginx..."
cat >/etc/nginx/sites-available/music-ec2.conf <<EOF
server {
    listen 80;
    server_name _;

    root $FRONTEND;
    index login.html;

    location / {
        try_files \$uri \$uri/ /login.html;
    }

    location /api {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /health {
        proxy_pass http://127.0.0.1:$PORT/health;
    }
}
EOF

rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/music-ec2.conf /etc/nginx/sites-enabled/music-ec2.conf

systemctl daemon-reload
systemctl enable music-ec2-api
systemctl restart music-ec2-api
systemctl enable nginx
systemctl restart nginx

# -------------------------
#  DATA LOAD STEP
# -------------------------

echo "Loading AWS tables + S3 data..."

cd /opt/music-app/backend

sudo -u ubuntu bash -c "
source .venv/bin/activate

echo 'Creating DynamoDB tables...'
python create_aws_tables.py

python seed_aws_users.py

echo 'Loading songs + uploading images to S3...'
python load_aws_data.py \
  --file 2026a2_songs.json \
  --upload-images \
  --bucket \"$S3_BUCKET_NAME\"
"

echo "DONE  EC2 fully bootstrapped"
