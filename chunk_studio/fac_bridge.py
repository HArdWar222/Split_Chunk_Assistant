"""只读 JSON 访问层：本仓库与切块产物之间的唯一数据入口。

支持两种数据源（都只读）
------------------------
1. ``FAC-RAG`` manifest 产物
   - ``data/chunks_v2/_manifest.json``
   - ``data/chunks_v2/<group>__<law>.jsonl``（每行一个 chunk，15 字段）
   - ``data/chunks_v2/_extras.jsonl``（``{"id": ..., "extras": {...}}`` 侧车）
   - ``manifest.files[i].path``（源 .md 的绝对路径）
2. 本地「两目录约定」数据源（:class:`DirManifest`）
   - ``<data_root>/chunks/<名>.chunks.jsonl``
   - ``<data_root>/chunks/<名>.extras.jsonl``（可选，字段平铺）
   - ``<data_root>/source/<名>.md``

**刻意不 import fac_rag、不调用 chunking_v2**：自动切块归 FAC-RAG，
本工作台只消费它已经切好的产物。P4 的参数重切才会新增裸调用入口。
"""

from __future__ import annotations

import json
from pathlib import Path

from .manifest import DirManifest, FileEntry, Manifest, ManifestError


class FacBridge:
    """按 key 读取切块产物；带缓存，重复打开同一文件不重复解析。

    ``source`` 可以是：

    - ``Manifest`` / ``DirManifest`` 实例（推荐，双数据源时由 Studio 注入）；
    - ``Path`` / ``str``：按 FAC-RAG 产物目录处理（P0 兼容路径）。
    """

    def __init__(self, source: Manifest | Path | str):
        if isinstance(source, (Manifest, DirManifest)):
            self._manifest: Manifest | DirManifest | None = source
            self.chunks_dir = source.chunks_dir
        else:
            self._manifest = None
            self.chunks_dir = Path(source)
        self._chunks_cache: dict[str, list[dict]] = {}

    # -- manifest -------------------------------------------------------
    @property
    def manifest(self) -> Manifest | DirManifest:
        if self._manifest is None:
            self._manifest = Manifest(self.chunks_dir)
        return self._manifest

    def reload(self) -> None:
        self._manifest = None
        self._chunks_cache.clear()

    # -- chunks ---------------------------------------------------------
    def chunks(self, key: str) -> list[dict]:
        """返回某文件的 chunk 列表（15 字段 dict，原样）。"""
        if key in self._chunks_cache:
            return self._chunks_cache[key]
        path = Path(self.manifest.chunk_file(key))
        if not path.is_file():
            raise ManifestError(f"切块文件缺失：{path}")
        chunks: list[dict] = []
        with path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    chunks.append(_normalize_chunk(json.loads(line)))
                except json.JSONDecodeError as exc:
                    raise ManifestError(
                        f"{path.name} 第 {line_no} 行不是合法 JSON：{exc}"
                    ) from exc
        self._chunks_cache[key] = chunks
        return chunks

    # -- extras ---------------------------------------------------------
    def extras(self, key: str) -> dict[str, dict]:
        """返回某文件 chunk_id -> extras 的映射。"""
        all_extras = self.manifest.extras_for(key)
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
            "mode": m.mode,
            "chunks_dir": str(self.chunks_dir),
            "n_files": m.n_files,
            "n_chunks": m.n_chunks,
            "generated_at": m.generated_at,
            "gate_ok": bool(getattr(m, "gate", {}).get("ok", True)),
            "n_groups": len(m.groups),
        }


def _normalize_chunk(raw: dict) -> dict:
    """补齐/校正本地切块包与 FAC-RAG 产物的字段差异（不丢原字段）。"""
    chunk = dict(raw)
    text = str(chunk.get("chunk") or "")
    # 本地切块包没有 token_len；FAC-RAG 的 token_len 也是字符数口径。
    if not chunk.get("token_len"):
        chunk["token_len"] = len(text)
    for name in ("part_index", "part_total"):
        try:
            chunk[name] = int(chunk.get(name) or 1)
        except (TypeError, ValueError):
            chunk[name] = 1
    if not chunk.get("law_invalid"):
        chunk["law_invalid"] = ""
    return chunk
