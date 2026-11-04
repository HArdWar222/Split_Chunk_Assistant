"""跨模块共用的小工具（纯函数、无业务依赖）。"""
from __future__ import annotations

from typing import Iterable, Sequence

_CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}


def cn2num(text: str) -> int | None:
    """中文数字 → int（支持 十/百/千 与阿拉伯数字混写）；识别不了返回 ``None``。"""
    text = str(text).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total = 0
    current = 0
    for ch in text:
        if ch in _CN_DIGITS:
            current = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            total += (current or 1) * _CN_UNITS[ch]
            current = 0
        else:
            return None
    return total + current


def next_nonempty(lines: Sequence[str], start: int) -> str:
    """返回 ``lines[start:]`` 里第一个非空行（已 strip），没有则空串。"""
    for raw in lines[max(0, start):]:
        text = str(raw).strip()
        if text:
            return text
    return ""


def dedupe(items: Iterable[str]) -> list[str]:
    """保序去重。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
