"""digest.py — agentic 日报：关键词初筛 → LLM 相关性打分与摘要 → markdown 日报。

流水线位置：fetch(抓) → ingest(入库) → digest(理解) ← 你在这里
跑法：
    python digest.py [--date YYYYMMDD] [--top 12]
依赖：.env 里的 LLM_API_KEY（DeepSeek，OpenAI 兼容协议）。

设计要点（讲解线索，配合之后的教学）：
1. 两级漏斗：先用关键词命中数做"免费"粗筛（0 成本、可解释），
   再让 LLM 精筛打分（有成本、懂语义）——这是工程上标准的 cascade 模式；
2. LLM 输出用 response_format=json_object 强约束成 JSON，解析失败有降级（保留无摘要条目）；
3. temperature=0.2：打分任务要稳定不要发散；
4. 每次调用间隔 0.3s + 429/5xx 退避重试：对 API 礼貌，也扛限流。
"""
import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from config import (DATA_DIR, OUT_DIR, INTEREST_KEYWORDS,
                    LLM_BASE_URL, LLM_MODEL, LLM_API_KEY)

SYSTEM_PROMPT = (
    "你是论文筛选助手。用户研究兴趣：大模型 Agent、Agent 评测、Agent 安全。"
    "对给定论文打 1-5 相关性分（5=核心相关）并写两句话中文摘要（含方法与结论要点）。"
    "只输出 JSON。"
)

# 全流程成本计量：简历/评测用的工程数字（调用数、token 用量）
COST = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}


def llm_score_paper(paper: dict, system_prompt: str | None = None,
                    abstract_override: str | None = None) -> dict:
    """调一次 LLM 给单篇打分。返回 {"score": int, "summary": str}。

    system_prompt / abstract_override 两个可选参数是给安全实验（security.py）留的口子：
    可以替换系统提示（模拟防御）或替换摘要内容（模拟被投毒的不可信输入）。
    """
    system = system_prompt or SYSTEM_PROMPT
    abstract = (abstract_override if abstract_override is not None
                else paper.get("abstract", ""))[:1500]
    user_prompt = (
        f"标题: {paper['title']}\n作者: {', '.join(paper['authors'][:5])}\n"
        f"发表日期: {paper['published']}\n摘要: {abstract}\n\n"
        '请输出 JSON：{"score": 1-5的整数, "summary": "两句话中文摘要"}'
    )
    body = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,          # 打分任务要稳定
        "max_tokens": 300,
        "response_format": {"type": "json_object"},  # 强制 JSON 输出（DeepSeek 支持）
    }).encode("utf-8")

    url = LLM_BASE_URL.rstrip("/") + "/chat/completions"
    last_err = None
    for attempt in range(3):  # 429/5xx 退避重试
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {LLM_API_KEY}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read())
            usage = data.get("usage", {})  # 成本计量
            COST["calls"] += 1
            COST["prompt_tokens"] += usage.get("prompt_tokens", 0)
            COST["completion_tokens"] += usage.get("completion_tokens", 0)
            text = data["choices"][0]["message"]["content"]
            parsed = json.loads(text)
            score = max(1, min(5, int(parsed.get("score", 1))))  # 夹紧到 1-5
            return {"score": score, "summary": str(parsed.get("summary", "")).strip()}
        except (urllib.error.HTTPError, urllib.error.URLError,
                KeyError, json.JSONDecodeError, ValueError) as err:
            last_err = err
            wait = 3 * (attempt + 1)
            print(f"  LLM 调用失败（{err}），{wait}s 后重试 {attempt + 1}/3")
            time.sleep(wait)
    # 三次都失败：降级——保留条目但无 LLM 信息（日报不因单篇失败而中断）
    print(f"  ✗ 放弃打分：{paper['title'][:50]}（{last_err}）")
    return {"score": 0, "summary": ""}


def prescreen(papers: list[dict]) -> list[dict]:
    """关键词命中数粗筛：免费、可解释、排序稳定。"""
    for p in papers:
        text = f"{p.get('title', '')} {p.get('abstract', '')}".lower()
        p["_hits"] = sum(1 for kw in INTEREST_KEYWORDS if kw.lower() in text)
    ranked = sorted(papers, key=lambda p: (p["_hits"], p.get("published", "")), reverse=True)
    # 至少 1 个关键词命中才值得花 LLM 的钱；全空则原样返回前几篇兜底
    hit = [p for p in ranked if p["_hits"] > 0]
    return hit if hit else ranked


def stars(score: int) -> str:
    return "★" * score + "☆" * (5 - score)


def render(papers: list[dict], date_str: str, skipped: int) -> str:
    lines = [
        f"# 📚 论文日报 {date_str}",
        "",
        f"> 兴趣轴：{' / '.join(INTEREST_KEYWORDS[:6])} · 打分模型：{LLM_MODEL}"
        f" · 初筛 {len(papers) + skipped} 篇 → 精评 {len(papers)} 篇",
        "",
    ]
    for i, p in enumerate(papers, 1):
        lines.append(f"## {i}. [{p['title']}]({p['abs_url']})")
        lines.append("")
        authors = ", ".join(p["authors"][:3]) + (" et al." if len(p["authors"]) > 3 else "")
        lines.append(f"**{stars(p['_score'])}（{p['_score']}/5）** ｜ {p['published']} ｜ {authors} ｜ "
                     f"关键词命中 {p['_hits']}")
        if p.get("_summary"):
            lines.append("")
            lines.append(f"摘要：{p['_summary']}")
        lines.append("")
    if skipped:
        lines.append("---")
        lines.append(f"*另有 {skipped} 篇初筛未命中兴趣关键词，未做精评。*")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--top", type=int, default=12, help="送 LLM 精评的篇数上限")
    args = parser.parse_args()

    raw_path = DATA_DIR / f"raw_{args.date}.jsonl"
    if not raw_path.exists():
        raise SystemExit(f"找不到 {raw_path}，先跑 fetch_papers.py")
    if not LLM_API_KEY:
        raise SystemExit("缺少 LLM_API_KEY：检查 .env 或 export LLM_API_KEY=sk-...")

    papers = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()
              if line.strip()]
    ranked = prescreen(papers)
    to_score, skipped_count = ranked[:args.top], len(ranked) - args.top

    print(f"初筛 {len(papers)} 篇 → 精评 {len(to_score)} 篇（模型 {LLM_MODEL}）")
    for i, p in enumerate(to_score, 1):
        print(f"[{i}/{len(to_score)}] {p['title'][:60]}")
        result = llm_score_paper(p)
        p["_score"], p["_summary"] = result["score"], result["summary"]
        time.sleep(0.3)  # 对 API 礼貌一点

    md = render(to_score, args.date, skipped_count)
    out = OUT_DIR / f"daily_{args.date}.md"
    # 成本足迹写进日报页脚（网站可见）+ 独立 json（脚本可读）
    md += (
        f"\n---\n*本日 LLM 用量：{COST['calls']} 次调用，"
        f"{COST['prompt_tokens'] + COST['completion_tokens']} tokens"
        f"（输入 {COST['prompt_tokens']} / 输出 {COST['completion_tokens']}）。*\n"
    )
    out.write_text(md, encoding="utf-8")
    (OUT_DIR / "cost_latest.json").write_text(
        json.dumps({"date": args.date, **COST}, ensure_ascii=False), encoding="utf-8")
    # 结构化结果同步落盘：评测层（eval.py 的忠实度 judge）要读它
    structured = [{"paper_id": p.get("paper_id"), "title": p["title"],
                   "score": p.get("_score", 0), "summary": p.get("_summary", "")}
                  for p in to_score]
    (OUT_DIR / f"daily_{args.date}.json").write_text(
        json.dumps(structured, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n日报完成 → {out}（网站首页自动展示）")
    top3 = [p for p in to_score if p.get("_score", 0) >= 4]
    print(f"≥4 分（强相关）：{len(top3)} 篇")
    for p in top3:
        print(f"  ★{p['_score']} {p['title'][:66]}")


if __name__ == "__main__":
    main()
