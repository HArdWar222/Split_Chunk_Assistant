# -*- coding: utf-8 -*-
"""第⑤步 gate：切块质量质检（技术版 §10 的 7 个指标）。

设计口径
--------
本模块只做「体检」，不修改产物：

1. ``line_coverage``   进块行数 / 非噪音行数，阈值 ``gate.min_line_coverage``（默认 0.60）。
   噪音行 = 空行、过短行、纯页码、分割线、图占位、管道表行、HTML 表行、
   「（续）」这类续页标记行；调用方传入已掩码（目次/前言/条文说明/背材料已置空）
   的正文时，效果等同「只统计应该进块的正文行」。
2. ``toc_leak``        块文本里「条目式行 + 页码」形态的行数，阈值 0（设计稿要求 0）。
3. ``bad_block_ratio`` 含管道符 / HTML 标签 / ``**`` / ``\\(`` 的块占比，阈值 0.02。
4. ``ref_series``      同区域引用键单调（允许跳号、不允许回退，重复键 = 同条多 part 合法）。
5. ``dup_ref_ratio``   同区域非连续重复引用键 / 总引用键，阈值 0.01。
6. ``block_count``     块数相对期望（通常传 v1 块数）偏离不超过 ±40%；未传对照时跳过。
7. ``warnings``        解析 warnings 条数，阈值 5。

8. ``假设一致性``（M8a，可选）：调用方传入 probe 体检单时，核对「源文特征
   与产物形态」是否自洽——有表格信号却 0 表块、有目次信号却未识别目次区、
   generic 族表格字符占比高且 0 表块，均判失败。三条规则只做保守拦截（阈值见
   ``TABLE_SIGNAL_PIPE_LINES`` / ``TABLE_HEAVY_CHAR_RATIO``），对现役 65 份
   语料 0 误报（``runs/_m8a_precheck.py``）。不传 probe 时行为完全不变。

任一指标不达标 → ``ok=False``、``failures`` 里写明原因；默认不阻断整体运行
（技术版 §10：默认不阻断运行但阻断入库）。崩坏产物（例如 v1 的 GB 4053.2
目次垃圾）必须能触发 ``ok=False``——见 ``runs/_m4_gate_check.py``。
"""

from __future__ import annotations

from .patterns import (
    RE_APPENDIX as RE_APPENDIX_REF,
    RE_CHAPTERISH,
    RE_FRONT as RE_FRONT_MARK,
    RE_LATEX_ESCAPE,
    RE_MD_BOLD as RE_BOLD,
)

import re
from html import unescape
from typing import Any, Iterable, Mapping, Sequence

from .model import ChunkingError, GateRule

__all__ = [
    "GATE_METRIC_KEYS",
    "evaluate",
    "format_report",
]

#: 指标键（写进 run_v2_compare / report_v2 / chunk_report 的固定顺序）
GATE_METRIC_KEYS = (
    "line_coverage",
    "toc_leak",
    "bad_block_ratio",
    "ref_series",
    "dup_ref_ratio",
    "block_count",
    "warnings",
)

#: 假设一致性阈值：管道表行数达到该值视为「有表格信号」（M8a）
TABLE_SIGNAL_PIPE_LINES = 10
#: 假设一致性阈值：表格行字符占源文比例达到该值视为「表格重的源文」（M8a）
TABLE_HEAVY_CHAR_RATIO = 0.10

#: 条目式行 + 页码：「第X章 总则 …… 12」「4.1 倾角与梯段高度 3」「1 范围（1）」
RE_TOC_LEAK = re.compile(
    r"^\s*(?:[-*+]\s*)?(?:"
    r"第[一二三四五六七八九十百千零两0-9]+[章节条]"
    r"|\d+(?:\.\d+)*"
    r"|[A-Z]\.\d+(?:\.\d+)*"
    r"|前\s*言|参\s*考\s*文\s*献"
    r")\s+.*?"
    r"(?:\.{2,}|…{2,}|⋯{2,}|\s[（(]\s*\d{1,4}\s*[)）]|\s+\d{1,4})\s*$"
)
RE_PIPE = re.compile(r"\|")
RE_HTML_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>")

RE_PAGE_NO = re.compile(r"^\d{1,4}$")
RE_RULE = re.compile(r"^(?:[-—–_=*]\s*){3,}$")
RE_ARTICLE = re.compile(r"^第([零一二三四五六七八九十百千两0-9]+)条")
RE_CLAUSE_HEAD = re.compile(r"^(?:([A-Z])\.)?(\d+(?:\.\d+)*)")
RE_YAML_FENCE = re.compile(r"^---\s*$")
#: 标准/法规的条目式行（用于抠出「编号之后的正文」做覆盖匹配）
RE_NUMBERED_LINE = re.compile(r"^(?:[A-Z]\.)?\d+(?:\.\d+)*[　\s]+(\S.*)$")
#: 展开后的单行 HTML 表格行 / 单元格（行覆盖统计用，M8b/task-12）
RE_HTML_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
RE_HTML_CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
#: HTML 表格行覆盖判据：至少 80% 的非空单元格文本出现在块文本里
HTML_ROW_HIT_RATIO = 0.80

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}


def _cn2num(text: str) -> int | None:
    """中文数字 → int（沿用 v1 statute.cn2num 的口径；识别不了返回 None）。"""
    if text.isdigit():
        return int(text)
    total, cur = 0, 0
    for ch in text:
        if ch in _CN_DIGITS:
            cur = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            cur = (cur or 1) * _CN_UNITS[ch]
            total += cur
            cur = 0
        else:
            return None
    return total + cur


def _normalize_line(line: str) -> str:
    """覆盖统计用的行归一：去 markdown 装饰、压空白，保留正文内容。"""
    text = str(line).strip()
    text = re.sub(r"^[-*+]\s+", "", text)
    text = text.replace("**", "")
    text = re.sub(r"^#+\s*", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _is_noise_line(line: str) -> bool:
    text = str(line).strip()
    if not text:
        return True
    if text.startswith("|") or text.lower().startswith("<table"):
        return True
    if len(text) < 4:
        return True
    if RE_PAGE_NO.match(text) or RE_RULE.match(text):
        return True
    if text in ("[图示]", "（续）", "(续)"):
        return True
    return False


def _merge_chunk_text(chunks: Sequence[dict[str, Any]]) -> str:
    return "\n".join(_normalize_line(part) for chunk in chunks for part in str(chunk.get("chunk", "")).splitlines())


def _normalized_candidates(line: str) -> list[str]:
    """一行可能在块里以多种形态出现（编号被拆进 law_num / 列表符号被去）。"""
    norm = _normalize_line(line)
    out = [norm] if norm else []
    match = RE_ARTICLE.match(norm)
    if match:
        body = _normalize_line(norm[match.end():])
        if len(body) >= 2:
            out.append(body)
    match = RE_NUMBERED_LINE.match(norm)
    if match:
        body = _normalize_line(match.group(1))
        if len(body) >= 2 and body not in out:
            out.append(body)
    match = RE_CHAPTERISH.match(norm)
    if match:
        body = _normalize_line(norm[match.end():])
        if len(body) >= 2 and body not in out:
            out.append(body)
    return out


def _html_row_cells(line: str) -> list[str] | None:
    """展开后的 HTML 表格行 → 非空单元格文本；不是表格行返回 ``None``。

    M8b/task-12：单行 HTML 大表预展开后，源文行是 ``<tr>…</tr>``，块文本却是
    ``列名：值 · 列名：值`` 形式的渲染结果，按整行字面匹配必然失败。这里把
    表格行拆成单元格文本，交给 :func:`_line_coverage` 按单元格命中。
    """
    text = str(line)
    if "<tr" not in text.lower():
        return None
    match = RE_HTML_ROW.search(text)
    if not match:
        return None
    cells: list[str] = []
    for inner in RE_HTML_CELL.findall(match.group(1)):
        value = unescape(RE_HTML_TAG.sub("", inner))
        value = re.sub(r"\s+", " ", value).strip()
        if len(value) >= 2:
            cells.append(value)
    return cells


def _front_section_indexes(lines: Sequence[str]) -> set[int]:
    """前言/引言段、YAML front matter：按设计整段丢弃，不计入覆盖率分母。"""
    skip: set[int] = set()
    n = len(lines)
    if n and RE_YAML_FENCE.match(lines[0].strip()):
        for index in range(1, min(n, 200)):
            if RE_YAML_FENCE.match(lines[index].strip()):
                skip.update(range(0, index + 1))
                break
    for index, raw in enumerate(lines):
        if not RE_FRONT_MARK.match(_normalize_line(raw)):
            continue
        skip.add(index)
        for j in range(index + 1, n):
            cand = _normalize_line(lines[j])
            raw2 = lines[j]
            structural = (
                raw2.lstrip().startswith("#")
                and (
                    RE_CHAPTERISH.match(cand)
                    or RE_CLAUSE_HEAD.match(cand)
                    or RE_APPENDIX_REF.match(cand)
                    or cand in ("参考文献", "条文说明")
                )
            ) or (not raw2.lstrip().startswith("#") and (RE_CHAPTERISH.match(cand) or RE_APPENDIX_REF.match(cand) or cand in ("参考文献", "条文说明")))
            if structural:
                break
            skip.add(j)
    return skip


def _table_char_ratio(source_text: str) -> float:
    """源文里表格行（HTML ``<table`` / 管道表行）字符数占源文总字符数的比例。"""
    total = len(source_text or "")
    if total <= 0:
        return 0.0
    table_chars = 0
    for line in str(source_text).splitlines():
        text = line.strip()
        if not text:
            continue
        if text.lower().startswith("<table") or text.startswith("|"):
            table_chars += len(text)
    return table_chars / total


def _table_block_count(chunks: Sequence[dict[str, Any]]) -> int:
    """产物里的表块数量（chunk_kind == "table"）。"""
    return sum(1 for chunk in chunks if str(chunk.get("chunk_kind", "")) == "table")


def _line_coverage(
    chunks: Sequence[dict[str, Any]],
    source_text: str,
    regions: Sequence[Any] | None = None,
) -> tuple[float, int, int]:
    """只有正文/附录区域的行计入分母；前言/目次/条文说明/参考文献按设计丢弃。

    分母参照系是**源文行**（不是区域划分本身）：传 ``regions`` 时，落在任何
    区域之外的行也不计入分母——否则区域识别把整篇判成 body 时，覆盖率会
    退化成「用区域划分自证区域划分」，永远 1.0（M8a 修正）。

    HTML 表格行（``<tr>…</tr>``，单行大表预展开后的形态）按单元格文本匹配：
    至少 ``HTML_ROW_HIT_RATIO`` 的非空单元格出现在块文本里才算命中；全空行
    不计入分母（M8b/task-12）。
    """
    lines = source_text.splitlines()
    denom = 0
    matched = 0
    merged = _merge_chunk_text(chunks)
    skip_indexes = _front_section_indexes(lines)

    def _keep(index: int) -> bool:
        if index in skip_indexes:
            return False
        if not regions:
            return True
        for region in regions:
            if getattr(region, "start", 0) <= index < getattr(region, "end", 0):
                return getattr(region, "kind", "body") in ("body", "appendix")
        return False

    for index, raw in enumerate(lines):
        if not _keep(index):
            continue
        row_cells = _html_row_cells(raw)
        if row_cells is not None:
            if not row_cells:
                continue
            denom += 1
            hits = sum(1 for cell in row_cells if cell in merged)
            if hits >= (len(row_cells) * 4 + 4) // 5:  # ceil(0.8 * n)
                matched += 1
            continue
        if _is_noise_line(raw):
            continue
        candidates = _normalized_candidates(raw)
        if not candidates:
            continue
        denom += 1
        if any(candidate in merged for candidate in candidates):
            matched += 1
    if denom == 0:
        return 1.0, 0, 0
    return matched / denom, matched, denom


def _toc_leak(chunks: Sequence[dict[str, Any]], limit: int = 5) -> tuple[int, list[str]]:
    hits = 0
    samples: list[str] = []
    for chunk in chunks:
        for line in str(chunk.get("chunk", "")).splitlines():
            if RE_TOC_LEAK.match(line):
                hits += 1
                if len(samples) < limit:
                    samples.append(line.strip()[:80])
    return hits, samples


def _bad_blocks(chunks: Sequence[dict[str, Any]]) -> tuple[int, list[str], int]:
    """bad block = 表块里出现管道符/HTML/``**``/``\\(``，或任意块出现 HTML/**/latex。

    非表块的管道符不计入 bad_block_ratio：这是语料里「无表题作业票表单」的既有
    正文形态（D-044 已裁不转表块、不追求清零），单独统计成
    ``non_table_pipe_blocks`` 供报表与 M5 复查。
    """
    bad = 0
    kinds: set[str] = set()
    non_table_pipe = 0
    for chunk in chunks:
        text = str(chunk.get("chunk", ""))
        kind = str(chunk.get("chunk_kind", ""))
        hit = False
        if kind == "table" and RE_PIPE.search(text):
            kinds.add("pipe")
            hit = True
        if RE_HTML_TAG.search(text):
            kinds.add("html_tag")
            hit = True
        if RE_BOLD.search(text):
            kinds.add("md_bold")
            hit = True
        if RE_LATEX_ESCAPE.search(text):
            kinds.add("latex_escape")
            hit = True
        if hit:
            bad += 1
        elif kind != "table" and RE_PIPE.search(text):
            non_table_pipe += 1
    return bad, sorted(kinds), non_table_pipe


def _ref_key(law_num: str) -> tuple[str, tuple] | None:
    """引用键 → ``(区域, 可比较键)``；无法可靠比较时返回 None。

    * 「第X条」→ ``("body", (0, (序号,)))``
    * 纯数字条号「4.1.2」「4.1.2 表A.1 第1-2行」→ ``("body", (1, (4,1,2)))``
    * 字母附录条号「A.1.2」→ ``("appendix", (ord("A"), (1,2)))``
    * 「附 录 A」「附 录 A 表A.1」→ None（附录标题/表块的挂靠键，不参与单调判断）
    """
    text = str(law_num or "").strip()
    if RE_APPENDIX_REF.match(text):
        return None
    match = RE_ARTICLE.match(text)
    if match:
        num = _cn2num(match.group(1))
        if num is not None:
            return "body", (0, (num,))
    match = RE_CLAUSE_HEAD.match(text)
    if match:
        letter = match.group(1) or ""
        nums = tuple(int(part) for part in match.group(2).split("."))
        if letter:
            return "appendix", (ord(letter), nums)
        return "body", (1, nums)
    return None


def _ref_series(chunks: Sequence[dict[str, Any]]) -> tuple[bool, list[str], int, int]:
    """同区域引用键单调；同时统计非连续重复键。返回 (ok, 违规样例, 重复数, ref 总数)。"""
    violations: list[str] = []
    seen: dict[str, set[Any]] = {"body": set(), "appendix": set()}
    prev_key: dict[str, Any] = {}
    refs = 0
    dups = 0
    for chunk in chunks:
        law_num = str(chunk.get("law_num", ""))
        parsed = _ref_key(law_num)
        if parsed is None:
            continue
        region, key = parsed
        refs += 1
        last_key = prev_key.get(region)
        if key == last_key:
            continue  # 同条多 part，合法
        if last_key is not None and key < last_key:
            if len(violations) < 5:
                violations.append(f"{law_num} 在 {region} 区回退（上一键 {last_key}）")
        if key in seen[region]:
            dups += 1
        else:
            seen[region].add(key)
        prev_key[region] = key
    return (not violations), violations, dups, refs


def _grid_of(
    chunk: Mapping[str, Any],
    extras: Mapping[str, Mapping[str, Any]] | None,
) -> Mapping[str, Any] | None:
    """取表格块的结构化 grid：优先块内嵌（块级自检），否则查 extras 侧车的
    ``table_grid``（15 字段契约块不带 grid）。"""
    grid = chunk.get("grid")
    if isinstance(grid, Mapping):
        return grid
    if extras:
        entry = extras.get(str(chunk.get("id") or ""))
        if isinstance(entry, Mapping):
            grid = entry.get("table_grid")
            if isinstance(grid, Mapping):
                return grid
    return None


def _table_invariants(
    chunks: Sequence[dict[str, Any]],
    extras: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Phase 3：表格块内部不变量自检（只依赖产物，不依赖源文）。

    检查项：``grid`` 存在、``width>0``、``len(headers)==width``、
    ``header_rows>=1``、每行宽度 == ``width``、``note_from`` 不越界、同一张表
    （同 ``law_num``）的多块表头/宽度一致。``grid`` 优先取块内嵌，
    否则从 ``extras[chunk.id]["table_grid"]``（侧车）取。

    默认只记账（``metrics.table_invariants``），不参与 ``ok`` 判定；把
    ``profile.gate.strict_tables`` 设为 ``true`` 才会升级为 failure —— 这样
    不给现有 65 份语料的 gate 结论引入新的假红。
    """
    violations: list[str] = []
    checked = 0
    tables: dict[str, list[Mapping[str, Any]]] = {}
    for index, chunk in enumerate(chunks):
        grid = _grid_of(chunk, extras)
        is_table = str(chunk.get("chunk_kind") or "") == "table"
        if not is_table and grid is None:
            continue
        checked += 1
        if grid is None:
            violations.append(f"#{index} 表块缺 grid")
            continue
        width = int(grid.get("width") or 0)
        headers = list(grid.get("headers") or [])
        rows = list(grid.get("rows") or [])
        header_rows = int(grid.get("header_rows") or 0)
        if width <= 0:
            violations.append(f"#{index} grid.width={width}")
        if len(headers) != width:
            violations.append(f"#{index} headers={len(headers)} != width={width}")
        if header_rows < 1:
            violations.append(f"#{index} header_rows={header_rows}")
        for row_index, row in enumerate(rows):
            if len(row) != width:
                violations.append(
                    f"#{index} row{row_index} 宽度 {len(row)} != {width}"
                )
        note_from = int(grid.get("note_from") or 0)
        if not 0 <= note_from <= len(rows):
            violations.append(f"#{index} note_from={note_from} 越界（rows={len(rows)}）")
        key = str(chunk.get("law_num") or "")
        if key:
            tables.setdefault(key, []).append(grid)

    for key, grids in tables.items():
        if len(grids) < 2:
            continue
        head = grids[0]
        ref_headers = list(head.get("headers") or [])
        ref_width = int(head.get("width") or 0)
        ref_header_rows = int(head.get("header_rows") or 0)
        for grid in grids[1:]:
            if int(grid.get("width") or 0) != ref_width:
                violations.append(f"{key} 同表多块 width 不一致")
                break
            if list(grid.get("headers") or []) != ref_headers:
                violations.append(f"{key} 同表多块表头不一致")
                break
            if int(grid.get("header_rows") or 0) != ref_header_rows:
                violations.append(f"{key} 同表多块 header_rows 不一致")
                break

    return {
        "checked_blocks": checked,
        "n_tables": len(tables),
        "violations": len(violations),
        "samples": violations[:5],
    }


def _assumption_metrics(
    probe: Mapping[str, Any] | None,
    chunks: Sequence[dict[str, Any]],
    *,
    regions: Sequence[Any] | None,
    source_text: str,
) -> tuple[dict[str, Any], list[str]]:
    """M8a：probe 体检单 vs 产物形态的假设一致性（返回 metrics 与 failures）。"""
    if probe is None:
        return {}, []
    failures: list[str] = []
    if probe is not None:
        flags = probe.get("flags") or {}
        family = str(probe.get("family") or "")
        html_lines = int(flags.get("html_table_lines") or 0)
        pipe_lines = int(flags.get("pipe_table_lines") or 0)
        toc_entries = int(flags.get("toc_entries") or 0)
        dot_leader = int(flags.get("dot_leader_pages") or 0)
        has_toc_signal = bool(flags.get("has_toc")) or toc_entries > 0 or dot_leader > 0
        has_toc_region = any(
            getattr(region, "kind", "") == "toc" for region in (regions or ())
        )
        table_blocks = _table_block_count(chunks)
        table_char_ratio = _table_char_ratio(source_text) if source_text else 0.0

        fail_tables = (
            html_lines > 0 or pipe_lines > TABLE_SIGNAL_PIPE_LINES
        ) and table_blocks == 0
        fail_toc = has_toc_signal and not has_toc_region
        fail_generic = (
            family == "generic"
            and table_char_ratio >= TABLE_HEAVY_CHAR_RATIO
            and table_blocks == 0
        )

        if fail_tables:
            failures.append(
                "assumption_tables: 源文有表格信号"
                f"（html_table_lines={html_lines}, pipe_table_lines={pipe_lines}）"
                "但产物 0 表块——表格被压平，质量假设不成立"
            )
        if fail_toc:
            failures.append(
                "assumption_toc: 源文有目次信号"
                f"（has_toc={bool(flags.get('has_toc'))}, dot_leader_pages={dot_leader},"
                f" toc_entries={toc_entries}）但未识别出 toc 区域"
                "——目次可能被当正文，质量假设不成立"
            )
        if fail_generic:
            failures.append(
                "assumption_generic_table: profile.family=generic"
                f"（置信度 {probe.get('confidence')}）但源文表格行字符占比"
                f" {table_char_ratio:.2%} >= {TABLE_HEAVY_CHAR_RATIO:.0%}"
                "且产物 0 表块——generic 兜底不该吞表格文档"
            )

        assumption_metrics = {
            "probe_family": family,
            "probe_confidence": probe.get("confidence"),
            "html_table_lines": html_lines,
            "pipe_table_lines": pipe_lines,
            "table_char_ratio": round(table_char_ratio, 4),
            "table_blocks": table_blocks,
            "assumption_tables": "fail" if fail_tables else "pass",
            "assumption_toc": "fail" if fail_toc else "pass",
            "assumption_generic_table": "fail" if fail_generic else "pass",
        }
    return assumption_metrics, failures


def evaluate(
    chunks: Sequence[dict[str, Any]],
    *,
    source_text: str = "",
    regions: Sequence[Any] | None = None,
    expected_blocks: int | None = None,
    allow_block_count_change: bool = False,
    warnings: int = 0,
    rule: GateRule | None = None,
    extra: dict[str, Any] | None = None,
    probe: Mapping[str, Any] | None = None,
    extras: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """对一份文件的块清单跑 7 指标，返回 ``{"ok", "metrics", "failures", ...}``。

    ``regions`` 是 :class:`fac_rag.chunking_v2.regions.RegionPlan` 的
    ``regions`` 列表（可选）；传入后 ``line_coverage`` 只统计正文/附录区域。
    ``expected_blocks`` 传 v1 块数做块数对照；``allow_block_count_change=True``
    时块数偏离只记指标不判失败（L2 计划变更文件由报表另行说明）。
    ``probe`` 传 :func:`fac_rag.chunking_v2.probe.probe_lines` 的体检单（可选）：
    传入后额外核对「源文表格/目次信号 vs 产物形态」的假设一致性（M8a），
    不传时行为与旧版完全一致。
    """
    rule = rule or GateRule()
    failures: list[str] = []

    if source_text:
        coverage, matched, denom = _line_coverage(chunks, source_text, regions)
    else:
        coverage, matched, denom = 1.0, 0, 0
    if coverage < rule.min_line_coverage:
        failures.append(f"line_coverage={coverage:.3f} < {rule.min_line_coverage:.2f}（{matched}/{denom} 行进块）")

    toc_leak, toc_samples = _toc_leak(chunks)
    if toc_leak > 0:
        failures.append(f"toc_leak={toc_leak}（应为 0，例：{toc_samples[:3]}）")

    bad, bad_kinds, non_table_pipe = _bad_blocks(chunks)
    bad_ratio = (bad / len(chunks)) if chunks else 0.0
    if bad_ratio > rule.max_bad_block_ratio:
        failures.append(f"bad_block_ratio={bad_ratio:.3f} > {rule.max_bad_block_ratio:.2f}（{bad}/{len(chunks)} 块，类型 {bad_kinds}）")

    ref_ok, ref_violations, dups, refs = _ref_series(chunks)
    dup_ratio = (dups / refs) if refs else 0.0
    if not ref_ok:
        failures.append(f"ref_series 回退 {len(ref_violations)} 处：{ref_violations[:3]}")
    if dup_ratio > rule.max_dup_ref_ratio:
        failures.append(f"dup_ref_ratio={dup_ratio:.3f} > {rule.max_dup_ref_ratio:.2f}（{dups}/{refs}）")

    block_delta: float | None = None
    if expected_blocks is not None and expected_blocks > 0:
        block_delta = (len(chunks) - expected_blocks) / expected_blocks
        if abs(block_delta) > rule.block_count_tolerance and not allow_block_count_change:
            failures.append(
                f"block_count={len(chunks)} 相对期望 {expected_blocks} 偏离 {block_delta:+.1%}"
                f"（阈值 ±{rule.block_count_tolerance:.0%}）"
            )

    if warnings > rule.max_warnings:
        failures.append(f"warnings={warnings} > {rule.max_warnings}")

    # ---- Phase 3：表格块内部不变量（默认只记账，strict_tables 才判失败）----
    table_invariants = _table_invariants(chunks, extras)
    if rule.strict_tables and table_invariants["violations"]:
        failures.append(
            "table_invariants: "
            f"{table_invariants['violations']} 处异常，例：{table_invariants['samples'][:3]}"
        )

    # ---- M8a 假设一致性：probe 体检单 vs 产物形态 ----
    assumption_metrics, assumption_failures = _assumption_metrics(
        probe, chunks, regions=regions, source_text=source_text
    )
    failures.extend(assumption_failures)

    metrics = {
        "line_coverage": round(coverage, 4),
        "line_coverage_detail": {"matched": matched, "denom": denom},
        "toc_leak": toc_leak,
        "toc_leak_samples": toc_samples,
        "bad_block_ratio": round(bad_ratio, 4),
        "bad_blocks": bad,
        "bad_block_kinds": bad_kinds,
        "non_table_pipe_blocks": non_table_pipe,
        "ref_series": ref_ok,
        "ref_violations": ref_violations,
        "dup_ref_ratio": round(dup_ratio, 4),
        "dup_refs": dups,
        "ref_total": refs,
        "block_count": len(chunks),
        "block_count_expected": expected_blocks,
        "block_count_delta": None if block_delta is None else round(block_delta, 4),
        "warnings": warnings,
        "table_invariants": table_invariants,
    }
    if assumption_metrics:
        metrics.update(assumption_metrics)
    result: dict[str, Any] = {
        "ok": not failures,
        "n_chunks": len(chunks),
        "metrics": metrics,
        "failures": failures,
    }
    if extra:
        result.update(extra)
    return result


def format_report(result: dict[str, Any], *, name: str = "") -> str:
    """把 gate 结果压成一行报表文本（chunk_report / run_v2_compare 用）。"""
    metrics = result.get("metrics", {})
    head = f"{name}：" if name else ""
    status = "OK" if result.get("ok") else "FAIL"
    parts = [
        f"coverage={metrics.get('line_coverage')}",
        f"toc_leak={metrics.get('toc_leak')}",
        f"bad_ratio={metrics.get('bad_block_ratio')}",
        f"ref_ok={metrics.get('ref_series')}",
        f"dup_ratio={metrics.get('dup_ref_ratio')}",
        f"blocks={metrics.get('block_count')}",
        f"warnings={metrics.get('warnings')}",
    ]
    text = f"{head}{status}  " + " ".join(parts)
    failures = result.get("failures") or []
    if failures:
        text += "  失败：" + "；".join(str(item) for item in failures[:3])
    return text
