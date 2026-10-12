#!/bin/bash
# 一键流水线。
#   bash run_pipeline.sh          # full：抓取→入库→日报→评测→安全
#   bash run_pipeline.sh light    # light：抓取→入库→日报（服务器每日 cron 用这个）
# python 自适应：服务器用 .venv，本地 WSL 用 conda llm。
cd /home/daniel/paper-agent 2>/dev/null || cd /opt/paper-agent || exit 1
if [ -n "$PAPER_PYTHON" ]; then
  P="$PAPER_PYTHON"
elif [ -x /opt/paper-agent/.venv/bin/python ]; then
  P=/opt/paper-agent/.venv/bin/python
elif [ -x /home/daniel/miniconda3/envs/llm/bin/python ]; then
  P=/home/daniel/miniconda3/envs/llm/bin/python
else
  P=python3
fi
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY

echo "=== fetch ==="
"$P" fetch_papers.py --max 80 --days 3 || exit 1
echo "=== ingest ==="
"$P" ingest.py || exit 1
echo "=== digest ==="
"$P" digest.py --top 12 || exit 1

if [ "$1" != "light" ]; then
  echo "=== eval ==="
  "$P" eval.py || exit 1
  echo "=== security ==="
  "$P" security.py || exit 1
fi
echo "=== PIPELINE-OK ==="
