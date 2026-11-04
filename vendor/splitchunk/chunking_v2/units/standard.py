"""units.standard：GB/行业标准 handler（3.1.2 / 附录 A）。"""

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
    ANCESTOR_LEVELS,
    DROP_APPENDIX,
    KEEP_FRONT_MATTER,
    MAX_CHUNK_CHARS,
    RE_APPENDIX_CLAUSE,
    RE_TABLE_CAP,
    RE_TABLE_CAP_CONT,
    RE_TABLE_CAP_CONT_V1,
    RE_TABLE_CAP_EMBEDDED,
    RE_TABLE_CAP_V1,
    RE_TOC_ENTRY,
    RE_TOC_HEAD,
    RE_YEAR_ONLY,
    ROW_PACK_CHARS,
    _annotate_appendix_blocks,
    _clause_key,
    _derive_table_title,
    _looks_like_derived_grid,
    _looks_like_unlabeled_grid,
    _merge_bare_titles,
    _next_pipe_within,
    _relabel_appendix_blocks,
    _std_no,
    _title_in_text,
    _to_units,
    clean_standard_line,
)

def _truncate_commentary(lines: list[str]) -> tuple[list[str], list[str]]:
    """「条文说明」及其后内容截断（与 v1 完全一致），返回 ``(lines, warns)``。"""
    warns: list[str] = []
    seen_body_heading = False
    for idx, raw in enumerate(lines):
        stripped = raw.lstrip()
        cl = clean_standard_line(raw)
        if stripped.startswith("#") and RE_CLAUSE.match(cl):
            seen_body_heading = True
        if RE_COMMENTARY.match(cl) and (seen_body_heading or idx >= 100):
            lines = lines[:idx]
            warns.append(f"「{cl}」之后是条文说明，已截断不参与切块")
            break
    return lines, warns


def _heading_clause_mode(lines: list[str]) -> bool:
    """标题式条号判定（与 v1 完全一致）。"""
    return any(
        RE_CLAUSE.match(clean_standard_line(raw)) and raw.lstrip().startswith("#")
        for raw in lines
    )


def _new_clause_block(
    clause: str,
    body: list[str],
    *,
    via_clause: bool,
    appendix: str | None,
) -> dict[str, Any]:
    """新条款块（原 ``_new_cur`` 的纯函数版）。"""
    return {
        "kind": "clause",
        "clause": clause,
        "lines": body,
        "via_clause": via_clause,
        "appendix": appendix,
    }


def _aligned_grid(
    entry: dict[str, Any], grid: list[list[str]]
) -> tuple[list[list[str]], bool]:
    """D-071/D-072：续表列数漂移按首表列数补齐/截断；返回 ``(grid, 是否漂移)``。"""
    first_grid = (entry.get("grids") or [[]])[0]
    store_width = len(first_grid[0]) if first_grid else 0
    width = len(grid[0]) if grid else 0
    if store_width < 2 or width == store_width:
        return grid, False
    if width < store_width:
        return [row + [""] * (store_width - width) for row in grid], True
    return [row[:store_width] for row in grid], True


def _unique_table_key(store: dict[str, Any], base: str) -> str:
    """同名表题但中间隔着内容/另一张表时的唯一 store 键（防覆盖旧表，D-072）。"""
    if base not in store:
        return base
    suffix = 2
    while f"{base}#{suffix}" in store:
        suffix += 1
    return f"{base}#{suffix}"


def _parse_standard_blocks(
    md_text: str,
    std_no: str,
    *,
    max_chunk_chars: int = MAX_CHUNK_CHARS,
    row_pack_chars: int = ROW_PACK_CHARS,
    keep_front_matter: bool = KEEP_FRONT_MATTER,
    drop_appendix: str = DROP_APPENDIX,
    tables_mode: str = "model",
    unlabeled: bool = False,
    derive_title: bool = False,
    cont_tolerance: float = 1.0,
    row_overflow: str = "cell",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """整篇正文 → 块清单（``table_ref`` 欠条最后统一兑现）。

    语义 = v1；``tables_mode="model"``（M3 / D-043）额外做两件事：

    * **D4**：没有表题的跨页续表（表头行与主表首行相同、宽度一致）并回上一张
      表；表与续表之间夹的「单位为日」这类单位行当表格元数据吞掉，不再进正文；
    * **D5**：单元格清洗与 ``TableModel`` 渲染（见 :mod:`fac_rag.chunking_v2.tables`）。

    ``tables_mode="legacy"`` 时表格分支退化为 v1 行为（无 D4、无 D5），用于
    「等价模式」回归。
    """
    model_tables = tables_mode == "model"
    # D4b 表题后缀/粘行表题属「表格链路」：legacy 模式必须与 v1 完全一致，
    # 否则等价模式回归会多出计划外变化。
    cap_re = RE_TABLE_CAP if model_tables else RE_TABLE_CAP_V1
    cap_cont_re = RE_TABLE_CAP_CONT if model_tables else RE_TABLE_CAP_CONT_V1
    store: dict[str, dict[str, Any]] = {}
    blocks: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    warns: list[str] = []
    # D-071：派生表名按附录字母编号（表A.1 / 表B.1…），pending_key 让
    # 同名的不同附录表各自占一个 store 键。
    derived_seq: dict[str, int] = {}
    pending_key: str | None = None
    # D-071：HTML 跨页续表的列数漂移（同一逻辑表不同页列数不一致）计数——只汇总
    # 一条 warning，避免逐表刷满 gate 的 warnings 阈值。
    drift_count = 0

    # M8a：静默丢弃点计数。这些行原先直接 ``continue`` 不留痕迹，导致「表格/目次
    # 被压平」在 gate 上表现为假绿；现在聚合成一条 warning 经 parsed.warnings
    # 进 manifest/gate（只加一条，避免把 warnings 顶过 gate 阈值 5）。
    drop_counts: dict[str, int] = {
        "html_table_no_caption": 0,
        "pipe_table_skipped_area": 0,
        "pipe_table_no_clause": 0,
        "line_before_any_clause": 0,
        "toc_entry_skipped": 0,
        "content_skipped_area": 0,
    }
    drop_labels = {
        "html_table_no_caption": "无表题的 HTML 表",
        "pipe_table_skipped_area": "前言/跳过附录区的管道表",
        "pipe_table_no_clause": "没有可挂靠条号的管道表",
        "line_before_any_clause": "出现在任何条号之前的正文行",
        "toc_entry_skipped": "目次条目形态的行",
        "content_skipped_area": "前言/跳过附录区的正文行",
    }

    cur: dict[str, Any] | None = None
    last_key: tuple[int, ...] | None = None
    last_clause = ""
    clause = ""

    in_toc = False
    in_front = False
    skip_appendix = False
    appendix_letter: str | None = None

    pending_cap: tuple[str, str, bool] | None = None
    last_cap_key = ""
    #: D4：上一张已注册主表（model 模式用来识别无表题跨页续表）
    last_table_key: str | None = None
    #: D4b：表题是从附录正文里「粘行」识别的（GBT 6067.1 型），表块 law_num
    #: 只能用「附 录 X」而不是上一条正文条号。
    pending_appendix_only = False
    #: M8b / task-12：无表题表格按出现顺序编号「表1」「表2」…（标题留空）。
    unlabeled_seq = 0

    i = 0
    lines = md_text.splitlines()

    lines, pre_warns = _truncate_commentary(lines)
    warns.extend(pre_warns)
    heading_mode = _heading_clause_mode(lines)

    def _new_cur(cur_clause: str, lines_: list[str], *, via_clause: bool) -> dict[str, Any]:
        return _new_clause_block(
            cur_clause,
            lines_,
            via_clause=via_clause,
            appendix=appendix_letter,
        )

    def _align_to_store_width(entry: dict[str, Any], grid: list[list[str]]) -> list[list[str]]:
        nonlocal drift_count
        aligned, drifted = _aligned_grid(entry, grid)
        if drifted:
            drift_count += 1
        return aligned

    def _new_table_key(base: str) -> str:
        return _unique_table_key(store, base)

    def _register_grid(grid: list[list[str]]) -> None:
        nonlocal cur, pending_cap, last_cap_key, last_table_key, pending_appendix_only, pending_key
        label, title, is_cont = pending_cap  # type: ignore[misc]
        pending_cap = None
        # D-072：附录区里的表格统一用「附 录 X」当引用键，别挂到上一条正文条号。
        # 否则表题在附录、表却记成「9.2 表A.5 注1」这种错挂，还会被 toc_leak 误报成
        # 目录泄漏（DB37/T 2972 的 表A.5/表A.6）。
        if appendix_letter and not RE_APPENDIX_CLAUSE.match(clause):
            table_clause = f"附 录 {appendix_letter}"
        else:
            table_clause = clause
        pending_appendix_only = False
        base = RE_CONT.sub("", title).strip()
        if is_cont and not base:
            base = last_cap_key
        if not base:
            # M8b/task-12 的无表题表格：label 形如「表N」、标题为空。
            # 必须拿 label 当 store 键，否则同一文件里多张无表题表会全塌到 key=""，
            # 后注册的覆盖先注册的，产物里只剩最后一张表（D-070 修复）。
            base = label
        store_key = pending_key or base
        pending_key = None
        if is_cont:
            if last_cap_key and last_cap_key in store:
                entry = store[last_cap_key]
                entry["grids"].append(_align_to_store_width(entry, grid))
            else:
                warns.append(f"{label} 是续表，但没找到主表「{base}」，已单独成表")
                store[store_key] = {"label": label, "title": base, "clause": table_clause, "grids": [grid]}
                last_cap_key = store_key
                blocks.append({"kind": "table_ref", "key": store_key, "clause": table_clause})
            return
        if (
            store_key in store
            and store_key == last_cap_key
            and blocks
            and blocks[-1].get("key") == store_key
        ):
            # D-072：同一表题紧挨着再一次出现（页内被拆成多张 HTML 表 / 重复表题行）
            # → 并入同一张逻辑表，且不再追加 table_ref；否则同一张表被渲染两遍、
            # 后渲染的块被正文去重丢掉（DB37/T 3132 表D.1 的 5 条重复 warning 即此）。
            entry = store[store_key]
            entry["grids"].append(_align_to_store_width(entry, grid))
            return
        store_key = _new_table_key(store_key)
        store[store_key] = {"label": label, "title": base, "clause": table_clause, "grids": [grid]}
        last_cap_key = store_key
        last_table_key = store_key
        if cur is not None:
            blocks.append(cur)
            cur = None
        blocks.append({"kind": "table_ref", "key": store_key, "clause": table_clause})

    while i < len(lines):
        raw = lines[i]
        i += 1
        line = raw.strip()
        if not line:
            continue
        c0 = clean_standard_line(line)

        # ---- 1) 目次 ----
        if RE_TOC_HEAD.match(c0):
            in_toc = True
            continue

        # ---- 2) 目次结束 ----
        if in_toc:
            if RE_FRONT.match(c0) or RE_APPENDIX.match(c0):
                in_toc = False
            elif RE_CLAUSE.match(c0) and not (
                RE_TOC_ENTRY.search(raw.strip()) or RE_TOC_PAGE_PAREN.search(raw.strip())
            ):
                in_toc = False
            else:
                continue

        # ---- 3) 参考文献：收工 ----
        if RE_REFS.match(c0):
            break

        # ---- 4) 附录标题 ----
        match = RE_APPENDIX.match(c0)
        if match:
            appendix_letter = match.group(1)
            skip_appendix = bool(drop_appendix) and appendix_letter in drop_appendix
            in_front = False
            if not skip_appendix and cur is not None:
                blocks.append(cur)
                cur = None
            continue

        # ---- 5) HTML 表格 ----
        if "<table" in line.lower():
            # D-071：表名派生要落在「表格起始行」上，否则从表尾往回扫会先撞 </table>。
            table_start = i - 1
            frag = scan_html_table(lines, table_start, model_tables=model_tables)
            if frag is None:  # 行内已确认含 <table>，正常不会发生
                continue
            line = frag.text
            grid = frag.grid
            i = frag.next_index
            if pending_cap is None:
                grid_area_ok = not (in_front or skip_appendix) and _looks_like_derived_grid(grid)
                if grid_area_ok:
                    if unlabeled and _looks_like_unlabeled_grid(grid):
                        unlabeled_seq += 1
                        pending_cap = (f"表{unlabeled_seq}", "", False)
                        # 注意：这里不把引用键归到「附 录 X」——附录内无表题表若改用
                        # 「附 录 X 表N」，law_num 过不了 is_law_num_parseable，
                        # check_sane 会因可解析比例骤降而拒绝整份文件（D-070 只修 store
                        # 键冲突；附录归属等给表补表题后再自然解决）。
                        pending_appendix_only = False
                        last_table_key = None
                        _register_grid(grid)
                        continue
                    # D-071：附录区无表题 HTML 表——从最近标题/加粗行派生表名，
                    # 按附录字母编号「表A.1」；同名或紧邻同表头的表按跨页续表合并。
                    if derive_title and appendix_letter:
                        derived = _derive_table_title(lines, table_start)
                        if derived and (
                            RE_APPENDIX_CLAUSE.match(derived) or RE_CLAUSE.match(derived)
                        ):
                            # 派生到的是「C.1 重大风险点统计表」这类小节标题 → 不是表名，
                            # 继续走 M8b 的小节号反推（保 DB37/T 3145 的 表C.1 口径）。
                            derived = ""
                        # D-071：派生标题自带表号时（如「表G.1 作业活动风险分级管控统计清单」）
                        # 拆成「标签 + 真名」，标签优先用它，避免表号与表名混在一起。
                        label_hint = ""
                        if derived:
                            m_label = re.match(
                                r"^表\s*([0-9A-Z]+(?:\.[0-9]+)*)\s*[.、]?\s*(.+)$", derived
                            )
                            if m_label and m_label.group(2).strip():
                                label_hint = f"表{m_label.group(1)}"
                                derived = m_label.group(2).strip()
                        pre_cont = any(
                            RE_CONT.match(lines[k].strip())
                            for k in range(max(0, table_start - 4), table_start)
                        )
                        same_title = bool(derived) and last_table_key in store and (
                            store[last_table_key].get("title") == derived
                        )
                        width = len(grid[0]) if grid else 0
                        store_width = 0
                        if last_table_key in store:
                            first_grid = (store[last_table_key].get("grids") or [[]])[0]
                            store_width = len(first_grid[0]) if first_grid else 0
                        # D-071：附录里连续的 HTML 表（同附录字母 + 同列宽 + 无新表名/同名/
                        # 前面有（续）标记）视为同一张逻辑表的跨页续表。表头以第一张为准，
                        # 后续 grid 的表头行由 tables.render_table_blocks 用
                        # guess_header_rows 跳过（与 v1 续表合并口径一致）。
                        cont_ok = (
                            bool(appendix_letter)
                            and width >= 2
                            and store_width >= 2
                            and (same_title or not derived or pre_cont)
                        )
                        if cont_ok and last_table_key in store:
                            if width != store_width:
                                # 跨页续表偶有列数漂移（某页缺列/并列）：按首表列数补齐或
                                # 截断；只计数，末尾汇总成一条 warning。
                                drift_count += 1
                                if width < store_width:
                                    grid = [
                                        row + [""] * (store_width - width) for row in grid
                                    ]
                                else:
                                    grid = [row[:store_width] for row in grid]
                            store[last_table_key]["grids"].append(grid)
                            # 续表前夹的「（续）」标记行不当正文
                            if cur is not None and cur.get("lines"):
                                tail = str(cur["lines"][-1]).strip()
                                if tail and RE_CONT.match(clean_standard_line(tail)):
                                    cur["lines"].pop()
                                    if not cur["lines"]:
                                        cur = None
                            continue
                        if derived:
                            derived_seq[appendix_letter] = derived_seq.get(appendix_letter, 0) + 1
                            derived_label = label_hint or f"表{appendix_letter}.{derived_seq[appendix_letter]}"
                            pending_cap = (derived_label, derived, False)
                            # D-071：派生表名必然来自附录区，引用键用「附 录 X」，
                            # law_num 形如「附 录 A 表A.1 第1-4行」（RE_TABLE_ROW 已同步放宽）。
                            pending_appendix_only = True
                            last_table_key = None
                            pending_key = f"{appendix_letter}::{derived_label}::{derived}"
                            _register_grid(grid)
                            continue
                    # M8b/task-12：附录小节（如「C.1 重大风险点统计表」）里的无表题
                    # 表格，用小节号反推表号（表C.1）——正文作业票表单不受影响。
                    appendix_clause = str(cur.get("clause", "")) if cur is not None else ""
                    if appendix_letter and cur is not None and RE_APPENDIX_CLAUSE.match(appendix_clause):
                        title = str(cur["lines"][0]).strip() if cur.get("lines") else ""
                        pending_cap = (f"表{appendix_clause}", title, False)
                        pending_appendix_only = False
                        last_table_key = None
                        _register_grid(grid)
                        continue
                drop_counts["html_table_no_caption"] += 1
                continue
            if not grid:
                warns.append(f"{pending_cap[0]} 表格没解析出任何行，已跳过")
                pending_cap = None
                continue
            _register_grid(grid)
            continue

        # ---- 5b) 管道表格 ----
        if line.startswith("|"):
            frag = scan_pipe_table(lines, i - 1, model_tables=model_tables)
            if frag is None:  # 行首已确认是 |，正常不会发生
                continue
            buf = frag.lines
            grid = frag.grid
            i = frag.next_index
            if pending_cap is None:
                # D4：无表题的跨页续表（表头行与上一张主表首行相同、宽度一致）
                if model_tables and last_table_key and last_table_key in store:
                    model = model_from_store(store, last_table_key, row_pack_chars=row_pack_chars)
                    if is_continuation_grid(model, grid, tolerance=cont_tolerance):
                        store[last_table_key]["grids"].append(grid)
                        continue
                if unlabeled and not (in_front or skip_appendix):
                    if _looks_like_unlabeled_grid(grid):
                        unlabeled_seq += 1
                        pending_cap = (f"表{unlabeled_seq}", "", False)
                        pending_appendix_only = False
                        last_table_key = None
                        _register_grid(grid)
                        continue
                if in_front or skip_appendix:
                    drop_counts["pipe_table_skipped_area"] += 1
                    continue
                if cur is None:
                    if not last_clause:
                        drop_counts["pipe_table_no_clause"] += 1
                        continue
                    cur = _new_cur(last_clause, [], via_clause=False)
                cur["lines"].extend(clean_standard_line(x) for x in buf)
                continue
            if not grid:
                warns.append(f"{pending_cap[0]} 表格没解析出任何行，已跳过")
                pending_cap = None
                continue
            _register_grid(grid)
            continue

        line = c0
        if not line:
            continue

        # ---- 6) 前言 / 引言 ----
        if RE_FRONT.match(line):
            in_front = not keep_front_matter
            continue

        # ---- 7) 表题 ----
        match = cap_re.match(line)
        if match:
            in_front = False
            pending_cap = (f"表{match.group(1)}", match.group(2), bool(RE_CONT.search(match.group(2))))
            last_table_key = None
            continue
        match = cap_cont_re.match(line)
        if match:
            in_front = False
            pending_cap = (f"表{match.group(1)}", "", True)
            continue
        # D4b：附录内「表题粘在正文行中间」的形态（GBT 6067.1 L1881）。判据从严：
        # 附录区 + 行首不是「表」 + 行内无句读 + 行尾非标点 + 后面紧跟管道表。
        if model_tables and appendix_letter and pending_cap is None and not line.startswith("表"):
            embedded = RE_TABLE_CAP_EMBEDDED.search(line)
            if (
                embedded
                and "。" not in line
                and "，" not in line
                and not line.endswith(("；", ";", "，", "。"))
                and _next_pipe_within(lines, i, 8)
            ):
                in_front = False
                pending_cap = (f"表{embedded.group(1)}", embedded.group(2), False)
                pending_appendix_only = True
                last_table_key = None
                continue

        # ---- 8) 章条号 ----
        match = RE_CLAUSE.match(line)
        if match:
            if RE_TOC_ENTRY.search(raw.strip()):
                drop_counts["toc_entry_skipped"] += 1
                continue
            num = (match.group(1) + "." if match.group(1) else "") + match.group(2)
            # M8b/task-12：附录字母反推。有的标准只在首个附录写「附 录 A」，
            # 后面用「（资料性附录）」+「B.1/B.2…」小节分隔，没有「附 录 B」标题行；
            # 已进入附录区（appendix_letter 非空）后，小节号的字母前缀即所属附录字母。
            if match.group(1) and appendix_letter:
                appendix_letter = match.group(1)
                skip_appendix = bool(drop_appendix) and appendix_letter in drop_appendix
            if RE_YEAR_ONLY.match(num):
                continue
            if heading_mode and not raw.lstrip().startswith("#"):
                sub_levels = len(num.split("."))
                if not match.group(1) and sub_levels < 2:
                    if in_front or skip_appendix:
                        continue
                    if cur is None:
                        if not last_clause:
                            continue
                        cur = _new_cur(last_clause, [], via_clause=False)
                    cur["lines"].append(line)
                    continue
            key = _clause_key(num)
            if key is not None and (last_key is None or key > last_key):
                if cur is not None:
                    blocks.append(cur)
                clause = num
                in_front = False
                last_table_key = None
                cur = _new_cur(clause, [match.group(3)], via_clause=True)
                last_clause = clause
                last_key = key
                continue

        # ---- 9) 跳过区 ----
        if in_front or skip_appendix:
            drop_counts["content_skipped_area"] += 1
            continue

        # ---- 10) 其它行 ----
        # D4：表与续表之间夹的「单位为日」这类单位行，model 模式按表格元数据吞掉，
        # 不再作为正文（v1 会把它塞进上一条 clause，GB 15499 的 11 块 D4 缺陷即此）。
        if model_tables and last_table_key and last_table_key in store and is_unit_note_line(line):
            store[last_table_key].setdefault("unit_note", line)
            continue
        if cur is None:
            if not last_clause:
                drop_counts["line_before_any_clause"] += 1
                continue
            cur = _new_cur(last_clause, [], via_clause=False)
        cur["lines"].append(line)

    if cur is not None:
        blocks.append(cur)

    # ---- 11) 兑现欠条 ----
    final_blocks: list[dict[str, Any]] = []
    for block in blocks:
        if block["kind"] != "table_ref":
            final_blocks.append(block)
            continue
        if model_tables:
            # M3：TableModel 渲染（D4 合并后的多 grid 一并出块；D5 已洗单元格）
            model = model_from_store(store, block["key"], row_pack_chars=row_pack_chars)
            new_blocks, n_rows, mode = render_table_blocks(
                model,
                clause=block["clause"],
                std_no=std_no,
                max_chunk_chars=max_chunk_chars,
                row_pack_chars=row_pack_chars,
                row_overflow=row_overflow,
            )
            for table_block in new_blocks:
                table_block["table_model"] = model
        else:
            new_blocks, n_rows, mode = legacy_table_to_blocks(
                store,
                block["key"],
                block["clause"],
                std_no,
                max_chunk_chars=max_chunk_chars,
                row_pack_chars=row_pack_chars,
            )
        final_blocks.extend(new_blocks)
        table = store[block["key"]]
        tables.append(
            {
                "label": table["label"],
                "title": table["title"],
                "rows": n_rows,
                "mode": mode,
                "clause": block["clause"],
            }
        )

    if pending_cap is not None:
        warns.append(f"表题「{pending_cap[0]} {pending_cap[1]}」后面没找到表格，已忽略")

    if drift_count:
        warns.append(f"HTML 续表列数漂移 {drift_count} 处，已按首表列数补齐/截断")

    dropped = [key for key, count in drop_counts.items() if count]
    if dropped:
        detail = "；".join(
            f"{drop_labels[key]} {drop_counts[key]} 处" for key in dropped
        )
        warns.append(f"静默跳过（未进块，M8a 可追溯）：{detail}")

    return final_blocks, tables, warns


def parse_standard(
    text: str,
    *,
    law_name: str = "",
    profile: Profile | None = None,
    plan: RegionPlan | None = None,
    max_chunk_chars: int = MAX_CHUNK_CHARS,
    row_pack_chars: int = ROW_PACK_CHARS,
    ancestor_levels: int = ANCESTOR_LEVELS,
    keep_front_matter: bool = KEEP_FRONT_MATTER,
    drop_appendix: str = DROP_APPENDIX,
    tables_mode: str = "model",
    unlabeled: bool = False,
    derive_title: bool = False,
    cont_tolerance: float = 1.0,
    row_overflow: str = "cell",
) -> ParseResult:
    """标准式 handler：掩码 → v1 状态机 → 附录标注/relabel → Unit。"""
    name = (law_name or "").strip() or _title_in_text(text)
    std_no = _std_no(name) if name else ""
    masked = plan.apply_mask(text) if plan is not None else text
    blocks, _tables, warns = _parse_standard_blocks(
        masked,
        std_no,
        max_chunk_chars=max_chunk_chars,
        row_pack_chars=row_pack_chars,
        keep_front_matter=keep_front_matter,
        drop_appendix=drop_appendix,
        tables_mode=tables_mode,
        unlabeled=unlabeled,
        derive_title=derive_title,
        cont_tolerance=cont_tolerance,
        row_overflow=row_overflow,
    )
    explicit_letters = (
        {letter for _index, letter, _title in plan.appendix_heads}
        if plan is not None
        else set()
    )
    _annotate_appendix_blocks(blocks, explicit_letters=explicit_letters)
    blocks = _merge_bare_titles(blocks, ancestor_levels=ancestor_levels)
    _relabel_appendix_blocks(blocks)
    warnings = list(warns)
    if plan is not None and plan.toc_fixed:
        warnings.extend(w for w in plan.warnings if "目次" in w)
    mode = "relabel"
    if profile is not None:
        mode = getattr(profile.appendix, "mode", "relabel") or "relabel"
    if mode == "unit":
        warnings.append("appendix.mode=unit 在 M2 未实现（D-042），本次按 relabel 处理")
    return ParseResult(title=name, units=_to_units(blocks), warnings=warnings)
