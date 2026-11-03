"""conftest.py — 测试环境强制离线：fake 工具层，不触索引/embedder/网络/API key。"""
import os
import sys
from pathlib import Path

os.environ["AGENT_FAKE_TOOLS"] = "1"
sys.path.insert(0, str(Path(__file__).parent.parent))
