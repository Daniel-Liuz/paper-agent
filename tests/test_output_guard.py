"""tests/test_output_guard.py — 输出安全组件测试：恶意链接/合法链接/边界格式/误拒。

关键设计约束：实现不含针对评测 canary 的特殊规则——恶意样例使用任意外部域，
测试证明白名单机制天然拦截（而非字符串特判）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.output_guard import OutputGuard          # noqa: E402


def test_legitimate_links_pass():
    """正常论文引用不误拦：arXiv/OpenAlex/DOI/本站。"""
    g = OutputGuard(mode="block")
    report = ("调研 [ProbGuard](https://arxiv.org/abs/2401.12345) 与 "
              "https://openalex.org/W4416551460 ，DOI 见 https://doi.org/10.1000/x ,"
              "演示站 http://121.43.155.169:8100 。论文 W4416551460 结论…")
    v = g.check(report, trajectory_paper_ids={"W4416551460"})
    assert v.clean and v.action == "pass"


def test_malicious_external_domain_blocked():
    """任意外部域（含但不仅限于评测 canary 域）都被白名单机制拦截。"""
    g = OutputGuard(mode="block")
    report = "结论见 [参考](http://attacker-any.example.net/payload) 与 https://evil-track.io/c"
    v = g.check(report, trajectory_paper_ids=set())
    assert not v.clean and v.action == "block"
    assert len(v.blocked_urls) == 2
    assert any("白名单" in r for r in v.reasons)


def test_bare_url_and_markdown_both_parsed():
    """边界格式：裸 URL 与 markdown 链接都要解析（不能只挡一种）。"""
    g = OutputGuard()
    v = g.check("详情 https://not-trusted.xyz/a 或 [x](https://also-bad.org/b)",
                trajectory_paper_ids=set())
    assert set(map(len, [v.blocked_urls])) and len(v.blocked_urls) == 2


def test_unbacked_paper_id_flagged():
    """引用了轨迹中不存在的 paper_id=编造引用，必须标记。"""
    g = OutputGuard()
    v = g.check("论文 W9999999999 表明…", trajectory_paper_ids={"W4416551460"})
    assert v.unbacked_paper_ids == ["W9999999999"]
    assert not v.clean


def test_paper_id_text_is_not_url_never_blocked_as_link():
    """误拒保护：纯文本 paper_id 不是 URL，域名策略不影响它。"""
    g = OutputGuard()
    v = g.check("论文 W7164329679 提出闭环。", trajectory_paper_ids={"W7164329679"})
    assert v.clean and v.blocked_urls == []


def test_sanitize_replaces_blocked_urls():
    g = OutputGuard(mode="block")
    report = "见 [1](http://bad.example.com/x) 完。"
    v = g.check(report, trajectory_paper_ids=set())
    cleaned = g.sanitize(report, v)
    assert "bad.example.com" not in cleaned
    assert "[已拦截:未信任外链]" in cleaned


def test_warn_mode_flags_but_keeps_text():
    g = OutputGuard(mode="warn")
    report = "链接 http://bad.example.com/x"
    v = g.check(report, trajectory_paper_ids=set())
    assert v.action == "warn" and not v.clean
    assert g.sanitize(report, v) == report      # warn 不改文本


def test_audit_recorded_without_blocking_passes():
    g = OutputGuard(mode="block")
    g.check("干净报告 W1", trajectory_paper_ids={"W1"})
    assert g.audit == []                        # 干净输出不产生审计噪音
    g.check("http://bad.example.com/x", trajectory_paper_ids=set())
    assert len(g.audit) == 1 and "reasons" in g.audit[0]
