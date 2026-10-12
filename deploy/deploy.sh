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

echo "== 同步代码+数据+产物（不含模型/venv，总量 <10MB）=="
rsync -az -e "ssh $SSHOPTS" --delete \
  --exclude '.git' --exclude '__pycache__' --exclude '.venv' \
  ./ "$SRV":/opt/paper-agent/

echo "== 在服务器上执行初始化 =="
ssh $SSHOPTS "$SRV" 'bash /opt/paper-agent/deploy/setup_server.sh'

echo "== 验证 =="
ssh $SSHOPTS "$SRV" 'systemctl is-active paper-agent'
curl -s -o /dev/null -w "公网访问: http=%{http_code}\n" --max-time 20 http://121.43.155.169:8100/
