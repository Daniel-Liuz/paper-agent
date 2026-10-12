#!/bin/bash
# 在服务器上执行的一次性初始化（由 deploy.sh 触发）。
# 适配 2GiB 内存：不装 torch/sentence-transformers，向量化走 SiliconFlow API。
set -e
cd /opt/paper-agent

echo "== 系统 + 轻量 venv =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv rsync >/dev/null
python3 -m venv .venv
.venv/bin/pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple \
  fastapi uvicorn jinja2 markdown faiss-cpu numpy requests

echo "== .env 服务器侧配置 =="
touch .env
grep -q '^EMBED_PROVIDER=' .env || echo 'EMBED_PROVIDER=api' >> .env
grep -q '^EMBED_API_KEY=' .env || echo 'EMBED_API_KEY=待填SiliconFlow的key' >> .env

echo "== systemd 常驻服务 =="
cat > /etc/systemd/system/paper-agent.service <<'EOF'
[Unit]
Description=paper-agent web (papers workflow)
After=network.target

[Service]
WorkingDirectory=/opt/paper-agent
Environment=HF_HUB_OFFLINE=1
ExecStart=/opt/paper-agent/.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8100
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now paper-agent
sleep 4
# 内容级自检：确认 8100 上是本应用（避免误撞其他服务的 200 假阳性）
curl -s http://localhost:8100/ | grep -q paper-agent && echo "本机自检: paper-agent 内容确认" || echo "警告: 8100 自检未通过，查 journalctl -u paper-agent"

echo "== 每日 07:00 定时流水线（light：抓取→入库→日报）=="
( crontab -l 2>/dev/null | grep -v 'paper-agent/run_pipeline' ; \
  echo '0 7 * * * cd /opt/paper-agent && bash run_pipeline.sh light >> outputs/cron.log 2>&1' \
) | crontab -
crontab -l | tail -2
echo "== SETUP-OK =="
