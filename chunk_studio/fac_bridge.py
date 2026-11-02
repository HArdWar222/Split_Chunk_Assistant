"""只读 JSON 访问层：本仓库与 FAC-RAG 产物之间的唯一数据入口。

P0–P3 数据源（全部只读）
------------------------
``data/chunks_v2/_manifest.json``
    文件清单（97 个文件、块数、源 md 路径……）
``data/chunks_v2/<group>__<law>.jsonl``
    每行一个 chunk，共 15 字段
``data/chunks_v2/_extras.jsonl``
    ``{"id": ..., "extras": {...}}`` 侧车
``manifest.files[i].path``
    源 .md 的绝对路径

**刻意不 import fac_rag、不调用 chunking_v2**：自动切块归 FAC-RAG，
本工作台只消费它已经切好的产物。P4 的参数重切才会新增裸调用入口。
"""

from __future__ import annotations

import json
from pathlib import Path

from .manifest import FileEntry, Manifest, ManifestError

EXTRAS_NAME = "_extras.jsonl"


class FacBridge:
    """按 key 读取切块产物；带缓存，重复打开同一文件不重复解析。"""

    def __init__(self, chunks_dir: Path | str):
        self.chunks_dir = Path(chunks_dir)
        self._manifest: Manifest | None = None
        self._extras: dict[str, dict] | None = None
        self._chunks_cache: dict[str, list[dict]] = {}

    # -- manifest -------------------------------------------------------
    @property
    def manifest(self) -> Manifest:
        if self._manifest is None:
            self._manifest = Manifest(self.chunks_dir)
        return self._manifest

    def reload(self) -> None:
        self._manifest = None
        self._extras = None
        self._chunks_cache.clear()

    # -- chunks ---------------------------------------------------------
    def chunks(self, key: str) -> list[dict]:
        """返回某文件的 chunk 列表（15 字段 dict，原样）。"""
        if key in self._chunks_cache:
            return self._chunks_cache[key]
        entry = self.manifest.get(key)
        path = self.chunks_dir / entry.jsonl
        if not path.is_file():
            raise ManifestError(f"切块文件缺失：{path}")
        chunks: list[dict] = []
        with path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    chunks.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ManifestError(
                        f"{entry.jsonl} 第 {line_no} 行不是合法 JSON：{exc}"
                    ) from exc
        self._chunks_cache[key] = chunks
        return chunks

    # -- extras ---------------------------------------------------------
    def _load_extras(self) -> dict[str, dict]:
        if self._extras is None:
            path = self.chunks_dir / EXTRAS_NAME
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
                            extras[chunk_id] = dict(row.get("extras") or {})
            self._extras = extras
        return self._extras

    def extras(self, key: str) -> dict[str, dict]:
        """返回某文件 chunk_id -> extras 的映射。"""
        all_extras = self._load_extras()
        ids = {str(c.get("id")) for c in self.chunks(key)}
        return {cid: all_extras.get(cid, {}) for cid in ids}

    def load_doc(self, key: str) -> tuple[FileEntry, list[dict], dict[str, dict]]:
        """一次取齐：清单条目 + 切块 + extras。"""
        entry = self.manifest.get(key)
        return entry, self.chunks(key), self.extras(key)

    # -- 源文件 ---------------------------------------------------------
    def source_text(self, key: str) -> str:
        """读取该文件的源 .md 全文（只读）。"""
        return self.manifest.source_text(key)

    # -- 统计 -----------------------------------------------------------
    def stats(self) -> dict:
        m = self.manifest
        return {
            "chunks_dir": str(self.chunks_dir),
            "n_files": m.n_files,
            "n_chunks": m.n_chunks,
            "generated_at": m.generated_at,
            "gate_ok": bool(m.gate.get("ok", True)),
            "n_groups": len(m.groups),
        }
