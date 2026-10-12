# paper-agent：论文获取与理解 Agent 工作流（开发中）

> 定位：**Deep Research 的垂直特化**——面向"论文跟踪与理解"的 agentic RAG 工作流。
> 简历口径：「构建论文理解 agent 工作流，并对其进行系统评测与注入攻防测试」。
> 建立：2026-10-10。

## 一、架构总览（mermaid，企业里就用这类图做设计沟通）

```mermaid
flowchart LR
    subgraph 数据层
        A[fetch_papers.py<br/>OpenAlex 主源] --> R[(data/raw_*.jsonl)]
        B[fetch_arxiv.py<br/>arXiv 备用源] -.限流降级.-> R
    end
    subgraph 理解层
        R --> I[ingest.py<br/>BGE-M3 向量化] --> F[(FAISS 索引<br/>+元数据 pickle)]
        R --> D[digest.py<br/>关键词粗筛→LLM打分摘要]
    end
    subgraph 消费层
        F --> S[app.py /search<br/>语义检索]
        F --> RD[reading.py<br/>深度阅读 agentic RAG]
        D --> DG[outputs/daily_*.md]
        S & RD & DG --> W[Web 界面 :8000]
    end
    subgraph 质量与安全层（差异化核心）
        F --> E[eval.py<br/>检索MRR/Hit + 摘要忠实度judge]
        D -.注入攻防.-> SEC[security.py<br/>3攻击×3防御 ASR矩阵]
        E & SEC --> W
    end
```

模块职责一句话表：

| 模块 | 职责 | 关键设计 |
|---|---|---|
| fetch_papers / fetch_arxiv | 多源抓取 | 限流降级、增量去重、未来日期脏数据过滤 |
| ingest | 向量入库 | 归一化+IndexFlatIP=余弦、行号-元数据严格对齐 |
| digest | agentic 日报 | 两级漏斗（关键词免费粗筛→LLM精筛）、JSON强约束输出 |
| app + reading | 消费层 | 语义检索、深度阅读（检索相关工作→结构化解读，带缓存） |
| eval | 评测 | 自检索 MRR/Hit@k、摘要忠实度 LLM-as-judge |
| security | 攻防 | 间接提示注入：3 攻击载荷 × 3 防御 → ASR + 任务保持率 |

**计划中（部署后）**：crontab 定时任务 `fetch → ingest → digest` 每日自动更新；阿里云上线。

## 二、任务卡

### ① ingest.py（你写，明晚）——从《aiagent构建指南》直接搬
- 输入：`data/raw_*.jsonl`（每行一篇：arxiv_id/title/authors/abstract/...）
- 处理：abstract 物理截断到 `CHUNK_CHARS=500`（防 O(L²) 的老教训）→ BGE-M3 编码
- 输出：`FAISS_INDEX`（IndexFlatIP，向量需归一化用内积当余弦）+ `META_STORE`（pickle，存原文元数据）
- 去重：按 arxiv_id，增量入库（已存在的跳过）
- 参照：你自己的 `计算机前后端知识/aiagent构建指南.md` §2 ETL 流水线，几乎同构
- 验收：`python ingest.py` 后能查到今天抓的论文，index 维度 = 已入库篇数

### ② digest.py（你写，10/12 前）——第一次真正"agent 化"
- 输入：今天的论文（按 published 日期过滤）+ `INTEREST_KEYWORDS` 相关性初筛
- 调 `LLM_API_KEY` 对应接口（OpenAI 兼容格式，见 config.py）：让 LLM 对每篇打分（1-5 相关度）+ 两句话摘要 + 推荐理由
- 输出：`outputs/daily_YYYYMMDD.md`：按相关度排序的日报（标题/作者/链接/摘要/评分）
- 进阶（可选，先不做）：用入库 FAISS 检索"和我兴趣历史最像的论文"做交叉引用
- 验收：一份你真的愿意每天看的日报

## 三、快速开始

```bash
# 环境：直接用 conda llm（torch 已装）。首次依赖已由 AI 安装中：
# ~/miniconda3/envs/llm/bin/pip install -r requirements.txt -i tuna镜像

# 1. 抓取（今晚跑通它）
~/miniconda3/envs/llm/bin/python fetch_arxiv.py --max 100

# 2. 入库（任务卡①）
~/miniconda3/envs/llm/bin/python ingest.py

# 3. 日报（任务卡②）
export LLM_API_KEY="你的key"   # 智谱开放平台；或改 LLM_BASE_URL 指向任意 OpenAI 兼容服务
~/miniconda3/envs/llm/bin/python digest.py
```

## 四、注意事项

- 网络：arXiv API 一般可直连；若超时，`export http_proxy=... https_proxy=...`（走 Windows 梯子）。
- BGE-M3 首次运行会从 HF 下载模型（~2GB）；若 HF 慢：`export HF_ENDPOINT=https://hf-mirror.com`。
- 分工约定：**ingest / digest / eval 设计 / 安全实验由你亲手写**（fetch 是示例模式），AI 负责 review、debug、架构讨论——面试要能守住每一行。
- 里程碑：M0 今天 → M1 抓取今晚 → M2 入库明晚 → M3 日报 10/12 → M4 评测层下周 → M5 安全层下下周 → 推 GitHub（走 ghproxy）上简历。
