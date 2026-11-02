"""chunk_studio.core —— FAC-RAG chunking v2 切块的可视化 + 人工切片后端核心。

设计原则
--------
* **只读** FAC-RAG：``data/chunks_v2`` 与源 ``.md`` 都只读，任何人工改动写到
  ``Split_assistant/edits/`` 覆盖层，不污染你的数据集；
* 复用你的真实流水线：``chunk_file_full`` 重跑产物可精确复现（已实测 74 块
  id/text/hash 全同），所以「参数重切」也用同一条链路，只是把 params 覆盖；
* 原文对照：chunk 正文是清洗/渲染后的文本，不是原文逐字子串，因此用
  「规范化行匹配」定位：

  - 条文/段落块（clause/paragraph/appendix/front）：按 chunk 首个非空行在源
    工作行里顺序查找（去空白、去 HTML、去面包屑后比较，先精确后包含）；
  - 表格块（table）：用 extras 里的 ``table_grid.rows`` 逐行反查源行（去掉
    ``|``/HTML 标签/空白后比较），命中即为表格块的真实行区间；
  - 整个 chunk 的定位状态记成 ``exact / substr / table_rows / table_label /
    miss``，前端把 miss 的块标黄，人工可以手动重切。

所有输出仍是 15 字段契约（外加 ``extras``），落盘走 ``fac_rag.contracts.Chunk``
自检，id / 日期 / token_len / chunk_hash 由该契约对象统一补齐。
"""
from __future__ import annotations

import json
import os
import re
import sys
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

warnings.filterwarnings("ignore")

FAC_ROOT = Path(os.environ.get("FAC_ROOT", r"C:\Users\QiuYe\Desktop\FAC-RAG"))
if str(FAC_ROOT) not in sys.path:
    sys.path.insert(0, str(FAC_ROOT))

from fac_rag import contracts  # noqa: E402
from fac_rag.chunking.sources import load_sources  # noqa: E402
from fac_rag.chunking_v2 import (  # noqa: E402
    chunk_file_full,
    expand_lines,
    load_profiles,
    probe_lines,
    select_profile,
)
from fac_rag.chunking_v2.render import DEFAULT_CLEAN_RULES, clean_text, strip_media  # noqa: E402

RE_TAG = re.compile(r"<[^>]+>")
RE_ID = re.compile(r"^(.+)-(\d{4,})(?:-p(\d+))?$")

DEFAULT_EFFECTIVE_DATE = ""
DEFAULT_LAW_INVALID = "2030-12-12"


class StudioError(RuntimeError):
    """工具层错误（给前端 4xx 用）。"""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_name(key: str) -> str:
    """``national_laws/xxx`` → ``national_laws__xxx``（落盘文件名）。"""
    return key.replace("/", "__").replace("\\", "__").strip() or "unnamed"


def _strip_html(s: str) -> str:
    if "<" not in s:
        return s
    from html import unescape

    return unescape(RE_TAG.sub("", s))


def _norm_line(raw: str, rules: Iterable[str] | None) -> str:
    """规范化：去 HTML/图片占位/面包屑/清洗规则 → 再去全部空白。"""
    s = _strip_html(strip_media(str(raw).strip()))
    if " > " in s:  # 标准式渲染给 unit.lines[0] 注入的面包屑前缀
        s = s.split(" > ")[-1]
    if rules:
        s = clean_text(s, tuple(rules))
    return "".join(s.split())


def _norm_pipe(raw: str) -> str:
    s = _strip_html(str(raw))
    return "".join(s.replace("|", "").split())


def _norm_row(cells: Any) -> str:
    parts: list[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, (list, tuple)):
            for y in x:
                walk(y)
        elif x is not None:
            parts.append(str(x))

    walk(cells)
    return "".join("".join(parts).split())


def _row_variants(cells: Any) -> list[str]:
    """grid 行 → 候选规范化字符串（合并单元格会导致相邻重复，去重一份备用）。"""
    flat: list[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, (list, tuple)):
            for y in x:
                walk(y)
        elif x is not None:
            flat.append(str(x))

    walk(cells)
    raw = "".join("".join(flat).split())
    dedup: list[str] = []
    for cell in flat:
        if dedup and dedup[-1] == cell:
            continue
        dedup.append(cell)
    variants = [raw]
    d = "".join("".join(dedup).split())
    if d and d != raw:
        variants.append(d)
    return [v for v in variants if v]


def _scan(lines: list[str], target: str, start: int, *, allow_substr: bool = True) -> int:
    """从 start 起找第一个等于 target（或包含 target）的行。"""
    if not target:
        return -1
    n = len(lines)
    for i in range(start, n):
        if lines[i] == target:
            return i
    if not allow_substr or len(target) < 4:
        return -1
    for i in range(start, n):
        if target in lines[i]:
            return i
    return -1


# ---------------------------------------------------------------------------
# 覆盖层
# ---------------------------------------------------------------------------


class GroupOverride:
    """给 SourceGroup 套一层 params 覆盖，用于「参数重切」。"""

    def __init__(self, base: Any, overrides: dict[str, Any]):
        self._base = base
        self._overrides = {k: v for k, v in overrides.items() if v is not None}

    def __getattr__(self, item: str) -> Any:
        return getattr(self._base, item)

    def params_for(self, filename: str) -> dict[str, Any]:
        params = dict(self._base.params_for(filename))
        params.update(self._overrides)
        return params

    def meta_for(self, filename: str) -> dict[str, Any]:
        return self._base.meta_for(filename)


# ---------------------------------------------------------------------------
# 核心
# ---------------------------------------------------------------------------


class Studio:
    def __init__(
        self,
        fac_root: str | Path | None = None,
        *,
        chunks_dir: str | Path | None = None,
        out_dir: str | Path | None = None,
    ):
        self.fac_root = Path(fac_root) if fac_root else FAC_ROOT
        self.chunks_dir = Path(chunks_dir) if chunks_dir else self.fac_root / "data" / "chunks_v2"
        self.manifest_path = self.chunks_dir / "_manifest.json"
        self.extras_path = self.chunks_dir / "_extras.jsonl"
        self.profiles_dir = self.fac_root / "configs" / "profiles"
        self.sources_path = self.fac_root / "configs" / "sources.yaml"
        self.out_dir = Path(out_dir) if out_dir else Path(__file__).resolve().parent.parent / "edits"
        self.out_dir.mkdir(parents=True, exist_ok=True)

        if not self.manifest_path.is_file():
            raise StudioError(f"找不到 manifest：{self.manifest_path}")
        self.manifest: dict[str, Any] = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.files: dict[str, dict[str, Any]] = {f["key"]: f for f in self.manifest.get("files") or []}
        self.sources = load_sources(self.sources_path)
        self.profiles = load_profiles(self.profiles_dir)

    # ---------------- 文件清单 ----------------

    def list_files(self) -> list[dict[str, Any]]:
        out = []
        for key, fi in sorted(self.files.items()):
            summary = fi.get("summary") or {}
            sanity = fi.get("sanity") or {}
            gate = fi.get("gate") or {}
            out.append(
                {
                    "key": key,
                    "group": fi.get("group"),
                    "law_name": fi.get("law_name"),
                    "n_chunks": fi.get("n_chunks"),
                    "kinds": summary.get("kinds") or {},
                    "chars_in_source": summary.get("chars_in_source"),
                    "char_coverage": summary.get("char_coverage"),
                    "gate_ok": gate.get("ok"),
                    "gate_failures": gate.get("failures") or [],
                    "law_num_ratio": sanity.get("law_num_ratio"),
                    "warnings": len(fi.get("warnings") or []),
                    "gaps": len(fi.get("gaps") or []),
                    "edited": self._overlay_paths(key)[0].is_file(),
                }
            )
        return out

    def entry(self, key: str) -> dict[str, Any]:
        fi = self.files.get(key)
        if not fi:
            raise StudioError(f"manifest 里没有这个文件：{key}")
        return fi

    def _overlay_paths(self, key: str) -> tuple[Path, Path, Path]:
        stem = self.out_dir / safe_name(key)
        return stem.with_suffix(".jsonl"), stem.with_suffix(".extras.jsonl"), stem.with_suffix(".meta.json")

    # ---------------- 构建单文件视图 ----------------

    def doc(self, key: str, *, overrides: dict[str, Any] | None = None, use_edits: bool = True) -> dict[str, Any]:
        fi = self.entry(key)
        path = Path(fi["path"])
        if not path.is_file():
            raise StudioError(f"源文件不存在：{path}")
        group = self.sources.get(fi["group"])
        if group is None:
            raise StudioError(f"sources.yaml 里没有分组：{fi['group']}")
        run_group = GroupOverride(group, overrides) if overrides else group

        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()

        report = probe_lines(lines, path=str(path), hint=getattr(group, "profile", None) or fi["group"])
        profile = select_profile(report, self.profiles)
        work_lines, source_line_map, expand_info = expand_lines(lines)

        record, extras, probe_report = chunk_file_full(
            path, group=run_group, profiles=self.profiles
        )
        chunks = list(record.get("chunks") or [])

        edited = False
        overlay_jsonl, overlay_extras, overlay_meta = self._overlay_paths(key)
        if use_edits and not overrides and overlay_jsonl.is_file():
            chunks, extras = self._load_overlay(key)
            edited = True

        aligned = self._align(chunks, work_lines, source_line_map, profile, extras)

        display_lines = []
        for i, raw in enumerate(work_lines):
            src = source_line_map[i] if source_line_map else i
            display_lines.append(
                {
                    "n": i,
                    "src": (int(src) + 1) if src is not None else i + 1,
                    "text": _strip_html(raw),
                }
            )

        kinds: dict[str, int] = {}
        for ch in chunks:
            k = ch.get("chunk_kind") or "?"
            kinds[k] = kinds.get(k, 0) + 1

        payload = {
            "key": key,
            "group": fi.get("group"),
            "law_name": fi.get("law_name"),
            "source_path": str(path),
            "source_chars": record.get("source_chars") or len(text),
            "profile": {
                "name": getattr(profile, "name", ""),
                "family": (report.get("family") or probe_report.get("family") if isinstance(probe_report, dict) else report.get("family")),
                "confidence": report.get("confidence"),
                "clean_rules": list(profile.render.clean),
            },
            "params": dict(run_group.params_for(path.name)),
            "expand": None if not expand_info else {
                "applied": bool(expand_info.get("applied")),
                "orig_lines": expand_info.get("orig_lines"),
                "work_lines": expand_info.get("work_lines"),
                "expanded_tables": expand_info.get("expanded_tables"),
                "expanded_source_lines": expand_info.get("expanded_source_lines"),
            },
            "lines": display_lines,
            "chunks": [
                {
                    **ch,
                    "extras": extras.get(ch.get("id"), {}) if extras else {},
                    "span": a["span"],
                    "align": a["status"],
                }
                for ch, a in zip(chunks, aligned)
            ],
            "stats": {
                "n_chunks": len(chunks),
                "kinds": kinds,
                "n_aligned": sum(1 for a in aligned if a["span"]),
                "n_miss": sum(1 for a in aligned if not a["span"]),
                "align_kinds": _counter(aligned),
            },
            "manifest": {
                "n_chunks": fi.get("n_chunks"),
                "gate": fi.get("gate"),
                "sanity": fi.get("sanity"),
                "summary": fi.get("summary"),
                "warnings": fi.get("warnings") or [],
                "gaps": fi.get("gaps") or [],
                "path": fi.get("path"),
            },
            "edited": edited,
            "generated_at": _now(),
        }
        return payload

    # ---------------- 对齐 ----------------

    def _align(
        self,
        chunks: list[dict[str, Any]],
        work_lines: list[str],
        source_line_map: list[int] | None,
        profile: Any,
        extras: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rules = tuple(profile.render.clean) or DEFAULT_CLEAN_RULES
        ns = [_norm_line(w, rules) for w in work_lines]
        npipe: list[str | None] = [None] * len(ns)
        cursor = 0
        out: list[dict[str, Any]] = []

        for ch in chunks:
            kind = ch.get("chunk_kind") or ""
            span: list[int] | None = None
            status = "miss"

            if kind == "table":
                ex = extras.get(ch.get("id")) or ch.get("extras") or {}
                grid = ex.get("table_grid") or {}
                rows = grid.get("rows") or []
                start = max(0, cursor - 2)
                first = last = None
                j = start
                for row in rows:
                    variants = _row_variants(row)
                    if not variants:
                        continue
                    hit = -1
                    for nr in variants:
                        if not npipe[j] and npipe[j] is not None:
                            j += 1
                        for k in range(j, len(ns)):
                            if k >= len(ns):
                                break
                            if npipe[k] is None:
                                npipe[k] = _norm_pipe(work_lines[k])
                            ok = npipe[k] == nr
                            if not ok and len(nr) >= 6:
                                ok = nr in npipe[k]
                            if ok:
                                hit = k
                                break
                        if hit >= 0:
                            break
                    if hit >= 0:
                        if first is None:
                            first = hit
                        last = hit
                        j = hit + 1
                if first is not None:
                    span = [first, last + 1]
                    status = "table_rows"
                    # 往上把表题/表号行带进来（最多 6 行，且不越过上一块终点）
                    label = str(ex.get("table_label") or "")
                    title = str(ex.get("table_title") or "")
                    for b in range(first - 1, max(cursor - 1, first - 7), -1):
                        if b < 0:
                            break
                        line = ns[b]
                        if (label and label.replace(" ", "") in line) or (
                            title and title[:6] in line
                        ) or (line.startswith("表") and len(line) < 80):
                            span[0] = b
                            break
                    cursor = max(cursor, span[1])
                else:
                    label = str(ex.get("table_label") or "")
                    title = str(ex.get("table_title") or "")
                    keys = [k for k in (label, title) if len(k) > 3]
                    for i in range(start, len(ns)):
                        if any(k in ns[i] for k in keys):
                            span = [i, i + 1]
                            status = "table_label"
                            cursor = max(cursor, i + 1)
                            break
            else:
                tlines = [t for t in (_norm_line(x, rules) for x in str(ch.get("chunk") or "").split("\n")) if t]
                first_t = tlines[0] if tlines else ""
                start = max(0, cursor - 2)
                found = _scan(ns, first_t, start)
                if found < 0 and cursor > 0:
                    found = _scan(ns, first_t, 0)
                if found >= 0:
                    last = found
                    j = found + 1
                    for t in tlines[1:]:
                        k = j
                        limit = min(len(ns), j + 200)
                        while k < limit and ns[k] != t and (not t or t not in ns[k]):
                            k += 1
                        if k < limit:
                            last = k
                            j = k + 1
                    span = [found, last + 1]
                    status = "exact" if ns[found] == first_t else "substr"
                    cursor = max(cursor, last + 1)

            if span and source_line_map:
                a = source_line_map[span[0]] if span[0] < len(source_line_map) else None
                b = source_line_map[span[1] - 1] if span[1] - 1 < len(source_line_map) else None
                if a is not None and b is not None:
                    span = [int(a), int(b) + 1]

            out.append({"span": span, "status": status})
        return out

    # ---------------- 覆盖层读写 ----------------

    def _load_overlay(self, key: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        overlay_jsonl, overlay_extras, overlay_meta = self._overlay_paths(key)
        chunks: list[dict[str, Any]] = []
        if overlay_jsonl.is_file():
            for line in overlay_jsonl.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    chunks.append(json.loads(line))
        extras: dict[str, Any] = {}
        if overlay_extras.is_file():
            for line in overlay_extras.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                extras[row.get("id")] = row.get("extras") or {}
        return chunks, extras

    def overlay_info(self, key: str) -> dict[str, Any] | None:
        _, _, meta_path = self._overlay_paths(key)
        if not meta_path.is_file():
            return None
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    # ---------------- 保存 ----------------

    def save(self, key: str, chunks: list[dict[str, Any]], note: str = "") -> dict[str, Any]:
        fi = self.entry(key)
        path = Path(fi["path"])
        group = self.sources.get(fi["group"])
        if group is None:
            raise StudioError(f"sources.yaml 里没有分组：{fi['group']}")
        meta = dict(group.meta_for(path.name))
        law_name = fi.get("law_name") or path.stem

        if not isinstance(chunks, list) or not chunks:
            raise StudioError("chunks 不能为空")

        used: set[str] = set()
        max_seq = 0
        for ch in chunks:
            m = RE_ID.match(str(ch.get("id") or ""))
            if m:
                max_seq = max(max_seq, int(m.group(2)))
        records: list[dict[str, Any]] = []
        spans: dict[str, list[int] | None] = {}
        statuses: dict[str, str] = {}
        next_seq = max_seq + 1

        for ch in chunks:
            cid = str(ch.get("id") or "").strip()
            if not cid or ch.get("_new"):
                cid = f"{law_name}-{next_seq:04d}"
                next_seq += 1
            if cid in used:
                raise StudioError(f"id 重复：{cid}")
            used.add(cid)
            body = str(ch.get("chunk") or "")
            try:
                obj = contracts.Chunk(
                    id=cid,
                    chunk=body,
                    law_name=str(ch.get("law_name") or law_name),
                    law_num=str(ch.get("law_num") or ""),
                    law_invalid=str(ch.get("law_invalid") or meta.get("law_invalid") or DEFAULT_LAW_INVALID),
                    law_type=str(ch.get("law_type") or meta.get("law_type") or ""),
                    law_level=str(ch.get("law_level") or meta.get("law_level") or ""),
                    issuer=str(ch.get("issuer") or meta.get("issuer") or ""),
                    effective_date=str(ch.get("effective_date") or meta.get("effective_date") or ""),
                    chunk_kind=str(ch.get("chunk_kind") or "clause"),
                    source=str(ch.get("source") or meta.get("source") or ""),
                    part_index=int(ch.get("part_index") or 1),
                    part_total=int(ch.get("part_total") or 1),
                ).finalize()
            except contracts.ContractError as exc:
                raise StudioError(f"块 {cid} 不合契约：{exc}") from exc
            rec = obj.to_dict()
            extras = ch.get("extras") or {}
            if isinstance(extras, dict) and extras:
                rec["extras"] = extras
            records.append(rec)
            span = ch.get("span")
            spans[cid] = list(span) if isinstance(span, (list, tuple)) and len(span) == 2 else None
            statuses[cid] = str(ch.get("align") or "")

        overlay_jsonl, overlay_extras, overlay_meta = self._overlay_paths(key)
        tmp = overlay_jsonl.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            for rec in records:
                row = {k: v for k, v in rec.items() if k != "extras"}
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        tmp.replace(overlay_jsonl)

        extras_lines = {cid: (rec.get("extras") or {}) for cid, rec in ((r["id"], r) for r in records) if rec.get("extras")}
        tmp2 = overlay_extras.with_suffix(".jsonl.tmp")
        with tmp2.open("w", encoding="utf-8", newline="\n") as fh:
            for cid, ex in extras_lines.items():
                fh.write(json.dumps({"id": cid, "extras": ex}, ensure_ascii=False) + "\n")
        tmp2.replace(overlay_extras)

        meta_payload = {
            "key": key,
            "saved_at": _now(),
            "note": note,
            "n_chunks": len(records),
            "source_path": str(path),
            "spans": spans,
            "align": statuses,
            "params": self.overlay_info(key) or {},
        }
        overlay_meta.write_text(
            json.dumps(meta_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {
            "saved_to": str(overlay_jsonl),
            "extras_to": str(overlay_extras),
            "meta_to": str(overlay_meta),
            "n_chunks": len(records),
            "saved_at": meta_payload["saved_at"],
            "ids": [r["id"] for r in records],
        }

    def reset(self, key: str) -> dict[str, Any]:
        removed = []
        for p in self._overlay_paths(key):
            if p.is_file():
                p.unlink()
                removed.append(str(p))
        return {"removed": removed}

    # ---------------- 质检 ----------------

    def gate(self, key: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
        from fac_rag.chunking_v2 import gate_chunks

        fi = self.entry(key)
        path = Path(fi["path"])
        text = path.read_text(encoding="utf-8")
        clean = []
        for ch in chunks:
            row = {k: v for k, v in ch.items() if k not in {"extras", "span", "align", "_new"}}
            clean.append(row)
        try:
            result = gate_chunks(clean, source_text=text, profiles=self.profiles)
        except Exception as exc:  # noqa: BLE001 - 质检失败也要回给前端看
            return {"ok": False, "error": str(exc)}
        return result if isinstance(result, dict) else {"ok": bool(getattr(result, "ok", False)), "raw": repr(result)}


def _counter(aligned: list[dict[str, Any]]) -> dict[str, int]:
    counter: dict[str, int] = {}
    for a in aligned:
        status = a.get("status") or "?"
        span = a.get("span")
        key = status if span else "miss"
        counter[key] = counter.get(key, 0) + 1
    return counter
