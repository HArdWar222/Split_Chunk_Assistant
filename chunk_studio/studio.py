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
        self._default_roots: dict[str, str] = {}
        self._init_bridge()

    # -- 内部 -----------------------------------------------------------
    def _init_bridge(self) -> None:
        self._bridge = None
        self._error = None
        self._doc_cache.clear()
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

    # -- 单文件三栏载荷（P1） -------------------------------------------
    def doc_payload(self, key: str) -> dict:
        """原文行 + 切片 + 对齐结果。缓存最近 DOC_CACHE_SIZE 个文件。"""
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
                }
            )

        stats = summarize_align(aligned)
        source_chars = sum(len(line) + 1 for line in lines)
        stats.update(
            {
                "n_lines": len(lines),
                "n_chunks": len(chunks),
                "source_chars": source_chars,
                "align_rate": round(stats["n_aligned"] / len(chunks), 4) if chunks else 0.0,
            }
        )

        payload = {
            "ok": True,
            "key": key,
            "group": entry.group,
            "law_name": entry.law_name,
            "source_path": str(source_path),
            "source_chars": source_chars,
            "lines": out_lines,
            "chunks": out_chunks,
            "stats": stats,
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
            "edited": False,
        }
        self._doc_cache[key] = payload
        while len(self._doc_cache) > DOC_CACHE_SIZE:
            self._doc_cache.popitem(last=False)
        return payload
