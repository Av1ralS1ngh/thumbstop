#!/bin/bash
# Boot script for a small Amazon Linux 2023 EC2 server that runs the Thumbstop app from the public repo.
# The server has no AWS credentials or instance role on purpose: the default mode replays the recorded demo
# answers, and live model modes need the visitor's own key, so nobody can spend the owner's account.
# CloudFront sits in front for HTTPS; the security group only lets CloudFront reach port 8501.
set -euxo pipefail
dnf install -y git
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile swap swap defaults 0 0' >> /etc/fstab
fi
id app || useradd -m app
sudo -u app bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'
sudo -u app bash -lc 'cd ~ && rm -rf thumbstop && git clone --depth 1 https://github.com/Av1ralS1ngh/thumbstop.git \
  && cd thumbstop && ~/.local/bin/uv venv --python 3.12 .venv \
  && ~/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt'
# Optional: recorded answers for the assignment's input pack, which must never be in the public repo. The deployer
# puts a short-lived private S3 link in RECORDED_ANSWERS_URL at the top of the user data (never committed). The app
# only replays them for someone who uploads those exact scripts, and runs fine without them.
set +x
if [ -n "${RECORDED_ANSWERS_URL:-}" ]; then
  install -d -o app -g app -m 700 /home/app/private
  if curl -fsSL "$RECORDED_ANSWERS_URL" -o /home/app/private/recorded_answers.json.gz; then
    chown app:app /home/app/private/recorded_answers.json.gz && chmod 600 /home/app/private/recorded_answers.json.gz
  else
    echo "could not download the recorded answers; the app runs without them"
  fi
fi
set -x
cat > /etc/systemd/system/thumbstop.service <<'EOF'
[Unit]
Description=Thumbstop promo finder (Streamlit)
After=network-online.target
Wants=network-online.target

[Service]
User=app
WorkingDirectory=/home/app/thumbstop
Environment=AWS_EC2_METADATA_DISABLED=true
Environment=THUMBSTOP_RECORDED_ANSWERS=/home/app/private/recorded_answers.json.gz
ExecStart=/home/app/thumbstop/.venv/bin/streamlit run app.py --server.headless true --server.address 0.0.0.0 \
  --server.port 8501 --server.enableCORS false --server.enableXsrfProtection false \
  --browser.gatherUsageStats false --client.toolbarMode minimal
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now thumbstop
