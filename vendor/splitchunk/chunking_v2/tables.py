# -*- coding: utf-8 -*-
"""fac_rag.chunking_v2.tables —— 第③步的表格模型（技术版 §7，M3 实装）。

职责
====

1. **来源解析**：管道表（``| … |``）与 HTML 表（``<table>``）→ ``grid: list[list[str]]``；
2. **表格模型**：``TableModel``（``grids`` = 主表 + 各跨页续表；``header_rows`` /
   ``col_names`` / ``row_pack_chars``）；一个 ``TableModel`` 可能由多张 grid 拼成
   （D4：同表跨页、表头重复、无表题的续表）；
3. **单元格清洗**（D5）：``**``、HTML 标签、``\\(`` 等 LaTeX 转义、行尾「（续）」
   在进入网格前就洗掉；块文本里因此不再残留 ``|`` / ``**`` / ``<tag>`` / 续页标记；
4. **行渲染**：每行一块（``列名：值 · 列名：值``），小表整表一块；``row_pack_chars``
   默认 700 攒包，超过才拆；``law_num`` 行窗口 = ``{clause} {label} 第 a-b 行``，
   表注单独成块（``注 n``）。

与 v1 :mod:`fac_rag.chunking.tables` 的关系
===========================================

* 语义（整表/打包阈值、行文本拼法、注行处理、law_num 窗口）逐条对齐 v1，
  目标是 ``tables.mode: model`` 时表格块数量级不变、内容更干净；
* 本模块**不复用** v1 的函数（M3 的既定目标），但旧行为仍可由
  ``profile.tables.mode=legacy`` 走 v1 实现（见 ``units.py`` 的分派），
  用于「等价模式」回归：关掉修复后仍应复现 M2 的 61/65 hash 等价。
"""

from __future__ import annotations

from .patterns import RE_TAG

import re
from html import unescape
from typing import Any, Iterable

from .model import TableModel
from .render import clean_text

# ---------------------------------------------------------------------------
# 正则 / 常量
# ---------------------------------------------------------------------------

RE_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
RE_IMG = re.compile(r"<img\b[^>]*?/?>", re.IGNORECASE)
RE_P_JOIN = re.compile(r"</p\s*>\s*<p\b[^>]*>", re.IGNORECASE)
RE_SEP_CELL = re.compile(r"^:?-{3,}:?$")
RE_ATTR = re.compile(
    r'([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))'
)
RE_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr\s*>", re.IGNORECASE | re.DOTALL)
RE_CELL = re.compile(r"<t([dh])\b([^>]*)>(.*?)</t\1\s*>", re.IGNORECASE | re.DOTALL)

#: 单元格清洗默认启用的规则（D5；不启用 page_tail —— 单元格里的「（1）」可能是表意内容）
DEFAULT_CELL_CLEAN: tuple[str, ...] = (
    "md_bold",
    "latex_escapes",
    "continuation_markers",
)

#: 「单位为日」「单位为 mm」这类跨行单位行：D4 里它夹在表与续表之间，
#: 模型模式下当表格元数据吞掉，不再当正文。
RE_UNIT_NOTE = re.compile(
    r"^单位为(?:日|年|月|小时|分钟|秒|毫米|厘米|米|吨|千克|克|%|％|人|次|个|件|元|万元)[。.]?$"
)

DEFAULT_MAX_CHUNK_CHARS = 800
DEFAULT_ROW_PACK_CHARS = 700


# ---------------------------------------------------------------------------
# 单元格 / 网格
# ---------------------------------------------------------------------------


def _attr_int(attrs: str, name: str) -> int:
    """从标签属性串里抠 ``rowspan`` / ``colspan``（非法或缺省 → 1）。"""
    for attr_name, v1, v2, v3 in RE_ATTR.findall(attrs or ""):
        if attr_name.lower() != name:
            continue
        raw = v1 or v2 or v3
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            return 1
        return value if value > 0 else 1
    return 1


def clean_cell_text(inner: str, *, rules: Iterable[str] = DEFAULT_CELL_CLEAN) -> str:
    """D5：单元格文本清洗（HTML 实体 / ``**`` / LaTeX 转义 / 续页标记）。"""
    text = str(inner or "")
    text = RE_IMG.sub("[图示]", text)
    text = RE_P_JOIN.sub("；", text)
    text = RE_BR.sub(" ", text)
    text = RE_TAG.sub("", text)
    text = unescape(text)
    text = clean_text(text, rules)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clean_title_text(text: str) -> str:
    """表题/表号清洗：同 ``clean_cell_text`` 但不压缩空白。

    v1 的表题来自 ``clean_line``（只 strip，不压空白），保留全角空格；
    因此这里不做 ``\\s+``→空格，避免「年　月　日」被改成「年 月 日」。
    """
    value = str(text or "")
    value = RE_IMG.sub("[图示]", value)
    value = RE_P_JOIN.sub("；", value)
    value = RE_BR.sub(" ", value)
    value = RE_TAG.sub("", value)
    value = unescape(value)
    value = clean_text(value, DEFAULT_CELL_CLEAN)
    return value.strip()


def pipe_table_to_grid(lines: Iterable[str]) -> list[list[str]]:
    """管道表 → 网格（去分隔行、去跨页重复表头、右侧补空对齐）。"""
    rows: list[list[str]] = []
    for raw in lines:
        text = str(raw).strip()
        if not text.startswith("|"):
            continue
        inner = text[1:-1] if text.endswith("|") else text[1:]
        cells = [clean_cell_text(cell) for cell in inner.split("|")]
        if cells and all(RE_SEP_CELL.match(cell.replace(" ", "")) for cell in cells):
            continue
        rows.append(cells)
    if not rows:
        return []
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    # 跨页重复表头：首行在段内再次出现时只保留第一份
    if len(rows) > 2:
        header = rows[0]
        deduped = [rows[0]]
        for row in rows[1:]:
            if row == header:
                continue
            deduped.append(row)
        rows = deduped
    return rows


def html_table_to_grid(table_html: str) -> list[list[str]]:
    """HTML 表 → 网格（``rowspan`` / ``colspan`` 抄满棋盘）。

    行定位用「已被 ``<tr>`` 开始过的行号」集合，而不是 ``len(grid)``：
    ``rowspan`` 会提前把后续行建出来当占位行，它们要等后面真正的 ``<tr>``
    来填；若拿 ``len(grid)`` 当行号，占位行会被当成「已开始的一行」，
    导致后续每个 ``<tr>`` 整体下移一行（旧实现的 bug）。格子是否已被
    ``rowspan`` 占住用 ``filled`` 布尔棋盘记，而不是靠「值是否为空」——
    空文本格子也算占位。
    """
    html = str(table_html or "")
    if not html.strip():
        return []
    grid: list[list[str]] = []
    filled: list[list[bool]] = []
    started: set[int] = set()

    def ensure_row(index: int) -> None:
        while len(grid) <= index:
            grid.append([])
            filled.append([])

    def ensure_col(row_idx: int, target: int) -> None:
        while len(grid[row_idx]) <= target:
            grid[row_idx].append("")
            filled[row_idx].append(False)

    for row_html in RE_ROW.findall(html):
        cells = RE_CELL.findall(row_html)
        if not cells:
            continue
        row_idx = 0
        while row_idx in started:  # 第一个还没被 <tr> 开始过的行 = 占位行
            row_idx += 1
        started.add(row_idx)
        ensure_row(row_idx)
        col = 0
        for _tag, attrs, inner in cells:
            while col < len(filled[row_idx]) and filled[row_idx][col]:
                col += 1
            text = clean_cell_text(inner)
            row_span = _attr_int(attrs, "rowspan")
            col_span = _attr_int(attrs, "colspan")
            for dr in range(row_span):
                ensure_row(row_idx + dr)
                for dc in range(col_span):
                    target = col + dc
                    ensure_col(row_idx + dr, target)
                    grid[row_idx + dr][target] = text
                    filled[row_idx + dr][target] = True
            col += col_span
    if not grid:
        return []
    width = max(len(row) for row in grid)
    grid = [row + [""] * (width - len(row)) for row in grid]
    while grid and not any(cell.strip() for cell in grid[-1]):
        grid.pop()
    return grid


# ---------------------------------------------------------------------------
# 表头 / 行渲染
# ---------------------------------------------------------------------------


def _is_span_row(row: list[str]) -> bool:
    """行是否由 colspan/rowspan 展开而来：非空单元格 ≥3 且存在相邻重复值。"""
    values = [str(cell).strip() for cell in row if str(cell).strip()]
    if len(values) < 3:
        return False
    best = run = 1
    for prev, cur in zip(values, values[1:]):
        run = run + 1 if prev == cur else 1
        best = max(best, run)
    return best >= 2


def _looks_like_label_row(row: list[str]) -> bool:
    """行是否像列名行：非空、彼此不同、短、无句末标点。"""
    values = [str(cell).strip() for cell in row if str(cell).strip()]
    if len(values) < 3 or len(set(values)) != len(values):
        return False
    return all(
        len(value) <= 24 and not value.endswith(("。", "；", ";", "，", ",", "：", ":"))
        for value in values
    )


def header_layout(grid: list[list[str]]) -> tuple[int, int]:
    """判定表头布局，返回 ``(meta_rows, header_rows)``。

    ``meta_rows`` 是表头之前的「属性行」数（colspan 展开的 工作分工/管辖范围 行，
    如 DB37/T 2972 表A.5/A.6）；``header_rows`` 是真正的列名行数。
    旧规则（前两行同列有相同非空值 → 2）保持不变。
    """
    if not grid:
        return (0, 1)
    lead = 0
    while lead < len(grid) and _is_span_row(grid[lead]):
        lead += 1
    if lead >= len(grid):
        return (0, 1)
    if lead >= 2:
        # 多行属性行（工作分工/管辖范围），真表头在其后
        if lead + 1 < len(grid) and _is_span_row(grid[lead + 1]):
            return (lead, 2)
        return (lead, 1)
    if lead == 1:
        # 单行 span：表头跨两行（工种 + 子表头）
        if len(grid) > 1 and _looks_like_label_row(grid[1]):
            return (0, 2)
        return (0, 1)
    # 无 span：沿用原两行表头规则
    row0 = grid[0]
    row1 = grid[1] if len(grid) > 1 else []
    for idx in range(min(len(row0), len(row1))):
        a, b = row0[idx].strip(), row1[idx].strip()
        if a and b and a == b:
            return (0, 2)
    return (0, 1)


def guess_header_rows(grid: list[list[str]]) -> int:
    """列名行数（兼容旧调用；表头前的属性行由 :func:`header_layout` 给出）。"""
    return header_layout(grid)[1]


def merge_header(grid: list[list[str]], header_rows: int, header_from: int = 0) -> list[str]:
    """多行表头逐列合并（``·`` 连接，重复值只留一个）。"""
    if not grid:
        return []
    start = max(0, min(int(header_from), len(grid) - 1))
    rows = grid[start:start + max(0, int(header_rows))]
    if not rows:
        return []
    width = max(len(row) for row in grid)
    names: list[str] = []
    for col in range(width):
        parts: list[str] = []
        for row in rows:
            value = row[col].strip() if col < len(row) else ""
            if value and value not in parts:
                parts.append(value)
        names.append("·".join(parts))
    return names


def is_note_row(row: list[str]) -> bool:
    """表注行：非空单元 ≥2 且全部相同（v1 同规则）。"""
    values = [cell.strip() for cell in row if cell.strip()]
    return len(values) >= 2 and len(set(values)) == 1


def render_row(breadcrumb: str, headers: list[str], cells: list[str]) -> str:
    """一行 → ``列名：值 · 列名：值`` 文本（空值跳过；空行返回空串，语义同 v1）。"""
    parts: list[str] = []
    for idx, cell in enumerate(cells):
        value = str(cell).strip()
        if not value:
            continue
        name = headers[idx].strip() if idx < len(headers) else ""
        parts.append(f"{name}：{value}" if name else value)
    for cell in cells[len(headers):]:
        value = str(cell).strip()
        if value:
            parts.append(value)
    if not parts:
        return ""
    prefix = f"{breadcrumb} · " if breadcrumb else ""
    return prefix + " · ".join(parts)


# ---------------------------------------------------------------------------
# 超长单行降级（Phase 1.1）
# ---------------------------------------------------------------------------

_CELL_SEP = " · "
_WRAP_PUNCT = "，；：、,;: "


def _cell_parts(headers: list[str], cells: list[str]) -> list[str]:
    """一行 → 「列名：值」片段（与 :func:`render_row` 同口径，空值跳过）。"""
    parts: list[str] = []
    for idx, cell in enumerate(cells):
        value = str(cell).strip()
        if not value:
            continue
        name = headers[idx].strip() if idx < len(headers) else ""
        parts.append(f"{name}：{value}" if name else value)
    for cell in cells[len(headers):]:
        value = str(cell).strip()
        if value:
            parts.append(value)
    return parts


def _hard_wrap(text: str, budget: int) -> list[str]:
    """按标点优先把一段文本硬切成 ≤ ``budget`` 的若干片（无标点就按字符切）。"""
    budget = max(1, int(budget))
    pieces: list[str] = []
    rest = str(text)
    while len(rest) > budget:
        window = rest[:budget]
        cut = max(window.rfind(ch) for ch in _WRAP_PUNCT)
        cut = cut + 1 if cut >= budget // 2 else budget
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        pieces.append(rest)
    return [piece for piece in pieces if piece]


def _split_oversize_row(headers: list[str], cells: list[str], budget: int) -> list[str]:
    """单行渲染文本超 ``budget`` 时按单元格边界拆段，单段仍超则硬切。

    返回非空片段列表；内容与 ``render_row("", headers, cells)`` 等价，只是
    分隔位置不同（不丢任何单元格内容）。
    """
    budget = max(1, int(budget))
    segments: list[str] = []
    cur = ""
    for part in _cell_parts(headers, cells):
        if len(part) > budget:
            if cur:
                segments.append(cur)
                cur = ""
            segments.extend(_hard_wrap(part, budget))
            continue
        trial = part if not cur else f"{cur}{_CELL_SEP}{part}"
        if len(trial) <= budget:
            cur = trial
        else:
            segments.append(cur)
            cur = part
    if cur:
        segments.append(cur)
    return [seg for seg in segments if seg]


# ---------------------------------------------------------------------------
# TableModel 组装
# ---------------------------------------------------------------------------


def build_table_model(
    label: str,
    title: str,
    grids: list[list[list[str]]],
    *,
    row_pack_chars: int = DEFAULT_ROW_PACK_CHARS,
) -> TableModel:
    """由网格拼出 ``TableModel``（表头行数 / 列名在这里定一次）。"""
    clean_grids = [list(grid) for grid in grids if grid]
    meta_rows, header_rows = header_layout(clean_grids[0]) if clean_grids else (0, 1)
    col_names = (
        merge_header(clean_grids[0], header_rows, header_from=meta_rows)
        if clean_grids
        else []
    )
    return TableModel(
        label=clean_title_text(label),
        title=clean_title_text(title),
        grids=clean_grids,
        header_rows=header_rows,
        meta_rows=meta_rows,
        col_names=col_names,
        row_pack_chars=row_pack_chars,
    )


def model_from_store(store: dict[str, Any], key: str, *, row_pack_chars: int) -> TableModel:
    """v2 解析器里的中间 store（label/title/grids）→ ``TableModel``。

    D4：解析时在表后吞掉的「单位为日」这类单位行，若表题本身没写单位，
    就并入表题（技术版 §7「跨行单位并入表题」）；表题已含「单位」则跳过。
    """
    entry = store[key]
    title = str(entry.get("title") or "")
    unit_note = str(entry.get("unit_note") or "").strip()
    if unit_note and "单位" not in title:
        title = f"{title}（{unit_note}）" if title else unit_note
    return build_table_model(
        entry.get("label", ""),
        title,
        list(entry.get("grids", [])),
        row_pack_chars=row_pack_chars,
    )


RE_SPACE_ANY = re.compile(r"\s+")
RE_PAREN_CONT = re.compile(r"[（(]\s*续\s*[)）]")


def _norm_header_cell(text: str) -> str:
    """表头归一化：去所有空白与「（续）」标记，用于宽容匹配。"""
    value = RE_PAREN_CONT.sub("", str(text))
    return RE_SPACE_ANY.sub("", value).strip()


def is_continuation_grid(
    model: TableModel | None,
    grid: list[list[str]],
    *,
    tolerance: float = 1.0,
) -> bool:
    """D4：这张无表题网格是不是上一张表的跨页续表。

    ``tolerance=1.0``（默认）保持旧行为：首行表头逐格完全相等、宽度一致；
    ``tolerance<1.0`` 改为「合并列名归一化后按列匹配率」判定，容忍跨页表头
    出现的空格差异与「（续）」标记（宽度不等仍直接 False）。
    """
    if model is None or not model.grids or not grid or not grid[0]:
        return False
    head = model.grids[0][0] if model.grids[0] else []
    if not head or len(head) != len(grid[0]):
        return False
    if tolerance >= 1.0:
        return [cell.strip() for cell in grid[0]] == [cell.strip() for cell in head]

    left = merge_header(model.grids[0], model.header_rows, header_from=model.meta_rows)
    right = merge_header(grid, guess_header_rows(grid))
    if not left or len(left) != len(right):
        return False
    matched = sum(1 for a, b in zip(left, right) if _norm_header_cell(a) == _norm_header_cell(b))
    return matched / len(left) >= tolerance


def is_unit_note_line(text: str) -> bool:
    """「单位为日」这类跨行单位行（D4 修复时吞掉，不当正文）。"""
    return bool(RE_UNIT_NOTE.match(str(text).strip()))


# ---------------------------------------------------------------------------
# 渲染：TableModel → 块
# ---------------------------------------------------------------------------


def _table_breadcrumb(label: str, title: str, clause: str, std_no: str) -> str:
    label_title = f"{label} {title}".strip() or label
    if std_no:
        return f"{std_no} · {clause} · {label_title}"
    return f"{clause} · {label_title}"


def _meta_row_texts(rows: list[list[str]]) -> list[str]:
    """表头前的属性行（colspan 展开的合并行）→ 「标签：值」文本。"""
    texts: list[str] = []
    for row in rows:
        cells = [str(cell).strip() for cell in row]
        filled = [cell for cell in cells if cell]
        if not filled:
            continue
        label = cells[0] if cells and cells[0] else ""
        value = next((cell for cell in filled if cell != label), filled[0])
        if label and value and label != value:
            texts.append(f"{label}：{value}")
        else:
            texts.append(value or label)
    return texts


def render_table_blocks(
    model: TableModel,
    *,
    clause: str,
    std_no: str = "",
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    row_pack_chars: int = DEFAULT_ROW_PACK_CHARS,
    row_overflow: str = "cell",
) -> tuple[list[dict[str, Any]], int, str]:
    """``TableModel`` → 表格块（块 dict 形状与 v1 ``table_to_blocks`` 兼容）。

    ``row_overflow``：单行渲染文本超 ``max_chunk_chars`` 时的降级方式。
    ``"cell"``（默认）按单元格边界拆段、单段仍超再按标点硬切，保证每块
    ≤ ``max_chunk_chars``；``"hard"`` 保持旧行为（超长行独占一块）。
    """
    if not model.grids:
        return [], 0, "空表"

    label = model.label
    title = model.title
    headers = model.col_names or merge_header(
        model.grids[0], model.header_rows, header_from=model.meta_rows
    )
    breadcrumb = _table_breadcrumb(label, title, clause, std_no)
    # 表头前的属性行（DB37/T 2972 表A.5/A.6 的「工作分工/管辖范围」）渲染成前缀行，
    # 既不进列名，也不当普通数据行。
    meta_texts = _meta_row_texts(model.grids[0][: model.meta_rows])

    row_texts: list[str] = []
    note_texts: list[str] = []
    row_cells: list[list[str]] = []
    note_cells: list[list[str]] = []
    for gi, grid in enumerate(model.grids):
        meta_rows = model.meta_rows if gi == 0 else 0
        header_rows = model.header_rows if gi == 0 else guess_header_rows(grid)
        for row in grid[meta_rows + header_rows:]:
            if not any(str(cell).strip() for cell in row):
                continue
            if is_note_row(row):
                first = next(str(cell).strip() for cell in row if str(cell).strip())
                note_texts.append(f"{breadcrumb} · {first}")
                note_cells.append([str(cell) for cell in row])
            else:
                # 与 v1 一致：行文本不带面包屑，面包屑在块首放一次（整表/每包）。
                row_texts.append(render_row("", headers, row))
                row_cells.append([str(cell) for cell in row])

    if not row_texts and not note_texts:
        return [], 0, "空表"

    # 复核页画表格用的格子（D-010 / M8b）：**只写进块的 ``grid``**，落到 extras
    # 侧车供人眼复核，不参与 chunk 正文、chunk_hash 与台账指纹。
    # 与 v1 语义对齐：``rows`` 只放本块覆盖的 body/注行，**不含表头行**；
    # ``note_from`` 是「本块里注行从第几行开始」的下标。整表块 = 全部行 + 注行，
    # 打包块 = 本包那几行，注块 = 单独一行注。这样复核页把同表多个块拼起来
    # 就能还原整张表，块边界单独标注。
    width = max(
        [len(headers)] + [len(r) for r in row_cells] + [len(r) for r in note_cells]
    )
    hdr = list(headers) + [""] * (width - len(headers))

    def _norm(row: list[str]) -> list[str]:
        return [str(cell) for cell in row] + [""] * (width - len(row))

    def _grid(rows: list[list[str]], note_from: int) -> dict[str, Any]:
        return {
            "headers": hdr,
            "header_rows": model.header_rows,
            "meta_rows": model.meta_rows,
            "width": width,
            "rows": [_norm(row) for row in rows],
            "note_from": note_from,
        }

    label_title = f"{label} {title}".strip() or label

    # 整表一块（v1 同阈值：breadcrumb + 属性行 + 全部行 + 注，按 \n 连接后的字符数）
    joined = "\n".join([breadcrumb] + meta_texts + row_texts + note_texts)
    if len(joined) <= max_chunk_chars:
        rows_n = len(row_texts)
        whole_law_num = (
            f"{clause} {label} 第1-{rows_n}行".strip()
            if rows_n
            else f"{clause} {label}".strip()
        )
        blocks = [
            {
                "kind": "table",
                "clause": clause,
                "law_num": whole_law_num,
                "chunk": joined,
                "label": label,
                "title": title,
                "rows": len(row_texts),
                "mode": "整表",
                "grid": _grid(row_cells + note_cells, len(row_cells)),
            }
        ]
        return blocks, len(row_texts), "整表"

    blocks = []
    # Phase 1.1：单行渲染文本超 max_chunk_chars 时先按单元格边界拆段，
    # 否则 pack 为空时该行会被无条件塞入，产出一个 over_max 块。
    overflow_cell = str(row_overflow or "cell").strip().lower() == "cell"
    items: list[tuple[str, list[str] | None, int, bool]] = []
    for src_idx, (text, cells) in enumerate(zip(row_texts, row_cells), start=1):
        budget = max_chunk_chars - len(breadcrumb) - 1
        if overflow_cell and budget > 0 and len(text) > budget:
            for seg_idx, seg in enumerate(_split_oversize_row(headers, cells, budget)):
                items.append((seg, cells if seg_idx == 0 else None, src_idx, seg_idx == 0))
        else:
            items.append((text, cells, src_idx, True))

    # 属性行只随第一包出现（与「面包屑只放包首」同口径）
    pack: list[str] = list(meta_texts)
    pack_cells: list[list[str]] = []
    pack_rows = 0
    pack_start = 1
    pack_end = 1
    size = sum(len(text) + 1 for text in pack)

    def flush_pack() -> None:
        nonlocal pack, pack_cells, pack_rows, size
        if not pack:
            return
        blocks.append(
            {
                "kind": "table",
                "clause": clause,
                "law_num": f"{clause} {label} 第{pack_start}-{pack_end}行".strip(),
                "chunk": "\n".join([breadcrumb] + pack),
                "label": label,
                "title": title,
                "rows": pack_rows,
                "mode": "打包",
                "grid": _grid(pack_cells, len(pack_cells)),
            }
        )
        pack = []
        pack_cells = []
        pack_rows = 0
        size = 0

    for text, cells, src_idx, is_first in items:
        # 与 v1 同口径：面包屑只放包首，算长度时计入一次。
        trial = len(breadcrumb) + size + len(text) + 1
        if pack and trial > row_pack_chars:
            flush_pack()
            pack_start = src_idx
        pack.append(text)
        pack_end = src_idx
        if is_first and cells is not None:
            pack_cells.append(cells)
            pack_rows += 1
        size += len(text) + 1
    flush_pack()
    for note_idx, text in enumerate(note_texts, start=1):
        first_cell = next(
            (str(cell).strip() for cell in note_cells[note_idx - 1] if str(cell).strip()),
            "",
        )
        blocks.append(
            {
                "kind": "table",
                "clause": clause,
                "law_num": f"{clause} {label} 注{note_idx}".strip(),
                "chunk": text,
                "label": label,
                "title": title,
                "rows": 0,
                "mode": "注",
                "grid": _grid([[first_cell]], 0),
            }
        )
    return blocks, len(row_texts), "打包"


__all__ = [
    "DEFAULT_CELL_CLEAN",
    "DEFAULT_MAX_CHUNK_CHARS",
    "DEFAULT_ROW_PACK_CHARS",
    "RE_UNIT_NOTE",
    "build_table_model",
    "clean_cell_text",
    "guess_header_rows",
    "html_table_to_grid",
    "is_continuation_grid",
    "is_note_row",
    "is_unit_note_line",
    "merge_header",
    "model_from_store",
    "pipe_table_to_grid",
    "render_row",
    "render_table_blocks",
]
