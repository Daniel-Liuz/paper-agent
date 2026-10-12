"""backfill_urls.py — 修复论文链接质量：优先 arxiv.org（国内稳定可达）。

背景：OpenAlex 的 primary_location.landing_page_url 可能指向 zenodo / 出版社等
国内不可达的仓库（用户实测 zenodo 502）。本脚本按 paper_id 批量回查 OpenAlex 的
全部 locations，把 abs_url / pdf_url 替换为 arxiv 版本（没有则保留原值或 doi）。

同步更新两处：raw_*.jsonl（源头）与 papers_meta.pkl（FAISS 元数据）。
跑法：python backfill_urls.py    （幂等，可重复跑）
"""
import json
import pickle
import time
import urllib.request
from pathlib import Path

from config import DATA_DIR, META_STORE

UA = {"User-Agent": "paper-agent/0.2 (research-use)"}


def fetch_locations(ids: list[str]) -> dict[str, dict]:
    """一批 paper_id → {pid: {"locations": [...], "doi": str|None}}（游标分页）。"""
    out: dict[str, dict] = {}
    cursor = "*"
    while True:
        params = (f"https://api.openalex.org/works?filter=ids.openalex:{'|'.join(ids)}"
                  f"&select=id,locations,doi&per-page=50&cursor={cursor}"
                  "&mailto=paper-agent@example.com")
        req = urllib.request.Request(params, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        for w in data.get("results", []):
            out[w["id"].rsplit("/", 1)[-1]] = {"locations": w.get("locations") or [],
                                               "doi": w.get("doi")}
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor or len(out) >= len(ids):
            break
        time.sleep(0.4)
    return out


def pick_urls(info: dict) -> tuple[str | None, str | None]:
    """locations 里挑 arxiv 优先的 landing/pdf；退化顺序 arxiv > doi > 原值。"""
    abs_url = pdf_url = None
    for loc in info["locations"]:
        landing = (loc.get("landing_page_url") or "")
        pdf = (loc.get("pdf_url") or "")
        if "arxiv.org" in landing and not abs_url:
            abs_url = landing
        if "arxiv.org" in pdf and not pdf_url:
            pdf_url = pdf
    if not abs_url:
        for loc in info["locations"]:
            landing = (loc.get("landing_page_url") or "")
            if landing.startswith("http"):
                abs_url = landing
                break
    if not abs_url:
        abs_url = info.get("doi")
    return abs_url, pdf_url


def main() -> None:
    # 1. 收集全部条目（文件 → 行号 → 记录），W 开头 id 才能回查
    files = sorted(DATA_DIR.glob("raw_*.jsonl"))
    records: dict[str, list[tuple[Path, int, dict]]] = {}
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            p = json.loads(line)
            pid = p.get("paper_id") or p.get("arxiv_id")
            if pid and pid.startswith("W"):
                records.setdefault(pid, []).append((path, i, p))

    pids = list(records)
    print(f"待回查 {len(pids)} 篇")
    updates: dict[tuple[str, int], dict] = {}  # (文件, 行号) → 更新后的记录
    fixed = 0
    for chunk_start in range(0, len(pids), 50):
        chunk = pids[chunk_start:chunk_start + 50]
        infos = fetch_locations(chunk)
        for pid in chunk:
            info = infos.get(pid)
            if not info:
                continue
            abs_url, pdf_url = pick_urls(info)
            for path, i, p in records[pid]:
                changed = False
                if abs_url and p.get("abs_url") != abs_url:
                    p["abs_url"], changed = abs_url, True
                if pdf_url and (p.get("pdf_url") or "") != pdf_url:
                    p["pdf_url"], changed = pdf_url, True
                if changed:
                    updates[(str(path), i)] = p
                    fixed += 1
        time.sleep(0.4)

    # 2. 写回 jsonl（逐文件重写）
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        new_lines = []
        for i, line in enumerate(lines):
            hit = updates.get((str(path), i))
            if hit is not None:
                new_lines.append(json.dumps(hit, ensure_ascii=False))
            else:
                new_lines.append(line)
        path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    # 3. 同步元数据 pickle（索引向量不动，只改链接字段）
    if META_STORE.exists():
        metas = pickle.loads(META_STORE.read_bytes())
        by_pid = {rec[2].get("paper_id"): rec[2]
                  for recs in records.values() for rec in recs}
        for m in metas:
            fresh = by_pid.get(m.get("paper_id"))
            if fresh:
                if fresh.get("abs_url"):
                    m["abs_url"] = fresh["abs_url"]
                if fresh.get("pdf_url"):
                    m["pdf_url"] = fresh["pdf_url"]
        META_STORE.write_bytes(pickle.dumps(metas))
        print(f"metas 同步完成（{len(metas)} 条）")
    print(f"链接修复完成：{fixed} 条更新")


if __name__ == "__main__":
    main()
