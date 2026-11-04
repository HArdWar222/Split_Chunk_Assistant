"""units.auto：auto 档：按区域证据选检测器（P0）。"""

from __future__ import annotations

from ..patterns import (
    RE_APPENDIX,
    RE_APPENDIX_NO,
    RE_ARTICLE,
    RE_CLAUSE,
    RE_COMMENTARY,
    RE_CONT,
    RE_FRONT,
    RE_LAW_TITLE,
    RE_PAGE_TAIL,
    RE_REFS,
    RE_TAG,
    RE_TOC_PAGE_PAREN,
)

from ..textutil import cn2num

import re

from html import unescape

from typing import Any

from ..legacy_tables import (
    legacy_html_table_to_grid,
    legacy_pipe_table_to_grid,
    legacy_table_to_blocks,
)

from .. import detect

from ..model import AppendixRule, ParseResult, Profile, Region, TableModel, Unit

from ..regions import RegionPlan

from ..tables import (
    build_table_model,
    html_table_to_grid,
    is_continuation_grid,
    is_unit_note_line,
    model_from_store,
    pipe_table_to_grid,
    render_table_blocks,
)

from .generic import (
    parse_generic,
)

def _parse_auto(
    lines: list[str],
    *,
    law_name: str = "",
    profile: Profile | None = None,
    plan: RegionPlan | None = None,
    params: dict[str, Any] | None = None,
) -> ParseResult:
    """auto 档入口：逐区域跑检测器，按证据选单位类型（P0 区域级）。

    与三个写死 handler 的区别：单位类型不再由 ``profile.family`` 决定，而是由
    区域内的证据（``第X条`` / 十进制编号 / 表字符占比 / 标题树）决定。选表时
    仍交回既有的 ``parse_generic`` 表格流水线（结构识别与渲染复用，不另写一套）。

    决策日志放进 ``ParseResult.meta["decisions"]``，由上层写进 probe 报告
    （``report["decisions"]``），不进 15 字段块契约。
    """
    del params  # P0 未使用区域级阈值覆盖（P1 再做）；保留签名便于扩展
    result = ParseResult(title=law_name)
    front_drop = bool(getattr(getattr(profile, "front", None), "drop", True))
    regions = list(plan.regions) if plan is not None and getattr(plan, "regions", None) else []
    if not regions:
        regions = [Region(kind="body", start=0, end=len(lines))]

    units: list[Unit] = []
    decisions: list[dict[str, Any]] = []
    for region in regions:
        seg = list(lines[region.start:region.end])
        if not any(str(line).strip() for line in seg):
            continue
        if region.kind == "toc":
            result.skipped_lines += len(seg)
            decisions.append(
                {"region": "toc", "chosen": "skip", "why": "目次整段丢弃", "candidates": []}
            )
            continue
        if region.kind == "front" and front_drop:
            result.skipped_lines += len(seg)
            decisions.append(
                {"region": "front", "chosen": "skip", "why": "front.drop=true", "candidates": []}
            )
            continue
        detection = detect.choose_region_detector(seg, region=region.kind)
        decisions.append(detection.to_dict())
        if detection.chosen == "table":
            # 表区：复用既有 generic 表格流水线（pipe/HTML 结构识别 + 渲染）；
            # captions=True 让「表A.1 标题」这类表题绑到表格上，不再退化成段落。
            # 行打包宽度保持 profile 配置（行文本本身已带「列名：值」，没有
            # 单独表头开销；P1 的尺寸自适应只作用于文字单位）。
            sub = parse_generic(
                "\n".join(seg),
                law_name=law_name,
                profile=profile,
                captions=True,
            )
            for warning in sub.warnings:
                if warning not in result.warnings:
                    result.warnings.append(warning)
            sub_region = (
                region.kind
                if region.kind in ("appendix", "commentary", "references")
                else "body"
            )
            for unit in sub.units:
                # parse_generic 只认识 body；区域归属由外层区域决定
                unit.region = sub_region
                unit.meta["region"] = sub_region
            if sub_region in ("appendix", "commentary", "references"):
                # 附录开头常是「附 录 A / （资料性）/ 附录标题 / 引言句」这类极短行，
                # 逐行成块会留下大量 2-20 字符碎片；合并成一段，正文一字不丢。
                prepared: list[Unit] = []
                buffer: list[str] = []
                for unit in sub.units:
                    if unit.kind == "paragraph" and len(unit.text) < 60:
                        buffer.extend(unit.lines)
                        continue
                    if buffer:
                        prepared.append(
                            Unit(
                                kind="paragraph",
                                ref="",
                                region=sub_region,
                                lines=buffer,
                                meta={"region": sub_region},
                            )
                        )
                        buffer = []
                    prepared.append(unit)
                if buffer:
                    prepared.append(
                        Unit(
                            kind="paragraph",
                            ref="",
                            region=sub_region,
                            lines=buffer,
                            meta={"region": sub_region},
                        )
                    )
                units.extend(prepared)
            else:
                units.extend(sub.units)
            continue
        for spec in detection.units:
            result_region = (
                region.kind
                if region.kind in ("appendix", "commentary", "references")
                else "body"
            )
            units.append(
                Unit(
                    kind=str(spec.get("kind") or "paragraph"),
                    ref=str(spec.get("ref") or ""),
                    title=str(spec.get("title") or ""),
                    region=result_region,
                    path=tuple(
                        str(part) for part in (spec.get("path") or ()) if str(part).strip()
                    ),
                    lines=[str(x) for x in (spec.get("lines") or []) if str(x).strip()],
                    table=None,
                    meta={},
                )
            )

    # 区域拼接后全局重编号：id 规则 ``{law_name}-{seq:04d}`` 依赖 seq 唯一且单调
    for index, unit in enumerate(units, start=1):
        if not unit.ref:
            # 契约要求 law_num 非空：前言等无编号段用标题或「段N」兜底
            unit.ref = unit.title or f"段{index}"
        unit.meta["seq"] = index
        unit.meta.setdefault("region", unit.region)
    result.units = units
    result.meta["auto"] = True
    result.meta["decisions"] = decisions
    return result
