"""fac_rag.chunking_v2.probe —— 第①步：识别文档族 + 出体检单（技术版 §4）。

M1 只做「认文件」，不做切块：读原始行 → 16 个行级特征 → 族判定 + 置信度 +
体检单（flags/warnings）。M2 起 regions/units 才消费这份报告。

16 个行级特征（按技术版 §4 的 5 组 + 1 组文档标记）
--------------------------------------------------
1. 编号形态（4）：``article_lines``（第X条） / ``decimal_lines``（小数编号） /
   ``chapter_section_lines``（第X章/节） / ``appendix_label_lines``（附录/附件编号）；
2. 标题形态（3）：``first_line_heading`` / ``heading_level_counts`` / ``clause_style``
   （标题式 vs 行内式，用于定 ``heading_mode``）；
3. 结构标记（6）：``toc_head_lines`` / ``toc_entry_lines`` / ``page_forms``（单空格 /
   全角 / 点线 / 括号四种页码形态） / ``front_markers`` / ``commentary_lines`` /
   ``references_lines``；
4. 表格形态（1）：``table_markers``（管道表 / HTML 表 / 表题 / 续表标记）；
5. 排版噪声（1）：``noise_lines``（``\\(`` 转义 / ``**`` / HTML 标签 / 页码尾行）；
6. 文档标记（1）：``document_markers``（一级编号行 / 标准结构标记 / YAML front matter）。

族判定（技术版 §4 规则 ①–⑦）
----------------------------
* ``articles>=3 and decimals==0`` → statute（置信度 0.95）；
* ``decimals>=3 and articles==0`` → standard（0.90）；
* 两者都 >0 → mixed（0.60，M2 按章节边界拆后分别识别）；
* ``articles==0 and top_numbering>=3 and standard_marker>=1`` → standard（0.70，
  **M1 实测补充的兜底规则**：GB/T 13861-2022 这类正文以代码表为主、没有多级
  十进制编号的标准，按规则②会落到 generic，与生产语料 60/60 的验收口径冲突）；
* 其余 → generic（0.55，报警提示人工复核）。

文件名/文件夹名只作**弱先验**：与内容判定冲突时只报警、不静默采用（规则⑦）。
``confidence``=族置信度；``profile`` 字段留空，由
:func:`fac_rag.chunking_v2.profiles.select_profile` 填。
"""

from __future__ import annotations

from .patterns import RE_LATEX_ESCAPE, RE_MD_BOLD, RE_TABLE_HTML, RE_TABLE_PIPE

import json
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

#: 体检单/报告版本（写进 manifest 与报表，便于回溯口径）
PROBE_VERSION = 1

#: 16 个行级特征（顺序固定；tests 会断言键集合）
FEATURE_KEYS: tuple[str, ...] = (
    # 编号形态
    "article_lines",
    "decimal_lines",
    "chapter_section_lines",
    "appendix_label_lines",
    # 标题形态
    "first_line_heading",
    "heading_level_counts",
    "clause_style",
    # 结构标记
    "toc_head_lines",
    "toc_entry_lines",
    "page_forms",
    "front_markers",
    "commentary_lines",
    "references_lines",
    # 表格形态
    "table_markers",
    # 排版噪声
    "noise_lines",
    # 文档标记
    "document_markers",
)

#: 特征分组（技术版 §4 的 5 组 + 文档标记）
FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "编号形态": (
        "article_lines",
        "decimal_lines",
        "chapter_section_lines",
        "appendix_label_lines",
    ),
    "标题形态": ("first_line_heading", "heading_level_counts", "clause_style"),
    "结构标记": (
        "toc_head_lines",
        "toc_entry_lines",
        "page_forms",
        "front_markers",
        "commentary_lines",
        "references_lines",
    ),
    "表格形态": ("table_markers",),
    "排版噪声": ("noise_lines",),
    "文档标记": ("document_markers",),
}

# ---------------------------------------------------------------------------
# 正则：编号/标题/区域标记
# ---------------------------------------------------------------------------

# 语料正文大量用 markdown 标题写条号（如 ``### 5.5.1 …``），所以所有行首编号
# 正则都允许可选 ``#{0,6}`` 前缀——这是 M1 普查后的关键修正（见施工记录）。
_HEAD = r"^\s*#{0,6}\s*"

RE_ARTICLE = re.compile(_HEAD + r"第([零一二三四五六七八九十百千两0-9]+)条[　\s]*(.*)$")
RE_DECIMAL = re.compile(_HEAD + r"(\d+(?:\.\d+)+)(?:[　\s]|$)")
RE_TOP_NUMBER = re.compile(_HEAD + r"(\d+)[　\s]+(\S.*)$")
RE_CHAPTER = re.compile(_HEAD + r"第[零一二三四五六七八九十百千两0-9]+章[　\s]")
RE_SECTION = re.compile(_HEAD + r"第[零一二三四五六七八九十百千两0-9]+节[　\s]")

RE_HEADING = re.compile(r"^\s*(#{1,6})\s+\S")
RE_HEADING_LINE = re.compile(r"^\s*(#{1,6})\s+(.*)$")
#: 标题式条款：条号写在 markdown 标题里（如 ``### 5.5.1 …``），必须真的有 #
RE_TITLED_CLAUSE = re.compile(
    r"^\s*#{1,6}\s*(?:第[零一二三四五六七八九十百千两0-9]+条|\d+(?:\.\d+)*)[　\s]"
)
#: 行内式条款：条号在行首、不带 #（如 ``第一条　…``、``5.5.1　…``）
RE_INLINE_CLAUSE = re.compile(
    r"^\s*(?:第[零一二三四五六七八九十百千两0-9]+条|\d+(?:\.\d+)*)[　\s]"
)

RE_APPENDIX_LABEL = re.compile(
    _HEAD + r"附\s*[录件]\s*[A-Z一二三四五六七八九十]?(?:[　\s（(]|$)"
)
RE_APPENDIX_UNIT = re.compile(_HEAD + r"[A-Z]\.\d+(?:\.\d+)*[　\s]")

RE_TOC_HEAD = re.compile(r"^\s*#{0,6}\s*目\s*[次录]\s*$")
RE_TOC_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
RE_TOC_FIRST = re.compile(
    r"^(?:前\s*言|引\s*言|第[零一二三四五六七八九十百千两0-9]+[章节条]"
    r"|附\s*[录件]|参考文\s*献|目\s*[次录]|\d+(?:\.\d+)*[　\s])"
)

RE_PAGE_SINGLE = re.compile(r"(?<![ \t　])[ ]\d{1,4}\s*$")
RE_PAGE_FULLWIDTH = re.compile(r"[　]\d{1,4}\s*$")
RE_PAGE_DOT = re.compile(r"(?:\.{3,}|…{2,}|·{3,})\s*\d{1,4}\s*$")
RE_PAGE_PAREN = re.compile(r"[（(]\s*\d{1,4}\s*[）)]\s*$")

RE_FRONT = re.compile(_HEAD + r"(?:前\s*言|引\s*言|.*公\s*告)\s*$")
RE_DECREE = re.compile(r"国务院令")
RE_PUBLISH = re.compile(
    r"^\s*[（(]\s*\d{4}\s*年|(?:发布日期|发布时间|公布日期)[：:]"
)
RE_EFFECTIVE = re.compile(
    r"(?:实施日期|施行日期|生效日期)[：:]"
    r"|自\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日起(?:施行|实施)"
)
RE_COMMENTARY = re.compile(r"条\s*文\s*说\s*明")
RE_REFERENCES = re.compile(r"(?:参考文\s*献|引用标准名录|引用文件)")

RE_TABLE_CAPTION = re.compile(_HEAD + r"表\s*[A-Z]?\s*\d")
RE_CONTINUATION = re.compile(r"(?:（续）|\(续\)|续\s*表|表\s*[A-Z]?\d+\s*[（(]续[）)])")

RE_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
RE_PAGE_TAIL = re.compile(r"^\s*[-—–]\s*\d{1,4}\s*[-—–]\s*$|第\s*\d+\s*页")

#: 标准结构标记：标准号 / ICS/CCS / 标准起草套话（用于 standard 兜底判定）
RE_STD_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"标准\s*号[：:]"),
    re.compile(r"^\s*-?\s*ICS[　\s]"),
    re.compile(r"\bCCS[　\s]*[A-Z]"),
    re.compile(r"\bGB\s*/?\s*T?\s*\d"),
    re.compile(r"本文件(?:按照|代替|由)"),
    re.compile(r"国家标准化管理委员会"),
    re.compile(r"中华人民共和国国家标准"),
    re.compile(r"标准化工作导则"),
    re.compile(r"GB/\s*T\s*1\.1"),
)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _front_matter_span(lines: Sequence[str]) -> tuple[int, int]:
    """YAML front matter 的行区间 ``[start, end)``；没有则 ``(0, 0)``。"""
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or lines[i].strip() != "---":
        return (0, 0)
    for j in range(i + 1, min(len(lines), i + 80)):
        if lines[j].strip() == "---":
            return (i, j + 1)
    return (0, 0)


def _body_start(lines: Sequence[str]) -> int:
    """跳过 YAML front matter 与空行后的首个正文行号。"""
    _, fm_end = _front_matter_span(lines)
    i = fm_end
    while i < len(lines) and not lines[i].strip():
        i += 1
    return i


def first_heading_text(lines: Sequence[str]) -> str:
    """正文首行若是 markdown 标题，返回标题文本（去掉 ``#``）；否则空串。"""
    i = _body_start(lines)
    if i >= len(lines):
        return ""
    m = RE_HEADING_LINE.match(lines[i])
    return m.group(2).strip() if m else ""


def _looks_toc_entry(line: str) -> bool:
    """目次条目：带项目符号的章节/编号行，或不带符号但「标题 + 页码」的行。"""
    s = line.strip()
    if not s:
        return False
    m = RE_TOC_BULLET.match(line)
    if m:
        return bool(RE_TOC_FIRST.match(m.group(1).strip()))
    has_page = bool(
        RE_PAGE_DOT.search(line)
        or RE_PAGE_PAREN.search(line)
        or RE_PAGE_FULLWIDTH.search(line)
        or RE_PAGE_SINGLE.search(line)
    )
    return has_page and bool(RE_TOC_FIRST.match(s))


def _toc_info(lines: Sequence[str]) -> tuple[int | None, int]:
    """返回 ``(目次标题行号, 目次条目数)``；没有目次标题时 ``(None, 0)``。"""
    head: int | None = None
    for i, line in enumerate(lines):
        if RE_TOC_HEAD.match(line):
            head = i
            break
    if head is None:
        return (None, 0)
    entries = 0
    for i in range(head + 1, min(len(lines), head + 1 + 600)):
        line = lines[i]
        if not line.strip():
            continue
        if not _looks_toc_entry(line):
            break
        entries += 1
    return (head, entries)


def _page_forms(lines: Sequence[str]) -> dict[str, int]:
    """页码形态计数：单空格 / 全角 / 点线 / 括号（只看较短的行，避开正文长句）。"""
    forms = {"single_space": 0, "fullwidth": 0, "dot_leader": 0, "paren": 0}
    for line in lines:
        s = line.rstrip()
        if not s or len(s) > 120 or s.lstrip().startswith("|"):
            continue
        if RE_PAGE_DOT.search(s):
            forms["dot_leader"] += 1
        if RE_PAGE_PAREN.search(s):
            forms["paren"] += 1
        if RE_PAGE_FULLWIDTH.search(s):
            forms["fullwidth"] += 1
        if RE_PAGE_SINGLE.search(s):
            forms["single_space"] += 1
    return forms


def _count(lines: Iterable[str], pattern: re.Pattern[str]) -> int:
    return sum(1 for line in lines if pattern.search(line))


# ---------------------------------------------------------------------------
# 16 个特征
# ---------------------------------------------------------------------------


def extract_features(lines: Sequence[str]) -> dict[str, Any]:
    """抽取 16 个行级特征（纯函数，便于 tests 直接喂合成文本）。"""
    _, fm_end = _front_matter_span(lines)
    has_front_matter = fm_end > 0

    heading_levels: dict[str, int] = {str(level): 0 for level in range(1, 7)}
    for line in lines:
        m = RE_HEADING.match(line)
        if m:
            heading_levels[str(len(m.group(1)))] += 1

    toc_head, toc_entries = _toc_info(lines)

    titled = sum(1 for line in lines if RE_TITLED_CLAUSE.match(line))
    inline = sum(1 for line in lines if RE_INLINE_CLAUSE.match(line))

    page_forms = _page_forms(lines)

    top_numbering = sum(
        1 for line in lines if RE_TOP_NUMBER.match(line) and not RE_DECIMAL.match(line)
    )
    standard_markers = sum(
        1 for line in lines if any(marker.search(line) for marker in RE_STD_MARKERS)
    )

    features: dict[str, Any] = {
        # 1. 编号形态
        "article_lines": _count(lines, RE_ARTICLE),
        "decimal_lines": _count(lines, RE_DECIMAL),
        "chapter_section_lines": _count(lines, RE_CHAPTER) + _count(lines, RE_SECTION),
        "appendix_label_lines": _count(lines, RE_APPENDIX_LABEL) + _count(lines, RE_APPENDIX_UNIT),
        # 2. 标题形态
        "first_line_heading": bool(
            _body_start(lines) < len(lines)
            and RE_HEADING.match(lines[_body_start(lines)])
        ),
        "heading_level_counts": heading_levels,
        "clause_style": {"titled": titled, "inline": inline},
        # 3. 结构标记
        "toc_head_lines": _count(lines, RE_TOC_HEAD),
        "toc_entry_lines": toc_entries,
        "page_forms": page_forms,
        "front_markers": {
            "front": _count(lines, RE_FRONT),
            "decree": _count(lines, RE_DECREE),
            "publish_date": _count(lines, RE_PUBLISH),
            "effective_date": _count(lines, RE_EFFECTIVE),
        },
        "commentary_lines": _count(lines, RE_COMMENTARY),
        "references_lines": _count(lines, RE_REFERENCES),
        # 4. 表格形态
        "table_markers": {
            "pipe": _count(lines, RE_TABLE_PIPE),
            "html": _count(lines, RE_TABLE_HTML),
            "caption": _count(lines, RE_TABLE_CAPTION),
            "continuation": _count(lines, RE_CONTINUATION),
        },
        # 5. 排版噪声
        "noise_lines": {
            "latex_escape": _count(lines, RE_LATEX_ESCAPE),
            "md_bold": _count(lines, RE_MD_BOLD),
            "html_tag": _count(lines, RE_HTML_TAG),
            "page_tail": _count(lines, RE_PAGE_TAIL),
        },
        # 6. 文档标记
        "document_markers": {
            "top_numbering": top_numbering,
            "standard_marker": standard_markers,
            "yaml_front_matter": has_front_matter,
            "body_start": _body_start(lines),
            "toc_head_line": -1 if toc_head is None else toc_head,
        },
    }
    return features


# ---------------------------------------------------------------------------
# 族判定
# ---------------------------------------------------------------------------

FAMILY_LABELS = {
    "statute": "法规条式",
    "standard": "标准编号式",
    "generic": "通用兜底",
    "tabular": "表格/清单体",
}

#: 弱先验：只看文件夹/分组名，命中是「弱证据」，冲突只报警（技术版 §4 规则⑦）
_PRIOR_STANDARD = ("gb", "standard", "国标", "标准")
_PRIOR_STATUTE = ("law", "statute", "法规", "法律", "条例", "规章")


def prior_family(hint: str | Path | None) -> str | None:
    """从文件夹/分组名猜族（弱先验）；猜不出返回 ``None``。"""
    if hint is None:
        return None
    text = str(hint).strip().lower()
    if not text:
        return None
    if any(key in text for key in _PRIOR_STANDARD):
        return "standard"
    if any(key in text for key in _PRIOR_STATUTE):
        return "statute"
    return None


def _judge_family(
    features: dict[str, Any],
) -> tuple[str, float, list[str], bool, bool]:
    """返回 ``(family, confidence, warnings, is_mixed, is_fallback)``。"""
    articles = int(features["article_lines"])
    decimals = int(features["decimal_lines"])
    top = int(features["document_markers"]["top_numbering"])
    std_markers = int(features["document_markers"]["standard_marker"])
    warnings: list[str] = []

    if articles >= 3 and decimals == 0:
        return ("statute", 0.95, warnings, False, False)
    if decimals >= 3 and articles == 0:
        return ("standard", 0.90, warnings, False, False)
    if articles > 0 and decimals > 0:
        warnings.append(
            f"编号特征混杂（第X条 {articles} 行 / 小数编号 {decimals} 行）："
            "按 mixed 标记，M2 按章节边界拆分后分别识别（技术版 §4 规则③）"
        )
        return ("mixed", 0.60, warnings, True, False)
    if articles == 0 and top >= 3 and std_markers >= 1:
        warnings.append(
            f"标准族编号稀疏（decimal_lines=0），按标准结构标记兜底判 standard"
            f"（top_numbering={top}, standard_marker={std_markers}），置信度 0.70"
        )
        return ("standard", 0.70, warnings, False, True)
    tables = features["table_markers"]
    if articles == 0 and decimals == 0 and (
        tables["html"] > 0 or tables["pipe"] >= 5 or tables["caption"] > 0
    ):
        # 表格/清单体（裁量基准、目录、统计表等）：无条号、无编号层级，但表格信号明确。
        # 走 generic handler（同 parse_generic），只换 profile/阈值/置信度（D-068）。
        return ("tabular", 0.90, warnings, False, False)
    warnings.append(
        "未发现条款/编号特征（articles=0, decimals=0），按 generic 兜底"
        f"（置信度 0.55）：建议人工复核后补 profile（技术版 §4 规则④⑥）"
    )
    return ("generic", 0.55, warnings, False, False)


def _flatten_flags(
    features: dict[str, Any],
    *,
    family: str,
    heading_mode: str,
    prior: str | None,
    conflict: bool,
    is_mixed: bool,
    is_fallback: bool,
) -> dict[str, Any]:
    """把特征压成便于进 manifest/报表的「体检单 flags」（技术版 §4 示例形状）。"""
    page_forms = features["page_forms"]
    front = features["front_markers"]
    tables = features["table_markers"]
    noise = features["noise_lines"]
    return {
        # 结构标记
        "has_toc": bool(features["toc_head_lines"]),
        "toc_entries": int(features["toc_entry_lines"]),
        "single_space_pages": int(page_forms["single_space"]),
        "fullwidth_pages": int(page_forms["fullwidth"]),
        "dot_leader_pages": int(page_forms["dot_leader"]),
        "paren_pages": int(page_forms["paren"]),
        "has_front": bool(front["front"]),
        "decree_lines": int(front["decree"]),
        "publish_date_lines": int(front["publish_date"]),
        "effective_date_lines": int(front["effective_date"]),
        "has_appendix": bool(features["appendix_label_lines"]),
        "appendix_labels": int(features["appendix_label_lines"]),
        "has_commentary": bool(features["commentary_lines"]),
        "commentary_lines": int(features["commentary_lines"]),
        "has_references": bool(features["references_lines"]),
        "references_lines": int(features["references_lines"]),
        # 表格
        "has_tables": bool(tables["pipe"] or tables["html"] or tables["caption"]),
        "pipe_table_lines": int(tables["pipe"]),
        "html_table_lines": int(tables["html"]),
        "table_captions": int(tables["caption"]),
        "continuation_markers": int(tables["continuation"]),
        # 排版噪声
        "latex_escape_lines": int(noise["latex_escape"]),
        "md_bold_lines": int(noise["md_bold"]),
        "html_tag_lines": int(noise["html_tag"]),
        "page_tail_lines": int(noise["page_tail"]),
        # 判定结果
        "family": family,
        "heading_mode": heading_mode,
        "weak_prior": prior or "",
        "weak_prior_conflict": bool(conflict),
        "mixed_document": bool(is_mixed),
        "family_fallback": bool(is_fallback),
    }


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def probe_lines(
    lines: Sequence[str],
    *,
    path: str | Path | None = None,
    hint: str | Path | None = None,
) -> dict[str, Any]:
    """对一份文档的原始行做识别，返回「族 + 置信度 + 16 特征 + 体检单」。

    ``hint`` 是文件名/文件夹名的弱先验（与内容判定冲突时只写 warning，
    不改判定结果，见技术版 §4 规则⑦）。
    """
    all_lines = list(lines)
    features = extract_features(all_lines)
    family, family_confidence, warnings, is_mixed, is_fallback = _judge_family(features)

    style = features["clause_style"]
    heading_mode = "on" if style["titled"] > 0 and style["titled"] >= style["inline"] else "off"

    prior = prior_family(hint)
    conflict = prior is not None and prior != family
    if conflict:
        warnings.append(
            f"文件名/文件夹先验={prior} 与内容判定={family} 冲突：按内容判定"
            "（先验只作记录，不静默采用，技术版 §4 规则⑦）"
        )
    if features["commentary_lines"]:
        warnings.append(
            f"检测到条文说明 {features['commentary_lines']} 行："
            "按 D-039 在 regions 阶段识别后丢弃并记账（不进块）"
        )

    flags = _flatten_flags(
        features,
        family=family,
        heading_mode=heading_mode,
        prior=prior,
        conflict=conflict,
        is_mixed=is_mixed,
        is_fallback=is_fallback,
    )
    return {
        "version": PROBE_VERSION,
        "path": str(path) if path is not None else "",
        "hint": str(hint) if hint is not None else "",
        "family": family,
        "family_confidence": family_confidence,
        "confidence": family_confidence,   # select_profile 会覆盖成「最终 profile 置信度」
        "profile": "",                     # 由 select_profile 填
        "heading_mode": heading_mode,
        "features": features,
        "flags": flags,
        "warnings": warnings,
    }


def probe_text(
    text: str,
    *,
    path: str | Path | None = None,
    hint: str | Path | None = None,
) -> dict[str, Any]:
    """同 :func:`probe_lines`，只是入口换成整段文本。"""
    return probe_lines(text.splitlines(), path=path, hint=hint)


def probe_file(
    path: str | Path,
    *,
    hint: str | Path | None = None,
) -> dict[str, Any]:
    """读文件并做识别；``hint`` 缺省取父目录名（文件夹弱先验）。"""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    return probe_text(text, path=p, hint=path_hint(p) if hint is None else hint)


def path_hint(path: str | Path) -> str:
    """文件夹弱先验：文件所在目录名（语料按文件夹分组，见 configs/config.yaml）。"""
    return Path(path).parent.name


def dumps_report(report: dict[str, Any], *, indent: int = 2) -> str:
    """把体检单序列化成 JSON 字符串（manifest / 报表用，ensure_ascii=False）。"""
    return json.dumps(report, ensure_ascii=False, indent=indent)


__all__ = [
    "FEATURE_GROUPS",
    "FEATURE_KEYS",
    "FAMILY_LABELS",
    "PROBE_VERSION",
    "dumps_report",
    "extract_features",
    "first_heading_text",
    "path_hint",
    "prior_family",
    "probe_file",
    "probe_lines",
    "probe_text",
]
