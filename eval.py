"""eval.py — 评测层 v0.1：给"自己的工作流"打分。

两项评测（对应简历里"系统评测"的落地）：
1. 检索质量（自检索协议）：用论文自己的标题当查询，看向量检索能不能把它找回来。
   指标：Hit@1 / Hit@5 / MRR。这是检索系统最便宜的自检——不需要人工标注。
2. 摘要忠实度（LLM-as-judge）：拿日报里的 LLM 摘要 vs 原始摘要，让另一个 judge 调用
   按"是否完全被原文支持"打 1-5 分——衡量幻觉程度。

跑法：python eval.py [--sample 30] [--judge 8]
输出：outputs/eval_latest.json（网站"评测看板"读取）。
"""
import argparse
import json
import pickle
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import faiss
import numpy as np

from config import (DATA_DIR, OUT_DIR, EMBED_MODEL, FAISS_INDEX, META_STORE,
                    LLM_BASE_URL, LLM_MODEL, LLM_API_KEY)
from embedder import get_embedder

JUDGE_SYSTEM = (
    "你是评测员。给定论文原始摘要（ground truth）和一篇生成的中文摘要，"
    "评估生成摘要的每个事实性陈述是否被原文支持。只输出 JSON："
    '{"faithfulness": 1-5整数(5=完全支持), "unsupported": "最不被支持的一句，无则空串"}'
)


def self_retrieval(embedder, index, metas, sample: int, k: int = 5) -> dict:
    """标题当查询 → top-k 里找自己。返回 MRR / Hit@1 / Hit@5。"""
    rng = np.random.default_rng(42)
    picks = rng.choice(len(metas), size=min(sample, len(metas)), replace=False)
    titles = [metas[i]["title"] for i in picks]
    vecs = embedder.encode(titles)
    scores, ids = index.search(vecs, k + 1)  # +1 容错：自己排第 k 名之后的情况
    rr_list, hit1, hit5, n = [], 0, 0, 0
    for row, target in zip(ids, picks):
        n += 1
        positions = [r for r in row if r >= 0]
        rank = None
        if target in positions:
            rank = positions.index(target) + 1  # 从 1 开始的名次
        if rank is not None:
            rr_list.append(1.0 / rank)
            if rank == 1:
                hit1 += 1
            if rank <= 5:
                hit5 += 1
        else:
            rr_list.append(0.0)
    return {"n": n, "mrr": round(float(np.mean(rr_list)), 3),
            "hit1": round(hit1 / n, 3), "hit5": round(hit5 / n, 3)}


def judge_one(original: str, generated: str) -> dict:
    """单篇忠实度判定。返回 {faithfulness, unsupported}。"""
    body = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": f"原始摘要：\n{original[:1500]}\n\n生成摘要：\n{generated}"},
        ],
        "temperature": 0.1, "max_tokens": 250,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        LLM_BASE_URL.rstrip("/") + "/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LLM_API_KEY}"})
    started = time.perf_counter()
    data = None
    for attempt in range(3):  # 网络抖动/上游过载：退避重试，单次抖动不打穿整轮评测
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read())
            break
        except Exception as err:
            if attempt == 2:
                raise
            wait = 3 * (attempt + 1)
            print(f"  [judge] {type(err).__name__}，退避 {wait}s 重试（{attempt + 1}/2）")
            time.sleep(wait)
    latency_ms = round((time.perf_counter() - started) * 1000)
    parsed = json.loads(data["choices"][0]["message"]["content"])
    return {"faithfulness": max(1, min(5, int(parsed.get("faithfulness", 1)))),
            "unsupported": str(parsed.get("unsupported", "")), "latency_ms": latency_ms}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=30)
    parser.add_argument("--judge", type=int, default=8)
    args = parser.parse_args()

    if not LLM_API_KEY:
        raise SystemExit("缺少 LLM_API_KEY（.env）")

    # ---- 1. 检索质量 ----
    print("== 检索质量（自检索）==")
    embedder = get_embedder()
    index = faiss.read_index(str(FAISS_INDEX))
    metas = pickle.loads(META_STORE.read_bytes())
    retrieval = self_retrieval(embedder, index, metas, args.sample)
    print(retrieval)

    # ---- 2. 摘要忠实度 ----
    print("== 摘要忠实度（LLM-as-judge）==")
    digest_files = sorted(OUT_DIR.glob("daily_*.json"), reverse=True)
    faith = {"n": 0, "mean": None, "flagged": [], "judge_latency_ms": None}
    if digest_files:
        items = json.loads(digest_files[0].read_text(encoding="utf-8"))
        abstracts = {}
        for path in sorted(DATA_DIR.glob("raw_*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    p = json.loads(line)
                    abstracts[p.get("paper_id") or p.get("arxiv_id")] = p.get("abstract", "")
        picks = [it for it in items if it.get("summary")][: args.judge]
        scores, lats, flagged = [], [], []
        for it in picks:
            original = abstracts.get(it["paper_id"], "")
            if not original:
                continue
            try:
                result = judge_one(original, it["summary"])
            except Exception as err:  # 单篇失败只跳过，评测继续（评测必须比被测系统更稳）
                print(f"  [skip] {it['title'][:44]}：{type(err).__name__}")
                continue
            scores.append(result["faithfulness"])
            lats.append(result["latency_ms"])
            if result["faithfulness"] <= 3 and result["unsupported"]:
                flagged.append({"title": it["title"][:60],
                                "unsupported": result["unsupported"][:120]})
            print(f"  [{len(scores)}/{len(picks)}] 忠实度 {result['faithfulness']}/5 "
                  f"{it['title'][:44]}")
            time.sleep(0.3)
        if scores:
            faith = {"n": len(scores), "mean": round(sum(scores) / len(scores), 2),
                     "flagged": flagged,
                     "judge_latency_ms": int(sum(lats) / len(lats))}

    report = {"date": datetime.now().strftime("%Y-%m-%d %H:%M"),
              "n_index": index.ntotal,
              "retrieval": retrieval, "faithfulness": faith}
    out = OUT_DIR / "eval_latest.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n评测报告 → {out}")
    print(json.dumps(report, ensure_ascii=False)[:400])


if __name__ == "__main__":
    main()
