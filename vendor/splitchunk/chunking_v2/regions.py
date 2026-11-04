# -*- coding: utf-8 -*-
"""fac_rag.chunking_v2.regions —— 第②步：区域切分（技术版 §5，D-041 / D-042）。

M2 的策略很功利：**只修 v1 修不了的那几处，其余保持 v1 行为。**
L1 等价验收要求 ≥61/65 逐文件 ``chunk_hash`` 全同，任何多余的「改进」都会把
变化面推出 Lead 定下的 5 份硬口径（D-042）。所以这里产出的是「行级区域计划」，
而不是直接改写正文：只有确实需要修的区域才给出待置空的行号，解析器用它做
等长掩码（行号不变，等价性可控）。

区域划分（技术版 §5）
----------------------
* ``front``：文件头 → 目次/前言/附录/正文首个标记。v1 在第一条之前本来就丢行，
  这里只记账，供 extras 与体检单用。
* ``toc``：目次标题 → 正文起点。**只有 v1 会漏判的目次才整段置空**：
  判据是目次区内存在「章条号 + 任意空白 + 页码」形态的行，而 v1 的条目正则
  ``RE_TOC_ENTRY`` / ``RE_TOC_PAGE_PAREN`` 认不出来（GB 4053.2 的单空格页码
  就是这种；GB 45833/9448 同理）。命中后把 ``[head, end)`` 置空，标准解析器
  直接看到正文首个章条号——D1 的修法，不碰状态机。
* ``appendix``：``# 附录X 标题``（M2 只认 markdown 标题行，标准附录标题在
  生产语料里都是标题）。标题行本身照 v1 丢弃，字母交给 units 层做 D-042 的
  relabel（落 ``law_num`` / ``chunk_kind`` / ``extras.appendix``）。
* ``commentary``：复刻 v1 的截断判据（``RE_COMMENTARY`` 命中且
  「见过标题式条号 or idx>=100」）。条文说明按 Q1 不收，parse 里照旧截断。
* ``references`` / ``backmatter``：参考文献标题起到 EOF 丢弃（v1 行为）。
  背材料（用词说明/引用标准名录/修订说明/主编单位）按 D-039 丢弃，但**只处理
  「完整背材料段」**：位于最后一个附录标题之后、条文说明起点之前、段内命中
  ≥3 类标记。生产语料里只有 GB 50016 命中（GB 50058 只有 2 类、GB 50140 只有
  1 类，暂不处理，留 M5 复查；否则变化文件会从 4 份涨到 6 份，突破硬口径）。
"""

from __future__ import annotations

from .patterns import (
    RE_CHAPTERISH,
    RE_CLAUSE,
    RE_COMMENTARY,
    RE_FRONT,
    RE_PAGE_TAIL,
    RE_REFS,
    RE_TAG,
    RE_TOC_PAGE_PAREN,
)
from .textutil import next_nonempty as _next_nonempty

import re
from dataclasses import dataclass, field

from .model import Profile, Region

# ---------------------------------------------------------------------------
# 与 v1 同形的辅助正则 —— 改这里等于改等价性，动之前先跑 L1 对照
# ---------------------------------------------------------------------------

RE_TOC_HEAD = re.compile(r"^(?:目\s*[次录]|CONTENTS?)$", re.IGNORECASE)
RE_APPENDIX = re.compile(r"^附\s*录\s*([A-Z])(?:[　\s]+(.*))?$")

#: v1 认的目次条目尾巴（双空格/全角空白/罗马数字 / 点线页码）
RE_TOC_ENTRY_V1 = re.compile(r"(?:[\t　]|[ ]{2,})[0-9ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+$|" + RE_PAGE_TAIL.pattern)
#: 目次里只写「（12）」的页码
#: 放宽后的目次条目尾巴：任意空白 + 1~4 位页码（阿拉伯/罗马数字）——只在目次区用
RE_TOC_PAGE_TAIL_RELAXED = re.compile(r"\s+[0-9ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]{1,4}\s*$")
#: 点线/省略号紧贴页码的条目（「第一部分 总则....21」「第1号 …....30」，M8b/task-12）
RE_TOC_ENTRY_DOTS = re.compile(r".+(?:\.{2,}|…+|⋯+|．{2,}|·{2,})\s*\d{1,4}\s*$")
#: 目次里的分节行（「第一部分 总则....21」「（一）综合类」）与编号条目（「第 333 号 …」）
RE_TOC_ITEM_PART = re.compile(r"^第[零一二三四五六七八九十百千两0-9]+部分(?:\s*\S.*)?$")
RE_TOC_ITEM_PAREN = re.compile(r"^[（(][零一二三四五六七八九十百千两0-9]+[)）]\s*\S+")
RE_TOC_ITEM_NUMBERED = re.compile(r"^第\s*[0-9]{1,4}\s*号")

#: 背材料标记（D-039）；按「类」去重计数，≥3 类才认定是完整背材料段
_MARKER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("用词说明", re.compile(r"用\s*词\s*说\s*明")),
    ("引用标准名录", re.compile(r"引\s*用\s*标\s*准\s*名\s*录")),
    ("修订说明", re.compile(r"修\s*订\s*说\s*明")),
    ("编研单位", re.compile(r"主编单位|参编单位|起草单位")),
)
RE_BACKMATTER_START = re.compile(
    r"^(?:本?(?:规范|标准|文件)?\s*用\s*词\s*说\s*明|"
    r"引\s*用\s*标\s*准\s*名\s*录|修\s*订\s*说\s*明|"
    r"主编单位|参编单位|起草单位)"
)
# PDF→MD 转换偶尔丢「附 录 X」整行，只留下「（资料性）/（规范性）」标记。
# 这类行后面紧跟附录标题（# 工作表格），字母可从后续「表A.1」反推。
RE_APPENDIX_KIND_MARK = re.compile(r"^[（(]\s*(?:规范性|资料性)\s*[)）]\s*$")
RE_APPENDIX_TABLE_LETTER = re.compile(r"^表\s*([A-Z])\s*[.．]\s*\d")


def clean_line(s: str) -> str:
    """与 v1 ``standard.clean_line`` 完全同形：去列表符号/粗体/标题井号/HTML 标签。"""
    from html import unescape

    s = str(s).strip()
    s = re.sub(r"^[-*+]\s+", "", s)
    s = s.replace("**", "")
    s = re.sub(r"^#+\s*", "", s)
    s = RE_TAG.sub("", s)
    s = unescape(s)
    return s.strip()


# ---------------------------------------------------------------------------
# 区域计划
# ---------------------------------------------------------------------------


@dataclass
class RegionPlan:
    """一份文档的区域切分结果（行号 0 基，``end`` 不含）。"""

    regions: list[Region] = field(default_factory=list)
    #: 需要整段置空的行（D1 目次 / D-039 背材料）；解析时替换成空行，行号不变
    toc_lines: set[int] = field(default_factory=set)
    backmatter_lines: set[int] = field(default_factory=set)
    body_start: int = 0
    toc_head: int | None = None
    toc_end: int | None = None
    toc_fixed: bool = False
    commentary_index: int | None = None
    references_index: int | None = None
    #: [(标题行号, 字母, 标题)]；字母只用于 units 层的附录 relabel
    appendix_heads: list[tuple[int, str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def masked_lines(self) -> frozenset[int]:
        return frozenset(self.toc_lines | self.backmatter_lines)

    def apply_mask(self, text: str) -> str:
        """返回把 ``masked_lines`` 置空后的正文；行数与换行位置保持不变。"""
        masked = self.masked_lines
        if not masked:
            return text
        lines = text.splitlines()
        for idx in masked:
            if 0 <= idx < len(lines):
                lines[idx] = ""
        return "\n".join(lines)

    def region_of(self, index: int) -> str:
        for region in self.regions:
            if region.start <= index < region.end:
                return region.kind
        return "body"


# ---------------------------------------------------------------------------
# 各区域的探测
# ---------------------------------------------------------------------------


def _find_toc_head(lines: list[str]) -> int | None:
    for index, raw in enumerate(lines):
        if RE_TOC_HEAD.match(clean_line(raw)):
            return index
    return None


def _find_toc_end(lines: list[str], head: int, max_scan: int) -> int | None:
    """目次标题之后、正文起点之前的位置。

    退出条件（与 v1 ``standard._parse`` 第 2 步同构，但更宽松一点）：
    遇到 markdown 标题且是 前言/引言/附录/参考文献/条文说明/第X章，或非标题行
    的 前言/引言/附录/参考文献，或「行尾不挂页码尾」的章条号行。

    注意「章条号 + 页码」形态（含 v1 认不出的单空格页码）仍是目次条目：
    GB 4053.2 的 ``- 1　范围 1`` 若在这里退出，目次区会被截成 4 行，D1 修复
    判据就看不到真正的泄漏条目（2026-10-29 冒烟实测）。
    """
    limit = min(len(lines), head + max(1, int(max_scan)))
    for index in range(head + 1, limit):
        raw = lines[index]
        stripped = raw.strip()
        if not stripped:
            continue
        c0 = clean_line(raw)
        if stripped.startswith("#"):
            if (
                RE_FRONT.match(c0)
                or RE_APPENDIX.match(c0)
                or RE_REFS.match(c0)
                or RE_COMMENTARY.match(c0)
                or RE_CHAPTERISH.match(c0)
            ):
                return index
            if RE_CLAUSE.match(c0):
                return index
            continue
        if RE_FRONT.match(c0) or RE_APPENDIX.match(c0) or RE_REFS.match(c0):
            return index
        if RE_CLAUSE.match(c0):
            looks_entry = (
                RE_TOC_ENTRY_V1.search(stripped)
                or RE_TOC_PAGE_PAREN.search(stripped)
                or RE_TOC_PAGE_TAIL_RELAXED.search(c0)
            )
            if looks_entry:
                continue
            return index
    return None


def _looks_like_toc_entry(raw: str) -> bool:
    """条目式行（目次区判据，M8b/task-12）。

    覆盖 v1 的尾巴形态 + 点线紧贴页码（「标题....21」）+ 目次分节行
    （「第一部分 总则....21」「（一）综合类」）+ 「第 333 号 …」式条目。
    """
    stripped = str(raw).strip()
    if not stripped:
        return False
    if RE_TOC_ENTRY_DOTS.match(stripped):
        return True
    if RE_TOC_ENTRY_V1.search(stripped) or RE_TOC_PAGE_PAREN.search(stripped):
        return True
    c0 = clean_line(stripped)
    if not c0:
        return False
    if RE_TOC_PAGE_TAIL_RELAXED.search(c0) and len(c0) > 12:
        return True
    if RE_TOC_ITEM_PART.match(c0) or RE_TOC_ITEM_PAREN.match(c0):
        return True
    if RE_TOC_ITEM_NUMBERED.match(c0):
        return True
    return False


def _heading_is_toc_section(lines: list[str], index: int, limit: int) -> bool:
    """目次内部的 markdown 标题（如「## （三）教育培训类」）？

    判据：标题之后第一个非空、非标题行仍是目次条目式行（点线页码 / 「第N号」），
    说明这个标题只是目录的分节标题，不是正文起点（2025 山东自由裁量样本实测：
    TOC 有 337 条、跨 450 行，中间夹着「## （三）教育培训类」这类分节标题，
    旧逻辑在第一个分节标题处就退出，导致后面 300+ 条目被当正文切块）。
    """
    for j in range(index + 1, min(limit, index + 40)):
        stripped = lines[j].strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        return _looks_like_toc_entry(lines[j])
    return False


def _find_toc_end_extended(lines: list[str], head: int, max_scan: int) -> int | None:
    """无章条号目录的正文起点（M8b/task-12）。

    v1 的 ``_find_toc_end`` 依赖章条号行退出；「标题....页码」式的纯目次
    （山东自由裁量样本 B）没有条号，会在 max_scan 内扫不到出口。这里改用
    「连续条目行 + 下一个 markdown 标题/结构行即正文起点」兜底：

    * 目录标题之后至少 5 条条目式行才算识别成功（宁缺勿滥）；
    * 条目之间的空行、超长续行（换行的条目标题）不算中断；
    * 遇到 markdown 标题行或结构行（前言/附录/参考文献/第X章）即视为正文起点，
      但目录内部的分节标题（标题后紧跟条目行，见 ``_heading_is_toc_section``）继续算目录。
    """
    limit = min(len(lines), head + max(1, int(max_scan)))
    entries = 0
    for index in range(head + 1, limit):
        raw = lines[index]
        stripped = raw.strip()
        if not stripped:
            continue
        c0 = clean_line(raw)
        if stripped.startswith("#"):
            if entries >= 5 and not _heading_is_toc_section(lines, index, limit):
                return index
            continue
        if RE_FRONT.match(c0) or RE_APPENDIX.match(c0) or RE_REFS.match(c0):
            return index if entries >= 5 else None
        if _looks_like_toc_entry(raw):
            entries += 1
            continue
        if entries >= 1 and len(stripped) > 20 and not RE_CLAUSE.match(c0):
            continue  # 条目换行续行，不算中断
        if entries >= 5:
            return index
    return None


def _toc_needs_fix(lines: list[str], head: int, end: int) -> bool:
    """目次区里有没有「v1 会当成正文吃进去」的条目（D1 判据）。

    M8b/task-12 扩展：无条号目录（「标题....页码」「第N号 …」式，区内
    ``RE_CLAUSE`` 一个都认不出）且条目式行 ≥5 时同样判定需要置空——否则
    generic 兜底会把目录整段当正文切块（山东自由裁量样本 B）。
    """
    entry_like = 0
    clause_like = 0
    for index in range(head + 1, end):
        stripped = lines[index].strip()
        if not stripped:
            continue
        if _looks_like_toc_entry(lines[index]):
            entry_like += 1
        if RE_CLAUSE.match(clean_line(stripped)):
            clause_like += 1
        if RE_TOC_ENTRY_V1.search(stripped) or RE_TOC_PAGE_PAREN.search(stripped):
            continue
        candidate = clean_line(stripped)
        if not RE_CLAUSE.match(candidate):
            continue
        if RE_TOC_PAGE_TAIL_RELAXED.search(candidate):
            return True
    # 无条号目录（M8b/task-12）：条目式行够多、区内没有条号行 → 整段置空
    return clause_like == 0 and entry_like >= 5


def _find_commentary(lines: list[str]) -> int | None:
    """复刻 v1 ``standard._parse`` 的第 0 步：条文说明截断点。"""
    seen_body_heading = False
    for index, raw in enumerate(lines):
        stripped = raw.lstrip()
        cl = clean_line(raw)
        if stripped.startswith("#") and RE_CLAUSE.match(cl):
            seen_body_heading = True
        if RE_COMMENTARY.match(cl) and (seen_body_heading or index >= 100):
            return index
    return None


def _find_references(lines: list[str], start: int) -> int | None:
    for index in range(max(0, start), len(lines)):
        if RE_REFS.match(clean_line(lines[index])):
            return index
    return None




def _infer_appendix_letter(lines: list[str], start: int, limit: int = 80) -> str:
    """从附录正文里的「表X.1」「附 录 X」反推丢失的附录字母。"""
    for raw in lines[start:start + limit]:
        cl = clean_line(raw)
        match = RE_APPENDIX_TABLE_LETTER.match(cl)
        if match:
            return match.group(1)
        match = RE_APPENDIX.match(cl)
        if match:
            return match.group(1)
    return ""


def _find_appendix_heads(lines: list[str], start: int) -> list[tuple[int, str, str]]:
    heads: list[tuple[int, str, str]] = []
    last_index = -999
    for index in range(max(0, start), len(lines)):
        raw = lines[index]
        cl = clean_line(raw)
        if raw.lstrip().startswith("#"):
            match = RE_APPENDIX.match(cl)
            if match:
                heads.append((index, match.group(1), (match.group(2) or "").strip()))
                last_index = index
            continue
        # 只有「（资料性）/（规范性）」、没有「附 录 X」行时：若后面紧跟标题，
        # 且能从后续表号/附录行推定字母，就把这一行当附录起点。
        if RE_APPENDIX_KIND_MARK.match(cl) and index - last_index > 3:
            nxt = _next_nonempty(lines, index + 1)
            if not nxt.startswith("#"):
                continue
            letter = _infer_appendix_letter(lines, index + 1)
            if letter:
                heads.append((index, letter, ""))
                last_index = index
    return heads


def _find_backmatter(
    lines: list[str],
    appendix_heads: list[tuple[int, str, str]],
    commentary_index: int | None,
    references_index: int | None,
) -> tuple[set[int], list[str]]:
    """D-039：只丢「完整背材料段」（≥3 类标记），避免变化面突破硬口径。"""
    warnings: list[str] = []
    if not appendix_heads:
        return set(), warnings
    last_appendix = max(index for index, _, _ in appendix_heads)
    n = len(lines)
    end = n
    if references_index is not None:
        end = min(end, references_index)
    if commentary_index is not None:
        end = min(end, commentary_index)
    if last_appendix >= end:
        return set(), warnings

    start: int | None = None
    for index in range(last_appendix + 1, end):
        raw = lines[index]
        if not raw.strip():
            continue
        if raw.lstrip().startswith("-"):
            continue
        if RE_BACKMATTER_START.match(clean_line(raw)):
            start = index
            break
    if start is None:
        return set(), warnings

    kinds: set[str] = set()
    for index in range(start, end):
        cl = clean_line(lines[index])
        if not cl:
            continue
        for name, pattern in _MARKER_PATTERNS:
            if pattern.search(cl):
                kinds.add(name)
    if len(kinds) < 3:
        return set(), warnings

    warnings.append(
        f"背材料段（第 {start + 1}-{end} 行，含 {len(kinds)} 类标记："
        f"{'、'.join(sorted(kinds))}）按 D-039 丢弃"
    )
    return set(range(start, end)), warnings


# ---------------------------------------------------------------------------
# 区域清单
# ---------------------------------------------------------------------------


def _first_marker_index(lines: list[str]) -> int:
    for index, raw in enumerate(lines):
        c0 = clean_line(raw)
        if (
            RE_FRONT.match(c0)
            or RE_APPENDIX.match(c0)
            or RE_REFS.match(c0)
            or RE_COMMENTARY.match(c0)
            or RE_CHAPTERISH.match(c0)
        ):
            return index
    return 0


def _build_regions(lines: list[str], plan: RegionPlan) -> list[Region]:
    n = len(lines)
    regions: list[Region] = []

    if plan.toc_head is not None and plan.toc_end is not None:
        if plan.toc_head > 0:
            regions.append(Region(kind="front", start=0, end=plan.toc_head))
        regions.append(Region(kind="toc", start=plan.toc_head, end=plan.toc_end))
        body_start = plan.toc_end
    else:
        front_end = _first_marker_index(lines)
        if front_end > 0:
            regions.append(Region(kind="front", start=0, end=front_end))
        body_start = 0

    stops: list[int] = []
    if plan.appendix_heads:
        stops.append(plan.appendix_heads[0][0])
    if plan.commentary_index is not None:
        stops.append(plan.commentary_index)
    if plan.references_index is not None:
        stops.append(plan.references_index)
    body_end = min([s for s in stops if s >= body_start], default=n)
    if body_end > body_start:
        regions.append(Region(kind="body", start=body_start, end=body_end))

    for number, (index, letter, title) in enumerate(plan.appendix_heads):
        nxt = plan.appendix_heads[number + 1][0] if number + 1 < len(plan.appendix_heads) else n
        for candidate in (plan.commentary_index, plan.references_index):
            if candidate is not None and index < candidate < nxt:
                nxt = candidate
        if plan.backmatter_lines:
            bm = min(plan.backmatter_lines)
            if index < bm < nxt:
                nxt = bm
        regions.append(
            Region(kind="appendix", start=index, end=nxt, meta={"letter": letter, "title": title})
        )

    if plan.commentary_index is not None:
        end = plan.references_index if plan.references_index is not None else n
        regions.append(Region(kind="commentary", start=plan.commentary_index, end=max(end, plan.commentary_index)))
    if plan.references_index is not None:
        regions.append(Region(kind="references", start=plan.references_index, end=n))
    elif plan.backmatter_lines:
        regions.append(
            Region(kind="references", start=min(plan.backmatter_lines), end=n, meta={"backmatter": True})
        )
    return regions


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def detect_regions(lines: list[str], profile: Profile) -> RegionPlan:
    """对一份文档做区域切分。``lines`` 必须与解析时使用的行序完全一致。"""
    plan = RegionPlan()
    if not lines:
        return plan

    max_scan = int(getattr(profile.toc, "max_scan_lines", 600) or 600)
    head = _find_toc_head(lines)
    end = _find_toc_end(lines, head, max_scan) if head is not None else None
    end_extended = False
    if head is not None and end is None:
        # 无章条号的纯目次（点线页码 / 第N号 条目）：v1 的退出条件抓不到（M8b/task-12）
        end = _find_toc_end_extended(lines, head, max_scan)
        end_extended = end is not None
    if head is not None and end is not None and _toc_needs_fix(lines, head, end):
        plan.toc_fixed = True
        plan.toc_head = head
        plan.toc_end = end
        plan.toc_lines = set(range(head, end))
        plan.body_start = end
        if end_extended:
            plan.warnings.append(
                f"目次区（第 {head + 1}-{end} 行）是无条号条目式目录"
                "（点线页码 / 「第N号」式），v1 会当正文吃入，已整段置空后重切（M8b/task-12）"
            )
        else:
            plan.warnings.append(
                f"目次区（第 {head + 1}-{end} 行）含 v1 认不出的「章条号+空白+页码」条目，"
                "已整段置空后重切（D1 修复）"
            )
    else:
        if head is not None and end is not None:
            plan.toc_head = head
            plan.toc_end = end
        plan.body_start = end if end is not None else 0

    plan.commentary_index = _find_commentary(lines)
    plan.references_index = _find_references(lines, plan.body_start)
    plan.appendix_heads = _find_appendix_heads(lines, plan.body_start)
    plan.backmatter_lines, backmatter_warnings = _find_backmatter(
        lines, plan.appendix_heads, plan.commentary_index, plan.references_index
    )
    plan.warnings.extend(backmatter_warnings)

    # 目次被置空时，区域清单仍按原文位置给出（extras/报表可读）
    scan_plan = RegionPlan(
        toc_head=plan.toc_head,
        toc_end=plan.toc_end,
        body_start=plan.body_start,
        commentary_index=plan.commentary_index,
        references_index=plan.references_index,
        appendix_heads=plan.appendix_heads,
        backmatter_lines=plan.backmatter_lines,
    )
    plan.regions = _build_regions(lines, scan_plan)
    return plan


__all__ = ["RegionPlan", "clean_line", "detect_regions"]
