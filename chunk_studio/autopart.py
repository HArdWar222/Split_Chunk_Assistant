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


RE_TABLE_ROW_LINE = re.compile(r"^(编号|序号|栏号|项目|类别)\s*[:：]")

#: 表格行正文里常见的字段分隔符（合并块的表格段每行都带这几个键）。
_TABLE_ROW_FIELDS = ("违法行为", "法律规定", "处罚依据", "裁量阶次", "适用条件", "处罚标准")


def _is_table_row_line(line: str) -> bool:
    """判断一行是不是「表格行」正文（``编号：1 · 违法行为：…`` 这种）。

    只认一眼能看出是表格行的：以编号/序号/栏号等开头、且带 `` · `` 字段分隔，
    或者一行里同时出现两个以上表格字段名。避免把普通正文误判成表格行。
    """
    text = str(line or "").strip()
    if not text or " · " not in text:
        return False
    if RE_TABLE_ROW_LINE.match(text):
        return True
    return sum(1 for field in _TABLE_ROW_FIELDS if field + "：" in text) >= 2


def _looks_like_heading(line: str) -> bool:
    """合并块开头的「标题/面包屑」行：短、没有句号、不是表格行。"""
    text = str(line or "").strip()
    if not text or len(text) > 160:
        return False
    if "。" in text:
        return False
    return not _is_table_row_line(text)


def _table_origin_list(originals: list[dict] | None) -> list[dict]:
    """按原始顺序取出所有表格块（面包屑、行区间、侧车都从这里找）。"""
    return [c for c in (originals or []) if str(c.get("chunk_kind") or "") == "table"]


def _table_crumb(chunk: dict) -> str:
    """表格块正文第一行就是它自己的面包屑（例如「现场管理类…台账 · 表3」）。"""
    lines = str(chunk.get("chunk") or "").split("\n")
    return lines[0].strip() if lines else ""


def _table_line_index(originals: list[dict] | None) -> dict[str, list[tuple[int, dict]]]:
    """原始表格块索引：正文每一行（去首行面包屑）→ [(顺序, 块)]。

    同一行文本可能出现在多个表格块里（列名、编号行经常重复，例如两张表的
    第一行都是「编号：类型」），所以这里保留**所有**候选并按原始块顺序排序；
    匹配时用单调游标挑「当前表格块之后最近的那一个」，避免后面的表把前面
    的行抢走（3010 的 表3/表4 互相串块就是这个问题）。
    """
    index: dict[str, list[tuple[int, dict]]] = {}
    for order, chunk in enumerate(_table_origin_list(originals)):
        body_lines = str(chunk.get("chunk") or "").split("\n")
        for line in body_lines[1:]:
            key = _norm_line(line)
            if key:
                index.setdefault(key, []).append((order, chunk))
    return index


def _table_origin_index(originals: list[dict] | None) -> dict[str, dict]:
    """原始（物化前）表格块索引：按正文最后一行的规范文本找块信息。

    合并块里的表格行就是原来某个表格块的正文行；靠它把行号区间/侧车信息
    找回来，拆出来的表格块才能写回 ``表X 第a-b行``。
    """
    index: dict[str, dict] = {}
    for chunk in originals or []:
        if str(chunk.get("chunk_kind") or "") != "table":
            continue
        body_lines = str(chunk.get("chunk") or "").split("\n")
        if not body_lines:
            continue
        key = _norm_line(body_lines[-1])
        if key:
            index.setdefault(key, chunk)
    return index


def split_mixed_chunk(
    chunks: list[dict],
    index: int,
    max_chars: int,
    min_chars: int,
    originals: list[dict] | None = None,
    source_lines: list[str] | None = None,
    grids: dict[str, dict] | None = None,
) -> list[dict] | None:
    """把「文本 + 表格」混合的超长块按类型边界拆开（用户 m10872 定的方案）。

    - 文本段 → 普通段落块（过长再均分 part）；
    - 表格段 → 每个表格行一个独立表格块，行号区间/表格侧车从原始表格块找回；
    - 面包屑只在第一块出现；
    - 不是混合块（只有一种类型）时返回 None，交给原来的拆分逻辑。
    """
    chunk = chunks[index]
    body = normalize_text(chunk.get("chunk") or "")
    if len(body) <= max_chars:
        return None
    lines = [line for line in body.split("\n") if line.strip()]
    if not lines:
        return None

    origin_tables = _table_origin_list(originals)
    line_index = _table_line_index(originals)
    origin_index = _table_origin_index(originals)
    crumbs = {_norm_line(_table_crumb(c)) for c in origin_tables if _table_crumb(c)}
    # 面包屑 → 原始表格块：合并正文里每个表格块的正文开头都带一遍自己的
    # 面包屑，用它来「换表」最可靠；靠行内容猜测会撞上多张表开头完全相同的
    # 行（3010 的表1/表2、表3/表4 都是「风险点：… · 编号：类型」）。
    crumb_map: dict[str, list[dict]] = {}
    for info in origin_tables:
        crumb = _table_crumb(info)
        if crumb:
            crumb_map.setdefault(_norm_line(crumb), []).append(info)
    crumb_cursor: dict[str, int] = {}

    header: list[str] = []
    rest: list[str] = []
    for line in lines:
        text = line.strip()
        norm = _norm_line(text)
        known_row = norm in line_index or _is_table_row_line(text)
        if not rest and not known_row and norm not in crumbs and _looks_like_heading(text):
            header.append(text)
            continue
        rest.append(line)
    if not rest:
        return None

    groups: list[dict] = []
    cur_info: dict | None = None
    for line in rest:
        text = line.strip()
        norm = _norm_line(text)
        if not text:
            continue
        info_list = crumb_map.get(norm)
        if info_list:
            # 面包屑是结构行：只用来切换「当前是哪张表」，不进入正文。
            # 同一张表被拆成多个块时（行区间不同、面包屑相同），按出现
            # 顺序依次对应原来的块。
            index_in_list = crumb_cursor.get(norm, 0)
            cur_info = info_list[min(index_in_list, len(info_list) - 1)]
            crumb_cursor[norm] = index_in_list + 1
            continue
        is_row = norm in line_index or _is_table_row_line(text) or norm in origin_index
        continuation = (
            bool(groups)
            and groups[-1]["kind"] == "table"
            and not _looks_like_heading(text)
        )
        if is_row or continuation:
            # 已知表格行；或紧跟在表格行之后的续行（例如「注1:…」「……」），
            # 它们属于当前表格块；但「附录 D」这种标题行必须断开，否则会被
            # 粘进上一张表的正文里。
            kind = "table"
            row_info = cur_info
        else:
            kind = "text"
            row_info = None
        if (
            groups
            and groups[-1]["kind"] == kind
            and (kind != "table" or groups[-1]["info"] is row_info)
        ):
            groups[-1]["lines"].append(text)
        else:
            groups.append({"kind": kind, "info": row_info, "lines": [text]})
    if not groups:
        return None

    kinds = {group["kind"] for group in groups}
    header_seg_inserted = False
    if len(kinds) < 2:
        # 合并块的「段落部分」可能只有标题/面包屑（例如段落块正文就是
        # 「第二部分 裁量细则」一行）；标题被 header 吃掉后只剩表格段，
        # 这时把 header 补成一个段落段，仍按类型边界拆。
        if str(chunk.get("chunk_kind") or "") == "table" or not header:
            return None
    if header and (len(kinds) < 2 or groups[0]["kind"] == "table"):
        # header 是合并块最前面的标题/面包屑；如果第一段就是表格（例如
        # 「附录A + 表1」合并），必须先把 header 发成一个段落块，否则标题
        # 会被整个丢掉。
        groups.insert(0, {"kind": "text", "info": None, "lines": list(header)})
        header_seg_inserted = True

    used = {str(c.get("id")) for c in chunks}
    seq = max_seq(chunks)
    out: list[dict] = []

    def take_id() -> str:
        nonlocal seq
        seq += 1
        candidate = f"{str(chunk.get('law_name') or 'chunk')}-{seq:04d}"
        while candidate in used:
            seq += 1
            candidate = f"{str(chunk.get('law_name') or 'chunk')}-{seq:04d}"
        used.add(candidate)
        return candidate

    first = True
    for group in groups:
        kind = group["kind"]
        seg_lines = group["lines"]
        if kind == "text":
            text = "\n".join(seg_lines)
            if first and header and not header_seg_inserted:
                text = "\n".join(header) + "\n" + text
            parts = split_text_parts(text, max_chars, min_chars) if len(text) > max_chars else [text]
            text_items: list[dict] = []
            for number, part_text in enumerate(parts, start=1):
                item = _copy(chunk)
                item["id"] = str(chunk.get("id") or "") if first and number == 1 else take_id()
                item["chunk_kind"] = "paragraph"
                item["part_index"] = number if len(parts) > 1 else 1
                item["part_total"] = len(parts) if len(parts) > 1 else 1
                _derive(item, text=part_text)
                item["manual"] = True
                item["align"] = chunk.get("align") or "approx"
                item["span"] = list(chunk["span"]) if chunk.get("span") else None
                text_items.append(item)
            if len(text_items) > 1:
                _annotate_part_spans(text_items, chunk, source_lines)
            out.extend(text_items)
            first = False
            continue

        # 表格段：每个 group 对应一个原始表格块（同块连续行已在分段时合并）。
        info = group["info"]
        # 每个表格块用**它自己**的面包屑（而不是合并块 header 的末行），
        # 否则 表3/表4 会互相戴错表名（3010 的问题）。
        crumb = _table_crumb(info) if info else (header[-1] if header else "")
        item = _copy(chunk)
        item["id"] = str(chunk.get("id") or "") if first else take_id()
        item["chunk_kind"] = "table"
        item["part_index"] = 1
        item["part_total"] = 1
        lines_out: list[str] = []
        for row in seg_lines:
            text_row = row.strip()
            if crumb and text_row.startswith(crumb):
                # 有些表格块把面包屑写进了行正文（「工作表格 · 表A.3 … · 注1:…」），
                # 表格段自己已经补了面包屑，这里去掉重复的一段。
                text_row = text_row[len(crumb):].lstrip()
                if text_row.startswith("·"):
                    text_row = text_row.lstrip("·").strip()
            if text_row:
                lines_out.append(text_row)
        text = (crumb + "\n" if crumb else "") + "\n".join(lines_out)
        _derive(item, text=text)
        item["manual"] = True
        if info:
            item["law_num"] = str(info.get("law_num") or "")
            item["span"] = list(info["span"]) if info.get("span") else None
            item["align"] = info.get("align") or "approx"
            origin_extras = info.get("extras") if isinstance(info.get("extras"), dict) else {}
            extras = dict(item.get("extras") or {})
            for key in ("table_grid", "table_label", "table_title", "table_rows", "table_mode"):
                if key in origin_extras:
                    extras[key] = origin_extras[key]
            origin_id = str(info.get("id") or "")
            if grids and origin_id in grids:
                # table_grid 不随基线块下发（体积大），由 Studio 单独传入。
                extras["table_grid"] = grids[origin_id]
            item["extras"] = extras
        else:
            item["law_num"] = str(chunk.get("law_num") or "")
            item["span"] = list(chunk["span"]) if chunk.get("span") else None
            item["align"] = "approx"
        out.append(item)
        first = False

    return out if len(out) >= 2 else None


def materialize_chunks(
    chunks: list[dict],
    max_chars: int = DEFAULT_MAX_CHARS,
    min_chars: int = DEFAULT_MIN_PART_CHARS,
    source_lines: list[str] | None = None,
    originals: list[dict] | None = None,
    grids: dict[str, dict] | None = None,
) -> list[dict]:
    """把超长块**真正拆开**（文本 → ``-pN`` 块；多行表格 → 行区间块）。

    与 ``auto_part_chunks`` 的区别：这里产出的是真实块（id / part 字段 /
    span / part_span 都写进块里），后续命令可以直接按 part 操作、撤销重放
    也能复现同一个结果；``auto_part_chunks`` 只用于「不编辑时的展示」的旧路径。

    文本与表格混在一起的合并块（合并了段落 + 表格）走 ``split_mixed_chunk``：
    按类型边界拆成段落块与表格块，表格块从 ``originals``（物化前的原始块）
    找回行号区间与表格侧车。
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
        mixed = split_mixed_chunk(chunks, index, max_chars, min_chars, originals, source_lines, grids)
        if mixed:
            out.extend(mixed)
            changed = True
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
