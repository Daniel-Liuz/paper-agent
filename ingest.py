"""ingest.py — 入库：raw_*.jsonl → BGE-M3 向量化 → FAISS 索引 + 元数据。

带注释参考实现（你要能讲清每一行，面试会问）：
验收：
    python ingest.py           # 增量入库
    python ingest.py --stats   # 查看索引统计

设计要点（对照你《aiagent构建指南》的 ETL 那节，同构但有三个升级点）：
1. 归一化向量 + IndexFlatIP：内积=余弦相似度，量级 <1 万篇时 Flat 精确检索足够快；
2. 增量入库：已入库的 paper_id 跳过，新向量 index.add，索引文件整体重写；
3. 元数据列表与索引行号严格对齐：第 i 行向量 ↔ metas[i]，这是检索结果能还原论文的前提。
"""
import argparse
import json
import pickle

import faiss

from config import DATA_DIR, EMBED_MODEL, FAISS_INDEX, META_STORE, CHUNK_CHARS
from embedder import get_embedder

META_FIELDS = ("paper_id", "title", "authors", "published",
               "categories", "abs_url", "pdf_url", "source")


def load_state() -> tuple[list[dict], list[dict]]:
    """返回 (已入库元数据, 待入库新论文)。去重键兼容两种抓取器：paper_id / arxiv_id。"""
    metas: list[dict] = []
    if META_STORE.exists():
        metas = pickle.loads(META_STORE.read_bytes())
    seen = {m["paper_id"] for m in metas}
    fresh: list[dict] = []
    for path in sorted(DATA_DIR.glob("raw_*.jsonl")):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    p = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pid = p.get("paper_id") or p.get("arxiv_id")
                if pid and pid not in seen:
                    p["paper_id"] = pid
                    fresh.append(p)
                    seen.add(pid)
    return metas, fresh


def make_text(p: dict) -> str:
    """embedding 输入 = 标题 + 摘要（物理截断，防长文本 O(n²) 的老教训）。"""
    abstract = (p.get("abstract") or "")[:CHUNK_CHARS]
    return f'{p.get("title", "")}\n{abstract}'


def build_index(metas: list[dict], fresh: list[dict]) -> None:
    if not fresh:
        print(f"没有新论文，索引保持 {len(metas)} 篇")
        return

    print(f"加载编码器（{EMBED_MODEL}，本地或 API 由 EMBED_PROVIDER 决定）…")
    texts = [make_text(p) for p in fresh]
    vecs = get_embedder().encode(texts)  # 已归一化的 float32

    if FAISS_INDEX.exists():
        index = faiss.read_index(str(FAISS_INDEX))
    else:
        index = faiss.IndexFlatIP(vecs.shape[1])  # bge-m3 → 1024 维

    index.add(vecs)  # 追加到已有索引尾部 —— 所以 metas 也必须同步 append
    for p in fresh:
        metas.append({k: p.get(k, "") for k in META_FIELDS})

    faiss.write_index(index, str(FAISS_INDEX))
    META_STORE.write_bytes(pickle.dumps(metas))
    print(f"入库 {len(fresh)} 篇，索引总计 {index.ntotal} 篇")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stats", action="store_true", help="只看统计不写索引")
    args = parser.parse_args()

    if args.stats:
        if FAISS_INDEX.exists():
            index = faiss.read_index(str(FAISS_INDEX))
            print(f"索引: {FAISS_INDEX}\n向量数: {index.ntotal}\n维度: {index.d}")
            metas = pickle.loads(META_STORE.read_bytes())
            print(f"元数据条数: {len(metas)}（应与向量数一致）")
            print("最近一篇:", metas[-1]["published"], metas[-1]["title"][:60])
        else:
            print("索引还不存在，先跑一次 python ingest.py")
        return

    metas, fresh = load_state()
    build_index(metas, fresh)


if __name__ == "__main__":
    main()
