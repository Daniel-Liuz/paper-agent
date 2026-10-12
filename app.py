"""app.py — paper-agent 展示站点（演示层胶水，FastAPI）。

跑法（WSL 项目目录下）：
    ~/miniconda3/envs/llm/bin/uvicorn app:app --host 0.0.0.0 --port 8000
浏览器（Windows 直接开，mirrored 网络自动透出端口）：
    http://localhost:8000
"""
import json
import pickle
from datetime import datetime
from pathlib import Path

import faiss
import markdown
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import DATA_DIR, OUT_DIR, EMBED_MODEL, FAISS_INDEX, META_STORE
from embedder import get_embedder
import reading

app = FastAPI(title="paper-agent")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")  # mermaid.js 等本地静态资源


def load_json(name: str):
    path = OUT_DIR / name
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def sec_context() -> tuple[dict | None, list[str], list[str]]:
    """安全表格的行列标签在服务端预计算，模板只管渲染。"""
    data = load_json("security_latest.json")
    if not data:
        return None, [], []
    attacks, defenses = [], []
    for c in data.get("cells", []):
        if c["attack"] not in attacks:
            attacks.append(c["attack"])
        if c["defense"] not in defenses:
            defenses.append(c["defense"])
    return data, attacks, defenses

# 惰性加载的全局单例：编码器和索引都吃资源，绝不能每个请求加载一次
_embedder = None
_index = None
_metas: list[dict] = []


def get_embedder_cached():
    global _embedder
    if _embedder is None:
        _embedder = get_embedder()
    return _embedder


def get_index():
    """索引+元数据一起加载；任一缺失视为"检索不可用"。"""
    global _index, _metas
    if _index is None and FAISS_INDEX.exists() and META_STORE.exists():
        _index = faiss.read_index(str(FAISS_INDEX))
        _metas = pickle.loads(META_STORE.read_bytes())
    return _index


def latest_papers(n: int = 30) -> list[dict]:
    """按抓取文件从新到旧读，凑够 n 篇为止。"""
    rows: list[dict] = []
    for path in sorted(DATA_DIR.glob("raw_*.jsonl"), reverse=True):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                if len(rows) >= n:
                    return rows
    return rows


def latest_digest() -> dict | None:
    """日报数据：优先结构化 json（支持标签筛选），无则退回 markdown 渲染（旧数据）。"""
    jfiles = sorted(OUT_DIR.glob("daily_*.json"), reverse=True)
    if jfiles:
        items = json.loads(jfiles[0].read_text(encoding="utf-8"))
        counts: dict[str, int] = {}
        for it in items:
            for t in it.get("tags", []):
                counts[t] = counts.get(t, 0) + 1
        return {"date": jfiles[0].stem.removeprefix("daily_"), "entries": items,
                "tag_counts": sorted(counts.items(), key=lambda kv: -kv[1])}
    mfiles = sorted(OUT_DIR.glob("daily_*.md"), reverse=True)
    if not mfiles:
        return None
    text = mfiles[0].read_text(encoding="utf-8")
    return {"date": mfiles[0].stem.removeprefix("daily_"), "items": [],
            "tag_counts": [],
            "html": markdown.markdown(text, extensions=["tables"])}


def fetched_today() -> int:
    """今日抓取篇数（hero 统计用）。"""
    path = DATA_DIR / f"raw_{datetime.now():%Y%m%d}.jsonl"
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "papers": latest_papers(30),
        "digest": latest_digest(),
        "indexed": get_index().ntotal if get_index() is not None else 0,
        "fetched": fetched_today(),
        "eval_data": load_json("eval_latest.json"),
        "sec_data": sec_context()[0],
        "sec_attacks": sec_context()[1],
        "sec_defenses": sec_context()[2],
        "query": None, "results": None,
    })


@app.get("/search")
async def search(request: Request, q: str = "", k: int = 10):
    results: list[dict] = []
    idx = get_index()
    vec = None
    if q and idx is not None and idx.ntotal > 0:
        try:
            vec = get_embedder_cached().encode([q])
        except Exception as err:  # 未配置向量化（如缺 EMBED_API_KEY）时降级为空结果
            print(f"[warn] 向量化不可用，检索暂时停用：{err}")
    if vec is not None:
        scores, ids = idx.search(vec, min(k, idx.ntotal))
        for score, i in zip(scores[0], ids[0]):
            if i < 0:
                continue
            row = dict(_metas[i])
            row["score"] = f"{float(score):.3f}"  # 余弦相似度，越大越相关
            results.append(row)
    return templates.TemplateResponse(request, "index.html", {
        "papers": latest_papers(30),
        "digest": latest_digest(),
        "indexed": idx.ntotal if idx is not None else 0,
        "fetched": fetched_today(),
        "eval_data": load_json("eval_latest.json"),
        "sec_data": sec_context()[0],
        "sec_attacks": sec_context()[1],
        "sec_defenses": sec_context()[2],
        "query": q, "results": results,
    })


@app.get("/paper/{paper_id}")
async def paper_detail(request: Request, paper_id: str):
    """深度阅读页 v2：全文精读（PDF 提取）+ 向量相关工作 + 引用网络反查 + mermaid 方法图。
    首次访问需下载 PDF 与调用 LLM（约 15-60 秒），之后命中缓存秒开。"""
    paper = reading.find_paper(paper_id)
    if paper is None:
        return templates.TemplateResponse(request, "detail.html",
                                          {"paper": None, "html": "", "related": [],
                                           "citations": [], "has_fulltext": False},
                                          status_code=404)

    related: list[dict] = []
    idx = get_index()
    if idx is not None and idx.ntotal > 0:
        try:
            text = f'{paper.get("title", "")}\n{paper.get("abstract", "")[:500]}'
            vec = get_embedder_cached().encode([text])
            scores, ids = idx.search(vec, 7)
            for score, i in zip(scores[0], ids[0]):
                if i < 0 or _metas[i]["paper_id"] == paper_id:
                    continue
                row = dict(_metas[i])
                row["score"] = f"{float(score):.3f}"
                related.append(row)
            related = related[:5]
        except Exception as err:  # 向量化不可用时，深度阅读退化为"仅原文解读"
            print(f"[warn] 相关工作检索停用：{err}")

    fulltext = reading.get_fulltext(paper)
    citations = reading.citing_papers(paper_id)
    html = reading.get_reading(paper, related, fulltext=fulltext, citations=citations)
    return templates.TemplateResponse(request, "detail.html",
                                      {"paper": paper, "html": html, "related": related,
                                       "citations": citations,
                                       "has_fulltext": bool(fulltext)})
