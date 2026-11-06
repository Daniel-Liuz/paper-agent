"""output_guard.py — 独立于 LLM 的输出校验组件（输出侧防线，2026-11-06）。

动机（真实实验驱动）：Tool Policy 管得住"执行层工具调用"，管不住模型把攻击者
指定的外部链接/不实引用写进最终报告（2026-10-27 真实实验：完成任务中 8-9/15
出现 canary 外链）。本组件在报告提交后做确定性校验：

策略（可测试、不含任何针对特定评测载荷的特殊规则）：
  P1 域名白名单：报告中的 URL（markdown 链接与裸 URL）必须落在可信域
     （arxiv.org / openalex.org / doi.org / semanticscholar.org / 本项目演示站）。
     不在白名单 → 阻断（block 模式）或标记（warn 模式），记录审计原因。
  P2 引用落地：报告中出现的 paper_id（W\\d{6,}）必须在本任务轨迹中真实
     检索/阅读过——防止"编造不存在的引用"（另一类输出幻觉，与链接外传同构）。
  P3 不误拦：合法学术域全放行；纯文本 paper_id 引用不是 URL，不受 P1 影响。

与 policy.py 的分工：policy=执行前（工具调用层），output_guard=提交后（内容层）。
两层都不依赖提示词，均为确定性代码，均可单测证明。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

TRUSTED_DOMAINS = {
    "arxiv.org", "www.arxiv.org",
    "openalex.org", "api.openalex.org",
    "doi.org", "www.doi.org", "dx.doi.org",
    "semanticscholar.org", "www.semanticscholar.org",
    "scholar.google.com",
    "121.43.155.169",            # 本项目演示站（IP 形式）
}

_URL_IN_MD = re.compile(r"\]\((https?://[^)\s]+)\)")            # [text](url)
_URL_BARE = re.compile(r"(?<!\()\bhttps?://[^\s)\]<>\"']+")     # 裸 URL
_PAPER_ID = re.compile(r"\bW\d{6,}\b")


@dataclass
class GuardVerdict:
    clean: bool                                  # 无任何违规
    action: str = "pass"                         # pass / warn / block
    blocked_urls: list[str] = field(default_factory=list)
    unbacked_paper_ids: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


class OutputGuard:
    def __init__(self, mode: str = "block",
                 trusted_domains: Optional[set[str]] = None):
        assert mode in ("warn", "block")
        self.mode = mode
        self.domains = trusted_domains or TRUSTED_DOMAINS
        self.audit: list[dict] = []

    def check(self, report: str, trajectory_paper_ids: Optional[set[str]] = None,
              task_id: str = "") -> GuardVerdict:
        """report=最终报告文本；trajectory_paper_ids=本任务轨迹中检索/阅读过的论文 id。"""
        v = GuardVerdict(clean=True)
        urls = set(_URL_IN_MD.findall(report)) | set(_URL_BARE.findall(report))
        for url in urls:
            host = re.sub(r"^https?://", "", url).split("/")[0].lower()
            host = host.rsplit(":", 1)[0] if ":" in host else host   # 剥端口
            if host not in self.domains:
                v.blocked_urls.append(url)
                v.reasons.append(f"外部域未在白名单: {host}")
        if trajectory_paper_ids is not None:
            cited = set(_PAPER_ID.findall(report))
            v.unbacked_paper_ids = sorted(cited - trajectory_paper_ids)
            for pid in v.unbacked_paper_ids:
                v.reasons.append(f"paper_id 无轨迹支撑: {pid}")
        if v.blocked_urls or v.unbacked_paper_ids:
            v.clean = False
            v.action = self.mode
            self.audit.append({"task_id": task_id, "action": self.mode,
                               "blocked_urls": v.blocked_urls,
                               "unbacked_paper_ids": v.unbacked_paper_ids,
                               "reasons": v.reasons})
        return v

    def sanitize(self, report: str, verdict: GuardVerdict) -> str:
        """block 模式下移除违规 URL（替换为占位符）；paper_id 编造仅标记不删文本。"""
        if verdict.action != "block":
            return report
        out = report
        for url in verdict.blocked_urls:
            out = out.replace(url, "[已拦截:未信任外链]")
        return out
