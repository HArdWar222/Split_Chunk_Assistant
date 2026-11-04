"""units.table_section：表格片段扫描（所有 handler 共用）。

只负责「从这里开始、到哪一行结束、这段原文如何变成一个 grid」：

* :func:`scan_html_table` —— 行内含 ``<table`` 起，收集到 ``</table>`` 为止；
* :func:`scan_pipe_table` —— 行首 ``|`` 起，收集连续 ``|`` 行。

表题绑定、续表合并、附录 ``表A.1`` 派生、区域归属、丢弃计数等**策略**仍留在
各 handler（standard / generic）里，这里不做任何判定，避免把两套状态机混在
一起。``model_tables=False`` 时退回 v1 的 legacy 解析（等价模式回归用）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..legacy_tables import (
    legacy_html_table_to_grid,
    legacy_pipe_table_to_grid,
)

from ..tables import html_table_to_grid, pipe_table_to_grid


@dataclass(frozen=True)
class TableFragment:
    """一段被扫描器收集到的表格原文与解析结果。"""

    kind: str  # "html" | "pipe"
    lines: list[str]  # 收集到的行（html 保留原文；pipe 已 strip）
    grid: list[list[str]]
    start: int  # 起始行下标
    next_index: int  # 片段之后的第一行下标

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def scan_html_table(
    lines: Sequence[str],
    index: int,
    *,
    model_tables: bool = True,
) -> TableFragment | None:
    """从 ``lines[index]`` 起收集一张 HTML 表格（调用方负责确认含 ``<table``）。

    收集规则与拆分前完全一致：首行取 ``strip()`` 后的文本，后续行原样拼接，
    直到某一行含 ``</table>`` 为止。
    """
    if index < 0 or index >= len(lines):
        return None
    first = str(lines[index]).strip()
    if "<table" not in first.lower():
        return None
    buf = [first]
    next_index = index + 1
    while next_index < len(lines) and "</table>" not in buf[-1].lower():
        buf.append(str(lines[next_index]))
        next_index += 1
    text = "\n".join(buf)
    grid = (
        html_table_to_grid(text)
        if model_tables
        else legacy_html_table_to_grid(text)
    )
    return TableFragment("html", buf, grid, index, next_index)


def scan_pipe_table(
    lines: Sequence[str],
    index: int,
    *,
    model_tables: bool = True,
) -> TableFragment | None:
    """从 ``lines[index]`` 起收集连续 ``|`` 行（每行 strip 后拼接）。"""
    if index < 0 or index >= len(lines):
        return None
    first = str(lines[index]).strip()
    if not first.startswith("|"):
        return None
    buf = [first]
    next_index = index + 1
    while next_index < len(lines) and str(lines[next_index]).strip().startswith("|"):
        buf.append(str(lines[next_index]).strip())
        next_index += 1
    grid = (
        pipe_table_to_grid(buf)
        if model_tables
        else legacy_pipe_table_to_grid(buf)
    )
    return TableFragment("pipe", buf, grid, index, next_index)
