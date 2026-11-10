"""来源类别标注（文件级人工标注，P3）。

- 唯一真源：``data/sources.json``，形如 ``{"<law_name>": "地方标准"}``；
  由人在左栏右键文件选择类别，写入后同时把该文件 ``chunks.jsonl`` 所有行的
  ``source`` 字段改成同一类别（检索可直接用）。
- 重新切块（SPLITCHUNK 引擎重写 chunks）后，由 ``chunker`` 按本文件回填，
  保证人工标注不丢。
- 契约里 ``source`` 是文件级常量（来源类别），与 ``law_name`` 一起可作为
  检索的前置过滤条件。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SOURCES_NAME = "sources.json"

#: 工作台支持的来源类别（按优先级/常用度排序）
CATEGORIES = ("法律", "部门规章", "地方标准", "规范性文件")

UNSET_LABEL = "未设置"


class SourceError(Exception):
    """来源类别操作失败（给用户看的消息）。"""


def sources_path(data_root: Path | str) -> Path:
    return Path(data_root) / SOURCES_NAME


def load(data_root: Path | str) -> dict[str, str]:
    """读 ``data/sources.json``；缺失/损坏时返回空表（不抛错）。"""
    path = sources_path(data_root)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for name, category in raw.items():
        name = str(name or "").strip()
        category = str(category or "").strip()
        if name and category in CATEGORIES:
            out[name] = category
    return out


def save(data_root: Path | str, mapping: dict[str, str]) -> Path:
    """原子写回 ``data/sources.json``（空表也写，方便人工查看）。"""
    path = sources_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {
        str(name): category
        for name, category in mapping.items()
        if category in CATEGORIES
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(clean, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return path


def category_for(mapping: dict[str, str], law_name: str) -> str:
    return mapping.get(str(law_name or "").strip(), "")


def validate(category: object) -> str:
    """校验并规范化类别；空串表示清除；其它非法值抛 SourceError。"""
    text = str(category or "").strip()
    if not text:
        return ""
    if text not in CATEGORIES:
        raise SourceError(
            "来源类别只能是：" + " / ".join(CATEGORIES) + f"（收到「{text}」）"
        )
    return text


def apply_to_chunks(chunks: list[dict], category: str) -> int:
    """把块列表的 ``source`` 统一改成 category，返回块数。"""
    for chunk in chunks:
        chunk["source"] = category
    return len(chunks)


def apply_to_chunks_file(chunks_path: Path | str, category: str) -> int:
    """重写 ``chunks.jsonl`` 所有行的 ``source``（tmp + os.replace 原子）。"""
    path = Path(chunks_path)
    if not path.is_file():
        raise SourceError(f"切片文件不存在：{path}")
    rows: list[str] = []
    changed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        row = json.loads(text)
        if not isinstance(row, dict):
            raise SourceError(f"切片文件格式不对：{path}")
        row["source"] = category
        rows.append(json.dumps(row, ensure_ascii=False))
        changed += 1
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(row + "\n" for row in rows), encoding="utf-8")
    os.replace(tmp, path)
    return changed
