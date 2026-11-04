"""fac_rag.chunking_v2.profiles —— profile 加载 / 校验 / 选择（技术版 §3）。

profile YAML = 一类文档格式方言的切块策略。M1 的落法
--------------------------------------------------
* 目录：默认包内 ``profile_data/``（``Config.chunking_profiles_dir`` 可改）；
* 四份 M1 profile：``statute_cn_law``（全国人大法律）/ ``statute_cn_regulation``
  （行政法规、规章，如 708/493 号）/ ``gb_standard``（GB、GB/T、GBZ 国标）/
  ``generic``（无编号汇编兜底）；
* 只有 ``name`` / ``family`` 必填，其余小节缺省用 ``model.py`` 的 dataclass 默认值
  （技术版「不明确处按最简可用实现」）；**非法类型、未知键、坏正则一律报错**，
  报错信息带文件名 + YAML 路径，便于施工时定位。

YAML 形状（对应 :class:`fac_rag.chunking_v2.model.Profile`）::

    name: gb_standard            # 必填；profile 键名（重复报错）
    family: standard             # 必填；statute | standard | generic
    description: ...             # 可选，给人看
    match:                       # 选择规则：require 全中才合格，weight 加权，forbid 扣分
      require: [decimal_lines]
      weight: {decimal_lines: 3, table_markers.pipe: 2}
      forbid: []
    title:   {source: first_heading, strip_chars: "　 \\t#"}
    front:   {drop: true, metadata: [standard_id, publish_date]}
    toc:     {drop: true, page_forms: [single_space, fullwidth, dot_leader, paren],
              max_scan_lines: 600}
    regions: {front: drop, toc: drop, appendix: keep, commentary: drop, references: drop}
    units:   {article_pattern: null, clause_pattern: "...", top_pattern: "...",
              heading_mode: auto, default_kind: clause, paragraph_split: false}
    appendix:{enabled: true, heading_pattern: "...", unit_pattern: "...",
              kinds: [规范性, 资料性]}
    tables:  {sources: [pipe, html], caption_pattern: "...",
              continuation_markers: ["（续）"], header_rows: 0, row_pack_chars: 700}
    render:  {breadcrumb_depth: 2, breadcrumb_sep: " > ",
              clean: [latex_escapes, md_bold, html_tags, page_tail, continuation_markers],
              max_chars: 800}
    split:   {max_chars: 800, part_suffix: "-p{i}"}
    gate:    {min_line_coverage: 0.60, max_bad_block_ratio: 0.02,
              max_dup_ref_ratio: 0.01, block_count_tolerance: 0.40, max_warnings: 5}

选择算法（技术版 §4 规则⑤⑥）
--------------------------
1. 取 ``family`` 相同的候选；``match.require`` 全部命中才合格；
2. 合格者按 ``0.4 + 0.6 * 加权归一`` 打分（计数特征饱和归一，见 ``_FEATURE_CAPS``），
   ``forbid`` 每命中一项扣 0.3；
3. 有多个合格者取最高分（同分按名字排序并 warning）；无合格者时：
   - 族内唯一候选 → 兜底采用 + warning（GB/T 13861 这类「标准标记足但编号稀疏」用）；
   - 多候选都不中 → 报错（要求补 profile.match 或人工指定）；
4. 最终置信度 = ``min(族置信度, match 分)``；低于 0.6 写 warning（技术版 §4 规则⑥）。
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path
from typing import Any, Mapping

import yaml


from .model import (
    AppendixRule,
    FrontRule,
    GateRule,
    MatchRules,
    Profile,
    RegionPolicy,
    RenderRule,
    SplitRule,
    TableRule,
    TitleRule,
    TocRule,
    UnitRule,
)

#: 默认 profile 目录（与 ``configs/config.yaml`` 的 ``chunking.profiles_dir`` 默认一致）
DEFAULT_PROFILES_DIR: Path = Path(__file__).resolve().parent / "profile_data"

#: 技术版 §3 的族取值
FAMILIES: frozenset[str] = frozenset({"statute", "standard", "generic", "tabular"})

#: 技术版 §6/§9 允许的块种类（与 contracts.CHUNK_KINDS 对齐）
UNIT_DEFAULT_KINDS: frozenset[str] = frozenset(
    {"clause", "table", "title", "front", "appendix", "stub", "paragraph"}
)

_ALLOWED_TOP: frozenset[str] = frozenset(
    {
        "name",
        "family",
        "description",
        "match",
        "title",
        "front",
        "toc",
        "regions",
        "units",
        "appendix",
        "tables",
        "render",
        "split",
        "gate",
    }
)

_ALLOWED_SECTIONS: dict[str, frozenset[str]] = {
    "match": frozenset({"require", "weight", "forbid"}),
    "title": frozenset({"source", "strip_chars"}),
    "front": frozenset({"drop", "metadata"}),
    "toc": frozenset({"drop", "page_forms", "max_scan_lines"}),
    "regions": frozenset({"front", "toc", "appendix", "commentary", "references"}),
    "units": frozenset(
        {
            "article_pattern",
            "clause_pattern",
            "top_pattern",
            "heading_mode",
            "default_kind",
            "paragraph_split",
        }
    ),
    "appendix": frozenset({"enabled", "heading_pattern", "unit_pattern", "kinds", "mode"}),
    "tables": frozenset(
        {
            "sources",
            "caption_pattern",
            "continuation_markers",
            "header_rows",
            "row_pack_chars",
            "mode",
            "unlabeled",
            "derive_title",
            "row_overflow",
            "cont_tolerance",
        }
    ),
    "render": frozenset({"breadcrumb_depth", "breadcrumb_sep", "clean", "max_chars"}),
    "split": frozenset(
        {
            "max_chars",
            "part_suffix",
            "soft_max_chars",
            "combine_under_chars",
            "separators",
            "protect_fences",
        }
    ),
    "gate": frozenset(
        {
            "min_line_coverage",
            "max_bad_block_ratio",
            "max_dup_ref_ratio",
            "block_count_tolerance",
            "max_warnings",
            "strict_tables",
        }
    ),
}

_ALLOWED_TITLE_SOURCES = frozenset({"first_heading", "front_matter", "filename"})
_ALLOWED_HEADING_MODES = frozenset({"auto", "on", "off"})
_ALLOWED_PAGE_FORMS = frozenset({"single_space", "fullwidth", "dot_leader", "paren"})
_ALLOWED_CLEAN = frozenset(
    {"latex_escapes", "md_bold", "html_tags", "page_tail", "continuation_markers"}
)
_ALLOWED_REGION_ACTIONS = frozenset({"drop", "keep"})
_ALLOWED_TABLE_SOURCES = frozenset({"pipe", "html"})
_ALLOWED_TABLE_MODES = frozenset({"legacy", "model"})
#: Phase 1.1：超长单行降级方式（cell=按单元格拆段，hard=旧行为）
_ALLOWED_ROW_OVERFLOW = frozenset({"cell", "hard"})

#: 计数特征饱和归一的上限（够多就算「特征明显」；技术版 §4 规则⑥）
_FEATURE_CAPS: dict[str, float] = {
    "article_lines": 3,
    "decimal_lines": 3,
    "chapter_section_lines": 2,
    "appendix_label_lines": 1,
    "toc_head_lines": 1,
    "toc_entry_lines": 3,
    "heading_level_counts": 3,
    "front_markers.front": 1,
    "front_markers.decree": 1,
    "front_markers.publish_date": 1,
    "front_markers.effective_date": 1,
    "commentary_lines": 1,
    "references_lines": 1,
    "table_markers.pipe": 1,
    "table_markers.html": 1,
    "table_markers.caption": 1,
    "table_markers.continuation": 1,
    "document_markers.top_numbering": 3,
    "document_markers.standard_marker": 1,
}

#: 低置信度阈值（技术版 §4 规则⑥）
CONFIDENCE_THRESHOLD = 0.6


class ProfileError(RuntimeError):
    """profile 缺失 / YAML 语法坏 / 字段非法。"""


# ---------------------------------------------------------------------------
# YAML 校验小工具（报错信息统一带「文件: YAML 路径」）
# ---------------------------------------------------------------------------


def _fail(path: Path, where: str, message: str) -> ProfileError:
    prefix = where or "<根节点>"
    return ProfileError(f"{path}: {prefix} {message}")


def _mapping(value: Any, path: Path, where: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _fail(path, where, f"必须是映射（key: value），实际是 {type(value).__name__}")
    return dict(value)


def _section(raw: Mapping[str, Any], path: Path, name: str) -> dict[str, Any]:
    data = _mapping(raw.get(name), path, name)
    unknown = sorted(set(data) - _ALLOWED_SECTIONS[name])
    if unknown:
        allowed = sorted(_ALLOWED_SECTIONS[name])
        raise _fail(path, name, f"出现未知键 {unknown}（允许：{allowed}）")
    return data


def _required_str(data: Mapping[str, Any], path: Path, where: str, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _fail(path, f"{where}.{key}" if where else key, f"必填且必须是非空字符串，实际 {value!r}")
    return value.strip()


def _str(data: Mapping[str, Any], path: Path, where: str, key: str, default: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise _fail(path, f"{where}.{key}" if where else key, f"必须是字符串，实际 {value!r}")
    return value


def _bool(data: Mapping[str, Any], path: Path, where: str, key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise _fail(path, f"{where}.{key}" if where else key, f"必须是 true/false，实际 {value!r}")
    return value


def _int(
    data: Mapping[str, Any],
    path: Path,
    where: str,
    key: str,
    default: int,
    *,
    minimum: int | None = None,
) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(path, f"{where}.{key}" if where else key, f"必须是整数，实际 {value!r}")
    if minimum is not None and value < minimum:
        raise _fail(path, f"{where}.{key}" if where else key, f"必须 >= {minimum}，实际 {value}")
    return value


def _float(
    data: Mapping[str, Any],
    path: Path,
    where: str,
    key: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(path, f"{where}.{key}" if where else key, f"必须是数字，实际 {value!r}")
    number = float(value)
    if minimum is not None and number < minimum:
        raise _fail(path, f"{where}.{key}" if where else key, f"必须 >= {minimum}，实际 {number}")
    if maximum is not None and number > maximum:
        raise _fail(path, f"{where}.{key}" if where else key, f"必须 <= {maximum}，实际 {number}")
    return number


def _enum(
    data: Mapping[str, Any],
    path: Path,
    where: str,
    key: str,
    allowed: frozenset[str],
    *,
    default: str | None = None,
    required: bool = False,
) -> str:
    if key not in data:
        if required:
            raise _fail(path, f"{where}.{key}" if where else key, f"必填，取值 {sorted(allowed)}")
        assert default is not None
        return default
    value = data[key]
    if not isinstance(value, str) or value.strip() not in allowed:
        raise _fail(
            path,
            f"{where}.{key}" if where else key,
            f"必须是 {sorted(allowed)} 之一，实际 {value!r}",
        )
    return value.strip()


def _str_list(
    data: Mapping[str, Any],
    path: Path,
    where: str,
    key: str,
    *,
    allowed: frozenset[str] | None = None,
) -> list[str]:
    value = data.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise _fail(path, f"{where}.{key}" if where else key, f"必须是字符串列表，实际 {value!r}")
    items = [item.strip() for item in value]
    if allowed is not None:
        unknown = sorted(set(items) - allowed)
        if unknown:
            raise _fail(
                path,
                f"{where}.{key}" if where else key,
                f"出现未知取值 {unknown}（允许：{sorted(allowed)}）",
            )
    return items


def _number_map(data: Mapping[str, Any], path: Path, where: str, key: str) -> dict[str, float]:
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _fail(path, f"{where}.{key}" if where else key, f"必须是「特征名: 权重」映射，实际 {value!r}")
    result: dict[str, float] = {}
    for name, weight in dict(value).items():
        if not isinstance(name, str) or not name.strip():
            raise _fail(path, f"{where}.{key}", f"权重键必须是非空字符串，实际 {name!r}")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight < 0:
            raise _fail(path, f"{where}.{key}.{name}", f"权重必须是非负数字，实际 {weight!r}")
        result[name.strip()] = float(weight)
    return result


def _regex(
    data: Mapping[str, Any],
    path: Path,
    where: str,
    key: str,
    *,
    required: bool = False,
) -> str | None:
    value = data.get(key)
    if value is None:
        if required:
            raise _fail(path, f"{where}.{key}" if where else key, "必填的正则字符串")
        return None
    if not isinstance(value, str) or not value.strip():
        raise _fail(path, f"{where}.{key}" if where else key, f"必须是非空正则字符串，实际 {value!r}")
    try:
        re.compile(value)
    except re.error as exc:
        raise _fail(path, f"{where}.{key}" if where else key, f"不是合法正则（{exc}）：{value!r}") from exc
    return value


# ---------------------------------------------------------------------------
# 各小节构造
# ---------------------------------------------------------------------------


def _build_match(raw: Mapping[str, Any], path: Path) -> MatchRules:
    data = _section(raw, path, "match")
    return MatchRules(
        require=_str_list(data, path, "match", "require"),
        weight=_number_map(data, path, "match", "weight"),
        forbid=_str_list(data, path, "match", "forbid"),
    )


def _build_title(raw: Mapping[str, Any], path: Path) -> TitleRule:
    data = _section(raw, path, "title")
    return TitleRule(
        source=_enum(
            data,
            path,
            "title",
            "source",
            _ALLOWED_TITLE_SOURCES,
            default="first_heading",
        ),
        strip_chars=_str(data, path, "title", "strip_chars", "　 \t#"),
    )


def _build_front(raw: Mapping[str, Any], path: Path) -> FrontRule:
    data = _section(raw, path, "front")
    return FrontRule(
        drop=_bool(data, path, "front", "drop", True),
        metadata=_str_list(data, path, "front", "metadata"),
    )


def _build_toc(raw: Mapping[str, Any], path: Path) -> TocRule:
    data = _section(raw, path, "toc")
    return TocRule(
        drop=_bool(data, path, "toc", "drop", True),
        page_forms=_str_list(data, path, "toc", "page_forms", allowed=_ALLOWED_PAGE_FORMS)
        or ["single_space", "fullwidth", "dot_leader", "paren"],
        max_scan_lines=_int(data, path, "toc", "max_scan_lines", 600, minimum=1),
    )


def _build_regions(raw: Mapping[str, Any], path: Path) -> RegionPolicy:
    data = _section(raw, path, "regions")
    actions: dict[str, str] = {}
    for key in ("front", "toc", "appendix", "commentary", "references"):
        actions[key] = _enum(
            data,
            path,
            "regions",
            key,
            _ALLOWED_REGION_ACTIONS,
            default="keep" if key == "appendix" else "drop",
        )
    return RegionPolicy(**actions)


def _build_units(raw: Mapping[str, Any], path: Path) -> UnitRule:
    data = _section(raw, path, "units")
    # YAML 1.1 里裸 ``off`` / ``on`` 会被 safe_load 解析成布尔；这里容错映射回
    # heading_mode 的合法字符串，避免 profile 作者踩坑（YAML 里加引号更稳妥）。
    mode = data.get("heading_mode")
    if isinstance(mode, bool):
        data = {**data, "heading_mode": "on" if mode else "off"}
    heading_mode = _enum(
        data, path, "units", "heading_mode", _ALLOWED_HEADING_MODES, default="auto"
    )
    default_kind = _enum(
        data, path, "units", "default_kind", UNIT_DEFAULT_KINDS, default="clause"
    )
    return UnitRule(
        article_pattern=_regex(data, path, "units", "article_pattern"),
        clause_pattern=_regex(data, path, "units", "clause_pattern"),
        top_pattern=_regex(data, path, "units", "top_pattern"),
        heading_mode=heading_mode,
        default_kind=default_kind,
        paragraph_split=_bool(data, path, "units", "paragraph_split", False),
    )


def _build_appendix(raw: Mapping[str, Any], path: Path) -> AppendixRule:
    data = _section(raw, path, "appendix")
    return AppendixRule(
        enabled=_bool(data, path, "appendix", "enabled", True),
        heading_pattern=_regex(data, path, "appendix", "heading_pattern"),
        unit_pattern=_regex(data, path, "appendix", "unit_pattern"),
        kinds=_str_list(data, path, "appendix", "kinds") or ["规范性", "资料性"],
        # D-042：relabel=块级重标注（M2 实装）；unit=附录独立单元（M5 规划）。
        mode=_enum(
            data,
            path,
            "appendix",
            "mode",
            frozenset({"relabel", "unit"}),
            default="relabel",
        ),
    )


def _build_tables(raw: Mapping[str, Any], path: Path) -> TableRule:
    data = _section(raw, path, "tables")
    return TableRule(
        sources=_str_list(data, path, "tables", "sources", allowed=_ALLOWED_TABLE_SOURCES)
        or ["pipe", "html"],
        caption_pattern=_regex(data, path, "tables", "caption_pattern"),
        continuation_markers=_str_list(data, path, "tables", "continuation_markers")
        or ["（续）", "(续)"],
        header_rows=_int(data, path, "tables", "header_rows", 0, minimum=0),
        row_pack_chars=_int(data, path, "tables", "row_pack_chars", 700, minimum=1),
        mode=_enum(data, path, "tables", "mode", _ALLOWED_TABLE_MODES, default="model"),
        unlabeled=_bool(data, path, "tables", "unlabeled", False),
        derive_title=_bool(data, path, "tables", "derive_title", False),
        row_overflow=_enum(
            data, path, "tables", "row_overflow", _ALLOWED_ROW_OVERFLOW, default="cell"
        ),
        cont_tolerance=_float(
            data, path, "tables", "cont_tolerance", 1.0, minimum=0.0, maximum=1.0
        ),
    )


def _build_render(raw: Mapping[str, Any], path: Path) -> RenderRule:
    data = _section(raw, path, "render")
    return RenderRule(
        breadcrumb_depth=_int(data, path, "render", "breadcrumb_depth", 2, minimum=0),
        breadcrumb_sep=_str(data, path, "render", "breadcrumb_sep", " > "),
        clean=_str_list(data, path, "render", "clean", allowed=_ALLOWED_CLEAN)
        or ["latex_escapes", "md_bold", "html_tags", "page_tail", "continuation_markers"],
        max_chars=_int(data, path, "render", "max_chars", 800, minimum=1),
    )


def _build_split(raw: Mapping[str, Any], path: Path) -> SplitRule:
    data = _section(raw, path, "split")
    part_suffix = _str(data, path, "split", "part_suffix", "-p{i}")
    if "{i}" not in part_suffix:
        raise _fail(path, "split.part_suffix", f"必须含 '{{i}}' 占位符，实际 {part_suffix!r}")
    soft_max_chars = _int(data, path, "split", "soft_max_chars", 0, minimum=0)
    max_chars = _int(data, path, "split", "max_chars", 800, minimum=1)
    if soft_max_chars and soft_max_chars > max_chars:
        raise _fail(
            path,
            "split.soft_max_chars",
            f"软上限必须 <= split.max_chars（{soft_max_chars} > {max_chars}）",
        )
    raw_seps = data.get("separators")
    if raw_seps is None:
        separators: list[str] = []
    elif not isinstance(raw_seps, list) or any(
        not isinstance(item, str) or item == "" for item in raw_seps
    ):
        raise _fail(
            path,
            "split.separators",
            f"必须是非空字符串列表（可含 \\n 等空白分隔符），实际 {raw_seps!r}",
        )
    else:
        separators = list(raw_seps)
    return SplitRule(
        max_chars=max_chars,
        part_suffix=part_suffix,
        soft_max_chars=soft_max_chars,
        combine_under_chars=_int(data, path, "split", "combine_under_chars", 0, minimum=0),
        separators=separators,
        protect_fences=_bool(data, path, "split", "protect_fences", False),
    )


def _build_gate(raw: Mapping[str, Any], path: Path) -> GateRule:
    data = _section(raw, path, "gate")
    return GateRule(
        min_line_coverage=_float(
            data, path, "gate", "min_line_coverage", 0.60, minimum=0.0, maximum=1.0
        ),
        max_bad_block_ratio=_float(
            data, path, "gate", "max_bad_block_ratio", 0.02, minimum=0.0, maximum=1.0
        ),
        max_dup_ref_ratio=_float(
            data, path, "gate", "max_dup_ref_ratio", 0.01, minimum=0.0, maximum=1.0
        ),
        block_count_tolerance=_float(
            data, path, "gate", "block_count_tolerance", 0.40, minimum=0.0, maximum=1.0
        ),
        max_warnings=_int(data, path, "gate", "max_warnings", 5, minimum=0),
        strict_tables=_bool(data, path, "gate", "strict_tables", False),
    )


# ---------------------------------------------------------------------------
# 加载期校验（M6 / K5）：不静默忽略没接线的字段 / 非法 match 特征名
# ---------------------------------------------------------------------------

#: 体检单摊平后实际存在的 match 特征路径（``match_view`` 的键）。
#: 与 ``probe.py`` 的 16 特征一一对应；``family`` / ``heading_mode`` 是额外两项。
_MATCH_VIEW_PATHS: frozenset[str] = frozenset(
    {
        "article_lines",
        "decimal_lines",
        "chapter_section_lines",
        "appendix_label_lines",
        "first_line_heading",
        "heading_level_counts.1",
        "heading_level_counts.2",
        "heading_level_counts.3",
        "heading_level_counts.4",
        "heading_level_counts.5",
        "heading_level_counts.6",
        "clause_style.titled",
        "clause_style.inline",
        "toc_head_lines",
        "toc_entry_lines",
        "page_forms.single_space",
        "page_forms.fullwidth",
        "page_forms.dot_leader",
        "page_forms.paren",
        "front_markers.front",
        "front_markers.decree",
        "front_markers.publish_date",
        "front_markers.effective_date",
        "commentary_lines",
        "references_lines",
        "table_markers.pipe",
        "table_markers.html",
        "table_markers.caption",
        "table_markers.continuation",
        "noise_lines.latex_escape",
        "noise_lines.md_bold",
        "noise_lines.html_tag",
        "noise_lines.page_tail",
        "document_markers.top_numbering",
        "document_markers.standard_marker",
        "family",
        "heading_mode",
    }
)

#: M6/K5 已知「YAML 里有、代码里暂未消费」的叶子字段（改了不生效，加载时告警）。
_UNWIRED_FIELDS: dict[str, frozenset[str]] = {
    "title": frozenset({"source", "strip_chars"}),
    "front": frozenset({"metadata"}),
    "toc": frozenset({"drop", "page_forms"}),
    "regions": frozenset({"front", "toc", "appendix", "commentary", "references"}),
    "units": frozenset(
        {
            "article_pattern",
            "clause_pattern",
            "top_pattern",
            "heading_mode",
            "default_kind",
            "paragraph_split",
        }
    ),
    "appendix": frozenset({"enabled", "heading_pattern", "unit_pattern", "kinds"}),
    "tables": frozenset({"sources", "caption_pattern", "continuation_markers", "header_rows"}),
    "render": frozenset({"breadcrumb_sep"}),
    "split": frozenset({"max_chars", "part_suffix"}),
}


def _match_rule_warnings(path: Path, raw: Mapping[str, Any], name: str) -> list[str]:
    """``match.require/weight/forbid`` 里写了体检单不存在的特征名 → 告警。"""
    data = _section(raw, path, "match")
    keys: list[str] = []
    for field in ("require", "weight", "forbid"):
        value = data.get(field)
        if isinstance(value, Mapping):
            keys.extend(str(key) for key in value)
        elif isinstance(value, (list, tuple)):
            keys.extend(str(item) for item in value)
    warnings_out: list[str] = []
    for key in sorted(set(keys)):
        if key in _MATCH_VIEW_PATHS:
            continue
        if key == "heading_level_counts" or key.startswith("heading_level_counts"):
            warnings_out.append(
                f"profile {name!r}：match 里用了裸名 {key!r}，"
                "体检单会把它摊平成 heading_level_counts.1..6，该权重/条件恒不命中；"
                "请改写成具体层级或删掉"
            )
        else:
            warnings_out.append(
                f"profile {name!r}：match 特征名 {key!r} 在体检单里不存在，永远不会命中"
                f"（有效示例：article_lines / decimal_lines / table_markers.pipe / family）"
            )
    return warnings_out


def _unwired_warnings(path: Path, raw: Mapping[str, Any], name: str) -> list[str]:
    """YAML 写了但运行时暂不消费的字段 → 告警（M6/K5：不静默）。"""
    unwired: list[str] = []
    for section, known in _UNWIRED_FIELDS.items():
        data = _section(raw, path, section)
        for key in sorted(set(data) & known):
            unwired.append(f"{section}.{key}")
    if not unwired:
        return []
    return [
        f"profile {name!r}：以下字段暂未接线（改了不生效，M6/K5 只告警不阻断）："
        + ", ".join(unwired)
    ]


def _build_profile(path: Path, raw: Mapping[str, Any]) -> Profile:
    unknown = sorted(set(raw) - _ALLOWED_TOP)
    if unknown:
        raise _fail(path, "", f"出现未知顶层键 {unknown}（允许：{sorted(_ALLOWED_TOP)}）")
    name = _required_str(raw, path, "", "name")
    family = _enum(raw, path, "", "family", FAMILIES, required=True)
    description = _str(raw, path, "", "description", "")
    profile = Profile(
        name=name,
        family=family,  # type: ignore[arg-type]  # _enum 已校验取值
        description=description,
        match=_build_match(raw, path),
        title=_build_title(raw, path),
        front=_build_front(raw, path),
        toc=_build_toc(raw, path),
        regions=_build_regions(raw, path),
        units=_build_units(raw, path),
        appendix=_build_appendix(raw, path),
        tables=_build_tables(raw, path),
        render=_build_render(raw, path),
        split=_build_split(raw, path),
        gate=_build_gate(raw, path),
    )
    profile.warnings = [
        *_match_rule_warnings(path, raw, name),
        *_unwired_warnings(path, raw, name),
    ]
    return profile


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------


def load_profile(path: str | Path) -> Profile:
    """加载单份 profile；YAML 语法/字段非法 → :class:`ProfileError`（带文件名与路径）。"""
    p = Path(path)
    if not p.is_file():
        raise ProfileError(f"profile 文件不存在：{p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProfileError(f"{p}: YAML 语法错误：{exc}") from exc
    if not isinstance(raw, Mapping):
        raise ProfileError(f"{p}: 根节点必须是映射（name / family / match / …），实际 {type(raw).__name__}")
    return _build_profile(p, dict(raw))


def load_profiles(dir_path: str | Path | None = None) -> dict[str, Profile]:
    """加载目录下全部 profile，返回 ``{name: Profile}``。

    * 目录不存在 / 一份 YAML 都没有 → :class:`ProfileError`；
    * profile 名重复 → 报错（带上两份文件的路径）。
    """
    directory = Path(dir_path) if dir_path is not None else DEFAULT_PROFILES_DIR
    if not directory.is_dir():
        raise ProfileError(f"profile 目录不存在：{directory}")
    files = sorted({*directory.glob("*.yaml"), *directory.glob("*.yml")})
    if not files:
        raise ProfileError(f"{directory} 下没有任何 profile YAML（*.yaml / *.yml）")
    profiles: dict[str, Profile] = {}
    sources: dict[str, Path] = {}
    for file_path in files:
        profile = load_profile(file_path)
        if profile.name in profiles:
            raise ProfileError(
                f"{file_path}: profile 名 {profile.name!r} 与 {sources[profile.name]} 重复"
            )
        profiles[profile.name] = profile
        sources[profile.name] = file_path
        # M6/K5：加载期校验结果不静默——每份 profile 的未接线字段 / 非法 match 特征名
        # 都用 warnings.warn 打出来（默认去重，不会每个文件刷屏）。
        for message in profile.warnings:
            warnings.warn(message, UserWarning, stacklevel=2)
    return profiles


# ---------------------------------------------------------------------------
# 选择
# ---------------------------------------------------------------------------


def match_view(report: Mapping[str, Any]) -> dict[str, Any]:
    """把体检单摊平成「特征名 → 值」，支持 ``front_markers.decree`` 这种点路径。"""
    view: dict[str, Any] = {}
    features = report.get("features") or {}
    for group, value in features.items():
        if isinstance(value, Mapping):
            for key, sub_value in value.items():
                view[f"{group}.{key}"] = sub_value
        else:
            view[group] = value
    view["family"] = report.get("family", "")
    view["heading_mode"] = report.get("heading_mode", "")
    return view


def _norm_value(name: str, value: Any) -> float:
    """特征值 → [0,1]：布尔 0/1，计数按 ``_FEATURE_CAPS`` 饱和归一。"""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        cap = _FEATURE_CAPS.get(name, 3.0)
        return max(0.0, min(1.0, float(value) / cap))
    if isinstance(value, str):
        return 1.0 if value.strip() else 0.0
    return 1.0 if value else 0.0


def _satisfied(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


def match_score(
    rules: MatchRules, view: Mapping[str, Any]
) -> tuple[float, list[str], list[str]]:
    """返回 ``(score, missing_require, forbid_hits)``；score 已被 clamp 到 [0,1]。"""
    missing = [name for name in rules.require if not _satisfied(view.get(name))]
    forbid_hits = [name for name in rules.forbid if _satisfied(view.get(name))]
    if rules.weight:
        total = sum(rules.weight.values()) or 1.0
        got = sum(
            weight * _norm_value(name, view.get(name))
            for name, weight in rules.weight.items()
        )
        ratio = got / total
    else:
        ratio = 1.0 if not missing else 0.0
    ratio = max(0.0, min(1.0, ratio))
    score = max(0.0, min(1.0, 0.4 + 0.6 * ratio - 0.3 * len(forbid_hits)))
    return score, missing, forbid_hits


def select_profile(
    report: dict[str, Any],
    profiles: Mapping[str, Profile],
) -> Profile:
    """按体检单选 profile，并把 ``profile`` / ``confidence`` / warning 写回体检单。"""
    family = str(report.get("family") or "")
    view = match_view(report)
    warnings: list[str] = report.setdefault("warnings", [])
    family_confidence = float(report.get("family_confidence", report.get("confidence", 0.0)))

    candidates = [profile for profile in profiles.values() if profile.family == family]
    if not candidates:
        fallback = profiles.get("generic")
        if fallback is None:
            raise ProfileError(
                f"family={family} 没有可用的 profile，且目录里没有 generic 兜底（已加载："
                f"{sorted(profiles)}）"
            )
        report["profile"] = fallback.name
        report["confidence"] = round(family_confidence * 0.8, 2)
        warnings.append(
            f"family={family} 没有专属 profile（已加载：{sorted(profiles)}），"
            f"按 generic 占位兜底；M1 的 stub 里不切块，M2 起 mixed 文档按区域拆分后分别识别"
        )
        return fallback

    scored = [
        (profile, *match_score(profile.match, view)) for profile in candidates
    ]
    qualified = [item for item in scored if not item[2]]
    if qualified:
        qualified.sort(key=lambda item: (-item[1], item[0].name))
        profile, score, _missing, _forbid_hits = qualified[0]
        if len(qualified) > 1:
            best_score = qualified[0][1]
            tied = [item[0].name for item in qualified if abs(item[1] - best_score) < 1e-9]
            if len(tied) > 1:
                warnings.append(
                    f"family={family} 有多个 profile 同分命中（{tied}），按名称排序取 "
                    f"{profile.name}；建议收紧 match.require 消歧"
                )
    elif len(candidates) == 1:
        profile = candidates[0]
        score, missing, _forbid_hits = match_score(profile.match, view)
        warnings.append(
            f"profile {profile.name} 的 match.require 未命中（缺少：{missing}），"
            f"按 family={family} 内唯一候选兜底；原始特征见 features"
        )
    else:
        # 多候选全不中 require：不 raise（否则一份异常文件会断掉整批运行），
        # 按加权分最高兜底并报警；是否阻断入库交给 M4 gate 的置信度阈值（技术版 §4 规则⑥）。
        scored.sort(key=lambda item: (-item[1], item[0].name))
        profile, score, missing, forbid_hits = scored[0]
        detail = "; ".join(f"{item[0].name} 缺 {item[2]}" for item in scored)
        warnings.append(
            f"family={family} 的 profile 候选都没命中 match.require（{detail}），"
            f"按加权分最高兜底取 {profile.name}（缺 {missing}，负特征 {forbid_hits}）；"
            "建议人工复核 profile 或补 match 规则"
        )

    confidence = round(min(family_confidence, score), 2)
    report["profile"] = profile.name
    report["confidence"] = confidence
    if confidence < CONFIDENCE_THRESHOLD:
        warnings.append(
            f"识别置信度 {confidence:.2f} 低于阈值 {CONFIDENCE_THRESHOLD:.2f}："
            "按技术版 §4 规则⑥应报警并人工复核（M1 stub 不阻断，M4 gate 会拦入库）"
        )
    return profile


__all__ = [
    "CONFIDENCE_THRESHOLD",
    "DEFAULT_PROFILES_DIR",
    "FAMILIES",
    "ProfileError",
    "load_profile",
    "load_profiles",
    "match_score",
    "match_view",
    "select_profile",
]
