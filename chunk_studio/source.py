"""源 .md 的定位与读取（只读）。

FAC-RAG 的 manifest 里 ``files[i].path`` 是源文件绝对路径；路径不可用时按
``config.source_roots`` 兜底（先分组根目录，再任意根目录按文件名找）。找不到就
抛 :class:`SourceMissingError`，前端提示「源文件缺失」而不是静默空白。
"""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .manifest import FileEntry, ManifestError, SourceMissingError

#: 单行超过该字符数时前端折叠显示（典型场景：一行 10 万字符的 HTML 表格）。
DISPLAY_MAX_CHARS = 4000


def default_roots_from_manifest(manifest) -> dict[str, str]:
    """从 manifest 的 files 推导每个分组第一个文件的父目录（设置面板默认值）。"""
    roots: dict[str, str] = {}
    for name, entries in manifest.groups.items():
        first = entries[0] if entries else None
        if first is not None and first.source_path:
            roots[name] = str(Path(first.source_path).parent)
    return roots


def locate_source(
    cfg: Config,
    entry: FileEntry,
    *,
    default_roots: dict[str, str] | None = None,
) -> Path:
    """按 manifest 路径 → 分组源根 → 任意源根 的顺序定位源 .md。"""
    filename = Path(entry.source_path).name if entry.source_path else f"{entry.law_name}.md"
    candidates: list[Path] = []
    if entry.source_path:
        candidates.append(Path(entry.source_path))

    roots: dict[str, str] = dict(default_roots or {})
    roots.update(cfg.source_roots or {})
    if entry.group in roots:
        candidates.append(Path(roots[entry.group]) / filename)
    for root in roots.values():
        candidates.append(Path(root) / filename)

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return candidate

    raise SourceMissingError(str(candidates[0]) if candidates else f"{entry.law_name}.md")


def read_lines(path: Path | str) -> list[str]:
    """读取源 .md 的原始行（1-based 行号 = 列表下标 + 1）。"""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if lines and lines[0].startswith("\ufeff"):
        lines[0] = lines[0][1:]
    return lines


def display_text(raw: str) -> tuple[str, bool]:
    """返回（展示文本，是否折叠）。超长行只送前 4000 字符，避免前端卡死。"""
    if len(raw) <= DISPLAY_MAX_CHARS:
        return raw, False
    return raw[:DISPLAY_MAX_CHARS] + f"……（本行共 {len(raw)} 字符，已折叠）", True


def line_kind(text: str) -> str:
    """行的轻量类型，供前端着色（标题 / 引用 / 空白 / 普通文本）。"""
    stripped = text.strip()
    if not stripped:
        return "blank"
    if stripped.startswith("### "):
        return "h3"
    if stripped.startswith("## "):
        return "h2"
    if stripped.startswith("# "):
        return "h1"
    if stripped.startswith("#"):
        return "h"
    if stripped.startswith(">"):
        return "quote"
    if stripped.startswith("|"):
        return "table"
    return "text"


def ensure_source_text(source_text: str, entry: FileEntry) -> str:
    """兜底：plaintext 里没有任何行时给出明确错误（而不是空文档）。"""
    if not source_text.strip():
        raise ManifestError(f"源文件为空：{entry.law_name}")
    return source_text
