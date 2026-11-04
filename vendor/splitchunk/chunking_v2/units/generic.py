"""units.generic：generic handler（# 标题 + 空行 + 表格）。"""

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

from .table_section import scan_html_table, scan_pipe_table

from ..tables import (
    build_table_model,
    html_table_to_grid,
    is_continuation_grid,
    is_unit_note_line,
    model_from_store,
    pipe_table_to_grid,
    render_table_blocks,
)

from .common import (
    MAX_CHUNK_CHARS,
    RE_GENERIC_HEADING,
    RE_TABLE_CAP,
    ROW_PACK_CHARS,
    _looks_like_unlabeled_grid,
    _table_block_to_unit,
    _title_in_text,
    clean_standard_line,
)

def parse_generic(
    text: str,
    *,
    law_name: str = "",
    profile: Profile | None = None,
    captions: bool = False,
    row_pack_chars: int | None = None,
) -> ParseResult:
    """无编号兜底：``#`` 标题 + 空行切段，``kind=paragraph``；表格按结构识别。

    M8b / task-12 起：

    * ``profile.tables.unlabeled=true`` 时，HTML / 管道表格（含单行大表预展开后的
      多行 HTML）按结构信号识别成 ``TableModel`` 表块：表号按出现顺序「表1」「表2」…
      表题留空、不编造；``law_num`` 由渲染器生成（打包时「表N 第a-b行」）；
    * 段落 ``ref`` 不再造「序号 N」（清单/目录体里它会被误当成引用键），
      改为「标题」或「段N」。

    ``captions=True`` 时额外认表题行（``表A.1 标题``），把它绑到紧随其后的表格上，
    表号/表题不再留空；auto 档的附录等表区用这个开关，profile 档保持旧行为。
    """
    result = ParseResult(title=(law_name or "").strip() or _title_in_text(text))
    table_rule = getattr(profile, "tables", None)
    unlabeled = bool(getattr(table_rule, "unlabeled", False))
    tables_mode = str(getattr(table_rule, "mode", "model") or "model")
    model_tables = tables_mode == "model"
    row_overflow = str(getattr(table_rule, "row_overflow", "cell") or "cell")
    header_rows = max(0, int(getattr(table_rule, "header_rows", 1) or 0))
    row_pack_chars = int(
        row_pack_chars
        if row_pack_chars is not None
        else getattr(table_rule, "row_pack_chars", ROW_PACK_CHARS) or ROW_PACK_CHARS
    )
    max_chunk_chars = int(
        getattr(getattr(profile, "render", None), "max_chars", MAX_CHUNK_CHARS) or MAX_CHUNK_CHARS
    )

    raw_lines = text.splitlines()
    items: list[tuple[str, Any]] = []          # ("para", {"title","lines"}) | ("table", block)
    buf: list[str] = []
    title = ""
    table_seq = 0
    warns: list[str] = []
    cap: tuple[str, str, str] | None = None     # (label, title, raw line)：待绑定的表题

    def flush() -> None:
        nonlocal buf
        clean = [clean_standard_line(x) for x in buf]
        clean = [x for x in clean if x]
        buf = []
        if clean:
            items.append(("para", {"title": title, "lines": clean}))

    def append_table(grid: list[list[str]], clause: str) -> None:
        nonlocal table_seq, cap
        table_seq += 1
        bound = cap
        cap = None
        label = bound[0] if bound else f"表{table_seq}"
        title = bound[1] if bound else ""
        if header_rows > 0:
            model = TableModel(
                label=label,
                title=title,
                grids=[grid],
                header_rows=header_rows,
                row_pack_chars=row_pack_chars,
            )
        else:
            # header_rows=0：按表格自身形态自动判表头（含「属性行 + 列名行」两段式）
            model = build_table_model(
                label, title, [grid], row_pack_chars=row_pack_chars
            )
        rendered, _n_rows, _mode = render_table_blocks(
            model,
            clause=clause,
            max_chunk_chars=max_chunk_chars,
            row_pack_chars=row_pack_chars,
            row_overflow=row_overflow,
        )
        if not rendered:
            warns.append(f"表{table_seq}（无表题表格）渲染为空，已跳过")
            return
        flush()
        for block in rendered:
            block["clause"] = clause
            block["table_model"] = model
            items.append(("table", block))

    i = 0
    while i < len(raw_lines):
        raw = raw_lines[i]
        i += 1
        stripped = raw.strip()
        if not stripped:
            flush()
            continue
        heading = RE_GENERIC_HEADING.match(stripped)
        if heading:
            flush()
            title = clean_standard_line(heading.group(1))
            buf.append(title)
            continue

        if captions:
            cap_match = RE_TABLE_CAP.match(stripped)
            if cap_match:
                flush()
                cap = (
                    f"表{cap_match.group(1)}",
                    clean_standard_line(cap_match.group(2)),
                    stripped,
                )
                continue

        # 无表题 HTML 表（含 expand.py 预展开后的多行 HTML）：整段读到 </table>。
        if unlabeled and "<table" in stripped.lower():
            frag = scan_html_table(raw_lines, i - 1, model_tables=model_tables)
            if frag is not None:
                i = frag.next_index
                if _looks_like_unlabeled_grid(frag.grid):
                    append_table(frag.grid, title or f"段{len(items) + 1}")
                    continue

        # 无表题管道表：连续 | 行，结构合格才认。
        if unlabeled and stripped.startswith("|"):
            frag = scan_pipe_table(raw_lines, i - 1, model_tables=model_tables)
            if frag is not None:
                i = frag.next_index
                if _looks_like_unlabeled_grid(frag.grid):
                    append_table(frag.grid, title or f"段{len(items) + 1}")
                    continue

        buf.append(stripped)

    flush()
    if cap is not None:
        # 表题后面没跟表格：退回普通段落，不丢内容
        items.append(("para", {"title": "", "lines": [cap[2]]}))
        cap = None

    units: list[Unit] = []
    for seq, (kind, payload) in enumerate(items, start=1):
        if kind == "table":
            block = dict(payload)
            units.append(_table_block_to_unit(block, seq))
            continue
        item_title = str(payload.get("title") or "")
        ref = item_title or f"段{seq}"
        units.append(
            Unit(
                kind="paragraph",
                ref=ref,
                title=item_title,
                region="body",
                path=(item_title,) if item_title else (),
                lines=list(payload.get("lines") or []),
                table=None,
                meta={"seq": seq, "ref_title": item_title},
            )
        )
    result.units = units
    result.warnings = warns
    return result
