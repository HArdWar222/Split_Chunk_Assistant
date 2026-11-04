"""fac_rag.chunking_v2.units —— 第③步：区域 → 引用单元（技术版 §6）。

本文件只做「公开面 + 分派」：

* :mod:`units.common`   共用常量/正则与单元格、表块辅助
* :mod:`units.statute`  法规「第X条」
* :mod:`units.standard` 标准「3.1.2 / 附录 A」
* :mod:`units.generic`  通用「# 标题 + 空行 + 表格」
* :mod:`units.auto`     auto 档（按区域证据选检测器）
"""

from __future__ import annotations

from .auto import _parse_auto
from .common import *  # noqa: F401,F403
from .common import _table_block_to_unit  # noqa: F401
from .common import RegionPlan, detect
from .generic import parse_generic
from .standard import _parse_standard_blocks, parse_standard  # noqa: F401
from .statute import parse_statute  # noqa: F401


def parse_document(
    *,
    lines: list[str],
    law_name: str,
    profile: Profile,
    plan: RegionPlan,
    params: dict[str, Any] | None = None,
) -> ParseResult:
    """按 profile.family 分派到对应 handler。"""
    params = dict(params or {})
    text = "\n".join(lines)
    if params.get("auto"):
        return _parse_auto(lines, law_name=law_name, profile=profile, plan=plan, params=params)
    family = getattr(profile, "family", "generic")
    if family == "statute":
        return parse_statute(text, law_name=law_name, profile=profile)
    if family == "standard":
        return parse_standard(
            text,
            law_name=law_name,
            profile=profile,
            plan=plan,
            max_chunk_chars=int(params.get("max_chunk_chars", profile.render.max_chars)),
            row_pack_chars=int(params.get("row_pack_chars", profile.tables.row_pack_chars)),
            ancestor_levels=int(params.get("ancestor_levels", profile.render.breadcrumb_depth)),
            keep_front_matter=bool(params.get("keep_front_matter", not profile.front.drop)),
            drop_appendix=str(params.get("drop_appendix", "")),
            tables_mode=str(params.get("tables_mode", getattr(profile.tables, "mode", "model"))),
            unlabeled=bool(params.get("unlabeled", getattr(profile.tables, "unlabeled", False))),
            derive_title=bool(
                params.get("derive_title", getattr(profile.tables, "derive_title", False))
            ),
            cont_tolerance=float(
                params.get("cont_tolerance", getattr(profile.tables, "cont_tolerance", 1.0))
            ),
            row_overflow=str(
                params.get("row_overflow", getattr(profile.tables, "row_overflow", "cell"))
            ),
        )
    # generic 兜底族也走 areas 掩码：无条号目次区（M8b/task-12）必须整段置空，
    # 否则目录条目会被按段落切进块里（山东自由裁量样本 B）。
    return parse_generic(plan.apply_mask(text), law_name=law_name, profile=profile)

__all__ = [
    "clean_standard_line",
    "clean_statute_line",
    "cn2num",
    "parse_document",
    "parse_generic",
    "parse_standard",
    "parse_statute",
]
