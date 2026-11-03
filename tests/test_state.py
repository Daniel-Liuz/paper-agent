"""tests/test_state.py — SQLite 状态层：任务生命周期 / 步骤轨迹 / 摘要脱敏。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.state import AgentState                    # noqa: E402


def make_state():
    return AgentState(Path(tempfile.mkdtemp()) / "s.db")


def test_task_lifecycle():
    s = make_state()
    tid = s.create_task("测试任务", {"max_steps": 8})
    assert s.get_task(tid)["status"] == "running"
    s.finish_task(tid, "finished", report="R", total_tokens=123)
    task = s.get_task(tid)
    assert task["status"] == "finished"
    assert task["result_report"] == "R" and task["total_tokens"] == 123
    assert task["finished_at"] >= task["created_at"]


def test_steps_recorded_in_order_with_digests():
    s = make_state()
    tid = s.create_task("t", {})
    s.log_step(tid, 1, "search_papers", "allowed", "ok",
               {"query": "agent"}, {"results": ["W1"]}, latency_ms=12)
    s.log_step(tid, 2, "send_email", "denied", "denied",
               {"to": "x"}, {"error": "denied"}, latency_ms=1, error="not-in-whitelist")
    steps = s.steps_of(tid)
    assert [x["idx"] for x in steps] == [1, 2]
    assert steps[0]["tool"] == "search_papers"
    assert "agent" in steps[0]["input_digest"]         # 摘要含前缀片段便于人工审计
    assert steps[1]["status"] == "denied"


def test_list_tasks_filter():
    s = make_state()
    a = s.create_task("a", {})
    b = s.create_task("b", {})
    s.finish_task(a, "failed", error="x")
    running = s.list_tasks(status="running")
    assert [t["task_id"] for t in running] == [b]


def test_resume_same_task_id_rerun():
    """恢复语义：同 task 重新执行由 runtime 层支持（幂等缓存），state 可重复写。"""
    s = make_state()
    tid = s.create_task("resume-me", {})
    s.log_step(tid, 1, "search_papers", "allowed", "ok", {}, {}, 5)
    # 模拟重跑再次记录（steps 追加，任务状态覆盖）
    s.log_step(tid, 1, "search_papers", "allowed", "ok", {}, {}, 5)
    assert len(s.steps_of(tid)) == 2                   # 轨迹保留完整历史
