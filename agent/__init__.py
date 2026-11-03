"""SafePaperAgent — 把 paper-agent 升级为可评测、可审计的安全科研 Agent。

子模块：
    llm.py     LLM 客户端抽象（RealLLM 走 DeepSeek；MockLLM 走脚本回放，离线可测）
    tools.py   工具注册表：schema / 执行 / 统一异常
    policy.py  安全中间件：白名单 / 参数校验 / 风险分级 / 不可信数据隔离 / 审计
    state.py   SQLite 状态持久化（tasks / steps）
    runtime.py 单 Agent 执行循环（预算控制 + 轨迹记录 + 失败恢复）
"""
