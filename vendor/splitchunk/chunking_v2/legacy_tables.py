# -*- coding: utf-8 -*-
"""FAC-RAG legacy 表格函数的按需加载垫片。

``chunking_v2`` 的 model 档（默认）**不需要** FAC-RAG；只有把 profile 配成
``tables.mode: legacy`` 时才会用到 ``fac_rag.chunking.tables`` 里的旧函数。
这里做懒加载，保证：

* 不装 FAC-RAG 也能 import / 使用切块引擎（model 档）；
* 真配了 legacy 档但环境里没有 FAC-RAG 时，抛出可读的错误，而不是 import 期就崩。
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "legacy_html_table_to_grid",
    "legacy_pipe_table_to_grid",
    "legacy_table_to_blocks",
]

_HINT = (
    "当前 profile 使用了 legacy 表格模式（tables.mode: legacy），"
    "该模式需要 FAC-RAG 的 fac_rag.chunking.tables；当前环境未找到。"
    "解决方式：安装 FAC-RAG，或把 profile 改成 tables.mode: model。"
)


def _fac_rag_tables():
    """按需导入 FAC-RAG legacy 表格函数；失败给可读提示。"""
    try:
        from fac_rag.chunking.tables import (  # type: ignore[import-not-found]
            html_table_to_grid,
            pipe_table_to_grid,
            table_to_blocks,
        )
    except Exception as exc:  # ImportError / RuntimeError（占位实现）都归一
        raise RuntimeError(_HINT) from exc
    return html_table_to_grid, pipe_table_to_grid, table_to_blocks


def legacy_html_table_to_grid(html: str) -> list[list[str]]:
    """``fac_rag.chunking.tables.html_table_to_grid`` 的懒加载版。"""
    func, _pipe, _blocks = _fac_rag_tables()
    return func(html)


def legacy_pipe_table_to_grid(lines: Any) -> list[list[str]]:
    """``fac_rag.chunking.tables.pipe_table_to_grid`` 的懒加载版。"""
    _html, func, _blocks = _fac_rag_tables()
    return func(lines)


def legacy_table_to_blocks(*args: Any, **kwargs: Any) -> Any:
    """``fac_rag.chunking.tables.table_to_blocks`` 的懒加载版。"""
    _html, _pipe, func = _fac_rag_tables()
    return func(*args, **kwargs)
