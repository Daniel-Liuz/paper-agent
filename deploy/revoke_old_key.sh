#!/bin/bash
# 撤销已泄漏的旧公钥（保留 ecs_key2 行 + 阿里云控制台兜底）
set -e
cd /home/daniel/paper-agent
SSHO="-o UserKnownHostsFile=deploy/known_hosts -o ConnectTimeout=15"
echo "== 撤销前 authorized_keys 指纹 =="
ssh -i deploy/ecs_key2 $SSHO root@121.43.155.169 \
  'awk "{print \$1, \$3}" ~/.ssh/authorized_keys'
echo "== 重写 authorized_keys：只保留新 key（ecs_key2）=="
scp -q -i deploy/ecs_key2 $SSHO deploy/ecs_key2.pub root@121.43.155.169:/tmp/k2.pub
ssh -i deploy/ecs_key2 $SSHO root@121.43.155.169 \
  'cp ~/.ssh/authorized_keys ~/.ssh/authorized_keys.bak.20261106 && echo "REVOKE-DONE($(date +%F)) 只保留新key" > ~/.ssh/authorized_keys && cat /tmp/k2.pub >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && wc -l ~/.ssh/authorized_keys'
echo "== 旧 key 应已失效（预期 Permission denied）=="
ssh -i deploy/ecs_key -o UserKnownHostsFile=deploy/known_hosts -o ConnectTimeout=15 -o BatchMode=yes root@121.43.155.169 'echo 旧key仍可用!!' 2>&1 | tail -1 || echo "旧 key 已失效 ✓"
echo "== 新 key 仍可用 =="
ssh -i deploy/ecs_key2 $SSHO root@121.43.155.169 'echo 新key正常 ✓ && systemctl is-active paper-agent'
