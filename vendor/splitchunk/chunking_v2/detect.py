# -*- coding: utf-8 -*-
"""fac_rag.chunking_v2.detect —— P0 结构检测器（auto 档的判别核心）。

问题背景：现状（v2）的切分单位由 ``profile.family`` 决定 —— probe 给整份文档
贴一个族标签（statute / standard / generic），再按族走写死的 handler。族判错、
或没有对应 profile 时，内容再像条款也会被切成段落（实测：安全生产法 119 条被
切成 283 段）。

本模块把「按族选 handler」换成「按证据选切分单位」：给定一段区域文本，并行跑
若干检测器，每个检测器输出自己的证据（覆盖率 / 编号规则性 / 样本量 / 字符占比）
与候选单位；``choose_region_detector`` 用「门槛 + 优先级」选出本区域该用哪种
单位。选出的单位规格（dict）由 :func:`units._parse_auto` 转成 ``Unit``。

设计约束：

* **纯文本模块**：只吃 ``list[str]``，返回规格 dict / 证据 dict；不 import
  ``units`` / ``model`` / ``render``（避免循环依赖）。
* 行首正则与 ``units.py`` 的同款判断保持一致的语义（中文数字、十进制编号、
  附录字母编号）；本模块保留独立副本。
* 判定是**区域级**的（P0）；更细的「区域内再分段判」留给 P1。
"""

from __future__ import annotations

from .patterns import (
    RE_ARTICLE,
    RE_TABLE_HTML as RE_HTML_TABLE,
    RE_TABLE_PIPE as RE_PIPE_ROW,
)
from .textutil import cn2num, next_nonempty as _next_nonempty

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

# ---------------------------------------------------------------------------
# 行首正则（与 units.py 同语义的独立副本）
# ---------------------------------------------------------------------------

#: 允许 ``###`` 标题前缀与 ``**`` 加粗包裹的条文行（实测《安全生产法》源文件里
#: 「第七十条」是 ``**第七十条**``、「第九十条」「第一百一十七条」是 ``### 第X条``）。
RE_ARTICLE_MD = re.compile(
    r"^#{0,6}\s*\*{0,2}第([零一二三四五六七八九十百千两0-9]+)条\*{0,2}[　\s]*(.*)$"
)
RE_CHAPTER_HASH = re.compile(r"^#{1,6}\s*第([零一二三四五六七八九十百千两0-9]+)章[　\s]*(.*)$")
RE_SECTION_HASH = re.compile(r"^#{1,6}\s*第([零一二三四五六七八九十百千两0-9]+)节[　\s]*(.*)$")
RE_CHAPTER_PLAIN = re.compile(r"^第([零一二三四五六七八九十百千两0-9]+)章[　\s]*(.*)$")
RE_SECTION_PLAIN = re.compile(r"^第([零一二三四五六七八九十百千两0-9]+)节[　\s]*(.*)$")
RE_HEADING = re.compile(r"^(#{1,6})\s+(\S.*)$")
RE_DECIMAL = re.compile(r"^(?:([A-Z])\.)?(\d+(?:\.\d+)+)[　\s]+(.+)$")
RE_TOP_NUMBERED = re.compile(r"^(\d+)[　\s]+(\S.*)$")
RE_APPENDIX_HEAD = re.compile(r"^附\s*录\s*([A-Z])(?:[　\s]+(.*))?$")


#: 候选优先级（数字越小越优先）；同分/门槛都过时按此顺序取
PRIORITY = ("article", "numbered", "table", "heading", "paragraph")

#: 样本量与覆盖率门槛
MIN_UNITS = 3
MIN_COVER = 0.50
MIN_TABLE_CHAR_RATIO = 0.30
#: 表格区域「表占绝对多数」时优先于条款检测（区域里主要是表）
TABLE_DOMINANT_RATIO = 0.50




def _strip_heading(line: str) -> tuple[int, str]:
    """``(标题级别, 标题正文)``；不是标题返回 ``(0, 原文去空白)``。"""
    match = RE_HEADING.match(line.strip())
    if match:
        return len(match.group(1)), match.group(2).strip()
    return 0, line.strip()


def _article_path(line: str) -> tuple[str, str]:
    """行首若是章/节标题，返回 ``("chapter"|"section", 名称)``。"""
    level, body = _strip_heading(line)
    for kind, pattern in (
        ("chapter", RE_CHAPTER_HASH if level else RE_CHAPTER_PLAIN),
        ("section", RE_SECTION_HASH if level else RE_SECTION_PLAIN),
    ):
        match = pattern.match(line.strip())
        if match:
            name = f"第{match.group(1)}{'章' if kind == 'chapter' else '节'}　{match.group(2)}"
            return kind, name.strip()
    return "", ""


# ---------------------------------------------------------------------------
# 候选与检测结果
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """一个检测器对一段区域给出的候选（单位规格用 dict，便于落决策日志）。"""

    kind: str
    units: list[dict[str, Any]] = field(default_factory=list)
    score: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "n_units": len(self.units),
            "score": round(self.score, 3),
            "evidence": self.evidence,
        }


@dataclass
class Detection:
    """一段区域的检测结论（可直接 ``to_dict`` 落决策日志）。"""

    region: str
    chosen: str
    units: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    why: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "region": self.region,
            "chosen": self.chosen,
            "why": self.why,
            "candidates": self.candidates,
        }


# ---------------------------------------------------------------------------
# 检测器
# ---------------------------------------------------------------------------


def _coverage(covered: int, total: int) -> float:
    return round(covered / total, 4) if total else 0.0


def _preamble_units(lines: Sequence[str]) -> list[dict[str, Any]]:
    """首个编号单位之前的内容（前言/引言等）按空行聚段，避免静默丢弃。

    标题行只作为后续段落的 ``title``/``path``，不单独成块；纯标题无正文时不产单位。
    """
    units: list[dict[str, Any]] = []
    title = ""
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        if buf:
            units.append(
                {
                    "kind": "paragraph",
                    "ref": "",
                    "title": title,
                    "path": [title] if title else [],
                    "lines": list(buf),
                }
            )
            buf = []

    for raw in lines:
        line = str(raw).strip()
        if not line:
            flush()
            continue
        level, body = _strip_heading(line)
        if level:
            flush()
            title = body
            continue
        buf.append(line)
    flush()
    return units




def detect_articles(lines: Sequence[str], *, region: str = "body") -> Candidate:
    """条文式：``第X条`` 开单位；章/节只进 ``path`` 面包屑。"""
    units: list[dict[str, Any]] = []
    pending: list[str] = []          # 首个单位之前的行（前言/标题），不静默丢弃
    cur: dict[str, Any] | None = None
    chapter = ""
    section = ""
    covered = 0
    total = 0
    numbers: list[int] = []

    for raw in lines:
        line = raw.strip()
        if not line:
            if not units:
                pending.append("")
            continue
        total += 1
        if not units:
            pending.append(line)
        chapter_kind, name = _article_path(line)
        if chapter_kind == "chapter":
            chapter, section = name, ""
            covered += 1
            continue
        if chapter_kind == "section":
            section = name
            covered += 1
            continue
        match = RE_ARTICLE_MD.match(line)
        if match:
            number = cn2num(match.group(1))
            head = match.group(2).strip()
            cur = {
                "kind": "clause",
                "ref": f"第{match.group(1)}条",
                "title": "",
                "path": [part for part in (chapter, section) if part],
                "lines": [head] if head else [],
            }
            if not units and pending:
                units.extend(_preamble_units(pending))
                pending = []
            units.append(cur)
            if number is not None:
                numbers.append(number)
            covered += 1
            continue
        if line.startswith("#"):
            # 文档标题等：不产单位，但算已覆盖（不是正文）
            covered += 1
            continue
        if cur is not None:
            cur["lines"].append(line)
            covered += 1
        else:
            covered += 1

    n = len(units)
    monotonic = 1.0
    if len(numbers) > 1:
        up = sum(1 for a, b in zip(numbers, numbers[1:]) if b > a)
        monotonic = round(up / (len(numbers) - 1), 4)
    cover = _coverage(covered, total)
    score = 0.55 * cover + 0.25 * monotonic + 0.20 * min(1.0, n / 6)
    strong = (
        n >= MIN_UNITS
        and cover >= MIN_COVER
        and monotonic >= 0.90
    )
    if not strong:
        score *= 0.5
    return Candidate(
        kind="article",
        units=units,
        score=round(score, 3),
        evidence={
            "n_units": n,
            "line_coverage": cover,
            "monotonic_ratio": monotonic,
            "mean_chars": round(
                sum(len("\n".join(u["lines"])) for u in units) / n, 1
            ) if n else 0.0,
            "strong": strong,
        },
    )


def detect_numbered(lines: Sequence[str], *, region: str = "body") -> Candidate:
    """十进制条款（``3.1.2``，含 ``A.2.1`` 附录条款与 ``1 范围`` 顶层条款）。"""
    units: list[dict[str, Any]] = []
    pending: list[str] = []          # 首个单位之前的行（前言等），不静默丢弃
    pending_heads: list[str] = []    # 无编号标题（如「小购物场所」），并入下一单位
    cur: dict[str, Any] | None = None
    container: str | None = None     # 当前无编号容器标题，供其后同组条款共用面包屑
    covered = 0
    total = 0
    refs: list[str] = []

    index = 0
    size = len(lines)
    while index < size:
        raw = lines[index]
        index += 1
        line = raw.strip()
        if not line:
            if not units:
                pending.append("")
            continue
        total += 1
        if not units:
            pending.append(line)
        # 标题行只取标题正文参与编号匹配（如「## 3.1 术语」），标题本身不产单位
        level, body = _strip_heading(line)
        match = RE_DECIMAL.match(body)
        if match is None:
            match = RE_TOP_NUMBERED.match(body)
        if match:
            if match.re is RE_DECIMAL:
                letter, number = match.group(1), match.group(2)
                rest = match.group(3).strip()
                ref = f"{letter}.{number}" if letter else number
                parts = [p for p in ref.split(".") if p]
            else:
                number, rest = match.group(1), match.group(2).strip()
                ref = number
                parts = [number]
            heads = list(pending_heads)
            pending_heads = []
            if heads:
                # 新容器标题出现：从下一个条款起共用；之后的同组条款（5.2.2 …）
                # 仍带这个容器，面包屑不会只剩数字祖先。
                container = heads[-1]
            base = heads if heads else ([container] if container else [])
            cur = {
                "kind": "clause",
                "ref": ref,
                "title": "",
                "path": base + ([".".join(parts[:-1])] if len(parts) > 1 else []),
                "lines": heads + ([rest] if rest else []),
            }
            if not units and pending:
                units.extend(_preamble_units(pending))
                pending = []
            units.append(cur)
            refs.append(ref)
            covered += 1
            continue
        if line.startswith("#"):
            # 无编号标题有两种身份：
            # ① 下一个条款的容器（如「## 小购物场所」后紧跟 5.2.1）→ 进 pending_heads，
            #    挂到下一单位的 path 与首行；
            # ② 当前条款内部的小节名（如 4 场所规模界定 下的各类场所名，后面跟正文）
            #    → 并入当前条款正文，避免内容丢失或错挂到别的条款。
            if body:
                nxt = _next_nonempty(lines, index)
                nxt_match = None
                if nxt:
                    _nxt_level, nxt_body = _strip_heading(nxt)
                    nxt_match = RE_DECIMAL.match(nxt_body) or RE_TOP_NUMBERED.match(nxt_body)
                if not units:
                    if nxt_match is not None and level >= 2:
                        # 首个条款之前的容器标题（如「## 小购物场所」→ 5.1.1）；
                        # 一级标题（文档标题重复出现）不挂面包屑，按正文走 _preamble_units。
                        pending_heads.append(body)
                    else:
                        # 首个条款之前的前言/文档标题：交给 _preamble_units 当标题处理，
                        # 不要挂到第 1 条上（否则「前 言」会成为第 1 条的 header_path）。
                        pending.append(line)
                elif cur is not None and nxt_match is None:
                    cur["lines"].append(body)
                else:
                    pending_heads.append(body)
            covered += 1
            continue
        if cur is not None:
            cur["lines"].append(line)
            covered += 1
        else:
            covered += 1

    if pending_heads:
        tail_title = " ".join(pending_heads)
        units.append(
            {
                "kind": "paragraph",
                "ref": tail_title,
                "title": tail_title,
                "path": list(pending_heads),
                "lines": list(pending_heads),
            }
        )

    n = len(units)
    cover = _coverage(covered, total)
    score = 0.55 * cover + 0.25 * min(1.0, n / 8) + 0.20
    strong = n >= MIN_UNITS and cover >= MIN_COVER
    if not strong:
        score *= 0.5
    return Candidate(
        kind="numbered",
        units=units,
        score=round(score, 3),
        evidence={
            "n_units": n,
            "line_coverage": cover,
            "sample_refs": refs[:5],
            "strong": strong,
        },
    )


def detect_tables(lines: Sequence[str], *, region: str = "body") -> Candidate:
    """表占区域多数时选表（真正的渲染仍交给既有表格流水线）。"""
    total_chars = 0
    table_chars = 0
    blocks = 0
    in_pipe = False

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        total_chars += len(line)
        is_pipe = bool(RE_PIPE_ROW.match(line))
        is_html = bool(RE_HTML_TABLE.search(line))
        if is_pipe:
            table_chars += len(line)
            if not in_pipe:
                blocks += 1
            in_pipe = True
        else:
            in_pipe = False
            if is_html:
                table_chars += len(line)
                blocks += 1

    ratio = round(table_chars / total_chars, 4) if total_chars else 0.0
    strong = blocks >= 1 and ratio >= MIN_TABLE_CHAR_RATIO
    score = (0.7 * ratio + 0.3 * min(1.0, blocks / 2)) if strong else 0.0
    return Candidate(
        kind="table",
        units=[],
        score=round(score, 3),
        evidence={
            "n_tables": blocks,
            "char_ratio": ratio,
            "strong": strong,
        },
    )


def detect_headings(lines: Sequence[str], *, region: str = "body") -> Candidate:
    """标题树：每个标题下的正文聚成一个段落单位，标题只进 ``path``。"""
    units: list[dict[str, Any]] = []
    stack: list[tuple[int, str]] = []
    cur: dict[str, Any] | None = None
    covered = 0
    total = 0
    n_headings = 0

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        total += 1
        level, body = _strip_heading(line)
        if level:
            n_headings += 1
            covered += 1
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, body))
            cur = {
                "kind": "paragraph",
                "ref": body,
                "title": body,
                "path": [name for _lvl, name in stack[:-1]],
                "lines": [],
            }
            units.append(cur)
            continue
        if cur is not None:
            cur["lines"].append(line)
            covered += 1
        else:
            covered += 1

    units = [u for u in units if u["lines"]]
    # path = 祖先标题 + 本段标题（与 parse_generic 的 path=(title,) 语义一致）
    for unit in units:
        unit["path"] = list(unit["path"]) + [unit["title"]]
    n = len(units)
    cover = _coverage(covered, total)
    depth = max((len(u["path"]) for u in units), default=0)
    score = 0.40 * cover + 0.30 * min(1.0, n / 4) + 0.30 * (1.0 if depth >= 2 else 0.0)
    strong = n >= MIN_UNITS or (n >= 2 and cover >= 0.60)
    if not strong:
        score *= 0.5
    return Candidate(
        kind="heading",
        units=units,
        score=round(score, 3),
        evidence={
            "n_headings": n_headings,
            "n_units": n,
            "line_coverage": cover,
            "max_depth": depth,
            "strong": strong,
        },
    )


def detect_paragraphs(lines: Sequence[str], *, region: str = "body") -> Candidate:
    """兜底：按空行切段（无结构证据时的最后选择）。"""
    units: list[dict[str, Any]] = []
    buf: list[str] = []
    covered = 0
    total = 0

    def flush() -> None:
        nonlocal buf
        if buf:
            units.append(
                {"kind": "paragraph", "ref": "", "title": "", "path": [], "lines": list(buf)}
            )
            buf = []

    for raw in lines:
        line = raw.strip()
        if not line:
            flush()
            continue
        total += 1
        if line.startswith("#"):
            flush()
            covered += 1
            continue
        buf.append(line)
        covered += 1
    flush()

    n = len(units)
    cover = _coverage(covered, total)
    score = 0.30 + 0.10 * min(1.0, n / 5) + 0.05 * cover
    return Candidate(
        kind="paragraph",
        units=units,
        score=round(score, 3),
        evidence={"n_units": n, "line_coverage": cover, "strong": False},
    )


# ---------------------------------------------------------------------------
# 选择器
# ---------------------------------------------------------------------------


def choose_region_detector(lines: Sequence[str], *, region: str = "body") -> Detection:
    """对一段区域并行跑全部检测器，按「门槛 + 优先级」选单位类型。

    规则（P0 简化版，写进决策日志便于回溯）：

    1. 表格字符占比 ≥ 50% 且过门槛 → 选 ``table``（区域主要是表）；
    2. 否则按 article → numbered → heading 的顺序取第一个「强证据」候选；
    3. 无强证据 → ``paragraph`` 段落兜底。
    """
    candidates = [
        detect_articles(lines, region=region),
        detect_numbered(lines, region=region),
        detect_tables(lines, region=region),
        detect_headings(lines, region=region),
        detect_paragraphs(lines, region=region),
    ]
    by_kind = {cand.kind: cand for cand in candidates}
    table = by_kind["table"]

    chosen: Candidate | None = None
    why = ""
    if table.evidence.get("strong") and table.evidence.get("char_ratio", 0.0) >= TABLE_DOMINANT_RATIO:
        chosen = table
        why = f"表占区域字符 {table.evidence.get('char_ratio')}，按表优先"
    else:
        for kind in ("article", "numbered", "heading"):
            cand = by_kind[kind]
            if cand.evidence.get("strong"):
                chosen = cand
                why = f"{kind} 证据过门槛（{cand.evidence}）"
                break
    if chosen is None:
        chosen = by_kind["paragraph"]
        why = "无结构性证据，段落兜底"

    return Detection(
        region=region,
        chosen=chosen.kind,
        units=chosen.units,
        candidates=[cand.summary() for cand in candidates],
        why=why,
    )


__all__ = [
    "Candidate",
    "Detection",
    "choose_region_detector",
    "cn2num",
    "detect_articles",
    "detect_headings",
    "detect_numbered",
    "detect_paragraphs",
    "detect_tables",
]
