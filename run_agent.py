"""run_agent.py — SafePaperAgent 的 CLI 入口。

用法：
    python run_agent.py "prompt injection 防御相关的论文对比"            # 真实 LLM
    python run_agent.py "..." --demo                                    # Mock 离线演示
    python run_agent.py "..." --task t123abc                            # 恢复/重跑任务
    python run_agent.py --list                                          # 任务列表
"""
from __future__ import annotations

import argparse
import json

from agent.llm import MockLLM, RealLLM
from agent.policy import PolicyEngine
from agent.runtime import AgentRuntime, Budget
from agent.state import AgentState

# Mock 演示脚本：模拟一个健康的 3 篇论文调研轨迹（离线可跑）
DEMO_SCRIPT = [
    {"tool_calls": [{"name": "search_papers",
                     "arguments": {"query": "prompt injection defense", "k": 3}}]},
    {"tool_calls": [{"name": "read_paper", "arguments": {"paper_id": "W7164329679"}}]},
    {"tool_calls": [{"name": "verify_citation",
                     "arguments": {"paper_id": "W7164329679",
                                   "quote": "achieves a target-interval compliance rate"}}]},
    {"tool_calls": [{"name": "finish",
                     "arguments": {"report": "[Mock 演示报告] 通过检索-阅读-核验完成调研。"}}]},
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?", help="研究任务描述")
    parser.add_argument("--demo", action="store_true", help="Mock 离线演示（不调真实模型）")
    parser.add_argument("--task", help="恢复/重跑既有 task_id")
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    state = AgentState()
    if args.list:
        for t in state.list_tasks():
            print(f'{t["task_id"]}  {t["status"]:>15}  {t["query"][:50]}')
        return
    if not args.query:
        parser.error("需要提供任务描述（或 --list）")

    llm = MockLLM(DEMO_SCRIPT) if args.demo else RealLLM()
    runtime = AgentRuntime(llm=llm, state=state, policy=PolicyEngine(),
                           budget=Budget(max_steps=args.max_steps))
    result = runtime.run(args.query, task_id=args.task)

    print(json.dumps({
        "task_id": result.task_id, "status": result.status,
        "steps": result.steps, "tokens": result.tokens,
        "error": result.error,
    }, ensure_ascii=False, indent=1))
    if result.report:
        print("\n===== 报告 =====\n" + result.report[:2000])


if __name__ == "__main__":
    main()
