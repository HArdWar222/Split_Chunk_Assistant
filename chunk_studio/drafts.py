"""P3 草稿层：磁盘草稿的保存 / 恢复 / 提交 / 丢弃（无 meta 精简版）。

约定（见 docs/07-decisions.md D21）：

- 正式产物：``data/chunks/<rel>/<名>/chunks.jsonl`` + ``extras.jsonl`` + ``report.json``
- 草稿：``data/draft/<stem>.<基线 sha12>.chunks.jsonl``，基线写进文件名，不另建状态文件
- 备份：``data/backup/<stem>.<时间戳>.chunks.jsonl``，只保留最近 3 份
- 草稿文件保存的是「界面块」（含 span/align/manual），提交时只写契约字段

提交用「tmp + os.replace」原子替换 chunks.jsonl；extras.jsonl 不动
（编辑只产生正文/条号/类型变化，侧车按 id 仍然适用）。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

DRAFT_SUFFIX = ".chunks.jsonl"
BACKUP_KEEP = 3

#: 契约字段（写正式产物时只保留这些；其余是界面运行时字段）
CONTRACT_KEYS = (
    "id",
    "chunk",
    "law_name",
    "law_num",
    "law_invalid",
    "law_type",
    "law_level",
    "issuer",
    "effective_date",
    "chunk_kind",
    "source",
    "part_index",
    "part_total",
    "token_len",
    "chunk_hash",
)


class DraftError(Exception):
    """草稿操作失败（给用户看的消息）。"""


def sha12(path: Path | str) -> str:
    """文件内容 sha256 的前 12 位（作为基线标识）。"""
    data = Path(path).read_bytes()
    return hashlib.sha256(data).hexdigest()[:12]


def stem_for(rel: str, name: str) -> str:
    """草稿/备份文件名主干：子目录用 ``__`` 拼进名字，避免同名冲突。"""
    rel = (rel or "").strip("/").replace("\\", "/")
    return f"{rel.replace('/', '__')}__{name}" if rel else name


def draft_file(draft_dir: Path | str, stem: str, baseline12: str) -> Path:
    return Path(draft_dir) / f"{stem}.{baseline12}{DRAFT_SUFFIX}"


def draft_files(draft_dir: Path | str, stem: str) -> list[Path]:
    return sorted(Path(draft_dir).glob(f"{stem}.*{DRAFT_SUFFIX}"))


def parse_draft_name(filename: str) -> tuple[str, str] | None:
    """从草稿文件名解析 ``(stem, baseline12)``；不是草稿返回 None。"""
    if not filename.endswith(DRAFT_SUFFIX):
        return None
    base = filename[: -len(DRAFT_SUFFIX)]
    if "." not in base:
        return None
    stem, sha = base.rsplit(".", 1)
    if len(sha) != 12 or not stem:
        return None
    return stem, sha


def read_chunks(path: Path | str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise DraftError(f"草稿文件格式不对：{path}")
        chunks.append(row)
    return chunks


def _dump(chunks: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in chunks)


def write_atomic(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save_draft(draft_dir: Path | str, stem: str, baseline12: str, chunks: list[dict[str, Any]]) -> Path:
    """写草稿；同一文档旧基线的草稿文件一并删除（只保留最新一份）。"""
    target = draft_file(draft_dir, stem, baseline12)
    for old in draft_files(draft_dir, stem):
        if old != target:
            old.unlink(missing_ok=True)
    write_atomic(target, _dump(chunks))
    return target


def discard_draft(draft_dir: Path | str, stem: str) -> int:
    removed = 0
    for path in draft_files(draft_dir, stem):
        path.unlink(missing_ok=True)
        removed += 1
    return removed


def backup_chunks(chunks_path: Path | str, backup_dir: Path | str, stem: str) -> Path | None:
    """提交前备份当前正式 chunks.jsonl；返回备份路径（源文件不存在时返回 None）。"""
    source = Path(chunks_path)
    if not source.is_file():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = Path(backup_dir) / f"{stem}.{stamp}.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    keep_backups(backup_dir, stem)
    return target


def keep_backups(backup_dir: Path | str, stem: str) -> None:
    """只保留最近 BACKUP_KEEP 份备份。"""
    olds = sorted(Path(backup_dir).glob(f"{stem}.*.jsonl"))
    for path in olds[:-BACKUP_KEEP]:
        path.unlink(missing_ok=True)


def contract_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只保留契约字段（去掉 span/align/manual/extras 等界面字段）。"""
    out: list[dict[str, Any]] = []
    for chunk in chunks:
        out.append({key: chunk.get(key) for key in CONTRACT_KEYS if key in chunk})
    return out


def extras_path(chunks_path: Path | str, law_name: str) -> Path:
    """文档侧车路径（新命名优先，兼容早期统一命名的 extras.jsonl）。"""
    doc_dir = Path(chunks_path).parent
    named = doc_dir / f"{law_name}.extras.jsonl"
    legacy = doc_dir / "extras.jsonl"
    if named.is_file() or not legacy.is_file():
        return named
    return legacy


def merge_extras_spans(extras_path: Path | str, chunks: list[dict[str, Any]]) -> Path:
    """把界面行区间写进侧车（``span`` / ``part_span``），供重新加载时对齐使用。

    只改视图字段，引擎写入的 ``table_grid`` 等字段原样保留；侧车两种写法
    （``{"id":…,"extras":{…}}`` 嵌套 / 字段平铺）都兼容。
    """
    path = Path(extras_path)
    rows: list[dict[str, Any]] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        cid = str(row.get("id") or "")
        if cid:
            by_id[cid] = row
    out: list[dict[str, Any]] = list(rows)
    for chunk in chunks:
        cid = str(chunk.get("id") or "")
        if not cid:
            continue
        span = chunk.get("span")
        part_span = chunk.get("part_span")
        if not (
            (isinstance(span, list) and len(span) == 2)
            or (isinstance(part_span, list) and len(part_span) == 2)
        ):
            continue
        row = by_id.get(cid)
        if row is None:
            row = {"id": cid, "extras": {}}
            by_id[cid] = row
            out.append(row)
        nested = row.get("extras")
        ex = nested if isinstance(nested, dict) else row
        # 界面上的 span 是 1-based 闭区间（第 a–b 行），侧车/对齐用
        # 0-based 半开区间（[start, end)），这里做一次换算。
        if isinstance(span, list) and len(span) == 2:
            a, b = int(span[0]), int(span[1])
            if a >= 1 and b >= a:
                ex["span"] = [a - 1, b]
        if isinstance(part_span, list) and len(part_span) == 2:
            a, b = int(part_span[0]), int(part_span[1])
            if a >= 1 and b >= a:
                ex["part_span"] = [a - 1, b]
    write_atomic(path, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in out))
    return path


def commit_draft(
    chunks_path: Path | str,
    draft_path: Path | str,
    backup_dir: Path | str,
    stem: str,
) -> dict[str, Any]:
    """校验基线 → 备份 → 原子写正式 chunks.jsonl → 删草稿。"""
    chunks_path = Path(chunks_path)
    draft_path = Path(draft_path)
    if not draft_path.is_file():
        raise DraftError("草稿不存在")
    parsed = parse_draft_name(draft_path.name)
    if parsed is None:
        raise DraftError(f"草稿文件名不合法：{draft_path.name}")
    if not chunks_path.is_file():
        raise DraftError(f"正式切片文件不存在：{chunks_path}")
    baseline12 = sha12(chunks_path)
    if parsed[1] != baseline12:
        raise DraftError("草稿已过期（基线变了），请放弃草稿后重新编辑")
    draft_chunks = read_chunks(draft_path)
    if not draft_chunks:
        raise DraftError("草稿里没有切片")
    backup = backup_chunks(chunks_path, backup_dir, stem)
    write_atomic(chunks_path, _dump(contract_chunks(draft_chunks)))
    draft_path.unlink(missing_ok=True)
    return {
        "n_chunks": len(draft_chunks),
        "baseline12": baseline12,
        "backup": str(backup) if backup else "",
    }
