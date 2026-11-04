# -*- coding: utf-8 -*-
"""fac_rag.chunking_v2.emitter —— 第⑤步之后的出口：契约块 + 侧车 + 数据集。

D-040 / D-042 口径：

* 块本体只写 15 个契约字段（``data/chunks_v2/{group}__{law_name}.jsonl``），
  与 v1 基线块文件命名一致，方便逐文件比对 ``chunk_hash``；
* 单元附加信息（region/appendix/profile…）全部走侧车
  ``data/chunks_v2/_extras.jsonl``，每行 ``{"id": ..., "extras": {...}}``；
* ``_manifest.json`` 记每份文件的块数 / sha256 / sanity / summary / profile / probe
  体检单，以及 ``extras_file``（路径/条数/sha256）。

产物目录独立（``data/chunks_v2/``），M2 不碰 ``data/chunks/`` 基线（D3 并行目录）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅类型提示
    from fac_rag.config import Config
    from fac_rag.chunking.sources import Sources


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _jsonl_text(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def write_dataset(
    cfg: "Config",
    sources: "Sources",
    out_dir: str | Path | None = None,
    *,
    verbose: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """全量跑 v2 pipeline，写 ``data/chunks_v2/``（jsonl + _extras.jsonl + _manifest.json）。

    ``limit`` 只给调试/快速验证用（切前 N 份），正式验收不传。
    """
    from . import _chunk_file_full

    out = Path(out_dir) if out_dir is not None else Path(cfg.chunks_v2_dir)
    out.mkdir(parents=True, exist_ok=True)

    files: list[dict[str, Any]] = []
    extras_rows: list[dict[str, Any]] = []
    group_stats: dict[str, dict[str, Any]] = {}
    total_chunks = 0

    for group, path in cfg.iter_corpus_files():
        if limit is not None and len(files) >= int(limit):
            break
        src_group = sources.get(group.name)
        record, extras_by_id, report = _chunk_file_full(path, group=src_group, verbose=verbose)
        chunks = record["chunks"]
        jsonl_name = f"{group.name}__{Path(path).stem}.jsonl"
        jsonl_text = _jsonl_text(chunks)
        (out / jsonl_name).write_text(jsonl_text, encoding="utf-8", newline="\n")

        file_extras = [
            {"id": chunk["id"], "extras": extras_by_id.get(chunk["id"], {})} for chunk in chunks
        ]
        extras_rows.extend(file_extras)
        total_chunks += len(chunks)

        files.append(
            {
                "key": f"{group.name}/{Path(path).stem}",
                "group": group.name,
                "law_name": record["law_name"],
                "path": str(path),
                "jsonl": jsonl_name,
                "n_chunks": len(chunks),
                "sha256": _sha256_text(jsonl_text),
                "sanity": record["sanity"],
                "summary": record["summary"],
                "profile": report.get("profile", ""),
                "probe": {
                    "family": report.get("family", ""),
                    "confidence": report.get("confidence", 0.0),
                    "heading_mode": report.get("heading_mode", ""),
                    "flags": report.get("flags", {}),
                    "warnings": list(report.get("warnings", [])),
                },
                "extras_count": len(file_extras),
            }
        )
        stats = group_stats.setdefault(
            group.name, {"strategy": getattr(src_group, "strategy", ""), "n_files": 0, "n_chunks": 0}
        )
        stats["n_files"] += 1
        stats["n_chunks"] += len(chunks)

    extras_text = _jsonl_text(extras_rows)
    extras_name = "_extras.jsonl"
    (out / extras_name).write_text(extras_text, encoding="utf-8", newline="\n")

    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "engine": "v2",
        "workspace": str(Path.cwd()),
        "fac_root": str(getattr(cfg, "fac_root", "")),
        "out_dir": str(out),
        "groups": group_stats,
        "files": files,
        "extras_file": {
            "path": extras_name,
            "count": len(extras_rows),
            "sha256": _sha256_text(extras_text),
        },
        "totals": {"n_files": len(files), "n_chunks": total_chunks},
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2)
    (out / "_manifest.json").write_text(manifest_text + "\n", encoding="utf-8", newline="\n")
    return manifest


__all__ = ["write_dataset"]
