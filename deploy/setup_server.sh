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
  fastapi uvicorn jinja2 markdown faiss-cpu numpy requests pymupdf

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
systemctl enable paper-agent
systemctl restart paper-agent   # 关键：代码已更新时旧进程不会自动加载，必须重启
sleep 4
# 内容级自检：确认 8100 上是本应用（避免误撞其他服务的 200 假阳性）
curl -s http://localhost:8100/ | grep -q paper-agent && echo "本机自检: paper-agent 内容确认" || echo "警告: 8100 自检未通过，查 journalctl -u paper-agent"

echo "== 每日 07:00 定时流水线（light：抓取→入库→日报）=="
# 用 /etc/cron.d 文件而非 root crontab：文件方式幂等、可审计，且不依赖交互式 crontab 命令
# （此前 crontab - 管道安装静默失败过，日报因此停在部署当天）。
cat > /etc/cron.d/paper-agent <<'EOF'
0 7 * * * root cd /opt/paper-agent && bash run_pipeline.sh light >> outputs/cron.log 2>&1
EOF
chmod 644 /etc/cron.d/paper-agent
ls -la /etc/cron.d/paper-agent
echo "== SETUP-OK =="
