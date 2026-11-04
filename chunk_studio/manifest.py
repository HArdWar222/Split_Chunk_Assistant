"""读取 FAC-RAG chunking v2 的 ``_manifest.json``（只读）。

只取本工作台用得到的字段：

``files[i]``
    key / group / law_name / path / jsonl / n_chunks / sha256 / summary / gate / warnings
``totals``
    n_files / n_chunks / kinds / char_coverage ...

这份 JSON 由 FAC-RAG 的切块流程生成；本模块**只读**，不会写回。
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_NAME = "_manifest.json"
EXTRAS_NAME = "_extras.jsonl"

#: 「本地两目录」约定下的文件名后缀。
CHUNKS_SUFFIX = ".chunks.jsonl"
EXTRAS_SUFFIX = ".extras.jsonl"


class ManifestError(RuntimeError):
    """产物目录不可用（不存在 / 缺 manifest / 格式不对）。"""


class SourceMissingError(ManifestError):
    """源 .md 文件缺失。"""

    def __init__(self, path: str):
        super().__init__(f"源文件缺失：{path}")
        self.path = path


@dataclass
class FileEntry:
    """一个语料文件在 manifest 中的摘要。"""

    key: str
    group: str
    law_name: str
    source_path: str
    jsonl: str
    n_chunks: int
    sha256: str = ""
    kinds: dict[str, int] = field(default_factory=dict)
    char_coverage: float = 0.0
    gate_ok: bool = True
    warnings: list[str] = field(default_factory=list)
    extras_count: int = 0
    local: bool = False  # True = 来自本地两目录数据源（无 manifest 质检信息）

    def to_payload(self) -> dict:
        """给前端的精简结构（列表页用，避免把 warnings 全文塞进去）。"""
        return {
            "key": self.key,
            "group": self.group,
            "law_name": self.law_name,
            "path": self.source_path,
            "n_chunks": self.n_chunks,
            "sha256": self.sha256[:12],
            "kinds": dict(self.kinds),
            "char_coverage": self.char_coverage,
            "gate_ok": self.gate_ok,
            "n_warnings": len(self.warnings),
            "warnings": self.warnings,
            "extras_count": self.extras_count,
            "local": self.local,
        }

    @classmethod
    def from_manifest(cls, item: dict) -> "FileEntry":
        summary = item.get("summary") or {}
        gate = item.get("gate") or {}
        return cls(
            key=str(item.get("key") or ""),
            group=str(item.get("group") or ""),
            law_name=str(item.get("law_name") or ""),
            source_path=str(item.get("path") or ""),
            jsonl=str(item.get("jsonl") or ""),
            n_chunks=int(item.get("n_chunks") or 0),
            sha256=str(item.get("sha256") or ""),
            kinds={str(k): int(v) for k, v in (summary.get("kinds") or {}).items()},
            char_coverage=float(summary.get("char_coverage") or 0.0),
            gate_ok=bool(gate.get("ok", True)),
            warnings=[str(w) for w in (item.get("warnings") or [])],
            extras_count=int(item.get("extras_count") or 0),
        )


class Manifest:
    """_manifest.json 的内存视图。"""

    mode = "manifest"

    def __init__(self, chunks_dir: Path | str):
        self.chunks_dir = Path(chunks_dir)
        self.generated_at: str = ""
        self.engine: str = ""
        self.workspace: str = ""
        self.totals: dict = {}
        self.gate: dict = {}
        self.files: "OrderedDict[str, FileEntry]" = OrderedDict()
        self._extras: dict[str, dict] | None = None
        self._load()

    # -- 加载 -----------------------------------------------------------
    def _load(self) -> None:
        path = self.chunks_dir / MANIFEST_NAME
        if not path.is_file():
            raise ManifestError(f"找不到 {MANIFEST_NAME}：{path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestError(f"{MANIFEST_NAME} 读取失败：{exc}") from exc
        if not isinstance(raw, dict):
            raise ManifestError(f"{MANIFEST_NAME} 格式不对：{path}")

        self.generated_at = str(raw.get("generated_at") or "")
        self.engine = str(raw.get("engine") or "")
        self.workspace = str(raw.get("workspace") or "")
        self.totals = raw.get("totals") or {}
        self.gate = raw.get("gate") or {}

        for item in raw.get("files") or []:
            entry = FileEntry.from_manifest(item)
            if entry.key:
                self.files[entry.key] = entry
        if not self.files:
            raise ManifestError(f"{MANIFEST_NAME} 里没有任何文件条目：{path}")

    # -- 查询 -----------------------------------------------------------
    def get(self, key: str) -> FileEntry:
        try:
            return self.files[key]
        except KeyError:
            raise ManifestError(f"manifest 里没有这个文件：{key}") from None

    @property
    def n_files(self) -> int:
        return len(self.files)

    @property
    def n_chunks(self) -> int:
        return sum(e.n_chunks for e in self.files.values())

    @property
    def groups(self) -> "OrderedDict[str, list[FileEntry]]":
        """按 manifest 出现顺序分组。"""
        out: "OrderedDict[str, list[FileEntry]]" = OrderedDict()
        for entry in self.files.values():
            out.setdefault(entry.group, []).append(entry)
        return out

    def source_text(self, key: str) -> str:
        """读取源 .md 全文（只读）。"""
        entry = self.get(key)
        path = Path(entry.source_path)
        if not path.is_file():
            raise SourceMissingError(entry.source_path)
        return path.read_text(encoding="utf-8")

    # -- 文件与侧车定位 -------------------------------------------------
    def chunk_file(self, key: str) -> Path:
        """该文件的切块 jsonl 路径。"""
        return self.chunks_dir / self.get(key).jsonl

    def extras_for(self, key: str) -> dict[str, dict]:
        """返回全局 ``_extras.jsonl`` 的 chunk_id -> extras 映射（整份缓存）。"""
        if self._extras is None:
            extras: dict[str, dict] = {}
            path = self.chunks_dir / EXTRAS_NAME
            if path.is_file():
                with path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        chunk_id = str(row.get("id") or "")
                        if chunk_id:
                            extras[chunk_id] = dict(row.get("extras") or {})
            self._extras = extras
        return self._extras

    # -- 给前端 ---------------------------------------------------------
    def payload(self) -> dict:
        groups = []
        for name, entries in self.groups.items():
            groups.append(
                {
                    "name": name,
                    "n_files": len(entries),
                    "n_chunks": sum(e.n_chunks for e in entries),
                    "files": [e.to_payload() for e in entries],
                }
            )
        return {
            "ok": True,
            "mode": self.mode,
            "chunks_dir": str(self.chunks_dir),
            "generated_at": self.generated_at,
            "engine": self.engine,
            "totals": {
                "n_files": self.n_files,
                "n_chunks": self.n_chunks,
                "kinds": dict(self.totals.get("kinds") or {}),
                "char_coverage": self.totals.get("char_coverage", 0),
                "gate_ok": bool(self.gate.get("ok", True)),
            },
            "groups": groups,
        }


class DirManifest:
    """本地「两目录约定」数据源的清单视图。

    目录约定
    --------
    ``<data_root>/source/``
        源 .md 文件（可带子目录，子目录名会拼进分组）
    ``<data_root>/chunks/``
        ``<名>.chunks.jsonl``（必需，每行一个 chunk）
        ``<名>.extras.jsonl``（可选，每行一个 chunk 的侧车，字段平铺）

    与 :class:`Manifest` 的差异：没有 JSON 清单文件，所有统计开服时现算；
    ``gate`` / ``warnings`` / ``sha256`` 没有上游数据，统一留空。
    """

    mode = "dirs"

    def __init__(self, data_root: Path | str):
        self.data_root = Path(data_root)
        self.source_dir = self.data_root / "source"
        self.chunks_dir = self.data_root / "chunks"
        self.generated_at: str = ""
        self.engine: str = "local-dirs"
        self.workspace: str = str(self.data_root)
        self.totals: dict = {}
        self.gate: dict = {"ok": True}
        self.files: "OrderedDict[str, FileEntry]" = OrderedDict()
        self._extras_cache: dict[Path, dict[str, dict]] = {}
        self._load()

    # -- 加载 -----------------------------------------------------------
    def _load(self) -> None:
        if not self.source_dir.is_dir() or not self.chunks_dir.is_dir():
            raise ManifestError(
                f"本地数据目录不完整（需含 source/ 与 chunks/）：{self.data_root}"
            )
        total_chars = 0
        total_source = 0
        merged_kinds: dict[str, int] = {}
        for path in sorted(self.chunks_dir.rglob("*" + CHUNKS_SUFFIX)):
            if not path.is_file():
                continue
            name = path.name[: -len(CHUNKS_SUFFIX)]
            rel = path.parent.relative_to(self.chunks_dir)
            group = "data" if rel.as_posix() == "." else "data/" + rel.as_posix()
            key = f"{group}/{name}"
            source = self.source_dir / rel / (name + ".md")
            warnings: list[str] = []
            if not source.is_file():
                found = sorted(self.source_dir.rglob(name + ".md"))
                if found:
                    source = found[0]
                else:
                    source = Path()
                    warnings.append(f"源文件缺失：{name}.md")
            n_chunks, kinds, chars = _scan_chunks(path)
            for kind, count in kinds.items():
                merged_kinds[kind] = merged_kinds.get(kind, 0) + count
            source_chars = 0
            if source.is_file():
                source_chars = len(source.read_text(encoding="utf-8"))
                total_source += source_chars
            total_chars += chars
            extras_path = path.with_name(name + EXTRAS_SUFFIX)
            extras_count = _count_lines(extras_path)
            coverage = round(chars / source_chars, 4) if source_chars else 0.0
            self.files[key] = FileEntry(
                key=key,
                group=group,
                law_name=name,
                source_path=str(source) if source.is_file() else "",
                jsonl=str(path),
                n_chunks=n_chunks,
                kinds=kinds,
                char_coverage=coverage,
                gate_ok=True,
                warnings=warnings,
                extras_count=extras_count,
                local=True,
            )
        self.totals = {
            "n_files": len(self.files),
            "n_chunks": sum(e.n_chunks for e in self.files.values()),
            "kinds": merged_kinds,
            "char_coverage": round(total_chars / total_source, 4) if total_source else 0,
        }

    # -- 查询 -----------------------------------------------------------
    def get(self, key: str) -> FileEntry:
        try:
            return self.files[key]
        except KeyError:
            raise ManifestError(f"本地数据源里没有这个文件：{key}") from None

    @property
    def n_files(self) -> int:
        return len(self.files)

    @property
    def n_chunks(self) -> int:
        return sum(e.n_chunks for e in self.files.values())

    @property
    def groups(self) -> "OrderedDict[str, list[FileEntry]]":
        out: "OrderedDict[str, list[FileEntry]]" = OrderedDict()
        for entry in self.files.values():
            out.setdefault(entry.group, []).append(entry)
        return out

    def source_text(self, key: str) -> str:
        entry = self.get(key)
        path = Path(entry.source_path)
        if not path.is_file():
            raise SourceMissingError(entry.source_path or entry.key)
        return path.read_text(encoding="utf-8")

    # -- 文件与侧车定位 -------------------------------------------------
    def chunk_file(self, key: str) -> Path:
        return Path(self.get(key).jsonl)

    def extras_for(self, key: str) -> dict[str, dict]:
        entry = self.get(key)
        path = Path(entry.jsonl).with_name(entry.law_name + EXTRAS_SUFFIX)
        if path not in self._extras_cache:
            extras: dict[str, dict] = {}
            if path.is_file():
                with path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        chunk_id = str(row.get("id") or "")
                        if chunk_id:
                            nested = row.get("extras")
                            if isinstance(nested, dict):
                                # FAC-RAG 全局侧车写法：{"id": …, "extras": {…}}
                                extras[chunk_id] = dict(nested)
                            else:
                                # 切块包写法：字段平铺在同一行
                                extras[chunk_id] = {
                                    k: v for k, v in row.items() if k != "id"
                                }
            self._extras_cache[path] = extras
        return self._extras_cache[path]

    # -- 给前端 ---------------------------------------------------------
    def payload(self) -> dict:
        groups = []
        for name, entries in self.groups.items():
            groups.append(
                {
                    "name": name,
                    "n_files": len(entries),
                    "n_chunks": sum(e.n_chunks for e in entries),
                    "files": [e.to_payload() for e in entries],
                }
            )
        return {
            "ok": True,
            "mode": self.mode,
            "chunks_dir": str(self.chunks_dir),
            "source_dir": str(self.source_dir),
            "generated_at": self.generated_at,
            "engine": self.engine,
            "totals": {
                "n_files": self.n_files,
                "n_chunks": self.n_chunks,
                "kinds": dict(self.totals.get("kinds") or {}),
                "char_coverage": self.totals.get("char_coverage", 0),
                "gate_ok": True,
            },
            "groups": groups,
        }


def _scan_chunks(path: Path) -> tuple[int, dict[str, int], int]:
    """数块 / 统计 chunk_kind / 累计正文字符（跳过坏行）。"""
    count = 0
    kinds: dict[str, int] = {}
    chars = 0
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            count += 1
            kind = str(row.get("chunk_kind") or "")
            kinds[kind] = kinds.get(kind, 0) + 1
            chars += len(str(row.get("chunk") or ""))
    return count, kinds, chars


def _count_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                count += 1
    return count
