"""fetch_papers.py — 通过 OpenAlex 抓最新论文（生产路径，重注释版）。

用法:
    python fetch_papers.py [--max 60] [--days 7] [--query "large language model agent"]
输出:
    data/raw_YYYYMMDD.jsonl （每行一篇 JSON；paper_id 去重、增量追加）

为什么是 OpenAlex 而不是 arXiv API：
    arXiv 官方 API 对国内/代理 IP 段频发 429（反爬），2025 年后尤其严。
    OpenAlex：免费、无 key、polite 池配额大方（mailto 标识）、纯 JSON、覆盖 arXiv 预印本。
    工程口径：单一数据源被限流 → 加降级数据源（fetch_arxiv.py 留作教学示例/备用源）。

OpenAlex 两个数据小坑（面试也能讲）：
    1) 摘要以"倒排索引"给出（词 → 位置列表），要自己拼回原文；
    2) 预印本常有"未来日期"脏数据（如 2031），客户端要过滤。
"""
import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from config import DATA_DIR

OPENALEX = "https://api.openalex.org/works"
MAILTO = "paper-agent@example.com"  # polite pool 标识（换成自己邮箱更好）
from config import FETCH_QUERY as DEFAULT_QUERY


def http_get_json(url: str, retries: int = 3) -> dict:
    """带退避重试：OpenAlex 过 Cloudflare 偶发 401/403/429/5xx（实测瞬时抖动，
    无重试会让当天 cron 整条流水线失败）——退避后重试比换源先做。"""
    import time as _t
    import urllib.error as _ue
    req = urllib.request.Request(url, headers={"User-Agent": f"paper-agent/0.1 ({MAILTO})"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except _ue.HTTPError as err:
            if err.code in (401, 403, 429, 500, 502, 503) and attempt < retries:
                wait = 5 * (attempt + 1)
                print(f"[fetch] HTTP {err.code}，退避 {wait}s 重试（{attempt + 1}/{retries}）")
                _t.sleep(wait)
                continue
            raise


def rebuild_abstract(inverted: dict | None) -> str:
    """把 {'word': [pos1, pos2], ...} 拼回一段顺序文本。"""
    if not inverted:
        return ""
    slots: list[str] = ["" for _ in range(50)]
    words = sum(len(v) for v in inverted.values())
    slots = [""] * words
    for word, positions in inverted.items():
        for pos in positions:
            slots[pos] = word
    return " ".join(slots)


def fetch_page(query: str, from_date: str, cursor: str, per_page: int) -> dict:
    params = {
        "filter": f"from_publication_date:{from_date},title_and_abstract.search:{query}",
        "sort": "publication_date:desc",
        "per-page": str(per_page),
        "cursor": cursor,
        "select": "id,title,publication_date,doi,authorships,abstract_inverted_index,"
                  "primary_location,topics",
        "mailto": MAILTO,
    }
    url = OPENALEX + "?" + urllib.parse.urlencode(params)
    return http_get_json(url)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=60)
    parser.add_argument("--days", type=int, default=7, help="抓最近 N 天")
    parser.add_argument("--query", type=str, default=DEFAULT_QUERY)
    args = parser.parse_args()

    from_date = (date.today() - timedelta(days=args.days)).isoformat()
    today = date.today().isoformat()
    papers, cursor, seen_ids = [], "*", set()
    today_str = datetime.now().strftime("%Y%m%d")
    out_path = DATA_DIR / f"raw_{today_str}.jsonl"

    # 历史去重：和 fetch_arxiv 一样按已有文件扫一遍
    for path in sorted(DATA_DIR.glob("raw_*.jsonl")):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    seen_ids.add(json.loads(line)["paper_id"])
                except (json.JSONDecodeError, KeyError):
                    continue

    while len(papers) < args.max:
        data = fetch_page(args.query, from_date, cursor, per_page=50)
        results = data.get("results", [])
        if not results:
            break
        for w in results:
            pub = w.get("publication_date") or ""
            if not pub or pub > today:
                continue  # 坑②：过滤未来日期脏数据
            loc = (w.get("primary_location") or {})
            paper_id = w["id"].rsplit("/", 1)[-1]  # https://openalex.org/W123… → W123…
            if paper_id in seen_ids:
                continue
            papers.append({
                "paper_id": paper_id,
                "title": w.get("title") or "",
                "authors": [a["author"]["display_name"]
                            for a in (w.get("authorships") or [])][:8],
                "abstract": rebuild_abstract(w.get("abstract_inverted_index"))[:2000],
                "published": pub,
                "categories": [t.get("display_name", "") for t in (w.get("topics") or [])[:3]],
                "abs_url": loc.get("landing_page_url") or w.get("doi") or "",
                "pdf_url": loc.get("pdf_url") or "",
                "source": (loc.get("source") or {}).get("display_name", ""),
            })
            if len(papers) >= args.max:
                break
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break
        time.sleep(0.5)  # 对 polite 池客气一点

    with open(out_path, "a", encoding="utf-8") as f:
        for p in papers:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"抓取 {len(papers)} 篇 → {out_path}")
    for p in papers[:5]:
        print("  ·", p["published"], p["title"][:70])


if __name__ == "__main__":
    main()
