"""state.py — 轻量状态持久化（SQLite，标准库零新依赖）。

表结构：
    tasks(task_id, query, status, config_json, result_report, error,
          total_tokens, created_at, finished_at)
    steps(step_id, task_id, idx, tool, decision, status,
          input_digest, output_digest, latency_ms, error, created_at)

status 取值：running / finished / failed / budget_exceeded / aborted
失败恢复策略（如实声明）：工具层缓存（readings/fulltexts/pdfs）幂等，恢复=以相同
task 重新运行整条轨迹，已完成步骤因缓存而近乎零成本；不做步骤级断点重放（对
本规模任务复杂度不划算，见 docs/architecture_audit.md P0 计划）。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from config import OUT_DIR

DB_PATH = Path(OUT_DIR) / "agent_state.db"


def _digest(obj: Any, n: int = 120) -> str:
    """输入/输出摘要（审计可见但不落全文，控制状态库体积）。"""
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12] + f"·{s[:n]}"


class AgentState:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks(
            task_id TEXT PRIMARY KEY, query TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            config_json TEXT, result_report TEXT, error TEXT,
            total_tokens INTEGER DEFAULT 0,
            created_at REAL, finished_at REAL);
        CREATE TABLE IF NOT EXISTS steps(
            step_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL, idx INTEGER NOT NULL,
            tool TEXT NOT NULL, decision TEXT, status TEXT,
            input_digest TEXT, output_digest TEXT,
            latency_ms INTEGER, error TEXT, created_at REAL);
        """)
        self.conn.commit()

    # ---------- tasks ----------
    def create_task(self, query: str, config: dict[str, Any]) -> str:
        task_id = f"t{uuid.uuid4().hex[:10]}"
        self.conn.execute(
            "INSERT INTO tasks(task_id, query, status, config_json, created_at)"
            " VALUES(?,?,?,?,?)",
            (task_id, query, "running", json.dumps(config, ensure_ascii=False),
             time.time()))
        self.conn.commit()
        return task_id

    def finish_task(self, task_id: str, status: str,
                    report: str = "", error: str = "",
                    total_tokens: int = 0) -> None:
        self.conn.execute(
            "UPDATE tasks SET status=?, result_report=?, error=?,"
            " total_tokens=?, finished_at=? WHERE task_id=?",
            (status, report, error, total_tokens, time.time(), task_id))
        self.conn.commit()

    def get_task(self, task_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def list_tasks(self, status: Optional[str] = None) -> list[dict]:
        if status:
            rows = self.conn.execute(
                "SELECT task_id, query, status, created_at FROM tasks"
                " WHERE status=? ORDER BY created_at DESC", (status,)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT task_id, query, status, created_at FROM tasks"
                " ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    # ---------- steps ----------
    def log_step(self, task_id: str, idx: int, tool: str, decision: str,
                 status: str, input_obj: Any, output_obj: Any,
                 latency_ms: int, error: str = "") -> None:
        self.conn.execute(
            "INSERT INTO steps(task_id, idx, tool, decision, status,"
            " input_digest, output_digest, latency_ms, error, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (task_id, idx, tool, decision, status,
             _digest(input_obj), _digest(output_obj),
             latency_ms, error, time.time()))
        self.conn.commit()

    def steps_of(self, task_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM steps WHERE task_id=? ORDER BY idx", (task_id,)).fetchall()
        return [dict(r) for r in rows]
