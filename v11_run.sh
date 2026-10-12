#!/bin/bash
# v1.1 数据升级跑：更大语料 + 更多安全实验载体 + 成本计量
cd /home/daniel/paper-agent || exit 1
P=/home/daniel/miniconda3/envs/llm/bin/python
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
echo "=== fetch（150篇/10天）==="
"$P" fetch_papers.py --max 150 --days 10 || exit 1
echo "=== ingest ==="
"$P" ingest.py || exit 1
echo "=== digest（top 15 + 成本计量）==="
"$P" digest.py --top 15 || exit 1
echo "=== eval ==="
"$P" eval.py || exit 1
echo "=== security（载体 8 篇，更强统计）==="
"$P" security.py --papers 8 || exit 1
echo "=== V11-DONE ==="
