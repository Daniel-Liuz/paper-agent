"""embedder.py — 向量化抽象层：本地模型 或 API，二选一（同一向量空间）。

为什么需要它：部署目标是 2GiB 内存的 ECS，装不下 2.3GB 的本地 BGE-M3；
SiliconFlow 免费托管 BAAI/bge-m3（与本地完全同一个模型），API 版让服务器
零模型内存跑检索。向量空间一致 → 本地建的索引和服务器查的查询可以混用。

配置（.env 或环境变量）：
    EMBED_PROVIDER=local   # 本地 GPU/CPU 版（默认，开发机用）
    EMBED_PROVIDER=api     # API 版（服务器用）
    EMBED_API_KEY=sk-...   # SiliconFlow key（api 模式必填）
"""
import json
import os
import urllib.request

import numpy as np

from config import EMBED_MODEL

PROVIDER = os.environ.get("EMBED_PROVIDER", "local")
API_BASE = os.environ.get("EMBED_API_BASE", "https://api.siliconflow.cn/v1")
API_KEY = os.environ.get("EMBED_API_KEY", "")


class LocalEmbedder:
    """开发机：sentence-transformers 直跑（WSL 有 GPU，已缓存模型）。"""

    def __init__(self):
        from sentence_transformers import SentenceTransformer  # 延迟导入：API 模式不需要 torch
        self.model = SentenceTransformer(EMBED_MODEL)

    def encode(self, texts: list[str]) -> np.ndarray:
        print(f"[embedder:local] 编码 {len(texts)} 条…")
        return self.model.encode(
            texts, batch_size=8, normalize_embeddings=True,
        ).astype("float32")


class ApiEmbedder:
    """服务器：OpenAI 兼容 /embeddings（SiliconFlow 托管同一 BGE-M3）。"""

    def encode(self, texts: list[str]) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), 16):
            chunk = texts[i:i + 16]
            body = json.dumps({"model": EMBED_MODEL, "input": chunk}).encode("utf-8")
            req = urllib.request.Request(
                API_BASE.rstrip("/") + "/embeddings", data=body, method="POST",
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {API_KEY}"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            data["data"].sort(key=lambda d: d["index"])  # 保证与输入顺序一致
            out.extend(d["embedding"] for d in data["data"])
        arr = np.asarray(out, dtype="float32")
        arr /= np.linalg.norm(arr, axis=1, keepdims=True) + 1e-12  # 保险起见手动归一
        return arr


def get_embedder():
    if PROVIDER == "api":
        if not API_KEY:
            raise RuntimeError("EMBED_PROVIDER=api 但缺少 EMBED_API_KEY（.env）")
        return ApiEmbedder()
    return LocalEmbedder()
