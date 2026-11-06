#!/bin/bash
# 密钥轮换：上传新公钥（无损追加）→ 验证新 key 登录
set -e
cd /home/daniel/paper-agent
SSHO="-o UserKnownHostsFile=deploy/known_hosts -o ConnectTimeout=15"
echo "== 步1：scp 新公钥上服务器 =="
scp -i deploy/ecs_key $SSHO deploy/ecs_key2.pub root@121.43.155.169:/tmp/ecs_key2.pub
echo "== 步骤2：追加到 authorized_keys（不动旧 key，无损）=="
ssh -i deploy/ecs_key $SSHO root@121.43.155.169 \
  'mkdir -p ~/.ssh && cat /tmp/ecs_key2.pub >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && echo KEY2-ADDED && wc -l ~/.ssh/authorized_keys'
echo "== 步骤3：用新 key 验证登录 =="
ssh -i deploy/ecs_key2 $SSHO root@121.43.155.169 \
  'echo 新KEY登录OK && systemctl is-active paper-agent'
echo "== 轮换前置完成（旧 key 尚未撤销，待验证后处理）=="
