"""reading.py — 深度阅读：单篇论文的 agentic RAG 解读。

流程（这就是简历里"深度阅读（规划-检索-综合）"的实现）：
    目标论文（标题+摘要） → 向量化 → FAISS 检索相关论文作为上下文
    → LLM 生成结构化解读（定位/方法/贡献/局限/与兴趣方向的关系/最接近的相关工作）
    → 缓存到 outputs/readings/{paper_id}.md（幂等：同一篇不重复花钱）。
"""
import json
import time
import urllib.request
from pathlib import Path

import markdown

from config import (DATA_DIR, OUT_DIR, LLM_BASE_URL, LLM_MODEL, LLM_API_KEY,
                    INTEREST_KEYWORDS)

READINGS_DIR = OUT_DIR / "readings"

READING_SYSTEM = (
    "你是资深研究员的论文阅读助手。基于给定的目标论文摘要和一组相关论文标题，"
    "生成结构化中文解读，使用 markdown 小节：\n"
    "### 一句话定位\n### 核心方法\n### 主要贡献\n### 局限与疑问\n"
    "### 与我的研究方向的关联（方向：大模型 Agent 评测与安全）\n"
    "### 最相关的工作\n（从相关论文里挑 1-2 篇并说明关系）\n"
    "要求：只依据给定信息，不编造；没有依据的写'摘要未提及'。"
)


def find_paper(paper_id: str) -> dict | None:
    for path in sorted(DATA_DIR.glob("raw_*.jsonl"), reverse=True):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            p = json.loads(line)
            if (p.get("paper_id") or p.get("arxiv_id")) == paper_id:
                return p
    return None


def get_reading(paper: dict, related: list[dict]) -> str:
    """返回解读的 HTML。命中缓存直接返回，否则调 LLM 生成并缓存。"""
    READINGS_DIR.mkdir(parents=True, exist_ok=True)
    cache = READINGS_DIR / f'{paper["paper_id"]}.html'
    if cache.exists():
        return cache.read_text(encoding="utf-8")

    related_block = "\n".join(
        f"- {r['title']}（{r.get('published', '')}，相似度 {r.get('score', '')}）"
        for r in related)
    user_prompt = (
        f"目标论文：\n标题：{paper['title']}\n摘要：{paper['abstract'][:1800]}\n\n"
        f"向量检索到的相关论文：\n{related_block or '（无）'}"
    )
    body = json.dumps({
        "model": LLM_MODEL,
        "messages": [{"role": "system", "content": READING_SYSTEM},
                     {"role": "user", "content": user_prompt}],
        "temperature": 0.3, "max_tokens": 900,
    }).encode("utf-8")
    req = urllib.request.Request(
        LLM_BASE_URL.rstrip("/") + "/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LLM_API_KEY}"})
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    text = data["choices"][0]["message"]["content"]
    html = markdown.markdown(text, extensions=["tables"])
    cache.write_text(html, encoding="utf-8")
    print(f'深度解读已生成并缓存：{paper["title"][:50]} '
          f'（{time.perf_counter() - started:.1f}s）')
    return html
