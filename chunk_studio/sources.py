"""来源类别标注（文件级人工标注，P3）。

- 唯一真源：``data/sources.json``，结构为::

      {"categories": ["法律", "部门规章", ...],
       "files": {"<law_name>": "地方标准"}}

  也兼容早期写法（顶层直接是 ``{"<law_name>": "地方标准"}``）。
  由人在左栏右键文件选择/新建类别，写入后同时把该文件 ``chunks.jsonl`` 所有行的
  ``source`` 字段改成同一类别（检索可直接用）。
- 类别列表 = 内置 4 类 + 用户自定义（新建后持久化到 ``categories``）。
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

#: 内置来源类别（新建工作区时的初始列表）
DEFAULT_CATEGORIES = ("法律", "部门规章", "地方标准", "规范性文件")

#: 兼容旧引用
CATEGORIES = DEFAULT_CATEGORIES

UNSET_LABEL = "未设置"

#: 自定义类别名的长度上限
MAX_NAME_LEN = 30


class SourceError(Exception):
    """来源类别操作失败（给用户看的消息）。"""


def sources_path(data_root: Path | str) -> Path:
    return Path(data_root) / SOURCES_NAME


def _clean_name(name: object) -> str:
    return str(name or "").strip()


def _read_registry(path: Path) -> tuple[list[str], dict[str, str]]:
    """读 sources.json，返回 (自定义类别, 文件映射)；任何异常都返回空。"""
    if not path.is_file():
        return [], {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], {}
    if not isinstance(raw, dict):
        return [], {}
    files_raw = raw.get("files")
    if isinstance(files_raw, dict):
        cats = [str(item or "").strip() for item in raw.get("categories") or []]
        files = {
            _clean_name(name): _clean_name(category)
            for name, category in files_raw.items()
            if _clean_name(name) and _clean_name(category)
        }
        return [item for item in cats if item], files
    # 早期的扁平写法：顶层就是 {文件名: 类别}
    files = {}
    for name, category in raw.items():
        n, c = _clean_name(name), _clean_name(category)
        if n and c:
            files[n] = c
    return [], files


def _write_registry(data_root: Path | str, categories: list[str], files: dict[str, str]) -> Path:
    path = sources_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "categories": [str(item) for item in categories],
        "files": {str(name): str(cat) for name, cat in files.items()},
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return path


def load_registry(data_root: Path | str) -> dict:
    """返回 ``{"categories": [...], "files": {...}}``（类别已含内置 + 自定义）。"""
    custom, files = _read_registry(sources_path(data_root))
    categories: list[str] = []
    for name in list(DEFAULT_CATEGORIES) + list(custom) + list(files.values()):
        name = _clean_name(name)
        if name and name not in categories:
            categories.append(name)
    return {"categories": categories, "files": files}


def list_categories(data_root: Path | str) -> list[str]:
    return load_registry(data_root)["categories"]


def load(data_root: Path | str) -> dict[str, str]:
    """读文件级映射（文件名 → 类别）；缺失/损坏时返回空表（不抛错）。"""
    return load_registry(data_root)["files"]


def _normalize_name(name: object) -> str:
    text = _clean_name(name)
    if not text:
        raise SourceError("来源类别不能为空")
    if len(text) > MAX_NAME_LEN:
        raise SourceError(f"来源类别最多 {MAX_NAME_LEN} 个字符（收到「{text}」）")
    bad = [ch for ch in text if ch in "/\\\n\r\t"]
    if bad or any(ord(ch) < 32 for ch in text):
        raise SourceError("来源类别名称不能包含斜杠、换行或控制字符")
    return text


def validate(category: object) -> str:
    """校验并规范化类别；空串表示清除；非法值抛 SourceError。

    自定义类别同样合法（不再限定内置四类）。
    """
    text = _clean_name(category)
    if not text:
        return ""
    return _normalize_name(text)


def add_category(data_root: Path | str, name: object) -> list[str]:
    """把自定义类别加入列表并持久化；已存在时幂等。返回最新类别列表。"""
    category = _normalize_name(name)
    registry = load_registry(data_root)
    categories = list(registry["categories"])
    if category not in categories:
        categories.append(category)
        _write_registry(data_root, categories, registry["files"])
        registry = load_registry(data_root)
    return registry["categories"]


def save_files(data_root: Path | str, mapping: dict[str, str]) -> Path:
    """原子写回文件映射（保留已有类别列表，并保证映射里的类别都在列表中）。"""
    registry = load_registry(data_root)
    categories = list(registry["categories"])
    files: dict[str, str] = {}
    for name, category in mapping.items():
        law = _clean_name(name)
        cat = _clean_name(category)
        if not law or not cat:
            continue
        files[law] = cat
        if cat not in categories:
            categories.append(cat)
    return _write_registry(data_root, categories, files)


def save(data_root: Path | str, mapping: dict[str, str]) -> Path:
    """兼容旧调用：等价于 :func:`save_files`。"""
    return save_files(data_root, mapping)


def category_for(mapping: dict[str, str], law_name: str) -> str:
    return mapping.get(_clean_name(law_name), "")


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
