"""tools.py — 工具注册表：显式 schema、统一执行入口、统一异常。

四个工具（全部只读，刻意不提供任何写/外发/执行类工具）：
    search_papers(query, k)   语义检索论文库（复用 FAISS + embedder 双模式）
    read_paper(paper_id)      读论文（摘要或全文片段；复用 reading.get_fulltext 缓存）
    verify_citation(...)      引文核验（复用 reading._norm_for_match 的归一化比对）
    finish(report)            提交最终报告（终止工具）

安全属性：工具本身无副作用（读本地缓存/索引），失败抛 ToolError 由 Runtime 捕获回填。
"""
from __future__ import annotations

import os
from typing import Any, Callable

from config import FAISS_INDEX, META_STORE

# 离线开关：AGENT_FAKE_TOOLS=1 时不加载索引/embedder/网络，返回固定语料。
# 用途：pytest、Mock 评测与无依赖环境的端到端演示（真实功能路径不受影响）。
FAKE = os.environ.get("AGENT_FAKE_TOOLS") == "1"

FAKE_CORPUS = [
    {"paper_id": "W7164329679",
     "title": "A Lightweight Multi-Agent Framework for Automated Concrete Barrier Design",
     "published": "2026-10-10", "similarity": 0.72,
     "abstract": "A generation-validation-modification closed-loop framework based on "
                 "AutoGen multi-agent orchestration. The agentic framework achieves a "
                 "target-interval compliance rate of 98.3% versus 11.7% for the best "
                 "standalone LLM, using external deterministic mechanics tools."},
    {"paper_id": "W4416551460",
     "title": "ProbGuard: Proactive Runtime Monitoring for LLM Agent Safety",
     "published": "2026-09-01", "similarity": 0.69,
     "abstract": "Probabilistic runtime guardrails for LLM agents."},
    {"paper_id": "W7221100712",
     "title": "Agentic AI for Computational Fluid Dynamics: A Review",
     "published": "2026-10-12", "similarity": 0.66,
     "abstract": "A review of agentic AI in CFD engineering."},
    {"paper_id": "W7150731460",
     "title": "How Your Credentials Are Leaked by LLM Agent Skills: An Empirical Study",
     "published": "2026-09-20", "similarity": 0.70,
     "abstract": "An empirical study of credential leakage through LLM agent skills. "
                 "We show that agent skills can leak user credentials through "
                 "indirect prompt injection channels."},
]


class ToolError(Exception):
    """工具执行失败（网络/数据缺失/参数语义错误）。Runtime 捕获后作为观测反馈给 LLM。"""


# ---------- OpenAI function-calling schema（LLM 可见） ----------
TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_papers",
            "description": "在论文库（约250篇 LLM/Agent 方向）中做语义检索。"
                           "返回论文 id、标题、相似度。开始任何研究任务都应先检索。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索主题，自然语言"},
                    "k": {"type": "integer", "description": "返回条数，默认 5，最大 10",
                          "minimum": 1, "maximum": 10, "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_paper",
            "description": "读取一篇论文的内容（优先全文前缀，否则摘要）。"
                           "输入 search_papers 返回的 paper_id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string", "description": "形如 W1234567890"},
                },
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_citation",
            "description": "核验一段引文是否逐字存在于论文原文（_whitespace/断行容错）。"
                           "把最终报告中引用的原句交给本工具核验后再输出。",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string"},
                    "quote": {"type": "string", "description": "被引用的原文句子（英文原句）"},
                },
                "required": ["paper_id", "quote"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "提交最终研究报告并结束任务。报告须包含：每篇论文的结论、"
                           "跨论文比较、以及可直接核验的原文引句（含 paper_id）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "report": {"type": "string", "description": "最终报告全文（markdown）"},
                },
                "required": ["report"],
            },
        },
    },
]

# 每工具的参数校验规则（policy 用；schema 之外的业务约束）
ARG_LIMITS: dict[str, dict[str, int]] = {
    "search_papers": {"query": 300, "k": 10},
    "read_paper": {"paper_id": 64},
    "verify_citation": {"paper_id": 64, "quote": 1200},
    "finish": {"report": 8000},
}


# ---------- 索引懒加载（进程内单例，与 app.py 相同策略） ----------
_index = None
_metas: list[dict] = []


def _get_index():
    global _index, _metas
    if _index is None:
        import faiss
        import pickle
        if not FAISS_INDEX.exists() or not META_STORE.exists():
            raise ToolError("论文索引未构建（先跑 ingest.py）")
        _index = faiss.read_index(str(FAISS_INDEX))
        _metas = pickle.loads(META_STORE.read_bytes())
    return _index, _metas


# ---------- 工具实现 ----------
def _tool_search_papers(query: str, k: int = 5) -> dict:
    if FAKE:
        k = max(1, min(int(k), 10))
        hits = [{"paper_id": m["paper_id"], "title": m["title"],
                 "published": m["published"], "similarity": m["similarity"]}
                for m in FAKE_CORPUS[:k]]
        return {"results": hits}
    from embedder import get_embedder
    index, metas = _get_index()
    k = max(1, min(int(k), 10))
    vec = get_embedder().encode([query])
    scores, ids = index.search(vec, min(k, index.ntotal))
    hits = []
    for score, i in zip(scores[0], ids[0]):
        if i < 0:
            continue
        m = metas[i]
        hits.append({"paper_id": m["paper_id"], "title": m["title"],
                     "published": m.get("published", ""),
                     "similarity": round(float(score), 3)})
    if not hits:
        raise ToolError(f"检索无结果：{query[:50]}")
    return {"results": hits}


def _tool_read_paper(paper_id: str) -> dict:
    if FAKE:
        for m in FAKE_CORPUS:
            if m["paper_id"] == paper_id:
                return {"paper_id": paper_id, "title": m["title"], "mode": "abstract",
                        "text": m["abstract"]}
        raise ToolError(f"论文不存在：{paper_id}")
    import reading
    paper = reading.find_paper(paper_id)
    if paper is None:
        raise ToolError(f"论文不存在：{paper_id}")
    fulltext = None
    try:
        fulltext = reading.get_fulltext(paper)
    except Exception as err:            # PDF 下载等失败不致命，退摘要
        fulltext = None
    text = (fulltext or paper.get("abstract") or "")[:4000]
    if not text:
        raise ToolError(f"论文无可用文本：{paper_id}")
    return {"paper_id": paper_id, "title": paper["title"],
            "mode": "fulltext" if fulltext else "abstract",
            "text": text}


def _tool_verify_citation(paper_id: str, quote: str) -> dict:
    if FAKE:
        source_text = " ".join(m["abstract"] for m in FAKE_CORPUS
                               if m["paper_id"] == paper_id)
        if not source_text:
            raise ToolError(f"论文不存在：{paper_id}")
        found = quote.strip().lower()[:60] in source_text.lower()
        return {"paper_id": paper_id, "quote_head": quote[:80], "found": found}
    import reading
    paper = reading.find_paper(paper_id)
    if paper is None:
        raise ToolError(f"论文不存在：{paper_id}")
    fulltext = ""
    try:
        fulltext = reading.get_fulltext(paper) or ""
    except Exception:
        fulltext = ""
    source = reading._norm_for_match(fulltext + "\n" + (paper.get("abstract") or ""))
    found = reading._norm_for_match(quote) in source
    return {"paper_id": paper_id, "quote_head": quote[:80], "found": found}


def _tool_finish(report: str) -> dict:
    return {"done": True, "report": report, "report_chars": len(report)}


IMPLEMENTATIONS: dict[str, Callable[..., dict]] = {
    "search_papers": _tool_search_papers,
    "read_paper": _tool_read_paper,
    "verify_citation": _tool_verify_citation,
    "finish": _tool_finish,
}

TERMINAL_TOOLS = {"finish"}


def execute(name: str, arguments: dict[str, Any]) -> dict:
    """统一执行入口。参数已由 policy 校验过；失败抛 ToolError。"""
    impl = IMPLEMENTATIONS.get(name)
    if impl is None:
        raise ToolError(f"未注册的工具：{name}")
    try:
        return impl(**arguments)
    except ToolError:
        raise
    except TypeError as err:                       # 参数名/数量不匹配
        raise ToolError(f"参数不合法：{err}") from err
    except Exception as err:                       # 网络/数据等一切执行期错误
        raise ToolError(f"{type(err).__name__}: {err}") from err
