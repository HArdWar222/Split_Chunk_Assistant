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

import shutil
from collections import OrderedDict
from pathlib import Path

from .align import align_chunks, summarize_align
from .autopart import materialize_chunks
from .chunker import WatchService, engine_info
from .config import Config, ConfigError
from . import drafts
from . import sources as source_tags
from . import status as review
from .fac_bridge import FacBridge
from .manifest import DirManifest, ManifestError
from .ops import OpError, apply_ops, chunk_diff, normalize_chunks
from .source import (
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
        self._data_bridge: FacBridge | None = None
        self._data_error: str | None = None
        self._doc_cache: "OrderedDict[str, dict]" = OrderedDict()
        self._tables_cache: "OrderedDict[str, dict]" = OrderedDict()
        self._default_roots: dict[str, str] = {}
        # P2 会话内编辑：key -> 命令列表；撤销 = 弹出并整体重放，不落盘（P3 才写覆盖层）
        self._ops: dict[str, list[dict]] = {}
        self._redo: dict[str, list[dict]] = {}
        # P3 磁盘草稿：key -> {stem,path,baseline12,saved_at,stale,chunks}
        self._drafts: dict[str, dict] = {}
        # 有未写进草稿的会话内修改
        self._dirty: set[str] = set()
        self._init_bridge()
        # 自动切块 / 轮询（引擎调用是子进程，工作台本身不 import 引擎）
        self.watch = WatchService(self)

    # -- 内部 -----------------------------------------------------------
    def _init_bridge(self) -> None:
        self._data_bridge = None
        self._data_error = None
        self._doc_cache.clear()
        self._tables_cache.clear()
        self._ops.clear()
        self._redo.clear()
        self._dirty.clear()
        self._default_roots = {}
        # 唯一数据源：本地「两目录」数据根（data/source + data/chunks）
        if not self.cfg.data_root:
            self._data_error = "未配置本地数据目录 data_root（下需含 source/ 与 chunks/）"
            return
        try:
            data_bridge = FacBridge(DirManifest(self.cfg.data_root))
            _ = data_bridge.manifest  # 先触发加载，尽早暴露错误
            self._data_bridge = data_bridge
        except (ManifestError, OSError) as exc:
            self._data_error = str(exc)
        finally:
            self._load_drafts()

    # -- 草稿（P3） ------------------------------------------------------
    def _stem_for(self, key: str) -> str:
        """文档 key → 草稿文件名主干（子目录拼 ``__``）。"""
        entry = self.bridge.manifest.get(key)
        chunks_path = Path(entry.jsonl)
        doc_dir = chunks_path.parent
        chunks_root = Path(self.cfg.data_root) / "chunks"
        try:
            rel = doc_dir.parent.relative_to(chunks_root).as_posix()
        except ValueError:
            rel = ""
        return drafts.stem_for("" if rel == "." else rel, doc_dir.name)

    # -- 审核状态（D22，文件级） -----------------------------------------
    def _chunks_path(self, key: str) -> Path:
        return Path(self.bridge_for(key).manifest.get(key).jsonl)

    def _review_for(self, key: str) -> dict:
        try:
            path = self._chunks_path(key)
            state = review.state_for_chunks(self.cfg.data_root, self._stem_for(key), path)
        except (ManifestError, OSError):
            return {"state": review.STATE_NONE, "updated_at": 0}
        state["label"] = review.label(state["state"])
        return state

    def _mark_in_review(self, key: str) -> None:
        try:
            path = self._chunks_path(key)
            review.mark_in_review(self.cfg.data_root, self._stem_for(key), drafts.sha12(path))
        except (ManifestError, OSError):
            pass

    def _mark_reviewed(self, key: str) -> None:
        try:
            path = self._chunks_path(key)
            review.mark_reviewed(self.cfg.data_root, self._stem_for(key), drafts.sha12(path))
        except (ManifestError, OSError):
            pass

    def _restore_review_previous(self, key: str) -> None:
        try:
            path = self._chunks_path(key)
            review.restore_previous(self.cfg.data_root, self._stem_for(key), drafts.sha12(path))
        except (ManifestError, OSError):
            pass

    def _load_drafts(self) -> None:
        """扫描 data/draft，把基线仍然匹配的草稿加载进内存。"""
        self._drafts = {}
        if self._data_bridge is None:
            return
        draft_dir = Path(self.cfg.data_root) / "draft"
        if not draft_dir.is_dir():
            return
        for key in list(self._data_bridge.manifest.files):
            try:
                stem = self._stem_for(key)
            except ManifestError:
                continue
            entry = self._data_bridge.manifest.get(key)
            chunks_path = Path(entry.jsonl)
            if not chunks_path.is_file():
                continue
            current12 = drafts.sha12(chunks_path)
            candidates = drafts.draft_files(draft_dir, stem)
            if not candidates:
                continue
            path = max(candidates, key=lambda p: p.stat().st_mtime)
            parsed = drafts.parse_draft_name(path.name)
            if parsed is None:
                continue
            stale = parsed[1] != current12
            self._drafts[key] = {
                "stem": stem,
                "path": path,
                "baseline12": parsed[1],
                "saved_at": path.stat().st_mtime,
                "stale": stale,
                "chunks": [] if stale else drafts.read_chunks(path),
            }

    def _active_draft(self, key: str) -> dict | None:
        draft = self._drafts.get(key)
        if draft and not draft.get("stale"):
            return draft
        return None

    def draft_payload(self, key: str) -> dict:
        """给 ``/api/doc`` 用的草稿状态（前端徽章 / 圆点用）。"""
        draft = self._drafts.get(key)
        if draft is None:
            return {"exists": False, "stale": False, "baseline12": "", "saved_at": 0}
        return {
            # 只要磁盘上有新鲜草稿就算存在（chunks 只是会话重放起点，保存后不再替换）
            "exists": not bool(draft.get("stale")),
            "stale": bool(draft.get("stale")),
            "baseline12": draft.get("baseline12") or "",
            "saved_at": float(draft.get("saved_at") or 0),
        }

    def save_draft(self, key: str) -> dict:
        """把当前（含未保存编辑）的块列表写进草稿文件。

        注意：内存里的 ``_drafts[key]["chunks"]`` 是**会话重放起点**（打开时从磁盘
        草稿读入），保存后不能替换成「已应用全部 ops 的结果」，否则撤销时
        （弹出最后一个 op 后）会从含该 op 结果的起点重放，导致块残留、后续
        drop 找不到切片。这里只更新文件与元信息，重放起点保持不变。
        """
        doc = self.doc_payload(key)
        entry = self.bridge_for(key).manifest.get(key)
        chunks_path = Path(entry.jsonl)
        if not chunks_path.is_file():
            raise drafts.DraftError(f"正式切片文件不存在：{chunks_path}")
        stem = self._stem_for(key)
        baseline12 = drafts.sha12(chunks_path)
        path = drafts.save_draft(
            Path(self.cfg.data_root) / "draft", stem, baseline12, doc["chunks"]
        )
        previous = self._drafts.get(key) or {}
        self._drafts[key] = {
            "stem": stem,
            "path": path,
            "baseline12": baseline12,
            "saved_at": path.stat().st_mtime,
            "stale": False,
            "chunks": [dict(chunk) for chunk in (previous.get("chunks") or [])],
        }
        self._dirty.discard(key)
        self._mark_in_review(key)
        return self.doc_payload(key)

    def discard_draft(self, key: str) -> dict:
        """删除草稿文件并回到基线（会话内编辑也一并丢弃）。"""
        self._base_doc(key)
        stem = self._stem_for(key)
        drafts.discard_draft(Path(self.cfg.data_root) / "draft", stem)
        self._drafts.pop(key, None)
        self._ops.pop(key, None)
        self._redo.pop(key, None)
        self._dirty.discard(key)
        self._restore_review_previous(key)
        return self.doc_payload(key)

    def commit_draft(self, key: str) -> dict:
        """提交草稿：校验基线 → 备份 → 原子覆盖 chunks.jsonl → 删草稿。"""
        draft = self._active_draft(key)
        if draft is None:
            raise drafts.DraftError("没有可提交的草稿（先点保存草稿）")
        entry = self.bridge_for(key).manifest.get(key)
        chunks_path = Path(entry.jsonl)
        saved = self.save_draft(key)  # 先把内存里的最新状态写进草稿
        # 把编辑器里的行区间（span/part_span）写进侧车，提交后重新加载时
        # 优先按它对齐，避免「提交后切好的块又被启发式对齐合并/重叠」。
        draft_chunks = drafts.read_chunks(draft["path"])
        drafts.merge_extras_spans(
            drafts.extras_path(chunks_path, entry.law_name), draft_chunks
        )
        self._base_doc(key)
        result = drafts.commit_draft(
            chunks_path,
            draft["path"],
            Path(self.cfg.data_root) / "backup",
            draft["stem"],
        )
        self.reload()
        self._mark_reviewed(key)
        doc = self.doc_payload(key)
        doc["committed"] = result
        return doc


    # -- 对外 -----------------------------------------------------------
    @property
    def bridge(self) -> FacBridge:
        """本地两目录数据源（唯一数据源）。"""
        if self._data_bridge is None:
            raise ManifestError(self._data_error or "本地数据目录不可用")
        return self._data_bridge

    def bridges(self) -> list[FacBridge]:
        return [self._data_bridge] if self._data_bridge is not None else []

    def bridge_for(self, key: str) -> FacBridge:
        """按 key 找所属文件（只有一个数据源）。"""
        if self._data_bridge is None:
            raise ManifestError(self._data_error or "本地数据目录不可用")
        if key not in self._data_bridge.manifest.files:
            raise ManifestError(f"没有这个文件：{key}")
        return self._data_bridge

    @property
    def available(self) -> bool:
        return self._data_bridge is not None

    @property
    def error(self) -> str | None:
        return None if self._data_bridge is not None else (self._data_error or "本地数据目录不可用")

    def reload(self) -> None:
        self._init_bridge()

    # -- 文件删除（P3，物理删除） ---------------------------------------
    def delete_files(self, keys: list[str]) -> dict:
        """物理删除选中文件：源文件 + 切块目录 + 草稿 + 备份 + 审核状态。

        key 只能来自文件清单，路径一律经 manifest 解析（不接受 ``..`` 等片段）；
        正在切块的文件会拒绝并返回 failed。
        """
        if not self.available:
            raise ManifestError(self.error or "本地数据目录不可用")
        ordered: list[str] = []
        for raw in keys or []:
            key = str(raw or "").strip()
            if key and key not in ordered:
                ordered.append(key)
        if not ordered:
            raise ManifestError("没有选中要删除的文件")

        data_root = Path(self.cfg.data_root)
        draft_dir = data_root / "draft"
        backup_dir = data_root / "backup"
        current = None
        try:
            cur = self.watch.snapshot().get("current")
            if isinstance(cur, dict):
                current = cur.get("subject") or cur.get("key")
            elif isinstance(cur, str):
                current = cur
        except Exception:  # watch 状态只是提示，取不到不阻塞删除
            current = None

        deleted: list[str] = []
        failed: list[dict] = []
        for key in ordered:
            if current and key == current:
                failed.append({"key": key, "error": "该文件正在切块，稍后再删"})
                continue
            try:
                bridge = self.bridge_for(key)
                entry = bridge.manifest.get(key)
            except (ManifestError, OSError) as exc:
                failed.append({"key": key, "error": str(exc)})
                continue

            errors: list[str] = []
            doc_dir = Path(entry.jsonl).parent
            try:
                shutil.rmtree(doc_dir)
            except FileNotFoundError:
                pass
            except OSError as exc:
                errors.append("切块目录：" + str(exc))

            stem = self._stem_for(key)
            targets = [Path(entry.source_path)] if entry.source_path else []
            targets += drafts.draft_files(draft_dir, stem)
            targets += sorted(backup_dir.glob(stem + ".*.jsonl"))
            targets.append(review.status_path(data_root, stem))
            for path in targets:
                try:
                    path.unlink(missing_ok=True)
                except IsADirectoryError:
                    try:
                        shutil.rmtree(path)
                    except OSError as exc:
                        errors.append(path.name + "：" + str(exc))
                except OSError as exc:
                    errors.append(path.name + "：" + str(exc))

            if errors:
                failed.append({"key": key, "error": "；".join(errors)})
            else:
                deleted.append(key)

        if deleted:
            self.reload()
        return {
            "ok": not failed,
            "deleted": deleted,
            "failed": failed,
            "n_deleted": len(deleted),
        }

    # -- 来源类别（P3，文件级人工标注） ---------------------------------
    def set_file_source(self, key: str, category: object) -> dict:
        """把文件下所有切片的 ``source`` 设为某类别，并记入 sources.json。

        - 有草稿时同步改草稿块，并按新基线重命名草稿（否则草稿立刻过期）；
        - 改类别算一次编辑：审核状态置 in_review。
        """
        if not self.available:
            raise ManifestError(self.error or "本地数据目录不可用")
        category = source_tags.validate(category)
        if category:
            # 自定义类别：先登记到类别列表（内置 4 类也会幂等保留）
            source_tags.add_category(self.cfg.data_root, category)
        entry = self.bridge_for(key).manifest.get(key)
        law_name = entry.law_name
        chunks_path = Path(entry.jsonl)
        if not chunks_path.is_file():
            raise ManifestError(f"切片文件不存在：{chunks_path}")

        mapping = source_tags.load(self.cfg.data_root)
        if category:
            mapping[law_name] = category
        else:
            mapping.pop(law_name, None)
        source_tags.save(self.cfg.data_root, mapping)

        n_chunks = source_tags.apply_to_chunks_file(chunks_path, category)

        draft = self._drafts.get(key)
        if draft and not draft.get("stale"):
            draft_chunks = drafts.read_chunks(draft["path"])
            source_tags.apply_to_chunks(draft_chunks, category)
            new_baseline = drafts.sha12(chunks_path)
            path = drafts.save_draft(
                Path(self.cfg.data_root) / "draft",
                draft["stem"],
                new_baseline,
                draft_chunks,
            )
            draft["path"] = path
            draft["baseline12"] = new_baseline
            draft["saved_at"] = path.stat().st_mtime
            draft["chunks"] = [dict(chunk) for chunk in draft_chunks]

        self._mark_in_review(key)
        self.reload()
        return {
            "ok": True,
            "key": key,
            "law_name": law_name,
            "source": category,
            "label": category or source_tags.UNSET_LABEL,
            "n_chunks": n_chunks,
            "categories": source_tags.list_categories(self.cfg.data_root),
        }

    # -- 载荷 -----------------------------------------------------------
    def files_payload(self) -> dict:
        """文件清单（左栏数据），只来自本地两目录数据源。"""
        bridges = self.bridges()
        if not bridges:
            return {
                "ok": False,
                "chunks_dir": "",
                "error": self.error or "本地数据目录不可用",
                "config": self.config_payload(),
            }
        bridge = bridges[0]
        payload = bridge.manifest.payload()
        source_map = source_tags.load(self.cfg.data_root)
        for group in payload.get("groups") or []:
            for item in group.get("files") or []:
                draft = self.draft_payload(item.get("key") or "")
                item["draft"] = bool(draft.get("exists"))
                item["draft_stale"] = bool(draft.get("stale"))
                item["source_category"] = source_tags.category_for(
                    source_map, item.get("law_name") or ""
                )
                review_state = self._review_for(item.get("key") or "")
                item["review_state"] = review_state.get("state")
                item["review_label"] = review_state.get("label")
                item["review_updated_at"] = review_state.get("updated_at")
        payload.update(
            {
                "ok": True,
                "mode": "dirs",
                "sources": [bridge.stats()],
                "source_categories": source_tags.list_categories(self.cfg.data_root),
                "config": self.config_payload(),
            }
        )
        return payload

    def config_payload(self) -> dict:
        """配置（设置面板展示用）。"""
        data_root = self.cfg.data_root
        return {
            "ok": True,
            "data_root": data_root,
            "data_source_dir": str(Path(data_root) / "source") if data_root else "",
            "data_chunks_dir": str(Path(data_root) / "chunks") if data_root else "",
            "port": self.cfg.port,
            "auto_part": self.cfg.auto_part,
            "max_chars": self.cfg.max_chars,
            "min_part_chars": self.cfg.min_part_chars,
            "auto_chunk": self.cfg.auto_chunk,
            "chunker_root": self.cfg.chunker_root or str(engine_info(self.cfg).get("root") or ""),
            "chunker_poll": self.cfg.chunker_poll,
            "chunker_strategy": self.cfg.chunker_strategy,
            "engine": engine_info(self.cfg),
            "errors": self.cfg.validate(),
            "data_ok": self._data_bridge is not None,
            "data_error": self._data_error,
            "mode": "dirs" if self._data_bridge is not None else "none",
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
        if self._data_bridge is None:
            raise ConfigError(self.error or "改了配置后本地数据目录依然不可用")
        return self.config_payload()

    # -- 单文件三栏载荷（P1 只读 + P2 会话内编辑） -----------------------
    @staticmethod
    def _match_extras(chunks: list[dict], extras: dict[str, dict]) -> dict[str, dict]:
        """按块内容把 extras 重新对号。

        早期提交只按列表位置重排了 chunk id，sidecar 里的表网格/短语仍挂在旧 id
        上；这里优先用 id，内容对不上时按 embed_text 与正文做一次重匹配。
        """

        def norm(text: object) -> str:
            return "".join(str(text or "").split())

        by_text: dict[str, dict] = {}
        for ex in extras.values():
            text = norm(ex.get("embed_text"))
            if text and text not in by_text:
                by_text[text] = ex

        matched: dict[str, dict] = {}
        for chunk in chunks:
            cid = chunk.get("id")
            body = norm(chunk.get("chunk"))
            ex = extras.get(cid)
            if ex is not None:
                embed = norm(ex.get("embed_text"))
                pinned = isinstance(ex.get("span"), list) and len(ex.get("span")) == 2
                if not pinned and embed and body and embed != body:
                    # 提交写入的行区间是权威的（正文被改过时 embed_text 仍是
                    # 引擎旧正文），只有没带 span 的旧侧车行才按内容重挂。
                    ex = by_text.get(body)
            elif body:
                ex = by_text.get(body)
            if ex is not None:
                matched[cid] = ex
        return matched

    def _base_doc(self, key: str) -> dict:
        """基线文档：源行 + 视图块 + 对齐，缓存最近 DOC_CACHE_SIZE 个文件。

        基线永远是「产物里的原样」，任何编辑都不改它；每次请求在其上重放命令。
        """
        if not self.available:
            raise ManifestError(self.error or "切块产物不可用")
        cached = self._doc_cache.get(key)
        if cached is not None:
            self._doc_cache.move_to_end(key)
            return cached

        bridge = self.bridge_for(key)
        entry = bridge.manifest.get(key)
        chunks = bridge.chunks(key)
        extras = self._match_extras(chunks, bridge.extras(key))
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
            # 私有：表块的完整 table_grid（按块 id），只给物化拆分时找回表格视图用；
            # 基线块的公开 extras 不带 table_grid（体积大，前端另外走 /api/tables）。
            "_grids": {
                str(chunk_id): extra["table_grid"]
                for chunk_id, extra in extras.items()
                if isinstance(extra.get("table_grid"), dict)
            },
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

    def _materializer(self, base: dict):
        """返回「自动分 part 物化」钩子（未开启 auto_part 时为 None）。

        物化发生在命令重放过程中：任何命令产生超长块后立刻拆成真实 ``-pN``
        块并算好 ``part_span``，后续命令就能按 part 操作，撤销重放也一致。
        基线物化结果按 (max_chars, min_part_chars) 缓存在 ``base["_materials"]``。
        """
        if not self.cfg.auto_part:
            return None
        max_chars = self.cfg.max_chars
        min_chars = self.cfg.min_part_chars
        raw_lines = base.get("_raw_lines") or []
        grids = base.get("_grids") or {}

        def run(chunks: list[dict]) -> list[dict]:
            return materialize_chunks(
                chunks,
                max_chars,
                min_chars,
                raw_lines,
                originals=base.get("chunks"),
                grids=grids,
            )

        return run

    def _materialized_base(self, base: dict, materializer) -> list[dict]:
        """把基线块物化一次并缓存（diff 的基准要与页面看到的一致）。"""
        if materializer is None:
            return normalize_chunks(base["chunks"], base["law_name"])
        cache = base.setdefault("_materials", {})
        token = (self.cfg.max_chars, self.cfg.min_part_chars)
        cached = cache.get(token)
        if cached is None:
            cached = normalize_chunks(materializer(base["chunks"]), base["law_name"])
            cache[token] = cached
        return cached

    def doc_payload(self, key: str) -> dict:
        """基线 / 草稿 + 会话内编辑命令重放后的三栏载荷。"""
        base = self._base_doc(key)
        ops = list(self._ops.get(key) or [])
        raw_lines = base.get("_raw_lines") or []
        materializer = self._materializer(base)
        draft = self._active_draft(key)
        if draft and draft.get("chunks"):
            chunks = [dict(chunk) for chunk in draft["chunks"]]
        else:
            chunks = base["chunks"]
        chunks = normalize_chunks(chunks, base["law_name"])
        if materializer is not None:
            chunks = materializer(chunks)
        if ops:
            chunks = apply_ops(
                chunks,
                ops,
                law_name=base["law_name"],
                source_lines=raw_lines,
                materialize=materializer,
                normalize=lambda items: normalize_chunks(items, base["law_name"]),
            )
        chunks = normalize_chunks(chunks, base["law_name"])
        payload = dict(base)
        payload.pop("_raw_lines", None)
        payload.pop("_materials", None)
        payload.pop("_grids", None)
        payload["chunks"] = chunks
        payload["stats"] = self._restat(chunks, base["stats"])
        payload["edited"] = False  # P3 覆盖层
        payload["dirty"] = key in self._dirty
        payload["draft"] = self.draft_payload(key)
        payload["review"] = self._review_for(key)
        payload["diff"] = chunk_diff(self._materialized_base(base, materializer), chunks)
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
        if not self.available:
            raise ManifestError(self.error or "切块产物不可用")
        cached = self._tables_cache.get(key)
        if cached is not None:
            self._tables_cache.move_to_end(key)
            return cached

        bridge = self.bridge_for(key)
        entry = bridge.manifest.get(key)
        chunks = bridge.chunks(key)
        extras = self._match_extras(chunks, bridge.extras(key))
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
        raw_lines = base.get("_raw_lines") or []
        materializer = self._materializer(base)
        draft = self._active_draft(key)
        if draft and draft.get("chunks"):
            start = [dict(chunk) for chunk in draft["chunks"]]
        else:
            start = base["chunks"]
        start = normalize_chunks(start, base["law_name"])
        if materializer is not None:
            start = materializer(start)
        candidate = list(self._ops.get(key) or []) + [op]
        apply_ops(
            start,
            candidate,
            law_name=base["law_name"],
            source_lines=raw_lines,
            materialize=materializer,
            normalize=lambda items: normalize_chunks(items, base["law_name"]),
        )  # 只做校验
        self._ops[key] = candidate
        self._redo[key] = []
        self._dirty.add(key)
        self._mark_in_review(key)
        return self.doc_payload(key)

    def undo_op(self, key: str) -> dict:
        base = self._base_doc(key)
        ops = list(self._ops.get(key) or [])
        if not ops:
            raise OpError("没有可撤销的编辑")
        last = ops.pop()
        self._redo.setdefault(key, []).append(last)
        self._ops[key] = ops
        if not ops:
            self._dirty.discard(key)
            self._restore_review_previous(key)
        return self.doc_payload(key)

    def redo_op(self, key: str) -> dict:
        base = self._base_doc(key)
        redo = list(self._redo.get(key) or [])
        if not redo:
            raise OpError("没有可重做的编辑")
        op = redo.pop()
        self._redo[key] = redo
        self._ops.setdefault(key, []).append(op)
        self._dirty.add(key)
        self._mark_in_review(key)
        return self.doc_payload(key)

    def reset_edits(self, key: str) -> dict:
        """丢弃本文件全部会话内编辑（P3 起会删除覆盖层）。"""
        self._base_doc(key)  # 校验 key 存在
        self._ops.pop(key, None)
        self._redo.pop(key, None)
        self._dirty.discard(key)
        self._restore_review_previous(key)
        return self.doc_payload(key)
