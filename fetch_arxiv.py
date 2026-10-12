"""fetch_arxiv.py — 抓取最新 arXiv 论文元数据（示例模块，重注释版）。

用法:
    python fetch_arxiv.py [--max 100] [--cats cs.AI,cs.CL]

输出:
    data/raw_YYYYMMDD.jsonl  （每行一篇论文的 JSON，增量追加、按 arxiv_id 去重）

为什么用 arXiv API 而不是 Midscene 爬网页：
    API 稳定、免费、无需登录、毫秒级返回；浏览器自动化留给无 API 的站点。
    这是本项目的第一工程决策（面试答辩口径）。

arXiv API 返回 Atom XML（一种 RSS 方言），用标准库 xml.etree 解析，零额外依赖。
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from config import DATA_DIR, DEFAULT_MAX_RESULTS, ARXIV_CATEGORIES

ARXIV_API = "https://export.arxiv.org/api/query"  # 直接 https，避免一次 302
# Atom 命名空间：所有标签都挂在它下面，ElementTree 查找必须带前缀
NS = {"a": "http://www.w3.org/2005/Atom"}
# arXiv 礼仪：自带标识的 UA 比默认 "Python-urllib" 不容易被反爬盯上
HEADERS = {"User-Agent": "paper-agent/0.1 (research-use; via WSL)"}


def http_get(url: str, timeout: int = 60, retries: int = 3) -> bytes:
    """带退避重试的 GET。遇到 429（限流）等错误时等几秒再试。"""
    import time
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as err:
            last_err = err
            wait = 5 * (attempt + 1)  # 5s / 10s / 15s
            print(f"HTTP {err.code}，{wait}s 后重试（{attempt + 1}/{retries}）")
            time.sleep(wait)
    raise last_err


def fetch_entries(categories: list[str], max_results: int) -> list[dict]:
    """按类别抓最新提交的论文，返回 dict 列表。"""
    # search_query 形如 cat:cs.AI OR cat:cs.CL；按提交时间倒序
    query = " OR ".join(f"cat:{c}" for c in categories)
    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": str(max_results),
    }
    url = ARXIV_API + "?" + urllib.parse.urlencode(params)
    print("GET", url)

    xml_bytes = http_get(url)  # urlopen 会读 http_proxy 环境变量；建议直连（见 README）

    root = ET.fromstring(xml_bytes)
    papers = []
    for entry in root.findall("a:entry", NS):
        # id 形如 http://arxiv.org/abs/2503.03704v1 → 取最后一段
        abs_url = entry.findtext("a:id", namespaces=NS)
        arxiv_id = abs_url.rsplit("/", 1)[-1]
        # 标题/摘要里常有换行和连续空格，" ".join(str.split()) 一律压平成单行
        papers.append({
            "arxiv_id": arxiv_id,
            "title": " ".join(entry.findtext("a:title", namespaces=NS).split()),
            "authors": [a.findtext("a:name", namespaces=NS)
                        for a in entry.findall("a:author", NS)],
            "abstract": " ".join(entry.findtext("a:summary", namespaces=NS).split()),
            "published": entry.findtext("a:published", namespaces=NS),  # UTC ISO 时间
            "categories": [c.get("term") for c in entry.findall("a:category", NS)],
            "abs_url": abs_url,
            "pdf_url": abs_url.replace("/abs/", "/pdf/"),
        })
    return papers


def load_seen() -> set[str]:
    """读所有历史 raw_*.jsonl，汇总已抓过的 arxiv_id（增量去重用）。"""
    seen = set()
    for path in sorted(DATA_DIR.glob("raw_*.jsonl")):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    seen.add(json.loads(line)["arxiv_id"])
                except (json.JSONDecodeError, KeyError):
                    continue  # 坏行直接跳过，不让单行脏数据炸整个流程
    return seen


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=DEFAULT_MAX_RESULTS)
    parser.add_argument("--cats", type=str, default=",".join(ARXIV_CATEGORIES))
    args = parser.parse_args()

    categories = [c.strip() for c in args.cats.split(",") if c.strip()]
    papers = fetch_entries(categories, args.max)

    seen = load_seen()
    fresh = [p for p in papers if p["arxiv_id"] not in seen]
    out_path = DATA_DIR / f"raw_{datetime.now():%Y%m%d}.jsonl"
    with open(out_path, "a", encoding="utf-8") as f:  # 追加模式：一天可跑多次
        for p in fresh:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    print(f"抓取 {len(papers)} 篇，新增 {len(fresh)} 篇 → {out_path}")
    for p in fresh[:5]:
        print("  ·", p["arxiv_id"], p["title"][:70])


if __name__ == "__main__":
    sys.exit(main())
