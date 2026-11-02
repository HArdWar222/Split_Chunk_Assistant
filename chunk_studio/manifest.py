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

    def __init__(self, chunks_dir: Path | str):
        self.chunks_dir = Path(chunks_dir)
        self.generated_at: str = ""
        self.engine: str = ""
        self.workspace: str = ""
        self.totals: dict = {}
        self.gate: dict = {}
        self.files: "OrderedDict[str, FileEntry]" = OrderedDict()
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
