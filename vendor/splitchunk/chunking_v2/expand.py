# -*- coding: utf-8 -*-
"""fac_rag.chunking_v2.expand —— 单行超长 HTML 大表预展开（M8b / task-12）。

泛化样本（危险化学品目录、山东省自由裁量标准）里整张表被压成**一行** HTML
（最长 45 万字符、约 3,000 行数据），行级切块器无法按行读取，且 gate 的行覆盖率
也永远匹配不到这种「一行=整表」的源行。本模块在切块/区域探测之前把这种行按
``<tr>`` / ``<td>`` 边界展开成多行 HTML：

* 只在「单行长度 > ``EXPAND_THRESHOLD`` 且含 ``<table``」时展开；现役 65 份
  生产语料最长行远低于阈值 → 零影响（回归硬约束）；
* 展开不改变任何字符内容，只在 ``<tr>`` 前插入换行、``<table>`` / ``</table>``
  各自独立成行，因此 ``html_table_to_grid`` 仍按原 HTML 解析；
* 返回「展开行 → 原行号（0-based）」映射，供报告/审查回溯（报告里记
  ``report["expand"]``，不落块契约）。

对外接口：:func:`expand_lines`；纯函数、无 IO、可重复调用。
"""

from __future__ import annotations

import re
from typing import Any

#: 触发预展开的单行长度阈值（字符）。取 20000：现役 65 份生产语料单行最长
#: 远低于此值（泛化样本 A 的整表行 45 万字符、B 的大表行 23 万字符）。
EXPAND_THRESHOLD = 20000

#: 一行里可能嵌有多张表：非贪婪匹配完整 ``<table ...>...</table>`` 片段。
RE_TABLE = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)
#: 开标签单独切出来（保留属性）。
RE_TABLE_OPEN = re.compile(r"<table\b[^>]*>", re.IGNORECASE)
#: 行：``<tr ...> ... </tr>``（跨行内容也收）。
RE_ROW = re.compile(r"<tr\b[^>]*>.*?</tr>", re.IGNORECASE | re.DOTALL)
#: 行容器 / 单元格容器标签：展开时丢弃（保留其内容）。
RE_ROW_WRAPPER = re.compile(r"</?(?:thead|tbody|tfoot)\b[^>]*>", re.IGNORECASE)


def _split_table(block: str) -> list[str]:
    """把一段完整 HTML 表拆成多行：开标签 / 每行 ``<tr>`` / 收标签。"""
    table = str(block or "")
    open_match = RE_TABLE_OPEN.match(table)
    open_tag = open_match.group(0) if open_match else "<table>"
    rest = table[len(open_tag):]
    close = ""
    close_at = rest.lower().rfind("</table>")
    if close_at >= 0 and not rest[close_at + len("</table>"):].strip():
        close = "</table>"
        rest = rest[:close_at]
    rows = [row.strip() for row in RE_ROW.findall(rest)]
    pieces: list[str] = [open_tag]
    if rows:
        pieces.extend(row for row in rows if row)
        leftover = RE_ROW_WRAPPER.sub("", RE_ROW.sub("", rest)).strip()
        if leftover:
            pieces.append(leftover)
    else:
        body = RE_ROW_WRAPPER.sub("", rest).strip()
        if body:
            pieces.append(body)
    if close:
        pieces.append(close)
    return [piece for piece in pieces if piece.strip()]


def _expand_line(line: str) -> list[str]:
    """一行 → 多行：表内按 ``<tr>`` 切，表外文本保持为独立行。"""
    pieces: list[str] = []
    last = 0
    for match in RE_TABLE.finditer(line):
        prefix = line[last:match.start()].strip()
        if prefix:
            pieces.append(prefix)
        pieces.extend(_split_table(match.group(0)))
        last = match.end()
    suffix = line[last:].strip()
    if suffix:
        pieces.append(suffix)
    return pieces or [line]


def expand_lines(
    lines: list[str], *, threshold: int = EXPAND_THRESHOLD
) -> tuple[list[str], list[int] | None, dict[str, Any] | None]:
    """把「单行超长 HTML 大表」展开成多行 HTML。

    返回 ``(work_lines, source_line_map, info)``：

    * 未触发时 ``(lines, None, None)``（调用方可完全按旧逻辑走）；
    * 触发时 ``work_lines`` 是展开后的行，``source_line_map[i]`` 是第 i 行对应的
      原始行号，``info`` 是可直接塞进报告/诊断的元数据（无时间戳，可复现）。
    """
    if threshold <= 0:
        return lines, None, None
    candidates = [
        index
        for index, line in enumerate(lines)
        if len(line) > threshold and "<table" in line.lower()
    ]
    if not candidates:
        return lines, None, None
    work: list[str] = []
    mapping: list[int] = []
    expanded_tables = 0
    for index, line in enumerate(lines):
        if index not in candidates:
            work.append(line)
            mapping.append(index)
            continue
        pieces = _expand_line(line)
        if len(pieces) <= 1:
            work.append(line)
            mapping.append(index)
            continue
        expanded_tables += len(RE_TABLE.findall(line))
        for piece in pieces:
            work.append(piece)
            mapping.append(index)
    info: dict[str, Any] = {
        "applied": True,
        "threshold": threshold,
        "orig_lines": len(lines),
        "work_lines": len(work),
        "expanded_tables": expanded_tables,
        "expanded_source_lines": len(candidates),
        "source_line_map": list(mapping),
    }
    return work, mapping, info


__all__ = ["EXPAND_THRESHOLD", "expand_lines"]
