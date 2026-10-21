"""mcp_server.py — paper-agent 的 MCP server（Model Context Protocol）。

把项目能力封装成标准 MCP 工具，任何 MCP 客户端（Claude Desktop、飞书机器人、
其他聊天客户端）都能直接调用——这是从"网站"到"可被 agent 编排的服务"的一步。

工具清单：
  - search_papers(query, k)     语义检索论文（BGE-M3 + FAISS）
  - get_latest_digest()         最新一期日报（标题/星级/摘要/标签）
  - get_paper_reading(paper_id) 深度解读（含事实锚点核验，首次调用需 15-60s 生成）

跑法（stdio 模式，MCP 客户端配置用）：
    python mcp_server.py

快速自测（不接客户端，直接列工具）：
    python mcp_server.py --list-tools
"""
import argparse
import json
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from config import FAISS_INDEX, META_STORE, OUT_DIR  # noqa: E402


def _load_index():
    import faiss
    return faiss.read_index(str(FAISS_INDEX)), pickle.loads(META_STORE.read_bytes())


def _tool_search(query: str, k: int = 5) -> str:
    from embedder import get_embedder
    index, metas = _load_index()
    vec = get_embedder().encode([query])
    scores, ids = index.search(vec, min(k, index.ntotal))
    rows = []
    for score, i in zip(scores[0], ids[0]):
        if i < 0:
            continue
        m = metas[i]
        rows.append({"paper_id": m["paper_id"], "title": m["title"],
                     "published": m.get("published", ""),
                     "url": m.get("abs_url", ""), "similarity": round(float(score), 3)})
    return json.dumps(rows, ensure_ascii=False, indent=1)


def _tool_digest() -> str:
    files = sorted(OUT_DIR.glob("daily_*.json"), reverse=True)
    if not files:
        return "暂无日报数据"
    items = json.loads(files[0].read_text(encoding="utf-8"))
    lines = [f"📄 paper-agent 日报 {files[0].stem.removeprefix('daily_')}（{len(items)} 篇）"]
    for it in items:
        stars = "★" * it.get("score", 0)
        tags = "/".join(it.get("tags", []))
        lines.append(f"{stars} [{tags}] {it['title']}\n   {it.get('summary', '')}")
    return "\n".join(lines)


def _tool_reading(paper_id: str) -> str:
    import reading
    paper = reading.find_paper(paper_id)
    if paper is None:
        return f"未找到论文 {paper_id}（可用 search_papers 先查 paper_id）"
    related = []  # MCP 场景精简：不做相关检索，解读本身含上下文
    fulltext = reading.get_fulltext(paper)
    text = reading.get_reading(paper, related, fulltext=fulltext, citations=[])
    import re
    plain = re.sub(r"<[^>]+>", " ", text)  # 缓存是 HTML，去标签回纯文本
    return plain[:6000]


def build_server():
    # mcp 1.x 叫 FastMCP，2.x 改名 MCPServer——两边都兼容
    try:
        from mcp.server.mcpserver import MCPServer as Server
    except ImportError:
        from mcp.server.fastmcp import FastMCP as Server
    mcp = Server("paper-agent")

    @mcp.tool()
    def search_papers(query: str, k: int = 5) -> str:
        """语义检索论文库（约 250 篇 LLM/Agent 方向）。query 用自然语言，返回标题+相似度+链接。"""
        return _tool_search(query, k)

    @mcp.tool()
    def get_latest_digest() -> str:
        """获取最新一期论文日报：星级排序的标题 + 两句话中文摘要 + 兴趣标签。"""
        return _tool_digest()

    @mcp.tool()
    def get_paper_reading(paper_id: str) -> str:
        """获取一篇论文的深度解读（三十秒速览/方法/贡献/局限，含经原文核验的事实锚点）。
        paper_id 来自 search_papers 结果。首次调用需生成，约 15-60 秒。"""
        return _tool_reading(paper_id)

    return mcp


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-tools", action="store_true", help="不启动 server，仅打印工具清单自测")
    args = parser.parse_args()
    mcp = build_server()
    if args.list_tools:
        import asyncio
        tools = asyncio.run(mcp.list_tools())
        for t in tools:
            print(f"- {t.name}: {t.description}")
    else:
        print("paper-agent MCP server 启动（stdio）——工具：search_papers / get_latest_digest / get_paper_reading",
              file=sys.stderr)
        mcp.run()
