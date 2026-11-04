# -*- coding: utf-8 -*-
"""fac_rag.chunking_v2.sizing —— P1 尺寸自适应（纯函数，不进 units/model）。

P0 的 auto 档解决「怎么切单位」，尺寸却仍是固定配置（max 800 / soft 500 /
combine 120 / 固定分隔符表）。P1 让尺寸跟着内容走：

* 按 ``Unit.kind`` 分组统计长度分布（p50 / p90 / max / 短单位占比）与 CJK 占比；
* 短文本组（p90 远小于上限）：**不启用**高级切分（软上限/回吸/分隔符），
  保持整单位成块——这也是旧行为，等价于「这块内容不需要切」；
* 长条密集组（p90 > 0.8×上限）：启用软上限，并在分隔符表里补中文句读，
  让切点落在「；：\n」附近而不是硬切；
* 碎片多的段落组（短单位 ≥40% 且 ≥4 个）：启用小段回吸，避免 1-20 字符碎块；
* 英文/数字为主（CJK <30%）：分隔符改用 ``. `` / ``; `` / ``, ``；
* 表格区不切文本；表格行打包宽度由 :func:`table_row_pack` 按区域行宽中位数估。

决策全部进 ``decisions``（``_chunk_file_inner`` 并入 ``report["decisions"]``），
可人工复核「这份文件为什么用这套尺寸」。

:func:`tighten_plans` 供 gate 驱动回退：不达标时收小上限、强制启用软上限重切。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

#: 判定「中文文本」的 CJK 区间（基本区 + 扩展 A + 兼容区）。
CJK_RANGES: tuple[tuple[int, int], ...] = ((0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF))

#: 自适应上限的底线：再紧也不能小于 200（否则条款会被切碎）。
MIN_MAX_CHARS = 200
#: 组内 p90 超过该比例就启用软上限。
SOFT_P90_RATIO = 0.8
#: 短单位占比达到该值就启用回吸。
COMBINE_SHORT_RATIO = 0.4
#: 回吸阈值占上限的比例（再夹到 60..160）。
COMBINE_RATIO = 0.15
#: 默认分隔符表（与 render.DEFAULT_SEPARATORS 同值；此处独立维护避免循环依赖）。
CN_SEPARATORS: tuple[str, ...] = ("\n\n", "\n", "。", "！", "？", "；", "：", "，", "、", " ")
EN_SEPARATORS: tuple[str, ...] = (". ", "; ", ", ", " ")


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in CJK_RANGES)


def cjk_ratio(text: str) -> float:
    """CJK 字符占非空白字符的比例（0..1；空串返回 0）。"""
    visible = [ch for ch in text if not ch.isspace()]
    if not visible:
        return 0.0
    return sum(1 for ch in visible if _is_cjk(ch)) / len(visible)


def _percentile(values: Sequence[int], ratio: float) -> int:
    """最近秩百分位（确定性，便于断言）。"""
    if not values:
        return 0
    ordered = sorted(values)
    index = int(round(ratio * (len(ordered) - 1)))
    return ordered[max(0, min(len(ordered) - 1, index))]


def _dedupe(items: Iterable[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.append(item)
    return tuple(seen)


@dataclass(frozen=True)
class SplitPlan:
    """一组单位（同 ``Unit.kind``）的切分参数。"""

    max_chars: int
    soft_max_chars: int = 0
    combine_under_chars: int = 0
    separators: tuple[str, ...] = ()
    why: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_params(self) -> dict[str, Any]:
        """给 ``render_units(split_override=...)`` 用的普通 dict。"""
        return {
            "max_chars": self.max_chars,
            "soft_max_chars": self.soft_max_chars,
            "combine_under_chars": self.combine_under_chars,
            "separators": list(self.separators),
        }

    def as_decision(self, kind: str) -> dict[str, Any]:
        """写进 ``report["decisions"]`` 的决策条目（JSON 友好）。"""
        return {
            "region": "sizing",
            "chosen": "split",
            "kind": kind,
            "why": self.why,
            "params": self.as_params(),
            "evidence": dict(self.evidence),
        }


def plan_split(
    units: Sequence[Any],
    *,
    max_cap: int,
    base_soft_max_chars: int = 0,
    base_combine_under_chars: int = 0,
    base_separators: Iterable[str] = (),
) -> tuple[dict[str, SplitPlan], list[dict[str, Any]]]:
    """按单位内容给每种 ``kind`` 定一套切分参数。

    返回 ``(plans, decisions)``；``plans`` 只含出现过的文本类 kind（表块不切）。
    """
    groups: dict[str, list[str]] = {}
    for unit in units:
        kind = str(getattr(unit, "kind", "") or "paragraph")
        if kind == "table":
            continue
        text = str(getattr(unit, "text", "") or "")
        if text.strip():
            groups.setdefault(kind, []).append(text)

    plans: dict[str, SplitPlan] = {}
    decisions: list[dict[str, Any]] = []
    for kind, texts in groups.items():
        lengths = [len(text) for text in texts]
        p50 = _percentile(lengths, 0.5)
        p90 = _percentile(lengths, 0.9)
        maximum = max(lengths)
        short_cut = max(80, int(0.25 * max_cap))
        short_ratio = sum(1 for value in lengths if value < short_cut) / len(lengths)
        ratio_cjk = cjk_ratio("\n".join(texts))

        soft = max(0, min(int(base_soft_max_chars or 0), max_cap))
        combine = max(0, min(int(base_combine_under_chars or 0), max_cap))
        separators = _dedupe(base_separators)
        why: list[str] = []

        # 触发条件一：多数单位接近上限（p90 高）→ 软上限；
        # 触发条件二：只有个别超长单位（max 接近/超过上限）→ 也用软上限，
        # 让这些单位在句读处切，而不是被硬切刀在句中截断。
        if p90 > SOFT_P90_RATIO * max_cap:
            soft = max(soft, int(round(SOFT_P90_RATIO * max_cap)))
            why.append(f"p90={p90} 超过 {int(SOFT_P90_RATIO * max_cap)}，启用软上限")
        elif maximum > 0.9 * max_cap:
            soft = max(soft, int(round(SOFT_P90_RATIO * max_cap)))
            why.append(f"max={maximum} 逼近上限 {max_cap}，启用软上限（句读切分）")
        if (
            len(lengths) >= 4
            and short_ratio >= COMBINE_SHORT_RATIO
            and kind in ("paragraph", "appendix")
            # 全是短单位时不合并：它们本身已是原子单位（前言条目、附录引导行），
            # 回吸只用来把「夹在成段正文里的碎片」并回去。
            and maximum >= 0.5 * max_cap
        ):
            combine = max(combine, min(160, max(60, int(round(COMBINE_RATIO * max_cap)))))
            why.append(f"短单位占比 {short_ratio:.0%}，启用小段回吸")

        if soft or combine:
            if not separators:
                separators = CN_SEPARATORS if ratio_cjk >= 0.30 else EN_SEPARATORS
            elif ratio_cjk < 0.30:
                separators = _dedupe(tuple(separators) + EN_SEPARATORS)
            if ratio_cjk < 0.30:
                why.append(f"CJK 占比 {ratio_cjk:.0%}，分隔符改用英文标点")

        plan = SplitPlan(
            max_chars=max_cap,
            soft_max_chars=min(soft, max_cap),
            combine_under_chars=min(combine, max_cap),
            separators=separators,
            why="；".join(why) if why else "p90/max 均未逼近上限，沿用基础参数（不额外收紧）",
            evidence={
                "n_units": len(lengths),
                "p50": p50,
                "p90": p90,
                "max": maximum,
                "short_ratio": round(short_ratio, 3),
                "cjk_ratio": round(ratio_cjk, 3),
            },
        )
        plans[kind] = plan
        decisions.append(plan.as_decision(kind))
    return plans, decisions


def tighten_plans(
    plans: dict[str, SplitPlan], *, factor: float = 0.9
) -> dict[str, SplitPlan]:
    """gate 不达标时的收紧紧急版：上限 ×factor（≥200），强制启用软上限。"""
    tightened: dict[str, SplitPlan] = {}
    for kind, plan in plans.items():
        new_max = max(MIN_MAX_CHARS, int(plan.max_chars * factor))
        new_soft = max(plan.soft_max_chars, int(round(0.75 * new_max)))
        new_soft = min(new_soft, new_max)
        separators = plan.separators or CN_SEPARATORS
        tightened[kind] = SplitPlan(
            max_chars=new_max,
            soft_max_chars=new_soft,
            combine_under_chars=plan.combine_under_chars,
            separators=separators,
            why=f"gate 回退收紧：{plan.max_chars}→{new_max}，软上限 {new_soft}",
            evidence=dict(plan.evidence),
        )
    return tightened
