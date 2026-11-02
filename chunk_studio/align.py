"""chunk ↔ 源文件行 对齐（只读 JSON 模式，不依赖 fac_rag）。

为什么需要对齐
--------------
切块产物里**没有源行号**：chunk 正文是渲染清洗后的文本（去列表符/粗体/HTML、
按 ``max_chars`` 拆分、表格按行打包）。所以「原文 ↔ 切片」双向高亮只能靠启发式
反查，对齐结果分档：

``exact``       首行在源文件中精确命中
``substr``      首行是某源行的子串（或反之）
``table_rows``  表格块用 ``extras.table_grid.rows`` 逐行反查到源行
``table_label`` 表格块只命中表号/表题
``miss``        对不上，前端置灰、不假高亮

三层策略（与 docs/05-alignment.md 一致）：
1. 条文/段落块：规范化行序列单调扫描，首行剥面包屑，允许回看 2 行；
2. 表格块：``table_grid`` 行反查（去相邻重复单元格 / 去 ``|`` / HTML / 空白），
   超长单行（≥2000 字符，通常是整张 HTML 表）不推进游标，同一行的多行可复用；
3. 兜底：表号/表题定位；再退化为块正文首行扫描。
"""

from __future__ import annotations

import html
import re
from typing import Any, Iterable

RE_TAG = re.compile(r"<[^>]+>")
RE_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
RE_PAGE_TAIL = re.compile(r"[.．·…]{3,}\s*\d+\s*$")
RE_CONT_SUFFIX = re.compile(r"（续）\s*$")

DEFAULT_CLEAN_RULES = ("latex_escapes",)

#: 单行 ≥ 该字符数时视为「膨胀后的整张 HTML 表」，同一行可被多个表格块复用。
HUGE_LINE_CHARS = 2000


def strip_html(s: str) -> str:
    s = RE_TAG.sub("", s)
    return html.unescape(s)


def strip_media(s: str) -> str:
    """Markdown 图片 → ``[图示]``（与 render.strip_media 对齐）。"""
    return RE_IMG.sub("[图示]", s)


def clean_text(s: str, rules: Iterable[str] = DEFAULT_CLEAN_RULES) -> str:
    """近似 render.clean_text：只做匹配需要的几步（两边同规则，不追求完全一致）。"""
    rules = set(rules or ())
    if "latex_escapes" in rules:
        s = s.replace("\\(", "（").replace("\\)", "）").replace("\\[", "【").replace("\\]", "】")
    if "md_bold" in rules:
        s = s.replace("**", "")
    if "page_tail" in rules:
        s = RE_PAGE_TAIL.sub("", s)
    if "continuation_markers" in rules:
        s = RE_CONT_SUFFIX.sub("", s)
    return s


def norm_line(raw: str, rules: Iterable[str] = DEFAULT_CLEAN_RULES) -> str:
    """规范化：去 HTML / 图片占位 / 面包屑 → 去全部空白（两边同规则）。"""
    s = strip_html(strip_media(str(raw).strip()))
    if " > " in s:  # 标准式渲染给 unit.lines[0] 注入的面包屑前缀
        s = s.split(" > ")[-1]
    s = clean_text(s, rules)
    return "".join(s.split())


def norm_pipe(raw: str) -> str:
    s = strip_html(str(raw))
    return "".join(s.replace("|", "").split())


def _flatten(cells: Any) -> list[str]:
    flat: list[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, (list, tuple)):
            for y in x:
                walk(y)
        elif x is not None:
            flat.append(str(x))

    walk(cells)
    return flat


def _cells(row: Any) -> list[str]:
    return [c for c in ("".join(str(x).split()) for x in _flatten(row)) if c]


def _row_in_line(cells: list[str], line: str) -> bool:
    """按单元格顺序在行内找；rowspan/colspan 会让部分单元格只出现一次，
    顺序匹配失败时退化为“所有单元格都出现在这一行”。"""
    pos = 0
    for cell in cells:
        idx = line.find(cell, pos)
        if idx < 0:
            break
        pos = idx + len(cell)
    else:
        return True
    if len(cells) < 2:
        return False
    return all(cell in line for cell in cells)


def norm_row(cells: Any) -> str:
    return "".join("".join(_flatten(cells)).split())


def row_variants(cells: Any) -> list[str]:
    """grid 行 → 候选规范化字符串（合并单元格会导致相邻重复，去重一份备用）。"""
    flat = _flatten(cells)
    raw = "".join("".join(flat).split())
    dedup: list[str] = []
    for cell in flat:
        if dedup and dedup[-1] == cell:
            continue
        dedup.append(cell)
    variants = [raw]
    d = "".join("".join(dedup).split())
    if d and d != raw:
        variants.append(d)
    return [v for v in variants if v]


def _scan(lines: list[str], target: str, start: int, *, allow_substr: bool = True) -> int:
    """从 start 起找第一个等于 target（或包含 target）的行。

    单趟扫描、先到先得：优先保证「源行顺序」，而不是「全局找精确匹配」。
    （例：附录标题在源文里写作 ``# （资料性附录）``，若先全局找精确匹配
    会跳到文末的 ``（资料性附录）``，把后续表格块的游标带偏。）
    """
    if not target:
        return -1
    n = len(lines)
    for i in range(start, n):
        if lines[i] == target:
            return i
        if allow_substr and len(target) >= 2 and target in lines[i]:
            return i
    return -1


def _align_table(
    chunk: dict,
    extras: dict,
    ns: list[str],
    npipe: list[str | None],
    raw_lines: list[str],
    cursor: int,
) -> tuple[list[int] | None, str]:
    grid = (extras or {}).get("table_grid") or {}
    rows = grid.get("rows") or []
    if not rows:
        return None, "miss"

    start = max(0, cursor - 2)
    j = start
    first: int | None = None
    last: int | None = None
    limit = len(ns)
    for row in rows:
        variants = row_variants(row)
        if not variants:
            continue
        hit = -1
        for nr in variants:
            for k in range(j, limit):
                if npipe[k] is None:
                    npipe[k] = norm_pipe(raw_lines[k])
                ok = npipe[k] == nr
                if not ok and len(nr) >= 6:
                    ok = nr in (npipe[k] or "")
                if ok:
                    hit = k
                    break
            if hit >= 0:
                break
        if hit < 0:
            # 兜底：按单元格顺序匹配（rowspan/colspan 会让拼接串对不上整张表）
            cells = _cells(row)
            if cells:
                for k in range(j, min(limit, j + 400)):
                    if _row_in_line(cells, ns[k]):
                        hit = k
                        break
        if hit >= 0:
            if first is None:
                first = hit
            last = hit
            # 超长单行（整张 HTML 表）允许后续行继续命中同一行
            j = hit if len(raw_lines[hit]) >= HUGE_LINE_CHARS else hit + 1

    if first is None or last is None:
        return None, "miss"

    span = [first, last + 1]
    label = str((extras or {}).get("table_label") or "")
    title = str((extras or {}).get("table_title") or "")
    # 往上把表号/表题带进来（最多 6 行，且不越过上一块终点）
    for b in range(first - 1, max(cursor - 1, first - 7), -1):
        if b < 0:
            break
        line = ns[b]
        if (label and label.replace(" ", "") in line) or (title and title[:6] in line) or (
            line.startswith("表") and len(line) < 80
        ):
            span[0] = b
            break
    return span, "table_rows"


def align_chunks(
    chunks: list[dict],
    source_lines: list[str],
    extras: dict[str, dict] | None = None,
    *,
    rules: Iterable[str] = DEFAULT_CLEAN_RULES,
) -> list[dict]:
    """返回与 ``chunks`` 等长的 ``[{"span": [start, end) | None, "status": str}]``。"""
    extras = extras or {}
    ns = [norm_line(line, rules) for line in source_lines]
    npipe: list[str | None] = [None] * len(ns)
    cursor = 0
    out: list[dict] = []

    for ch in chunks:
        kind = str(ch.get("chunk_kind") or "")
        ex = extras.get(str(ch.get("id") or "")) or {}
        span: list[int] | None = None
        status = "miss"

        if kind == "table":
            span, status = _align_table(ch, ex, ns, npipe, source_lines, cursor)
            if span:
                cursor = max(cursor, span[1])

        if span is None:
            tlines = [t for t in (norm_line(x, rules) for x in str(ch.get("chunk") or "").split("\n")) if t]
            first_t = tlines[0] if tlines else ""
            start = max(0, cursor - 2)
            found = _scan(ns, first_t, start)
            if found < 0 and cursor > 0:
                found = _scan(ns, first_t, 0)
            if found >= 0:
                last = found
                j = found + 1
                for t in tlines[1:]:
                    k = j
                    limit = min(len(ns), j + 200)
                    while k < limit and ns[k] != t and (not t or t not in ns[k]):
                        k += 1
                    if k < limit:
                        last = k
                        j = k + 1
                span = [found, last + 1]
                status = "exact" if ns[found] == first_t else "substr"
                cursor = max(cursor, last + 1)

        if span is None and kind == "table":
            label = str(ex.get("table_label") or "")
            title = str(ex.get("table_title") or "")
            keys = [k.replace(" ", "") for k in (label, title) if len(k) >= 2]
            for i in range(max(0, cursor - 2), len(ns)):
                if any(k in ns[i] for k in keys):
                    span = [i, i + 1]
                    status = "table_label"
                    cursor = max(cursor, i + 1)
                    break

        out.append({"span": span, "status": status})

    return out


def summarize_align(aligned: list[dict]) -> dict:
    kinds: dict[str, int] = {}
    for item in aligned:
        status = item.get("status") or "miss"
        kinds[status] = kinds.get(status, 0) + 1
    n_aligned = sum(1 for item in aligned if item.get("span"))
    return {
        "n_aligned": n_aligned,
        "n_miss": len(aligned) - n_aligned,
        "align_kinds": kinds,
    }


def locate_text_span(
    norm_lines: list[str],
    text: str,
    *,
    lo: int = 0,
    hi: int | None = None,
    rules: Iterable[str] = DEFAULT_CLEAN_RULES,
) -> list[int] | None:
    """在 ``norm_lines[lo:hi]`` 窗口里按行顺序定位一段正文，返回 0-based ``[start, end)``。

    用于拆分 / 手工块补行区间：正文行是渲染清洗后的行，所以先用 ``norm_line``
    同口径规范化，再在窗口内做「首行扫描 + 后续行顺序匹配」，不跨出窗口。
    """
    tlines = [t for t in (norm_line(x, rules) for x in str(text or "").split("\n")) if t]
    if not tlines:
        return None
    n = len(norm_lines)
    hi = n if hi is None else max(0, min(hi, n))
    lo = max(0, min(lo, hi))
    if lo >= hi:
        return None
    first = tlines[0]
    found = -1
    for k in range(lo, hi):
        if norm_lines[k] == first or (len(first) >= 2 and first in norm_lines[k]):
            found = k
            break
    if found < 0:
        return None
    last = found
    j = found + 1
    for t in tlines[1:]:
        k = j
        while k < hi and norm_lines[k] != t and (not t or t not in norm_lines[k]):
            k += 1
        if k < hi:
            last = k
            j = k + 1
    return [found, last + 1]

