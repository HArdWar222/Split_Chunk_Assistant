"""数据门面：配置 + 只读切块产物，向 HTTP 层提供干净的载荷。

职责边界
--------
- 持有当前 ``Config`` 与 ``FacBridge``；
- 把 manifest 转成前端要的文件清单结构（P0）；
- P1：把单个文件转成「原文行 + 切片 + 对齐结果」的三栏载荷；
- 负责配置的保存与重新加载（保存后立即生效，不用重启）；
- 这里**没有任何写切块产物的代码**——FAC-RAG 的数据永远只读。
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from .align import align_chunks, summarize_align
from .config import Config, ConfigError
from .fac_bridge import FacBridge
from .manifest import ManifestError
from .ops import OpError, apply_ops, chunk_diff
from .source import (
    default_roots_from_manifest,
    display_text,
    line_kind,
    locate_source,
    read_lines,
)

#: 公开给前端的 extras 字段白名单（table_grid 等体积大的只在服务端用于对齐）。
PUBLIC_EXTRAS = (
    "ref_title",
    "region",
    "profile",
    "namespace",
    "clause",
    "table_label",
    "table_title",
    "table_rows",
    "table_mode",
    "appendix",
)

#: 文档载荷缓存条数（来回切换文件不重复读盘 + 重复对齐）。
DOC_CACHE_SIZE = 8

#: 表格视图缓存条数（table_grid 体积大，按文件懒加载）。
TABLES_CACHE_SIZE = 8


def _public_extras(extras: dict) -> dict:
    out: dict = {}
    for key in PUBLIC_EXTRAS:
        value = extras.get(key)
        if value in (None, "", [], {}):
            continue
        out[key] = value
    probe = extras.get("probe")
    if isinstance(probe, dict):
        slim = {k: probe[k] for k in ("family", "confidence", "heading_mode") if probe.get(k) is not None}
        if slim:
            out["probe"] = slim
    return out


class Studio:
    def __init__(
        self,
        cfg: Config | None = None,
        config_path: Path | str | None = None,
    ):
        self.config_path = Path(config_path) if config_path else None
        self.cfg = cfg or Config.load(self.config_path)
        self._bridge: FacBridge | None = None
        self._error: str | None = None
        self._doc_cache: "OrderedDict[str, dict]" = OrderedDict()
        self._tables_cache: "OrderedDict[str, dict]" = OrderedDict()
        self._default_roots: dict[str, str] = {}
        # P2 会话内编辑：key -> 命令列表；撤销 = 弹出并整体重放，不落盘（P3 才写覆盖层）
        self._ops: dict[str, list[dict]] = {}
        self._redo: dict[str, list[dict]] = {}
        self._init_bridge()

    # -- 内部 -----------------------------------------------------------
    def _init_bridge(self) -> None:
        self._bridge = None
        self._error = None
        self._doc_cache.clear()
        self._tables_cache.clear()
        self._ops.clear()
        self._redo.clear()
        self._default_roots = {}
        try:
            bridge = FacBridge(self.cfg.chunks_dir)
            _ = bridge.manifest  # 先触发加载，尽早暴露错误
            self._bridge = bridge
            self._default_roots = default_roots_from_manifest(bridge.manifest)
        except ManifestError as exc:
            self._error = str(exc)

    # -- 对外 -----------------------------------------------------------
    @property
    def bridge(self) -> FacBridge:
        if self._bridge is None:
            raise ManifestError(self._error or "切块产物不可用")
        return self._bridge

    @property
    def available(self) -> bool:
        return self._bridge is not None

    @property
    def error(self) -> str | None:
        return self._error

    def reload(self) -> None:
        self._init_bridge()

    # -- 载荷 -----------------------------------------------------------
    def files_payload(self) -> dict:
        """文件清单（左栏数据）。"""
        if self._bridge is None:
            return {
                "ok": False,
                "chunks_dir": self.cfg.chunks_dir,
                "error": self._error or "切块产物不可用",
                "config": self.config_payload(),
            }
        payload = self._bridge.manifest.payload()
        payload["config"] = self.config_payload()
        return payload

    def config_payload(self) -> dict:
        """配置 + 从 manifest 推导的默认源根目录（设置面板展示用）。"""
        return {
            "ok": True,
            "fac_root": self.cfg.fac_root,
            "chunks_dir": self.cfg.chunks_dir,
            "source_roots": dict(self.cfg.source_roots),
            "default_source_roots": dict(self._default_roots),
            "groups": list(self._default_roots.keys()),
            "port": self.cfg.port,
            "errors": self.cfg.validate(),
            "manifest_ok": self._bridge is not None,
            "manifest_error": self._error,
        }

    def update_config(self, patch: dict) -> dict:
        """校验 → 落盘 → 重新加载产物。失败抛 ConfigError。"""
        if not isinstance(patch, dict):
            raise ConfigError("配置必须是 JSON 对象")
        merged = self.cfg.merged(patch)
        errors = merged.validate()
        if errors:
            raise ConfigError("；".join(errors))
        merged.save(self.config_path)
        self.cfg = merged
        self.reload()
        if self._bridge is None:
            raise ConfigError(self._error or "改了配置后产物依然不可用")
        return self.config_payload()

    # -- 单文件三栏载荷（P1 只读 + P2 会话内编辑） -----------------------
    def _base_doc(self, key: str) -> dict:
        """基线文档：源行 + 视图块 + 对齐，缓存最近 DOC_CACHE_SIZE 个文件。

        基线永远是「产物里的原样」，任何编辑都不改它；每次请求在其上重放命令。
        """
        if self._bridge is None:
            raise ManifestError(self._error or "切块产物不可用")
        cached = self._doc_cache.get(key)
        if cached is not None:
            self._doc_cache.move_to_end(key)
            return cached

        entry = self._bridge.manifest.get(key)
        chunks = self._bridge.chunks(key)
        extras = self._bridge.extras(key)
        source_path = locate_source(self.cfg, entry, default_roots=self._default_roots)
        lines = read_lines(source_path)
        aligned = align_chunks(chunks, lines, extras)

        out_lines = []
        for index, raw in enumerate(lines):
            text, truncated = display_text(raw)
            out_lines.append(
                {
                    "n": index + 1,
                    "text": text,
                    "truncated": truncated,
                    "chars": len(raw),
                    "kind": line_kind(text),
                }
            )

        out_chunks = []
        for chunk, result in zip(chunks, aligned):
            span = result["span"]
            out_chunks.append(
                {
                    **chunk,
                    "extras": _public_extras(extras.get(str(chunk.get("id") or "")) or {}),
                    # 前端用 1-based 闭区间
                    "span": [span[0] + 1, span[1]] if span else None,
                    "align": result["status"],
                    "manual": False,
                }
            )

        base_stats = summarize_align(aligned)
        source_chars = sum(len(line) + 1 for line in lines)
        base_stats.update(
            {
                "n_lines": len(lines),
                "n_chunks": len(chunks),
                "source_chars": source_chars,
                "align_rate": round(base_stats["n_aligned"] / len(chunks), 4) if chunks else 0.0,
            }
        )

        base = {
            "ok": True,
            "key": key,
            "group": entry.group,
            "law_name": entry.law_name,
            "source_path": str(source_path),
            "source_chars": source_chars,
            "lines": out_lines,
            "chunks": out_chunks,
            "stats": base_stats,
            # 私有：源文件原始行，用于编辑后给拆分块补行区间（不随 API 返回，不缓存到前端）
            "_raw_lines": lines,
            "manifest": {
                "n_chunks": entry.n_chunks,
                "gate_ok": entry.gate_ok,
                "kinds": entry.kinds,
                "char_coverage": entry.char_coverage,
                "warnings": entry.warnings,
                "sha256": entry.sha256,
                "jsonl": entry.jsonl,
                "path": entry.source_path,
            },
        }
        self._doc_cache[key] = base
        while len(self._doc_cache) > DOC_CACHE_SIZE:
            self._doc_cache.popitem(last=False)
        return base

    @staticmethod
    def _restat(chunks: list[dict], base_stats: dict) -> dict:
        """按当前块列表重算对齐统计（新增/手工块不再计入已对齐）。"""
        kinds: dict[str, int] = {}
        aligned = 0
        for chunk in chunks:
            status = str(chunk.get("align") or "miss")
            kinds[status] = kinds.get(status, 0) + 1
            if chunk.get("span"):
                aligned += 1
        stats = dict(base_stats)
        stats.update(
            {
                "n_chunks": len(chunks),
                "n_aligned": aligned,
                "n_miss": kinds.get("miss", 0),
                "align_kinds": kinds,
                "align_rate": round(aligned / len(chunks), 4) if chunks else 0.0,
            }
        )
        return stats

    def doc_payload(self, key: str) -> dict:
        """基线 + 会话内编辑命令重放后的三栏载荷。"""
        base = self._base_doc(key)
        ops = list(self._ops.get(key) or [])
        raw_lines = base.get("_raw_lines") or []
        if ops:
            chunks = apply_ops(
                base["chunks"],
                ops,
                law_name=base["law_name"],
                source_lines=raw_lines,
            )
        else:
            chunks = base["chunks"]
        payload = dict(base)
        payload.pop("_raw_lines", None)
        payload["chunks"] = chunks
        payload["stats"] = self._restat(chunks, base["stats"])
        payload["edited"] = False  # P3 覆盖层
        payload["dirty"] = bool(ops)
        payload["diff"] = chunk_diff(base["chunks"], chunks)
        payload["edits"] = {
            "n_ops": len(ops),
            "undo": len(ops),
            "redo": len(self._redo.get(key) or []),
        }
        return payload

    # -- P2 表格视图（table_grid） ---------------------------------------
    def tables_payload(self, key: str) -> dict:
        """单个文件所有表块的 ``table_grid``（结构化行列），供前端画真表格。

        不塞进 doc_payload 的原因：山东自由裁量标准这类文件有 684 个表块，
        网格 JSON 约 400KB，能画表格的页面按需单独取一次即可。
        """
        if self._bridge is None:
            raise ManifestError(self._error or "切块产物不可用")
        cached = self._tables_cache.get(key)
        if cached is not None:
            self._tables_cache.move_to_end(key)
            return cached

        entry = self._bridge.manifest.get(key)
        extras = self._bridge.extras(key)
        tables: dict[str, dict] = {}
        for chunk_id, extra in extras.items():
            grid = extra.get("table_grid")
            if not isinstance(grid, dict) or grid.get("rows") is None:
                continue
            rows = [[str(cell) for cell in row] for row in (grid.get("rows") or [])]
            headers = [str(head) for head in (grid.get("headers") or [])]
            width = max([int(grid.get("width") or 0), len(headers)] + [len(row) for row in rows] or [1])
            note_from = grid.get("note_from")
            tables[chunk_id] = {
                "headers": headers,
                "rows": rows,
                "width": width,
                "header_rows": int(grid.get("header_rows") or 1),
                "note_from": int(note_from) if note_from is not None else len(rows),
                "table_label": str(extra.get("table_label") or ""),
                "table_title": str(extra.get("table_title") or ""),
                "table_mode": str(extra.get("table_mode") or ""),
                "table_rows": int(extra.get("table_rows") or 0),
            }

        payload = {
            "ok": True,
            "key": key,
            "law_name": entry.law_name,
            "n_tables": len(tables),
            "tables": tables,
        }
        self._tables_cache[key] = payload
        while len(self._tables_cache) > TABLES_CACHE_SIZE:
            self._tables_cache.popitem(last=False)
        return payload

    # -- P2 编辑命令 -----------------------------------------------------
    def apply_op(self, key: str, op: dict) -> dict:
        """校验并追加一条编辑命令，返回重放后的完整文档载荷。"""
        if not isinstance(op, dict):
            raise OpError("op 必须是 JSON 对象")
        base = self._base_doc(key)
        candidate = list(self._ops.get(key) or []) + [op]
        apply_ops(base["chunks"], candidate, law_name=base["law_name"])  # 只做校验
        self._ops[key] = candidate
        self._redo[key] = []
        return self.doc_payload(key)

    def undo_op(self, key: str) -> dict:
        base = self._base_doc(key)
        ops = list(self._ops.get(key) or [])
        if not ops:
            raise OpError("没有可撤销的编辑")
        last = ops.pop()
        self._redo.setdefault(key, []).append(last)
        self._ops[key] = ops
        return self.doc_payload(key)

    def redo_op(self, key: str) -> dict:
        base = self._base_doc(key)
        redo = list(self._redo.get(key) or [])
        if not redo:
            raise OpError("没有可重做的编辑")
        op = redo.pop()
        self._redo[key] = redo
        self._ops.setdefault(key, []).append(op)
        return self.doc_payload(key)

    def reset_edits(self, key: str) -> dict:
        """丢弃本文件全部会话内编辑（P3 起会删除覆盖层）。"""
        self._base_doc(key)  # 校验 key 存在
        self._ops.pop(key, None)
        self._redo.pop(key, None)
        return self.doc_payload(key)
