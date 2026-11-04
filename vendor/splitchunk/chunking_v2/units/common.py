"""units 共用层：常量/正则/单元格辅助/表块→Unit 转换（原 units.py 的公共部分）。"""

from __future__ import annotations

from ..patterns import (
    RE_APPENDIX,
    RE_APPENDIX_NO,
    RE_ARTICLE,
    RE_CLAUSE,
    RE_COMMENTARY,
    RE_CONT,
    RE_FRONT,
    RE_LAW_TITLE,
    RE_PAGE_TAIL,
    RE_REFS,
    RE_TAG,
    RE_TOC_PAGE_PAREN,
)

from ..textutil import cn2num

import re

from html import unescape

from typing import Any

from ..legacy_tables import (
    legacy_html_table_to_grid,
    legacy_pipe_table_to_grid,
    legacy_table_to_blocks,
)

from .. import detect

from ..model import AppendixRule, ParseResult, Profile, Region, TableModel, Unit

from ..regions import RegionPlan

from ..tables import (
    build_table_model,
    html_table_to_grid,
    is_continuation_grid,
    is_unit_note_line,
    model_from_store,
    pipe_table_to_grid,
    render_table_blocks,
)

RE_TITLE = RE_LAW_TITLE  # 旧名保留（standard 分支内部引用）

_RE_APPENDIX_LETTER_ONLY = RE_APPENDIX_NO

RE_LAW_TOC_HEAD = re.compile(r"^#{1,6}\s*目\s*录\s*$")

RE_CHAPTER = re.compile(r"^#{1,6}\s*第([零一二三四五六七八九十百千两0-9]+)章\s*(.*)$")

RE_SECTION = re.compile(r"^#{1,6}\s*第([零一二三四五六七八九十百千两0-9]+)节\s*(.*)$")

MAX_CHUNK_CHARS = 800

ROW_PACK_CHARS = 700

ANCESTOR_LEVELS = 2

KEEP_FRONT_MATTER = False

DROP_APPENDIX = ""

RE_TOC_HEAD = re.compile(r"^(?:目\s*次|CONTENTS?)$", re.IGNORECASE)

RE_YEAR_ONLY = re.compile(r"^(?:19|20)\d{2}$")

RE_TABLE_CAP = re.compile(
    r"^表\s*((?:[0-9]+(?:\.[0-9]+)*|[A-Z](?:\.[0-9]+)*)(?:[-—–]\d+)?)[　\s]+(.+)$"
)

RE_TABLE_CAP_CONT = re.compile(
    r"^表\s*((?:[0-9]+(?:\.[0-9]+)*|[A-Z](?:\.[0-9]+)*)(?:[-—–]\d+)?)\s*[（(]\s*续\s*[)）]$"
)

RE_TABLE_CAP_EMBEDDED = re.compile(
    r"表\s*([A-Z](?:\.[0-9]+)*(?:[-—–]\d+)?)[　\s]+(\S.*)$"
)

RE_TABLE_CAP_V1 = re.compile(
    r"^表\s*([0-9]+(?:\.[0-9]+)*|[A-Z](?:\.[0-9]+)*)[　\s]+(.+)$"
)

RE_TABLE_CAP_CONT_V1 = re.compile(
    r"^表\s*([0-9]+(?:\.[0-9]+)*|[A-Z](?:\.[0-9]+)*)\s*[（(]\s*续\s*[)）]$"
)

RE_TOC_ENTRY = re.compile(
    r"(?:[\t　]|[ ]{2,})[0-9ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+$|" + RE_PAGE_TAIL.pattern
)

RE_STD_NO = re.compile(
    r"^((?:GB|JB|AQ|JT|YY|QB|JGJ)(?:/[TZ])?[　\s]*[A-Z]?[　\s]*\d+(?:\.\d+)*[—\-]\d{4})"
)

RE_APPENDIX_CLAUSE = re.compile(r"^[A-Z]\.\d")

RE_APPENDIX_KIND = re.compile(
    r"^[（(]?\s*(规范性|资料性)(?:\s*附\s*录)?\s*[）)]?$"
)

_RE_BOLD_TITLE_LINE = re.compile(r"^\*\*(.+?)\*\*\s*$")

_RE_ANY_HEADING_LINE = re.compile(r"^#{1,6}\s+(.+?)\s*$")

_RE_DERIVED_TITLE_SKIP = {"前言", "前 言", "引言", "引 言", "目次", "目 录", "参考文献"}

_RE_APPENDIX_KIND_PREFIX = re.compile(
    r"^[（(]?\s*(?:资料性|规范性)(?:\s*附\s*录)?\s*[）)]?\s*"
)

RE_GENERIC_HEADING = re.compile(r"^#{1,6}\s+(.+)$")

def clean_statute_line(s: str) -> str:
    """v1 statute.clean_line：去列表符号 / 粗体 / 标题井号（不去 HTML）。"""
    s = s.strip()
    s = re.sub(r"^[-*+]\s+", "", s)
    s = s.replace("**", "")
    s = re.sub(r"^#+\s*", "", s)
    return s.strip()


def clean_standard_line(s: str) -> str:
    """与 v1 standard.clean_line 完全同形（含去 HTML 标签与 unescape）。"""
    s = str(s).strip()
    s = re.sub(r"^[-*+]\s+", "", s)
    s = s.replace("**", "")
    s = re.sub(r"^#+\s*", "", s)
    s = RE_TAG.sub("", s)
    s = unescape(s)
    return s.strip()


def _std_no(name: str) -> str:
    """从标准名里抠标准号（``GB 15760—2025``）；抠不到返回原名。"""
    name = (name or "").strip()
    if not name:
        return ""
    match = RE_STD_NO.match(name)
    return match.group(1) if match else name


def _title_in_text(text: str) -> str:
    for raw in text.splitlines():
        match = RE_TITLE.match(raw.strip())
        if match:
            return clean_standard_line(match.group(1))
    return ""


def _clause_key(s: str) -> tuple[int, ...] | None:
    """章条号 → 可比较元组；附录字母 A→101、B→102，保证排在数字章之后。"""
    match = re.match(r"^(?:([A-Z])\.)?(\d+(?:\.\d+)*)$", s)
    if not match:
        return None
    key: list[int] = []
    if match.group(1):
        key.append(100 + (ord(match.group(1)) - 64))
    for part in match.group(2).split("."):
        if not part.isdigit():
            return None
        key.append(int(part))
    return tuple(key)


def _next_pipe_within(lines: list[str], start: int, within: int) -> bool:
    """D4b：从 ``start`` 往后 ``within`` 行内是否有管道表行（粘行表题判据）。"""
    end = min(len(lines), start + within)
    for idx in range(start, end):
        stripped = lines[idx].strip()
        if not stripped:
            continue
        return stripped.startswith("|")
    return False


def _looks_like_unlabeled_grid(grid: list[list[str]] | None) -> bool:
    """无表题表格识别判据（M8b / task-12）：结构合格才认，宁缺勿滥。

    要求：能解析出 ≥3 行（表头 + ≥2 行数据）、≥2 列、所有行列数一致。
    这样「表格没解析出任何行」/ 单行碎片不会被误认成表。
    """
    if not grid or len(grid) < 3:
        return False
    width = len(grid[0])
    if width < 2:
        return False
    return all(len(row) == width for row in grid)


def _looks_like_derived_grid(grid: list[list[str]] | None) -> bool:
    """D-071：派生表名路径的最低结构要求（表头 + ≥1 行数据、≥2 列、列数一致）。

    比无表题识别宽松一行：附录区的统计表常常只有 1 行数据（如
    DB37/T 3153 附录 A 的「重大风险点统计表」），派生到真表名后应当成表。
    """
    if not grid or len(grid) < 2:
        return False
    width = len(grid[0])
    if width < 2:
        return False
    return all(len(row) == width for row in grid)


def _clean_derived_title(text: str) -> str:
    """D-071：表名清洗——去「（资料性附录）」前缀、去尾部「（续）」、丢弃空/通用标题。"""
    s = str(text or "").strip()
    s = _RE_APPENDIX_KIND_PREFIX.sub("", s).strip()
    s = s.strip("*# ").strip()
    s = RE_CONT.sub("", s).strip()
    if not s or _RE_APPENDIX_LETTER_ONLY.match(s) or s in _RE_DERIVED_TITLE_SKIP:
        return ""
    return s


def _html_table_adjacent(lines: list[str], index: int) -> bool:
    """D-071：当前 HTML 表前面是否紧挨着上一张 HTML 表（跨页续表判据之一）。"""
    for j in range(index - 1, -1, -1):
        s = lines[j].strip()
        if not s:
            continue
        return s.lower().endswith("</table>")
    return False


def _derive_table_title(lines: list[str], index: int) -> str:
    """D-071：从表格上文派生表名（只给附录区无表题 HTML 表用）。

    顺序：①紧邻上方的独立加粗行；②往回找最近标题行（≤120 行）——标题本身
    可用就用它，若是「（资料性附录）」这类泛化标题，再在标题与表格之间找
    独立加粗行/短标题行。
    """
    for j in range(index - 1, max(index - 6, -1), -1):
        s = lines[j].strip()
        if not s:
            continue
        match = _RE_BOLD_TITLE_LINE.match(s)
        if match:
            title = _clean_derived_title(match.group(1))
            if title:
                return title
        if "<table" in s.lower() or s.lower().startswith("</table"):
            break
    for j in range(index - 1, max(index - 120, -1), -1):
        s = lines[j].strip()
        if not s:
            continue
        if "<table" in s.lower() or s.lower().startswith("</table"):
            return ""
        match = _RE_ANY_HEADING_LINE.match(s)
        if not match:
            continue
        title = _clean_derived_title(match.group(1))
        if title:
            return title
        for k in range(j + 1, index):
            s2 = lines[k].strip()
            if not s2:
                continue
            if _RE_ANY_HEADING_LINE.match(s2) or RE_APPENDIX_KIND.match(s2):
                continue
            bold = _RE_BOLD_TITLE_LINE.match(s2)
            candidate = _clean_derived_title(bold.group(1) if bold else s2)
            if candidate and 2 <= len(candidate) <= 60 and not bold and RE_APPENDIX_KIND.match(s2):
                continue
            if candidate and not (bold or candidate.startswith(("表", "注", "|"))):
                if len(candidate) <= 60 and "。" not in s2:
                    return candidate
    return ""


def _merge_bare_titles(
    blocks: list[dict[str, Any]],
    *,
    ancestor_levels: int = ANCESTOR_LEVELS,
) -> list[dict[str, Any]]:
    """纯标签父条不入库，标题搬到后代抬头（与 v1 完全一致）。"""
    titles: dict[str, str] = {}
    for block in blocks:
        if block.get("kind") == "clause" and block.get("lines"):
            titles.setdefault(block["clause"], block["lines"][0])

    has_child: set[str] = set()
    for block in blocks:
        parts = str(block.get("clause", "")).split(".")
        for k in range(1, len(parts)):
            has_child.add(".".join(parts[:k]))

    def crumb(c: str) -> str:
        if not c or ancestor_levels <= 0:
            return ""
        parts = c.split(".")
        out = []
        for k in range(1, len(parts)):
            anc = ".".join(parts[:k])
            if anc in titles:
                out.append(f"{anc} {titles[anc]}")
        if not out or ancestor_levels >= 99:
            return " > ".join(out)
        return " > ".join(out[-ancestor_levels:])

    out: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("kind") == "clause":
            if len(block["lines"]) == 1 and block["clause"] in has_child:
                continue
            pre = crumb(block["clause"])
            if pre:
                block = dict(block, lines=[pre + " > " + block["lines"][0]] + block["lines"][1:])
        else:
            pre = crumb(str(block.get("clause", "")))
            if pre:
                block = dict(block, chunk=pre + " > " + block["chunk"])
        out.append(block)
    return out


def _appendix_marker(lines: list[str]) -> tuple[str, str]:
    """从附录兜底块里抠出「（资料性）」与标题（块文本本身不动）。"""
    kind = ""
    title = ""
    for raw in lines:
        text = str(raw).strip()
        if not text:
            continue
        match = RE_APPENDIX_KIND.match(text)
        if match and not kind:
            kind = match.group(1)
            continue
        if not title:
            title = text
    return kind, title


def _appendix_letter_of(block: dict[str, Any]) -> str | None:
    """块条款号里的附录字母（``A.1``→A）；非附录小节号返回 None。"""
    match = RE_APPENDIX_CLAUSE.match(str(block.get("clause", "")))
    return match.group(0)[0] if match else None


def _annotate_appendix_blocks(
    blocks: list[dict[str, Any]], explicit_letters: set[str] | None = None
) -> None:
    """给附录区「标题/类别行被吞掉」的块记字母/类别/标题（D3 的定位依据）。

    判据（D-042 收敛后）：块位于附录区（``appendix`` 字母非空），且块文本里
    含独立成行的「（规范性）/（资料性）/**（规范性附录）**」标记——这类块正是
    v1 附录标题行被丢弃、标题/类别文字被挂到上一条正文条号上的 bug 类；只按
    标记行判定可避免把附录里的表格行、表注（「注 2：…」）、列表项（「a)…」）
    误标成 appendix（2026-10-29 全量细检的过宽问题）。

    M8b/task-12：「（资料性附录）」标记行出现在下一个小节标题**之前**，只有
    当下一节的小节号字母**没有**对应「# 附 录 X」标题行时（DB37/T 3145 的
    B.1/C.1），才把标记块改判给后一节；有显式标题的标准（GB/T 45420 的附录
    B…L、GB 15760 的附录 B）保持解析期字母，避免把标记块整体前移/后移。
    """
    explicit = set(explicit_letters or ())
    letters = [_appendix_letter_of(block) for block in blocks]
    for index, block in enumerate(blocks):
        if block.get("kind") != "clause":
            continue
        letter = block.get("appendix")
        if not letter:
            continue
        kind, title = _appendix_marker(block.get("lines", []))
        if not kind:
            continue
        inferred = next((letters[j] for j in range(index + 1, len(blocks)) if letters[j]), None)
        if inferred and inferred != letter and inferred not in explicit:
            letter = inferred
            block["appendix"] = letter
        block["appendix_detail"] = {"letter": letter, "kind": kind, "title": title}


def _relabel_appendix_blocks(blocks: list[dict[str, Any]]) -> None:
    """D-042：块文本不动，只把挂错块的 law_num / chunk_kind / extras 改对。"""
    for block in blocks:
        detail = block.get("appendix_detail")
        if not detail:
            continue
        block["law_num"] = f"附 录 {detail['letter']}"
        block["chunk_kind_override"] = "appendix"


def _table_block_to_unit(block: dict[str, Any], seq: int) -> Unit:
    """已渲染表格块 → v2 ``Unit``（``_to_units`` 与 ``parse_generic`` 共用）。"""
    clause = str(block.get("clause", ""))
    region = (
        "appendix"
        if (RE_APPENDIX_CLAUSE.match(clause) or clause.replace(" ", "").startswith("附录"))
        else "body"
    )
    meta: dict[str, Any] = {
        "seq": seq,
        "clause": block["clause"],
        "table_label": block["label"],
        "table_title": block["title"],
        "table_rows": block["rows"],
        "table_mode": block["mode"],
        "region": region,
        # M8b：复核页画真表格用的格子（v1 语义：rows 不含表头行）。
        # 只进 extras 侧车，不进块本体 15 字段，也不参与 chunk_hash。
        "table_grid": block.get("grid"),
        # Phase 1.3：表格 provenance —— 侧车补宽度与表头行数，检索/复核页
        # 不必从 table_grid 里再数一遍。
        "table_width": len(((block.get("grid") or {}).get("headers")) or []),
        "table_header_rows": int(((block.get("grid") or {}).get("header_rows")) or 0),
        # Phase 4：列名进侧车，检索时「按列名召回单元格值」不必再解析 grid。
        "table_headers": [
            str(name) for name in (((block.get("grid") or {}).get("headers")) or [])
        ],
    }
    return Unit(
        kind="table",
        ref=block["law_num"],
        title=block.get("title", ""),
        region=region,
        lines=[block["chunk"]],
        table=block.get("table_model"),
        meta=meta,
    )


def _to_units(blocks: list[dict[str, Any]]) -> list[Unit]:
    """块清单 → v2 Unit；``seq`` = 原文顺序流水号（与 v1 相同）。"""
    units: list[Unit] = []
    for seq, block in enumerate(blocks, start=1):
        if block["kind"] == "table":
            units.append(_table_block_to_unit(block, seq))
            continue
        clause = str(block.get("clause", ""))
        detail = block.get("appendix_detail")
        kind = "appendix" if detail else "clause"
        law_num = block.get("law_num") or block["clause"]
        meta = {"seq": seq, "clause": block["clause"], "region": "appendix" if detail else "body"}
        if detail:
            meta["appendix"] = dict(detail)
        units.append(
            Unit(
                kind=kind,
                ref=law_num,
                title="",
                region="appendix" if detail else "body",
                lines=list(block["lines"]),
                table=None,
                meta=meta,
            )
        )
    return units
