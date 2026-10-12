"""集中配置：改这里，不改各个脚本。"""
import os
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
OUT_DIR = ROOT / "outputs"
OUT_DIR.mkdir(exist_ok=True)

# ---------- 抓取 ----------
ARXIV_CATEGORIES = ["cs.AI", "cs.CL", "cs.CR", "cs.LG"]  # AI/NLP/安全/学习理论
DEFAULT_MAX_RESULTS = 100

# 兴趣关键词：日报相关性初筛 + 后续评测用
INTEREST_KEYWORDS = [
    "agent", "large language model", "LLM", "prompt injection",
    "tool use", "multi-agent", "evaluation", "benchmark", "safety", "memory",
]

# 内容标签体系：digest 打分时 LLM 从中选 1-3 个；网站日报区据此筛选（用户订阅体验）
PAPER_TAGS = [
    "Agent安全", "Agent评测", "多智能体", "工具调用", "RAG与检索",
    "记忆机制", "推理能力", "基准与数据集", "RL训练", "多模态", "LLM应用",
]

# ---------- 入库 ----------
EMBED_MODEL = "BAAI/bge-m3"
FAISS_INDEX = DATA_DIR / "papers.index"
META_STORE = DATA_DIR / "papers_meta.pkl"
CHUNK_CHARS = 500  # 物理截断：防长文本 O(L^2) 的老教训（来自你的构建指南）

# ---------- 生成（日报）----------
# .env 支持：密钥不进代码库（.gitignore 已排除 .env）
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")  # OpenAI 兼容
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")  # .env 或 export LLM_API_KEY=sk-...
