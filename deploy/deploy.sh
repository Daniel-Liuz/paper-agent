#!/bin/bash
# 从 WSL 把项目部署到阿里云 ECS。用法：
#   bash deploy/deploy.sh root@121.43.155.169
# 密钥与 known_hosts 都放本目录（绕开 ~/.ssh 属主问题）。
set -e
SRV="${1:-root@121.43.155.169}"
KEY=/home/daniel/paper-agent/deploy/ecs_key
KH=/home/daniel/paper-agent/deploy/known_hosts
SSHOPTS="-i $KEY -o UserKnownHostsFile=$KH -o StrictHostKeyChecking=accept-new"
cd /home/daniel/paper-agent

echo "== 同步代码+数据+产物（不含模型/venv/.env，总量 <10MB）=="
# 绝不同步 .env：本地 .env 是开发配置（EMBED_PROVIDER=local），rsync 覆盖服务器 .env
# 会抹掉真实 key（setup 再补写中文占位符 → Authorization 头 latin-1 编码错误 → 检索全挂）。
# 服务器 .env 由 setup_server.sh 首次部署时生成、之后独立维护。
rsync -az -e "ssh $SSHOPTS" --delete \
  --exclude '.git' --exclude '__pycache__' --exclude '.venv' --exclude '.env' \
  ./ "$SRV":/opt/paper-agent/

echo "== 在服务器上执行初始化 =="
ssh $SSHOPTS "$SRV" 'bash /opt/paper-agent/deploy/setup_server.sh'

echo "== 验证 =="
ssh $SSHOPTS "$SRV" 'systemctl is-active paper-agent'
curl -s -o /dev/null -w "公网访问: http=%{http_code}\n" --max-time 20 http://121.43.155.169:8100/
