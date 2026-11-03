"""extract_tasks_partial.py — 从 SQLite 提取已完成的真实 tasks 套件结果（不调 API）。

背景：真实 tasks 套件（16 题）因单任务耗时超预期（真实模型调研步数多）+1 个 PDF 下载
挂起，在 2700s 窗口内完成 5 题、1 题中途终止。本脚本把这 6 题的真实轨迹按与
run_eval.judge_task 相同的规则重建判定，写入 JSONL。完整 16 题的复现命令见报告。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TASKS = {json.loads(l)["query"]: json.loads(l) for l in
         (ROOT / "eval_harness" / "tasks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}


class R:  # 最小 result 替身（judge_task 只用这些字段）
    def __init__(self, status, report, trace, steps, tokens):
        self.status, self.report, self.trace = status, report, trace
        self.steps, self.tokens = steps, tokens


def main() -> None:
    conn = sqlite3.connect(str(ROOT / "outputs" / "agent_state.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("UPDATE tasks SET status='aborted' WHERE status='running'")
    conn.commit()
    from eval_harness.run_eval import judge_task
    out = []
    for row in conn.execute(
            "SELECT * FROM tasks WHERE created_at > 1793719500 ORDER BY created_at"):
        q = row["query"]
        if "prompt injection" in q or q not in TASKS:
            continue
        case = TASKS[q]
        steps = [dict(s) for s in conn.execute(
            "SELECT * FROM steps WHERE task_id=? ORDER BY idx", (row["task_id"],))]
        trace = []
        for s in steps:
            entry = {"idx": s["idx"], "tool": s["tool"], "status": s["status"],
                     "decision": s["decision"],
                     "args": _args_from_digest(s["input_digest"] or "")}
            trace.append(entry)
        result = R(row["status"], row["result_report"] or "", trace, len(steps),
                   row["total_tokens"] or 0)
        judged = judge_task(case, result)
        judged["task_id"] = case["task_id"]
        out.append(judged)
    path = ROOT / "outputs" / "eval_agent" / "real_policy_enforced_tasks_partial.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    succ = sum(r["success"] for r in out)
    print(f"部分套件：{succ}/{len(out)} 成功（完整 16 题见复现命令）→ {path.name}")
    for r in out:
        print(f"  {r['task_id']} {r['type']:>10} {'✓' if r['success'] else '✗'}"
              f" {r['status']} steps={r['steps']} | {r['failure_attribution'][:60]}")


def _args_from_digest(digest: str) -> dict:
    """input_digest 形如 'hash·{json前缀}'，尽力还原 paper_id/query 供 judge 使用。"""
    m = re.search(r"paper_id\"?\s*[:=]\s*\"?(W\d+)", digest)
    if m:
        return {"paper_id": m.group(1)}
    return {}


if __name__ == "__main__":
    main()
