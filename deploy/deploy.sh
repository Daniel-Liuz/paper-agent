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

echo "== 同步代码+静态资源（数据与密钥绝不双向覆盖）=="
# 方向纪律：
#  - data/（raw/index/pkl）由服务器 cron 自治产出——本地不同步过去，否则会把服务器
#    新抓的数据用本地旧库覆盖掉（10/15 事故：日报被 rsync --delete 回滚到三天前）；
#  - .env 同理（服务器 key 被本地开发配置覆盖过一次）；
#  - outputs/ 大多也是服务器产物（解读/全文缓存），只显式推送本地生成的评测看板数据。
rsync -az -e "ssh $SSHOPTS" --delete \
  --exclude '.git' --exclude '__pycache__' --exclude '.venv' --exclude '.env' \
  --exclude 'data' --exclude 'outputs' \
  ./ "$SRV":/opt/paper-agent/

# 本地生成的评测/安全实验产物 → 服务器看板（增量推送，不删除服务器自有产物）
rsync -az -e "ssh $SSHOPTS" --ignore-missing-args \
  outputs/eval_latest.json outputs/security_latest.json outputs/cost_latest.json \
  "$SRV":/opt/paper-agent/outputs/

echo "== 在服务器上执行初始化 =="
ssh $SSHOPTS "$SRV" 'bash /opt/paper-agent/deploy/setup_server.sh'

echo "== 验证 =="
ssh $SSHOPTS "$SRV" 'systemctl is-active paper-agent'
curl -s -o /dev/null -w "公网访问: http=%{http_code}\n" --max-time 20 http://121.43.155.169:8100/
