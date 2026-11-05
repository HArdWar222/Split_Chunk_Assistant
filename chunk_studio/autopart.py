"""自动分 part（P2 编辑后收尾）。

口径（用户 2026-11-04 决定）
---------------------------
- **文本块**：``token_len > max_chars`` 时均分成 ``-p1..-pN``；行边界优先，
  尾段 < ``min_part_chars`` 时与上一段重排；单行超长才在 ``。；，`` 处硬切；
  每段重复面包屑首行，不做重叠。
- **表格块（只做第一类）**：多行表格块总量超限时按行区间拆成多个独立表格块
  （``表X 第a-b行``）；单行超长的大表格保持原样不拆。

这里是纯函数，不做 IO；由 Studio 在重放编辑命令时调用（``materialize_chunks``
把超长块落成真实块，其它命令可继续按 part 操作）。
"""

from __future__ import annotations

import re
from typing import Any

from .align import norm_line as _norm_line
from .ops import (
    CHUNK_KINDS,
    DEFAULT_LAW_INVALID,
    NEW_LAW_NUM,
    OpError,
    _copy,
    _derive,
    chunk_hash,
    max_seq,
    next_id,
    normalize_text,
)

DEFAULT_MAX_CHARS = 800
DEFAULT_MIN_PART_CHARS = 200

RE_PART_SUFFIX = re.compile(r"-p\d+$")
RE_TABLE_ROWS = re.compile(r"第\s*(\d+)\s*[-–—~～]\s*(\d+)\s*行")

#: 硬切点：句号/分号/逗号（与 FAC-RAG 的 render._hard_split 同口径）。
HARD_CUTS = "。；;，,"


def _is_breadcrumb(line: str) -> bool:
    """块正文首行是不是面包屑（``区域 · 条号`` 这类，且不太长）。"""
    text = str(line or "").strip()
    if not text or len(text) > 160:
        return False
    return " · " in text


def _hard_split_line(line: str, size: int) -> list[str]:
    """把超长单行按标点硬切（切点不早于半段），没有标点就按长度切。"""
    size = max(int(size), 60)
    text = str(line or "")
    pieces: list[str] = []
    while len(text) > size:
        window = text[max(size // 2, 1) : size]
        cut = -1
        for mark in HARD_CUTS:
            pos = window.rfind(mark)
            if pos >= 0:
                cut = max(size // 2, 1) + pos + 1
                break
        if cut <= 0:
            cut = size
        pieces.append(text[:cut])
        text = text[cut:]
    if text:
        pieces.append(text)
    return pieces


def _pack_lines(lines: list[str], target: int) -> list[str]:
    """按行装箱到 ``target`` 字符左右；单行超长时硬切，切出的片独占一段。"""
    parts: list[str] = []
    current: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        if current:
            parts.append("\n".join(current))
            current = []
            size = 0

    for line in lines:
        if len(line) <= target:
            if current and size + 1 + len(line) > target:
                flush()
            current.append(line)
            size += len(line) + 1
            continue
        # 超长行：硬切成片段，每片独占一段（片段之间不拼）
        flush()
        for piece in _hard_split_line(line, target):
            parts.append(piece)
    flush()
    return parts


def _rebalance(parts: list[str], min_chars: int) -> list[str]:
    """尾段过小时从上一段搬行过来（保持行完整，实在搬不动就接受）。"""
    while len(parts) > 1 and len(parts[-1]) < min_chars:
        tail = parts.pop()
        prev_lines = parts.pop().split("\n")
        moved: list[str] = []
        while len("\n".join(moved + [tail])) < min_chars and len(prev_lines) > 1:
            moved.insert(0, prev_lines.pop())
        if not moved:
            # 上一段只有一行（或搬不动任何行）：合并会超长，只能接受现状，
            # 否则这里会一直把同一段 pop 回来造成死循环。
            parts.append("\n".join(prev_lines))
            parts.append(tail)
            break
        tail = "\n".join(moved + [tail])
        if not prev_lines:
            parts.append(tail)
            break
        parts.append("\n".join(prev_lines))
        parts.append(tail)
    return parts


def split_text_parts(
    text: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    min_chars: int = DEFAULT_MIN_PART_CHARS,
) -> list[str]:
    """把一段正文均分成若干 part（不含 part 编号，仅正文列表）。"""
    body = normalize_text(text)
    if not body:
        return [body]
    total = len(body)
    if total <= max_chars:
        return [body]

    lines = body.split("\n")
    breadcrumb = ""
    if len(lines) > 1 and _is_breadcrumb(lines[0]):
        breadcrumb, lines = lines[0], lines[1:]

    n = max(2, -(-total // max_chars))
    target = max(min_chars, -(-total // n))
    parts = _pack_lines(lines, target)
    parts = _rebalance(parts, min_chars)
    # 目标段数打不住（比如某一行特别长）时，允许段数多于 n，但不再压缩
    if breadcrumb:
        parts = [breadcrumb + "\n" + part if part else breadcrumb for part in parts]
    return parts


def _part_id(base: str, index: int) -> str:
    return f"{RE_PART_SUFFIX.sub('', base)}-p{index}"


def split_text_chunk(
    chunks: list[dict],
    index: int,
    max_chars: int,
    min_chars: int,
) -> list[dict] | None:
    """文本块拆 part；返回新块列表（长度 >=2），不需要拆时返回 None。"""
    chunk = chunks[index]
    parts = split_text_parts(str(chunk.get("chunk") or ""), max_chars, min_chars)
    template = f"{RE_PART_SUFFIX.sub('', str(chunk.get('id') or ''))}"
    if len(parts) < 2:
        return None
    stem = template.rsplit("-", 1)[0] if "-" in template else template
    out: list[dict] = []
    for number, text in enumerate(parts, start=1):
        item = _copy(chunk)
        item["id"] = _part_id(template, number)
        item["part_index"] = number
        item["part_total"] = len(parts)
        if not str(item.get("law_num") or "").strip():
            item["law_num"] = NEW_LAW_NUM
        _derive(item, text=text)
        item["manual"] = True
        # 继承原块的行区间/对齐状态，让 part 在前端仍能定位到原文
        item["align"] = chunk.get("align") or "manual"
        item["span"] = list(chunk["span"]) if chunk.get("span") else None
        out.append(item)
    # 极端情况下 part id 与现有块碰撞（例如已经手工拆过），顺延到最大序号
    used = {str(c.get("id")) for c in chunks}
    seq = max_seq(chunks)
    for item in out:
        if item["id"] in used and item["id"] != str(chunk.get("id")):
            while True:
                seq += 1
                candidate = f"{stem}-{seq:04d}-p{item['part_index']}"
                if candidate not in used:
                    item["id"] = candidate
                    used.add(candidate)
                    break
    return out


def split_table_chunk(chunks: list[dict], index: int, max_chars: int) -> list[dict] | None:
    """表格块按行区间拆成独立块（只处理多行且超限的表格）。"""
    chunk = chunks[index]
    body = normalize_text(chunk.get("chunk") or "")
    lines = body.split("\n")
    breadcrumb = ""
    if len(lines) > 1 and _is_breadcrumb(lines[0]):
        breadcrumb, rows = lines[0], lines[1:]
    else:
        rows = lines
    if len(rows) < 2:
        return None
    if len(body) <= max_chars:
        return None

    groups: list[list[str]] = []
    current: list[str] = []
    size = len(breadcrumb) + 1 if breadcrumb else 0
    for row in rows:
        if current and size + 1 + len(row) > max_chars:
            groups.append(current)
            current = []
            size = len(breadcrumb) + 1 if breadcrumb else 0
        current.append(row)
        size += len(row) + 1
    if current:
        groups.append(current)
    if len(groups) < 2:
        return None

    law_num = str(chunk.get("law_num") or "")
    match = RE_TABLE_ROWS.search(law_num)
    first_row = int(match.group(1)) if match else None

    seq = max_seq(chunks)
    out: list[dict] = []
    offset = 0
    for number, group in enumerate(groups):
        item = _copy(chunk)
        if number:
            seq += 1
            item["id"] = f"{str(chunk.get('law_name') or 'chunk')}-{seq:04d}"
        text = (breadcrumb + "\n" if breadcrumb else "") + "\n".join(group)
        _derive(item, text=text)
        item["manual"] = True
        item["align"] = "manual"
        # 行拆块仍落在同一张整表行上，继承原块 span，靠 law_num 行区间区分
        item["span"] = list(chunk["span"]) if chunk.get("span") else None
        item["part_index"] = 1
        item["part_total"] = 1
        if first_row is not None:
            start = first_row + offset
            end = start + len(group) - 1
            item["law_num"] = law_num[: match.start()] + f"第{start}-{end}行" + law_num[match.end() :]
        offset += len(group)
        out.append(item)
    return out


def _annotate_part_spans(
    parts: list[dict],
    parent: dict,
    source_lines: list[str] | None,
) -> None:
    """给拆出来的 part 标出「真实覆盖的源行区间」（``part_span``，视图字段）。

    part 正文里除面包屑首行外的每一行，在父块 span 范围内按 ``norm_line``
    顺序匹配，得到首/末行；匹配不到就回退父块 span（左侧泳道仍能画外层条）。
    """
    span = list(parent["span"]) if parent.get("span") else None
    if not source_lines or not span:
        for item in parts:
            item["part_span"] = list(span) if span else None
        return

    lo = max(0, int(span[0]) - 1)
    hi = min(len(source_lines), int(span[1]))
    window = [_norm_line(line) for line in source_lines[lo:hi]]
    cursor = 0
    for item in parts:
        lines = str(item.get("chunk") or "").split("\n")
        if lines and _is_breadcrumb(lines[0]):
            lines = lines[1:]
        wanted = [_norm_line(line) for line in lines if _norm_line(line)]
        start = end = -1
        k = cursor
        ok = True
        for want in wanted:
            hit = -1
            for j in range(k, len(window)):
                if window[j] and (window[j] == want or want in window[j]):
                    hit = j
                    break
            if hit < 0:
                ok = False
                break
            if start < 0:
                start = hit
            end = hit
            k = hit + 1
        if not ok or start < 0:
            item["part_span"] = list(span)
            continue
        item["part_span"] = [lo + start + 1, lo + end + 1]
        cursor = end + 1


def materialize_chunks(
    chunks: list[dict],
    max_chars: int = DEFAULT_MAX_CHARS,
    min_chars: int = DEFAULT_MIN_PART_CHARS,
    source_lines: list[str] | None = None,
) -> list[dict]:
    """把超长块**真正拆开**（文本 → ``-pN`` 块；多行表格 → 行区间块）。

    与 ``auto_part_chunks`` 的区别：这里产出的是真实块（id / part 字段 /
    span / part_span 都写进块里），后续命令可以直接按 part 操作、撤销重放
    也能复现同一个结果；``auto_part_chunks`` 只用于「不编辑时的展示」的旧路径。
    """
    max_chars = max(int(max_chars or DEFAULT_MAX_CHARS), 50)
    min_chars = max(int(min_chars or DEFAULT_MIN_PART_CHARS), 20)
    out: list[dict] = []
    changed = False
    for index, chunk in enumerate(chunks):
        text = str(chunk.get("chunk") or "")
        already = int(chunk.get("part_total") or 1) > 1
        if len(text) <= max_chars or already:
            out.append(chunk)
            continue
        kind = str(chunk.get("chunk_kind") or "")
        if kind == "table":
            replaced = split_table_chunk(chunks, index, max_chars)
        else:
            replaced = split_text_chunk(chunks, index, max_chars, min_chars)
        if not replaced:
            out.append(chunk)
            continue
        if kind != "table":
            _annotate_part_spans(replaced, chunk, source_lines)
        out.extend(replaced)
        changed = True
    return out if changed else chunks


def auto_part_chunks(
    chunks: list[dict],
    law_name: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    min_chars: int = DEFAULT_MIN_PART_CHARS,
) -> list[dict]:
    """按口径把超限块拆开（文本 → part；表格 → 行区间独立块）。"""
    max_chars = max(int(max_chars or DEFAULT_MAX_CHARS), 200)
    min_chars = max(int(min_chars or DEFAULT_MIN_PART_CHARS), 60)
    out: list[dict] = []
    changed = False
    for index, chunk in enumerate(chunks):
        text = str(chunk.get("chunk") or "")
        if len(text) <= max_chars:
            out.append(chunk)
            continue
        kind = str(chunk.get("chunk_kind") or "")
        replaced = None
        if kind == "table":
            replaced = split_table_chunk(chunks, index, max_chars)
        else:
            replaced = split_text_chunk(chunks, index, max_chars, min_chars)
        if replaced:
            out.extend(replaced)
            changed = True
        else:
            out.append(chunk)
    return out if changed else chunks


def build_extend_body(
    target: dict,
    absorbed: list[dict],
    extra_lines: list[str],
) -> str:
    """给前端兜底用：目标正文 + 被并块正文 + 末尾多选源行。"""
    parts = [str(target.get("chunk") or "")]
    for chunk in absorbed:
        parts.append(str(chunk.get("chunk") or ""))
    parts.extend(str(line) for line in extra_lines if str(line or "").strip())
    return normalize_text("\n".join(parts))
