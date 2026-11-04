# -*- coding: utf-8 -*-
"""fac_rag.chunking_v2 —— 切块层 v2 入口（技术版 §2；M2 流水线版）。

里程碑：**M2（regions + units + render + emitter）**
----------------------------------------------------
五步流水线已经串起来，``chunk_file`` 真正产块：

1. :mod:`fac_rag.chunking_v2.probe` —— 第①步：16 个行级特征 + 族判定 + 体检单；
2. :mod:`fac_rag.chunking_v2.profiles` —— 加载/校验 ``configs/profiles/*.yaml`` 并选 profile；
3. :mod:`fac_rag.chunking_v2.regions` —— 第②步：区域计划（D1 目次 / D-039 背材料）；
4. :mod:`fac_rag.chunking_v2.units` —— 第③步：statute / standard / generic 三 handler；
5. :mod:`fac_rag.chunking_v2.render` —— 第④步：清洗 + 去重 + part 拆分 + 契约前装盒；
6. :mod:`fac_rag.chunking_v2.emitter` —— 出口：15 字段块 + ``_extras.jsonl`` 侧车 + manifest。

入口返回结构（与 v1 逐键一致，验收会按这个集合断言）::

    {"path", "folder", "law_name", "chunks", "parsed",
     "sanity", "summary", "source_chars"}

``law_name`` 取文件名干（与 v1 相同，保证 id 同形）；块本体 15 字段、extras 走
侧车（D-040）。``chunk_folder`` 与 :func:`write_dataset` 分别服务「按 folder 切」
与「全量落 data/chunks_v2/」两个场景。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .emitter import write_dataset
from .expand import expand_lines
from .model import (
    AppendixRule,
    ChunkDraft,
    ChunkingError,
    Document,
    Family,
    FrontRule,
    GateRule,
    MatchRules,
    ParseResult,
    Profile,
    Region,
    RegionKind,
    RegionPolicy,
    RenderRule,
    SplitRule,
    TableModel,
    TableRule,
    TitleRule,
    TocRule,
    Unit,
    UnitKind,
    UnitRule,
)
from .probe import (
    FEATURE_GROUPS,
    FEATURE_KEYS,
    FAMILY_LABELS,
    PROBE_VERSION,
    extract_features,
    first_heading_text,
    prior_family,
    probe_file,
    probe_lines,
    probe_text,
)
from .profiles import (
    CONFIDENCE_THRESHOLD,
    DEFAULT_PROFILES_DIR,
    FAMILIES,
    ProfileError,
    load_profile,
    load_profiles,
    match_score,
    match_view,
    select_profile,
)
from .regions import RegionPlan, detect_regions
from .render import DEFAULT_CLEAN_RULES, check_sane, render_units, summarize
from .sizing import plan_split, tighten_plans
from .units import parse_document

if TYPE_CHECKING:  # pragma: no cover - 只为类型检查，运行时只做鸭子类型
    from fac_rag.chunking.base import Strategy
    from fac_rag.chunking.sources import SourceGroup, Sources
    from fac_rag.config import Config

__version__ = "0.1.4"

__all__ = [
    "__version__",
    # 入口（与 v1 同签名同返回结构）
    "chunk_file",
    "chunk_file_full",
    "chunk_file_gate",
    "chunk_file_run",
    "gate_chunks",
    "chunk_folder",
    "write_dataset",
    # 五步流水线
    "probe_file",
    "probe_lines",
    "probe_text",
    "extract_features",
    "first_heading_text",
    "prior_family",
    "FEATURE_GROUPS",
    "FEATURE_KEYS",
    "FAMILY_LABELS",
    "PROBE_VERSION",
    "detect_regions",
    "RegionPlan",
    "parse_document",
    "render_units",
    "check_sane",
    "summarize",
    # profile
    "ProfileError",
    "load_profile",
    "load_profiles",
    "select_profile",
    "match_score",
    "match_view",
    "DEFAULT_PROFILES_DIR",
    "CONFIDENCE_THRESHOLD",
    "FAMILIES",
    # 对象模型
    "AppendixRule",
    "ChunkDraft",
    "ChunkingError",
    "Document",
    "Family",
    "FrontRule",
    "GateRule",
    "MatchRules",
    "ParseResult",
    "Profile",
    "Region",
    "RegionKind",
    "RegionPolicy",
    "RenderRule",
    "SplitRule",
    "TableModel",
    "TableRule",
    "TitleRule",
    "TocRule",
    "Unit",
    "UnitKind",
    "UnitRule",
]


# ---------------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------------


def _chunk_file_inner(
    path: str | Path,
    *,
    group: "SourceGroup",
    strategy: "Strategy | None" = None,
    verbose: bool = False,
    profiles_dir: str | Path | None = None,
    profiles: dict[str, Profile] | None = None,
    params_override: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any], Any, str, Profile]:
    """完整 pipeline 内核：返回 ``(record, extras_by_id, probe_report, plan, text, profile)``。"""
    # P1：auto 已是默认档（``strategy="profile"`` 才回退到按 profile.family 死绑 handler）；
    # 检测器档见 chunking_v2/detect.py，尺寸自适应见 chunking_v2/sizing.py。
    auto = str(strategy or "").strip().lower() != "profile"
    p = Path(path)
    # utf-8-sig：与 utf-8 读法完全一致，但会剥掉 UTF-8 BOM（Windows 编辑器常见），
    # 避免 BOM 字符混进首行导致 `# 标题` / `## 目 次` 识别失败。
    text = p.read_text(encoding="utf-8-sig")
    lines = text.splitlines()

    hint = str(getattr(group, "profile", "") or getattr(group, "name", "") or "")
    report = probe_lines(lines, path=str(p), hint=hint or None)
    loaded = profiles if profiles is not None else load_profiles(profiles_dir)
    profile = select_profile(report, loaded)

    # M8b / task-12：单行超长 HTML 大表预展开。probe 仍看原行（gate 的体检单
    # 信号不变），展开只影响 regions / parse / coverage；映射记在 report["expand"]
    # 供报告与审查回溯（不落块契约）。现役 65 份语料不触发（阈值 20000 字符）。
    work_lines, source_line_map, expand_info = expand_lines(lines)
    if expand_info is not None:
        report["expand"] = expand_info
        report["_gate_text"] = "\n".join(work_lines)

    plan = detect_regions(work_lines, profile)
    meta = group.meta_for(p.name) if hasattr(group, "meta_for") else {}
    params = dict(group.params_for(p.name) if hasattr(group, "params_for") else {})
    if params_override:
        params.update(params_override)
    if auto:
        params["auto"] = True

    law_name = p.stem
    parsed = parse_document(
        lines=work_lines,
        law_name=law_name,
        profile=profile,
        plan=plan,
        params=params,
    )
    for warning in list(plan.warnings) + list(report.get("warnings", [])):
        if warning not in parsed.warnings:
            parsed.warnings.append(warning)
    # auto 档的检测决策日志进 probe 报告（不进 record、不进块契约）
    if parsed.meta.get("decisions"):
        report["decisions"] = parsed.meta["decisions"]
    if expand_info is not None:
        parsed.warnings.append(
            f"单行超长 HTML 大表已预展开（M8b/task-12）：原文 {expand_info['orig_lines']} 行 → "
            f"展开 {expand_info['work_lines']} 行、{expand_info['expanded_tables']} 张表；"
            "展开行→原行号映射见 report.expand"
        )

    max_chars = int(params.get("max_chunk_chars", profile.render.max_chars))
    split_rule = getattr(profile, "split", None)
    soft_max_chars = int(
        params.get("split_soft_max_chars", getattr(split_rule, "soft_max_chars", 0) or 0) or 0
    )
    combine_under_chars = int(
        params.get("split_combine_under_chars", getattr(split_rule, "combine_under_chars", 0) or 0) or 0
    )
    separators = tuple(
        params.get("split_separators") or getattr(split_rule, "separators", ()) or ()
    )
    protect_fences = bool(
        params.get("split_protect_fences", getattr(split_rule, "protect_fences", False))
    )

    # P1 gate 驱动回退（只在 auto 档）：收紧上限、强制软上限、关回吸。
    tighten = float(params.get("sizing_tighten", 1.0) or 1.0)
    if auto and tighten < 1.0:
        max_chars = max(200, int(round(max_chars * tighten)))
        soft_max_chars = max(soft_max_chars, int(round(0.75 * max_chars)))
        combine_under_chars = 0

    # P1 尺寸自适应（只在 auto 档启用）：按单位 kind 的长度分布 / CJK 占比定
    # soft、combine、separators；profile 档保持配置值（逐字节不变）。
    split_plan: dict[str, dict[str, Any]] = {}
    if auto:
        plans, sizing_decisions = plan_split(
            parsed.units,
            max_cap=max_chars,
            base_soft_max_chars=soft_max_chars,
            base_combine_under_chars=combine_under_chars,
            base_separators=separators,
        )
        split_plan = {kind: plan.as_params() for kind, plan in plans.items()}
        report.setdefault("decisions", []).extend(sizing_decisions)

    chunks, extras_by_id = render_units(
        parsed,
        law_name=law_name,
        law_invalid=str(meta.get("law_invalid", "")),
        source=str(meta.get("source", "")),
        law_type=str(meta.get("law_type", "")),
        law_level=str(meta.get("law_level", "")),
        issuer=str(meta.get("issuer", "")),
        effective_date=str(meta.get("effective_date", "")),
        default_kind="clause",
        max_chars=max_chars,
        soft_max_chars=soft_max_chars,
        combine_under_chars=combine_under_chars,
        separators=separators,
        protect_fences=protect_fences,
        split_override=split_plan or None,
        # auto 档：重复正文多为不同条款（如各类场所相同的通用要求），去重会丢条款，
        # 因此不按正文去重；id 由全局 seq 保证唯一。
        dedupe=not auto,
        # M3 / D-043：清洗规则由 profile.render.clean 决定（默认 5 条全开）。
        # 等价模式回归时把配置改成 ["latex_escapes"] + tables.mode=legacy 即可复现 v1。
        clean_rules=tuple(profile.render.clean) or DEFAULT_CLEAN_RULES,
    )

    probe_summary = {
        "family": report["family"],
        "confidence": report["confidence"],
        "heading_mode": report["heading_mode"],
    }
    for extras in extras_by_id.values():
        extras.setdefault("profile", profile.name)
        extras.setdefault("region", "body")
        appendix = extras.get("appendix")
        extras.setdefault("namespace", f"appendix:{appendix['letter']}" if appendix else "body")
        extras["probe"] = probe_summary

    min_ratio = 0.0 if getattr(profile, "family", "") in ("generic", "tabular") else 0.5
    sanity = check_sane(chunks, what=p.name, min_ratio=min_ratio)
    summary = summarize(chunks, parsed, len(text))

    if verbose:
        print(
            f"[v2] {p.name}：family={report['family']} profile={profile.name} "
            f"confidence={report['confidence']:.2f} chunks={len(chunks)} "
            f"条款号可解析 {sanity['n_law_num_parseable']}/{sanity['n_chunks']}"
        )
        for warning in parsed.warnings:
            print(f"[v2 警告] {warning}")

    record: dict[str, Any] = {
        "path": str(p),
        "folder": group.name,
        "law_name": law_name,
        "chunks": chunks,
        "parsed": parsed,
        "sanity": sanity,
        "summary": summary,
        "source_chars": len(text),
    }
    return record, extras_by_id, report, plan, text, profile


def _run(
    path: str | Path,
    *,
    group: "SourceGroup",
    strategy: "Strategy | None" = None,
    verbose: bool = False,
    profiles_dir: str | Path | None = None,
    profiles: dict[str, Profile] | None = None,
    want_gate: bool = False,
    retry_auto: bool = False,
    expected_blocks: int | None = None,
    allow_block_count_change: bool = False,
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, Any],
    dict[str, Any] | None,
]:
    """内部唯一流水线：``(record, extras_by_id, probe_report, gate_result|None)``。

    ``want_gate`` 打开第⑤步 gate；``retry_auto`` 只在 auto 档且 gate 不达标时重切一次
    （尺寸收紧，见 :func:`chunk_file_run`）；``expected_blocks`` 供 L2 计划变更对账。
    """
    from .gate import evaluate

    auto = str(strategy or "").strip().lower() != "profile"
    attempts: list[dict[str, Any]] = []
    override: dict[str, Any] | None = None
    record: dict[str, Any] = {}
    extras_by_id: dict[str, dict[str, Any]] = {}
    report: dict[str, Any] = {}
    gate_result: dict[str, Any] | None = None
    total_attempts = 2 if (want_gate and retry_auto and auto) else 1
    for attempt in range(total_attempts):
        record, extras_by_id, report, plan, text, profile = _chunk_file_inner(
            path,
            group=group,
            strategy=strategy,
            verbose=verbose,
            profiles_dir=profiles_dir,
            profiles=profiles,
            params_override=override,
        )
        if not want_gate:
            break
        parsed = record["parsed"]
        gate_result = evaluate(
            record["chunks"],
            source_text=report.get("_gate_text") or text,
            regions=plan.regions,
            expected_blocks=expected_blocks,
            allow_block_count_change=allow_block_count_change,
            warnings=len(getattr(parsed, "warnings", []) or []),
            rule=profile.gate,
            probe=report,
            extras=extras_by_id,
        )
        if not retry_auto:
            return record, extras_by_id, report, gate_result
        attempts.append(
            {
                "attempt": attempt + 1,
                "ok": bool(gate_result.get("ok")),
                "failures": list(gate_result.get("failures") or []),
                "params": dict(override or {}),
            }
        )
        if gate_result.get("ok") or attempt == total_attempts - 1:
            break
        # 只回退一次：上限 ×0.9（≥200）、强制软上限、关掉回吸
        override = {"sizing_tighten": 0.9}
    if attempts:
        report["fallback_chain"] = attempts
    return record, extras_by_id, report, gate_result


def _chunk_file_full(
    path: str | Path,
    *,
    group: "SourceGroup",
    strategy: "Strategy | None" = None,
    verbose: bool = False,
    profiles_dir: str | Path | None = None,
    profiles: dict[str, Profile] | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    """完整 pipeline：返回 ``(record, extras_by_id, probe_report)``。

    ``record`` 与 v1 ``chunk_file`` 的 8 键逐键一致；``extras_by_id`` 是
    ``id → extras`` 的侧车内容（写 ``_extras.jsonl`` 用）；``probe_report`` 是
    第①步体检单（自身不改进程；写 manifest 用）。
    """
    record, extras_by_id, report, _gate = _run(
        path,
        group=group,
        strategy=strategy,
        verbose=verbose,
        profiles_dir=profiles_dir,
        profiles=profiles,
    )
    return record, extras_by_id, report


def chunk_file(
    path: str | Path,
    *,
    group: "SourceGroup",
    strategy: "Strategy | None" = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """切一份文件；返回结构、键顺序与 v1 ``fac_rag.chunking.chunk_file`` 一致。"""
    return _run(
        path, group=group, strategy=strategy, verbose=verbose
    )[0]


def chunk_file_full(
    path: str | Path,
    *,
    group: "SourceGroup",
    strategy: "Strategy | None" = None,
    verbose: bool = False,
    profiles_dir: str | Path | None = None,
    profiles: dict[str, Profile] | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    """同 :func:`chunk_file`，但额外返回 ``(record, extras_by_id, probe_report)``。"""
    return _chunk_file_full(
        path,
        group=group,
        strategy=strategy,
        verbose=verbose,
        profiles_dir=profiles_dir,
        profiles=profiles,
    )


def chunk_file_run(
    path: str | Path,
    *,
    group: "SourceGroup",
    strategy: "Strategy | None" = None,
    verbose: bool = False,
    profiles_dir: str | Path | None = None,
    profiles: dict[str, Profile] | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """切一份文件并跑第⑤步 gate；返回 ``(record, extras_by_id, gate_result, probe_report)``。

    生产入口（``tools/run_chunking.py``）用这个：一次拿到块、侧车 extras、
    gate 结果与第①步体检单，避免重复跑流水线。``gate_result`` 是
    :func:`fac_rag.chunking_v2.gate.evaluate` 的返回字典（``ok`` / ``metrics`` / ``failures``）。

    P1 gate 驱动回退：auto 档下若 gate 不达标，自动收小尺寸（上限 ×0.9、强制软上限、
    关回吸）重切一次；每次尝试记在 ``probe_report["fallback_chain"]``（不进 record、
    不进块契约）。profile 档只跑一次，保持旧行为。
    """
    record, extras_by_id, report, gate_result = _run(
        path,
        group=group,
        strategy=strategy,
        verbose=verbose,
        profiles_dir=profiles_dir,
        profiles=profiles,
        want_gate=True,
        retry_auto=True,
    )
    assert gate_result is not None
    return record, extras_by_id, gate_result, report


def gate_chunks(
    chunks: list[dict[str, Any]],
    *,
    source_text: str,
    path: str | Path | None = None,
    group: "SourceGroup | None" = None,
    profiles_dir: str | Path | None = None,
    profiles: dict[str, Profile] | None = None,
    warnings: int = 0,
    expected_blocks: int | None = None,
    allow_block_count_change: bool = False,
    extras: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """对**已有块**（如 v1 产物）跑第⑤步 gate，不重切。

    ``tools/chunk_report.py --check-chunks`` 用它给已落盘的 v1/v2 产物做严格体检；
    ``tools/run_chunking.py`` 在 engine=v1 时也用它做 gate 记账（M6 / K2）。
    区域/体检单从源文本重新探测（只读，不改块）。
    """
    lines = source_text.splitlines()
    hint = str(getattr(group, "profile", "") or getattr(group, "name", "") or "")
    report = probe_lines(lines, path=str(path or ""), hint=hint or None)
    loaded = profiles if profiles is not None else load_profiles(profiles_dir)
    profile = select_profile(report, loaded)
    work_lines, _source_line_map, expand_info = expand_lines(lines)
    if expand_info is not None:
        report["expand"] = expand_info
    plan = detect_regions(work_lines, profile)
    from .gate import evaluate

    result = evaluate(
        chunks,
        source_text="\n".join(work_lines) if expand_info is not None else source_text,
        regions=plan.regions,
        expected_blocks=expected_blocks,
        allow_block_count_change=allow_block_count_change,
        warnings=warnings,
        rule=profile.gate,
        probe=report,
        extras=extras,
    )
    result["profile"] = profile.name
    result["family"] = report["family"]
    return result


def chunk_file_gate(
    path: str | Path,
    *,
    group: "SourceGroup",
    strategy: "Strategy | None" = None,
    verbose: bool = False,
    profiles_dir: str | Path | None = None,
    profiles: dict[str, Profile] | None = None,
    expected_blocks: int | None = None,
    allow_block_count_change: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """切一份文件并跑第⑤步 gate，返回 ``(record, gate_result)``。

    ``expected_blocks`` 传 v1 的块数时，gate 会额外检查块数相对期望的 ±40%；
    ``allow_block_count_change=True`` 时块数偏离只记指标不判失败（L2 计划变更文件）。
    """
    record, _extras, _report, gate_result = _run(
        path,
        group=group,
        strategy=strategy,
        verbose=verbose,
        profiles_dir=profiles_dir,
        profiles=profiles,
        want_gate=True,
        expected_blocks=expected_blocks,
        allow_block_count_change=allow_block_count_change,
    )
    assert gate_result is not None
    return record, gate_result


def chunk_folder(
    cfg: "Config",
    sources: "Sources",
    folder: str | None = None,
    *,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """按配置切一个（或全部）文件夹，返回每份文件一条记录。

    与 v1 一样是严格模式：sources 未登记、或 config.yaml 与 sources.yaml 的
    strategy 不一致 → 抛 :class:`ChunkingError`；一份文件都没匹配到也抛错。
    """
    profiles = load_profiles(getattr(cfg, "chunking_profiles_dir", None))
    records: list[dict[str, Any]] = []
    for group, path in cfg.iter_corpus_files():
        if folder and group.name != folder:
            continue
        src_group = sources.get(group.name)          # 未登记 → SourceError（不静默跳过）
        if getattr(src_group, "strategy", "") != group.strategy:
            raise ChunkingError(
                f"{group.name} 两处配置不一致：config.yaml 说 {group.strategy!r}，"
                f"sources.yaml 说 {getattr(src_group, 'strategy', '')!r}（先对齐再切）"
            )
        record, _extras, _report = _chunk_file_full(
            path,
            group=src_group,
            verbose=verbose,
            profiles=profiles,
        )
        records.append(record)
    if not records:
        raise ChunkingError(f"没有可切的文件：folder={folder!r}")
    return records
