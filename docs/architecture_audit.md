# Architecture Audit: paper-agent（2026-10-27，基于 commit f85104e）

> 审计范围：全部 12 个核心 Python 文件、模板、部署脚本、README。方法：逐文件代码级走读（过去两周的迭代中本文件作者参与/复核了 v1.1→v1.6 全部代码）。
> Git 工作区状态：干净，无未提交修改。生产配置（服务器 .env / systemd / cron）未触碰。

## 1. 端到端数据流（现状）

```
[每日 cron 07:00] fetch_papers.py ──OpenAlex(主)/arXiv(备,429降级)──▶ data/raw_YYYYMMDD.jsonl
                       │ 限流退避重试(401/403/429/5xx)、增量去重
                       ▼
                   ingest.py ──BGE-M3(本地GPU/SiliconFlow API 双模式)──▶ FAISS index + metas.pkl (250篇)
                       ▼
                   digest.py ──关键词粗筛top15→DeepSeek打分/摘要/标签──▶ outputs/daily_*.json
                       ▼
[用户请求] app.py (FastAPI, 同步def路由跑线程池)
   ├─ /            首页(日报/标签筛选/看板)
   ├─ /search      语义检索(embed→FAISS)
   ├─ /paper/{id}  精读: 相关工作检索→PDF全文(PyMuPDF)→图抽取→DeepSeek解读
   │               └─ v1.6 核验循环: blockquote引文 vs 原文逐字比对, 失败带反馈重试1次, meta.json
   └─ /figure/...  原图服务(白名单正则防目录穿越)
[mcp_server.py] 独立进程: search/digest/reading 三工具(stdio)
[eval.py]      手动CLI: 自检索MRR(n=100) + LLM-as-judge忠实度
[security.py]  手动CLI: 3攻击×3防御×8载体 ASR矩阵(注入点=digest打分环节)
```

状态管理：无数据库。缓存文件即状态（readings/*.html+meta.json、fulltexts/*.txt、pdfs/*.pdf、figures/*.png），全部幂等（存在即跳过）。

## 2. 功能实现状态三分类

**已真实实现（代码+线上验证）**：
- 多源抓取+限流降级+429/401退避；BGE-M3+FAISS 检索（双模式 embedder 抽象）；两级漏斗日报（cascade，token 计量落 cost_latest.json）；全文精读+证据锚点+核验循环（v1.6）；论文原图抽取（页码编码文件名）；引用网络反查；MCP server（三工具，兼容 mcp 1.x/2.x）；评测（自检索 n=100 MRR 0.99 / Hit@1 0.98 / Hit@5 1.00；忠实度 judge 4.75/5 n=12）；注入攻防矩阵（A1 12.5% 三防御残留 / A2 0% / A3 38%→0%，任务保持率 100%）；FastAPI 网站+GZip；systemd/cron 部署。

**部分实现/有已知边界**：
- 核验循环只校验 blockquote 引文，不校验正文数字（误报率考量，见 reading.py 注释）；重试仅 1 次；eval 的 judge 有 rubric 漂移前科（已修一次定义）；security.py 注入点单一（仅 digest 打分的 abstract_override），检索结果/工具返回/全文未覆盖；MCP server 无鉴权无速率限制（stdio 本地场景可接受，公网暴露不行）；评测看板数据靠手动跑脚本刷新。

**README 提及但未实现/刻意不做**：
- 订阅关键词自定义、周报趋势、邮件推送、多模型对比（迭代候选清单）；对话式 UI（决策：以 MCP 开放编排替代）；完整自主规划 Agent（明确定位 workflow+局部 agentic）。

## 3. Workflow vs Agent 判定（关键审计结论）

| 环节 | 判定 | 依据 |
|---|---|---|
| 抓取→入库→日报 | **纯 Workflow** | 流程硬编码于 run_pipeline.sh/digest.py，LLM 只在节点内打分 |
| 详情页精读 | **单步 agentic RAG** | 检索什么由论文标题决定（代码逻辑），LLM 不决定"要不要再检索" |
| v1.6 核验循环 | **代码级 verify-retry** | 循环由代码驱动（生成→核验→反馈→重试1次），非 LLM 自主决策 |
| MCP server | 工具层（无自主性） | 被动响应外部客户端 |
| 结论 | **没有任何 LLM 决定工具选择与下一步行动的环节**——"多步骤任务自主编排"是缺口，即本升级 P0-1 的对象 |

## 4. 问题清单（按任务书五个维度）

**异常处理**：
- P1｜LLM/HTTP 调用已有退避重试（reading._llm、fetch、eval judge），但工具间无统一异常契约（各文件自定义 print+返回 None），Runtime 化时需统一 ToolError 体系；
- P2｜ingest 的 index.add 与 metas.append 非原子（中断会错位——注释里自己写过教训，无断点保护）。

**凭证安全**：
- P0级好消息：.env 已被 .gitignore 排除、deploy.sh 已排除 .env 同步（v1.5 事故修复）、日志不打印 key；
- P1：安全拒绝/日志路径中无凭证脱敏的统一机制（当前没有打印敏感项的实际代码，但缺规范）；MCP 无鉴权（本地 stdio 可接受，需在文档明示边界）。

**输入隔离**：
- P1｜不可信数据（论文摘要/全文）进入 LLM 上下文**无隔离标记**（Spotlighting 仅在 security.py 实验路径中，生产 digest/reading 路径未用）；
- P1｜LLM 输出（解读文本）直接进 HTML | safe 渲染——markdown 库转义文本但无 XSS 审计（低风险：内容自产，但注入攻击下 LLM 输出可能携带 payload，需输出侧过滤）。

**评测数据泄漏**：
- P1｜自检索协议"标题即查询"=答案泄漏进查询（已知局限，已在文档声明）；judge 与被测同模型（DeepSeek 自评偏好风险）；安全实验 8 载体、正常评测 n=12 忠实度——样本量小，结论已在 README 标注为"初步"；
- P0 新风险：本升级的评测集若含真实论文标题做 expected，同样有泄漏问题——评测任务应基于检索行为而非记忆。

**测试可靠性**：
- P0｜**全仓库零自动化测试**（仅 /tmp 临时验证脚本，未入库）；无 CI；mcp 包未进 requirements.txt（服务器手装过）；pytest 未引入——这是 P0 工程质量的主要对象。

## 5. 升级开发计划（优先级排序）

**P0-1 最小 Agent Runtime**（单 Agent+工具编排）：
- 新增 `agent/` 包：`llm.py`（RealLLM/MockLLM 统一接口，DeepSeek function-calling）、`tools.py`（search_papers/read_paper/verify_citation/finish，含 schema/类型/异常）、`policy.py`（白名单+参数校验+风险分级+Spotlighting 包装+审计日志）、`state.py`（SQLite 任务/步骤表，支持 resume）、`runtime.py`（预算控制：max_steps/timeout/retry/cost）；
- 任务入口：`run_agent.py "研究主题"` CLI + FastAPI 新路由 `/agent/run`（不破坏现有接口）。

**P0-2 Evaluation Harness**：`eval_harness/`（tasks.jsonl 16 题+attacks.jsonl 15 题+run_eval.py CLI+judge+Markdown 报告；Mock/Real 分离）。

**P0-3 安全边界**：policy.py 落地（工具白名单硬边界、参数 schema 校验、无真实高危工具、拒绝审计日志、canary 检测统计）+ pytest 证明不可信文档无法绕过授权。

**P0-4 工程质量**：类型标注、pytest 全绿、Mock 端到端演示脚本、README 重写真实状态、docs/evaluation_report.md（真实数据）、docs/resume_bullets.md。

**P1（P0 稳定后）**：AgentDojo 风格场景、pgvector 记忆、轨迹可视化页、MCP 鉴权。

## 6. 本审计的边界声明

- 审计未运行任何破坏性操作；未触碰 data/outputs 生产数据与服务器配置；
- "已验证"均指 2026-10-15~20 期间公网/本地实测记录（学习手册 §十~十三），本次升级的复测见 evaluation_report.md；
- 已知但未列入 P0 的：ingest 原子性（现有 250 篇规模下错位风险低）、XSS 审计（自产内容+只读站点）。
