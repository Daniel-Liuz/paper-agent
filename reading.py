"""reading.py — 深度阅读 v2：全文精读 + mermaid 方法图 + 引用网络。

v2 相比 v1 的三个升级：
1. 全文优先：有 pdf_url 时下载 PDF → PyMuPDF 提取正文（缓存），解读基于全文而非仅摘要；
2. 方法流程图：解读的"核心方法"节强制带 mermaid flowchart，前端渲染成图；
3. 引用网络：通过 OpenAlex cites: 反查"谁引用了它"，作为相关工作的补充信号。
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import markdown

from config import LLM_BASE_URL, LLM_MODEL, LLM_API_KEY, PROFILE_NAME

READINGS_DIR = None  # 延迟初始化（config 无 OUT_DIR 引入顺序问题，见 get_reading）
FULLTEXT_DIR = None
PDF_DIR = None
UA = {"User-Agent": "paper-agent/0.2 (research-use)"}

READING_SYSTEM = (
    "你是资深研究员的论文精读助手。基于给定的论文（全文或摘要）与相关工作信息，"
    "输出 markdown 中文深度解读。结构严格使用以下编号标题：\n"
    "## 1 三十秒速览\n"
    "## 2 核心方法\n"
    "## 3 主要贡献\n"
    "## 4 局限与疑问\n"
    f"## 5 与「{PROFILE_NAME}」方向的关联\n"
    "## 6 最相关的工作\n"
    "写作要求：\n"
    "- 「## 1 三十秒速览」固定四项、每项独立成段（行间留空行），每段以加粗标签开头，"
    "内容各一句话：**问题**：论文要解决什么；**思路**：核心方法一句话；"
    "**关键数字**：最重要的实验数字（无则写「原文未提及」）；**值不值得读**：给读者的建议；\n"
    "- 「## 2 核心方法」开头先放 mermaid 图，再用短段落按图中阶段逐段讲解。"
    "mermaid 代码块（```mermaid 开头，flowchart TD 或 LR）用不超过 8 个节点描绘方法的数据流/模块关系，"
    "节点标签用简短中文、不要用引号包裹；信息只够画研究流程时就画研究流程，不要硬凑模块图；"
    "图后的每个自然段只讲图中的一个阶段，段落顺序与图的数据流方向一致；\n"
    "- 「## 3 主要贡献」若论文含关键实验数字，先给一个 markdown 表格"
    "（表头：对比对象｜指标｜数字），表格后再用短段落解读数字含义；\n"
    "- 证据锚点：「三十秒速览」的关键数字、以及「## 3 主要贡献」表格后的解读段落，"
    "必须在段后紧跟一条 blockquote 原文佐证，格式为：\n"
    "> 原文：〈英文原句，直接摘自给定文本，最多两句，保留原数字〉\n"
    "没有可摘的原文就整条省略，禁止改写或拼凑原文；\n"
    "- 每个自然段不超过 3 句话；行文用连贯短段落，尽量不用列表符号（- / *）；\n"
    "- 只依据给定信息，不编造；信息不足处写「原文未提及」。"
)


def find_paper(paper_id: str) -> dict | None:
    from config import DATA_DIR
    for path in sorted(DATA_DIR.glob("raw_*.jsonl"), reverse=True):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            p = json.loads(line)
            if (p.get("paper_id") or p.get("arxiv_id")) == paper_id:
                return p
    return None


def _http_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _ensure_pdf(paper: dict) -> Path | None:
    """拿到 PDF 本地路径：命中 outputs/pdfs/ 缓存直接返回，否则下载（10MB 上限）。
    正文抽取和原图抽取共用这份缓存，避免重复下载。"""
    from config import OUT_DIR
    global PDF_DIR
    PDF_DIR = OUT_DIR / "pdfs"
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    pid = paper.get("paper_id") or paper.get("arxiv_id")
    path = PDF_DIR / f"{pid}.pdf"
    if path.exists():
        return path
    pdf_url = paper.get("pdf_url") or ""
    if not pdf_url.startswith("http"):
        return None
    try:
        path.write_bytes(_http_get(pdf_url, timeout=90)[: 10 * 1024 * 1024])
        print(f"[pdf] 下载成功 {pid}（{path.stat().st_size // 1024} KB）")
        return path
    except Exception as err:
        print(f"[pdf] 下载失败 {pid}: {err}")
        return None


def get_fulltext(paper: dict) -> str | None:
    """下载 PDF 提取正文（缓存到 outputs/fulltexts/）。失败返回 None → 退回摘要模式。"""
    global FULLTEXT_DIR
    from config import OUT_DIR
    FULLTEXT_DIR = OUT_DIR / "fulltexts"
    FULLTEXT_DIR.mkdir(parents=True, exist_ok=True)
    pid = paper.get("paper_id") or paper.get("arxiv_id")
    cache = FULLTEXT_DIR / f"{pid}.txt"
    if cache.exists():
        text = cache.read_text(encoding="utf-8")
        return text or None
    pdf = _ensure_pdf(paper)
    if pdf is None:
        return None
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(pdf)
        text = "\n".join(page.get_text() for page in doc[:20]).strip()[:12000]
        cache.write_text(text, encoding="utf-8")
        print(f"[fulltext] 提取成功 {pid}（{len(text)} 字符）")
        return text or None
    except Exception as err:
        print(f"[fulltext] 失败 {pid}: {err}")
        return None


def extract_figures(paper: dict, max_figures: int = 4) -> list[dict]:
    """从 PDF 抽论文原图（缓存 outputs/figures/{pid}_p{页码}.{序号}.png）。
    只取大图（过滤 logo/小图标），任何失败都静默返回 []，不影响解读主流程。"""
    import fitz
    from config import OUT_DIR
    pid = paper.get("paper_id") or paper.get("arxiv_id")
    fig_dir = OUT_DIR / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    cached = sorted(fig_dir.glob(f"{pid}_p*.*.png"))
    if cached:  # 幂等：页码编码在文件名里，重访不重抽
        out = []
        for p in cached[:max_figures]:
            page = p.name.split("_p", 1)[1].split(".", 1)[0]
            out.append({"name": p.name, "page": int(page) if page.isdigit() else 0})
        return out
    pdf = _ensure_pdf(paper)
    if pdf is None:
        return []
    try:
        doc = fitz.open(pdf)
        out, seen = [], set()
        for pno in range(min(len(doc), 20)):
            for img in doc[pno].get_images(full=True):
                xref = img[0]
                if xref in seen:
                    continue
                seen.add(xref)
                pix = fitz.Pixmap(doc, xref)
                if pix.width < 200 or pix.height < 150:  # logo/装饰线的尺寸下限
                    continue
                if pix.colorspace and pix.n > 4:  # CMYK 等色彩空间转 RGB
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                name = f"{pid}_p{pno + 1}.{len(out) + 1}.png"
                pix.save(fig_dir / name)
                out.append({"name": name, "page": pno + 1})
                if len(out) >= max_figures:
                    return out
        print(f"[figures] {pid}: 抽取 {len(out)} 张原图")
        return out
    except Exception as err:
        print(f"[figures] 失败 {pid}: {err}")
        return []


def citing_papers(pid: str, n: int = 3) -> list[dict]:
    """OpenAlex 反查引用：谁引了这篇（取最新 n 篇）。pid 形如 W123…"""
    url = ("https://api.openalex.org/works?filter=cites:" + pid
           + f"&sort=publication_date:desc&per-page={n}"
             "&select=id,title,publication_date,doi&mailto=paper-agent@example.com")
    try:
        data = json.loads(_http_get(url, timeout=30))
        return [{"paper_id": w["id"].rsplit("/", 1)[-1],
                 "title": w.get("title") or "",
                 "published": w.get("publication_date") or "",
                 "abs_url": w.get("doi") or w["id"]}
                for w in data.get("results", [])]
    except Exception as err:
        print(f"[cites] 失败 {pid}: {err}")
        return []


def _llm(system: str, user: str, max_tokens: int = 1600, retries: int = 2) -> str:
    body = json.dumps({
        "model": LLM_MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.3, "max_tokens": max_tokens,
    }).encode("utf-8")
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(3 * attempt)  # 上游 429/503 过载：退避后重试，一次抖动不打穿整页
        req = urllib.request.Request(
            LLM_BASE_URL.rstrip("/") + "/chat/completions", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {LLM_API_KEY}"})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as err:
            if err.code in (429, 500, 502, 503, 504) and attempt < retries:
                print(f"[llm] HTTP {err.code}，退避 {3 * attempt}s 重试（{attempt + 1}/{retries}）")
                continue
            raise


def get_reading(paper: dict, related: list[dict], fulltext: str | None = None,
                citations: list[dict] | None = None) -> str:
    """返回解读 HTML（缓存幂等）。fulltext 有值走全文精读，否则摘要模式。"""
    from config import OUT_DIR
    global READINGS_DIR
    READINGS_DIR = OUT_DIR / "readings"
    READINGS_DIR.mkdir(parents=True, exist_ok=True)
    cache = READINGS_DIR / f'{paper["paper_id"]}.html'
    if cache.exists():
        return cache.read_text(encoding="utf-8")

    if fulltext:
        body_block = "【论文全文（PDF 提取，可能截断）】\n" + fulltext[:12000]
    else:
        body_block = "【论文摘要（无全文可用）】\n" + paper.get("abstract", "")[:1800]

    related_block = "\n".join(
        f"- {r['title']}（{r.get('published', '')}，向量相似度 {r.get('score', '')}）"
        for r in related) or "（无）"
    cite_block = "\n".join(
        f"- {c['title']}（{c.get('published', '')}，引用了本文）"
        for c in (citations or [])) or "（暂无记录）"

    user_prompt = (
        f"{body_block}\n\n【向量检索的相关论文】\n{related_block}\n\n"
        f"【最新引用本文的论文（OpenAlex 反查）】\n{cite_block}"
    )
    started = time.perf_counter()
    text = _llm(READING_SYSTEM, user_prompt)
    html = markdown.markdown(text, extensions=["tables", "fenced_code"])
    cache.write_text(html, encoding="utf-8")
    print(f'深度解读 v2 已生成：{paper["title"][:50]} '
          f'（{"全文" if fulltext else "摘要"}模式，{time.perf_counter() - started:.1f}s）')
    return html
