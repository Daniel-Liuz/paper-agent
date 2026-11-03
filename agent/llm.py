"""llm.py — LLM 客户端抽象：RealLLM（DeepSeek，OpenAI 兼容 tools）与 MockLLM（脚本回放）。

设计要点：
- 统一接口 chat(messages, tools) -> LLMResponse，Runtime 不感知后端；
- MockLLM 按"第 n 次调用"回放预设脚本，使 Runtime 全流程可离线测试；
- token 用量随响应返回（Real 来自 API usage；Mock 记 0，绝不冒充真实成本）。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL


@dataclass
class ToolCall:
    """LLM 发起的一次工具调用请求。"""
    name: str
    arguments: dict[str, Any]          # 已解析的参数（保持 JSON 可序列化）
    raw_arguments: str = ""            # 模型原始 JSON 文本（审计/排错用）


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


class BaseLLM(Protocol):
    def chat(self, messages: list[dict], tools: Optional[list[dict]] = None,
             temperature: float = 0.2) -> LLMResponse: ...


class RealLLM:
    """DeepSeek（OpenAI 兼容）function calling。带 429/5xx 退避重试。"""

    def __init__(self, model: str = LLM_MODEL, retries: int = 2):
        self.model = model
        self.retries = retries

    def chat(self, messages: list[dict], tools: Optional[list[dict]] = None,
             temperature: float = 0.2) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 2000,
        }
        if tools:
            body["tools"] = tools
        payload = json.dumps(body).encode("utf-8")
        started = time.perf_counter()
        data = None
        for attempt in range(self.retries + 1):
            if attempt:
                time.sleep(3 * attempt)
            req = urllib.request.Request(
                LLM_BASE_URL.rstrip("/") + "/chat/completions", data=payload,
                method="POST",
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {LLM_API_KEY}"})
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read())
                break
            except urllib.error.HTTPError as err:
                if err.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    continue
                raise
        assert data is not None
        msg = data["choices"][0]["message"]
        usage = data.get("usage", {})
        calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw)
                if not isinstance(args, dict):
                    raise ValueError("arguments 必须是 JSON 对象")
            except (json.JSONDecodeError, ValueError):
                args = {"__invalid_json__": raw[:200]}   # 交给 policy 拦截并反馈
            calls.append(ToolCall(name=fn.get("name") or "",
                                  arguments=args, raw_arguments=raw))
        return LLMResponse(
            content=msg.get("content") or "",
            tool_calls=calls,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


class MockLLM:
    """离线回放：按第 n 次 chat 调用返回脚本预设的响应。

    用途：Runtime 逻辑测试、policy 硬边界测试、无 key 环境的演示。
    约束：Mock 结果只用于工程验证，不得写入真实评测报告（run_eval 强制隔离）。
    """

    def __init__(self, script: list[dict]):
        self.script = script
        self._i = 0

    def chat(self, messages: list[dict], tools: Optional[list[dict]] = None,
             temperature: float = 0.2) -> LLMResponse:
        if self._i >= len(self.script):
            return LLMResponse(content="[mock] 脚本耗尽，直接结束。")
        step = self.script[self._i]
        self._i += 1
        calls = [ToolCall(name=c["name"], arguments=c.get("arguments", {}))
                 for c in step.get("tool_calls", [])]
        return LLMResponse(content=step.get("content", ""),
                           tool_calls=calls,
                           prompt_tokens=0, completion_tokens=0,
                           latency_ms=1)
