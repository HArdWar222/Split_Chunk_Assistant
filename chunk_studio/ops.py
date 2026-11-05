"""编辑命令：纯函数，输入「视图块列表 + 命令」，输出新块列表。

设计原则
--------
- **不改基线**：输入列表不被修改，返回新列表；基线永远只读。
- **派生字段重算**：``token_len = len(chunk)``、``chunk_hash = sha256(规范化正文)[:16]``，
  与 ``fac_rag.contracts`` 一致（这里不 import fac_rag，保持零依赖）。
- **id 规则**（D10）：拆分/新增取「当前最大 seq + 1」；合并且保留前者 id；
  删除留下 id 空洞，靠撤销栈回退。
- **对齐状态**：改动过正文的块标 ``manual``，不再声称与原文某行精确对应；
  拆分/合并能推出区间时保留 span 供参考，否则置 None。
- 所有函数都是幂等的纯计算：同一条 op 序列重复应用结果一致——
  这是「撤销 = 弹出最后一个 op 后整串重放」的前提。

op 形状
-------
- ``{"op": "split", "id": "...", "at_line": 2}`` 或 ``{"op": "split", "id": "...", "at_char": 40}``
- ``{"op": "merge", "id": "前一块", "with": "后一块"}``
- ``{"op": "merge_many", "ids": ["...", "...", "..."]}``（连续多块集体合并，保留第一块 id）
- ``{"op": "drop", "id": "..."}``
- ``{"op": "add", "text": "...", "law_num": "...", "chunk_kind": "paragraph", "after_id": "..." | null}``
- ``{"op": "patch", "id": "...", "chunk": "...", "law_num": "...", "chunk_kind": "..."}``（都可选）
"""

from __future__ import annotations

import copy
import hashlib
import re
from difflib import SequenceMatcher
from typing import Any, Callable

from .align import locate_text_span, norm_line

RE_ID = re.compile(r"^.+-(\d{4,})(?:-p(\d+))?$")

#: part 块 id 的 ``-pN`` 后缀（删除单个 part 后要重排编号）。
RE_PART_SUFFIX = re.compile(r"-p\d+$")

#: 条号识别：行首（允许列表符 / 加粗 / 标题记号 / 全角空格）的「第X条」
RE_LAW_NUM = re.compile(r"^\s*(?:[#>*\-+]\s*)*第\s*[〇零一二三四五六七八九十百千两0-9]{1,8}\s*条")

#: 标准式条号识别：行首的「5.4.1」这类数字层级号（至少带一个小数点，避免误吞正文里的普通数字）
RE_CLAUSE_NUM = re.compile(r"^\s*(?:[#>*\-+]\s*)*(\d{1,2}(?:\.\d{1,4}){1,5})(?=\s|\u3000|$|[^\d.])")

#: law_num 本身是否可作为倒推锚点（第X条 / 5.4.1 / 1）
RE_ANCHOR = re.compile(r"^(?:第\s*[〇零一二三四五六七八九十百千两0-9]{1,8}\s*条|\d{1,2}(?:\.\d{1,4}){1,5}|\d{1,2})$")

CHUNK_KINDS = frozenset({"clause", "table", "title", "front", "appendix", "stub", "paragraph"})
DEFAULT_LAW_INVALID = "2030-12-12"

#: 新增块没有条号时的占位（契约要求 law_num 非空）
NEW_LAW_NUM = "（新增）"

#: 拆分/新增时从原块继承的 extras 键（表格网格之类不跟过去）
INHERITED_EXTRAS = ("clause", "region", "ref_title", "num_cn")

#: 视图层附加字段（不属于 15 字段契约，操作时原样携带）
VIEW_KEYS = ("extras", "span", "align", "manual")


class OpError(ValueError):
    """编辑命令不合法（400）。"""


class OpNotFound(OpError):
    """要操作的块不存在（404）。"""


# ---------------------------------------------------------------------------
# 纯函数：正文规范化与派生字段（与 fac_rag.contracts 保持一致）
# ---------------------------------------------------------------------------


def normalize_text(text: Any) -> str:
    """统一换行、去掉每行尾空白；正文内容本身不动。"""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def chunk_hash(text: Any) -> str:
    """正文指纹（16 位十六进制），与 FAC-RAG 契约一致。"""
    normalized = "\n".join(line.rstrip() for line in str(text or "").strip().splitlines())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def estimate_token_len(text: Any) -> int:
    """字符数近似 token 数。"""
    return len(str(text or ""))


def _derive(chunk: dict, *, text: str | None = None) -> dict:
    """写入正文并重算派生字段；返回同一个 dict。"""
    if text is not None:
        chunk["chunk"] = normalize_text(text)
    else:
        chunk["chunk"] = normalize_text(chunk.get("chunk"))
    chunk["token_len"] = estimate_token_len(chunk["chunk"])
    chunk["chunk_hash"] = chunk_hash(chunk["chunk"])
    try:
        part_index = int(chunk.get("part_index") or 1)
        part_total = int(chunk.get("part_total") or 1)
    except (TypeError, ValueError) as exc:
        raise OpError(f"part 编号不是整数：{exc}") from None
    chunk["part_index"] = part_index
    chunk["part_total"] = max(part_total, part_index)
    return chunk


def _require_text(chunk: dict, what: str) -> None:
    if not str(chunk.get("chunk") or "").strip():
        raise OpError(f"{what}后正文为空，请换一个切分位置")


def _copy(chunk: dict) -> dict:
    return copy.deepcopy(chunk)


def detect_law_num(text: Any) -> str:
    """从一段正文的前几行里识别条号；识别不到返回空串。

    两种样式都认：
    - 法规式「第二条 …」；
    - 标准式「5.4.1 现场应…」（至少一个层级点，避免把正文里的「2019年」之类当条号）。

    用于拆分时自动推断下半块条号：合并过的块再拆开，下半块往往以
    「第二条 …」或「5.4.2 …」开头，不该继续沿用上半块的条号。
    """
    for line in str(text or "").split("\n")[:4]:
        match = RE_LAW_NUM.match(line)
        if match:
            return re.sub(r"\s+", "", match.group(0))
        match = RE_CLAUSE_NUM.match(line)
        if match:
            return match.group(1)
    return ""


def _parse_span(value: Any) -> list[int] | None:
    """解析可选的行区间 span（1-based 闭区间），非法时抛 OpError。"""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise OpError("span 必须是 [起始行, 结束行] 两个整数")
    try:
        start = int(value[0])
        end = int(value[1])
    except (TypeError, ValueError):
        raise OpError("span 的行号不是整数") from None
    if start < 1 or end < 1:
        raise OpError("span 的行号必须从 1 开始")
    if start > end:
        raise OpError(f"span 起始行 {start} 大于结束行 {end}")
    return [start, end]


# ---------------------------------------------------------------------------
# 基础查询
# ---------------------------------------------------------------------------


def find_index(chunks: list[dict], chunk_id: Any) -> int:
    chunk_id = str(chunk_id or "")
    for index, chunk in enumerate(chunks):
        if str(chunk.get("id")) == chunk_id:
            return index
    raise OpNotFound(f"找不到切片：{chunk_id}")


def max_seq(chunks: list[dict]) -> int:
    """当前块 id 里的最大序号（找不到数字时按 0）。"""
    best = 0
    for chunk in chunks:
        match = RE_ID.match(str(chunk.get("id") or ""))
        if match:
            best = max(best, int(match.group(1)))
    return best


def next_id(chunks: list[dict], law_name: str, reserved=None) -> str:
    """新块 id = law_name-(max_seq+1)，并保证不撞已有 id。

    ``reserved`` 用于把「本次编辑前存在过的 id」也算进来：删除后再新增
    不会复用已删块的编号（否则 diff 会把新块误判成旧块被修改）。
    """
    used = {str(chunk.get("id")) for chunk in chunks}
    seq = max_seq(chunks) + 1
    if reserved:
        seq = max(seq, max_seq([{"id": cid} for cid in reserved]) + 1)
        used |= {str(cid) for cid in reserved}
    while True:
        candidate = f"{law_name}-{seq:04d}"
        if candidate not in used:
            return candidate
        seq += 1


def _renumber(chunks: list[dict], law_name: str) -> list[dict]:
    """按当前顺序重排所有块的 id：首块 = ``{law_name}-0001``，多 part 共用 base 序号。

    插入类编辑（add）之后调用，保证「id 序号 == 该块在原文中的位置」；
    不改变块内容，只改 id（15 字段其余值不动，part 后缀也保留）。
    """
    out: list[dict] = []
    seq = 0
    part_base = None
    prev_total = 0
    prev_part_index = 0
    for chunk in chunks:
        item = _copy(chunk)
        try:
            total = int(item.get("part_total") or 1)
            part_index = int(item.get("part_index") or 1)
        except (TypeError, ValueError):
            total, part_index = 1, 1
        if total > 1:
            if part_base is not None and prev_total > 1 and part_index == prev_part_index + 1:
                base = part_base
            else:
                seq += 1
                base = seq
                part_base = base
            item["id"] = f"{law_name}-{base:04d}-p{part_index}"
        else:
            seq += 1
            item["id"] = f"{law_name}-{seq:04d}"
            part_base = None
        prev_total = total
        prev_part_index = part_index
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# 各命令
# ---------------------------------------------------------------------------


def _split(chunks: list[dict], op: dict, law_name: str, reserved=None, source_lines=None) -> list[dict]:
    index = find_index(chunks, op.get("id"))
    original = chunks[index]
    text = str(original.get("chunk") or "")
    lines = text.split("\n")

    at_line = op.get("at_line")
    at_char = op.get("at_char")
    if at_line is not None:
        try:
            at_line = int(at_line)
        except (TypeError, ValueError):
            raise OpError("at_line 必须是整数") from None
        if not (1 <= at_line < len(lines)):
            raise OpError(f"切分行号越界：{at_line}（该块共 {len(lines)} 行，可取 1–{len(lines) - 1}）")
        first_text = "\n".join(lines[:at_line])
        second_text = "\n".join(lines[at_line:])
    elif at_char is not None:
        try:
            at_char = int(at_char)
        except (TypeError, ValueError):
            raise OpError("at_char 必须是整数") from None
        if not (1 <= at_char < len(text)):
            raise OpError(f"切分位置越界：{at_char}（该块正文 {len(text)} 字符，可取 1–{len(text) - 1}）")
        first_text, second_text = text[:at_char], text[at_char:]
    else:
        raise OpError("拆分需要 at_line（行级）或 at_char（字符级）")

    first = _copy(original)
    _derive(first, text=first_text)
    _require_text(first, "拆分")
    first["align"] = "manual"
    first["manual"] = True

    second = _copy(original)
    second["id"] = next_id(chunks, law_name, reserved)
    second_law_num = str(op.get("second_law_num") or "").strip()
    if second_law_num:
        second["law_num"] = second_law_num
    second["part_index"] = 1
    second["part_total"] = 1
    second["extras"] = {k: v for k, v in (original.get("extras") or {}).items() if k in INHERITED_EXTRAS}
    second["span"] = None
    second["align"] = "manual"
    second["manual"] = True
    _derive(second, text=second_text)
    _require_text(second, "拆分")

    # 给两块补行区间：在「原块 span」窗口内按规范化行顺序定位，
    # 这样拆分后右栏仍显示原文行号、左侧范围条也能立刻断成两段。
    # （只有服务端传入 source_lines 时才做；纯单测调用保持 span=None 的旧行为。）
    second_span = None
    if source_lines:
        norm = [norm_line(line) for line in source_lines]
        span = original.get("span")
        if span and len(span) == 2:
            lo, hi = max(0, int(span[0]) - 1), min(len(norm), int(span[1]))
        else:
            lo, hi = 0, len(norm)
        first_span = locate_text_span(norm, first_text, lo=lo, hi=hi)
        if first_span:
            first["span"] = [first_span[0] + 1, first_span[1]]
            first["align"] = "manual"
        start_second = first_span[1] if first_span else lo
        second_span = locate_text_span(norm, second_text, lo=start_second, hi=hi)
        if second_span is None:
            second_span = locate_text_span(norm, second_text, lo=lo, hi=hi)
        if second_span:
            second["span"] = [second_span[0] + 1, second_span[1]]
            second["align"] = "manual"

    # 下半块条号：显式填写 > 从下半块正文首行识别 > 从切点所在原文行识别 > 继承原块。
    # 典型场景：两条合并成一块（law_num 保留第一条）后再拆开，下半块应识别出「第二条」。
    if not second_law_num:
        detected = detect_law_num(second_text)
        if not detected and second_span:
            detected = detect_law_num(source_lines[second_span[0]])
        if detected:
            second["law_num"] = detected

    return chunks[:index] + [first, second] + chunks[index + 1 :]


def _merge(chunks: list[dict], op: dict, law_name: str, reserved=None, source_lines=None) -> list[dict]:
    first_id = op.get("id")
    second_id = op.get("with")
    index = find_index(chunks, first_id)
    other = find_index(chunks, second_id)
    if abs(other - index) != 1:
        raise OpError("只能合并相邻的两块（前后紧挨着的两块）")
    # 允许两种写法：{id: 前块, with: 后块} 或 {id: 后块, with: 前块}（前端「并↑/并↓」两种方向）
    index, other = min(index, other), max(index, other)

    first = chunks[index]
    second = chunks[other]

    merged = _copy(first)
    _derive(merged, text=str(first.get("chunk") or "") + "\n" + str(second.get("chunk") or ""))
    _require_text(merged, "合并")

    if not str(merged.get("law_num") or "").strip() and str(second.get("law_num") or "").strip():
        merged["law_num"] = second["law_num"]
    if not (merged.get("extras") or {}) and (second.get("extras") or {}):
        merged["extras"] = copy.deepcopy(second["extras"])

    span_a, span_b = first.get("span"), second.get("span")
    merged["manual"] = bool(first.get("manual") or second.get("manual"))
    if span_a and span_b:
        # 即便某块是手工改过的（manual），只要两边都有行区间，合并后仍能推出覆盖区间，
        # 这样「段落 + 邻近表格」合并后不会退化成「未对齐」。
        merged["span"] = [min(int(span_a[0]), int(span_b[0])), max(int(span_a[1]), int(span_b[1]))]
        merged["align"] = "approx"
    else:
        merged["span"] = None
        merged["align"] = "manual"
        merged["manual"] = True

    return chunks[:index] + [merged] + chunks[other + 1 :]


def _merge_many(chunks: list[dict], op: dict, law_name: str, reserved=None, source_lines=None) -> list[dict]:
    """把选中的连续多块合并成一块（保留第一块的 id / 条号 / kind）。"""
    ids = op.get("ids")
    if not isinstance(ids, (list, tuple)) or len(ids) < 2:
        raise OpError("集体合并至少需要 2 个切片 id（ids: [\"...\", \"...\"]）")
    indexes = sorted({find_index(chunks, chunk_id) for chunk_id in ids})
    if len(indexes) != len(ids):
        raise OpError("集体合并的 id 列表里有重复项")
    start, end = indexes[0], indexes[-1]
    if indexes != list(range(start, end + 1)):
        raise OpError("集体合并只能选连续的切片（选中的块之间不能跳块）")

    group = chunks[start : end + 1]
    merged = _copy(group[0])
    _derive(merged, text="\n".join(str(chunk.get("chunk") or "") for chunk in group))
    _require_text(merged, "合并")

    if not str(merged.get("law_num") or "").strip():
        for chunk in group[1:]:
            if str(chunk.get("law_num") or "").strip():
                merged["law_num"] = chunk["law_num"]
                break
    if not (merged.get("extras") or {}):
        for chunk in group[1:]:
            if chunk.get("extras"):
                merged["extras"] = copy.deepcopy(chunk["extras"])
                break

    spans = [chunk.get("span") for chunk in group]
    merged["manual"] = any(bool(chunk.get("manual")) for chunk in group)
    if all(span for span in spans):
        merged["span"] = [min(int(span[0]) for span in spans), max(int(span[1]) for span in spans)]
        merged["align"] = "approx"
    else:
        merged["span"] = None
        merged["align"] = "manual"
        merged["manual"] = True

    return chunks[:start] + [merged] + chunks[end + 1 :]


def _extend(chunks: list[dict], op: dict, law_name: str, reserved=None, source_lines=None) -> list[dict]:
    """把选区覆盖的连续块并入目标块（保留目标 id），可选覆盖正文/条号。

    形状：``{"op": "extend", "id": 目标, "ids": [目标, …后续块], "chunk": 可选新正文, "law_num": 可选}``。
    行级语义：调用方（前端）必须把选区吸附到块边界——中间块整块并入，不做半块切分。
    """
    ids = [str(cid) for cid in (op.get("ids") or []) if str(cid or "").strip()]
    target_id = str(op.get("id") or "").strip()
    if not target_id:
        raise OpError("extend 需要 id（目标块）")
    if not ids:
        ids = [target_id]
    if ids[0] != target_id:
        raise OpError("extend 的 ids[0] 必须是目标块 id")
    if len(set(ids)) != len(ids):
        raise OpError("extend 的 ids 里有重复项")

    indexes = [find_index(chunks, cid) for cid in ids]
    if indexes != list(range(indexes[0], indexes[0] + len(indexes))):
        raise OpError("extend 只能合并连续的切片（选中的块之间不能跳块）")

    group = chunks[indexes[0] : indexes[-1] + 1]
    merged = _copy(group[0])
    if "chunk" in op:
        text = str(op.get("chunk") or "")
    else:
        text = "\n".join(str(chunk.get("chunk") or "") for chunk in group)
    _derive(merged, text=text)
    _require_text(merged, "扩展")

    if "law_num" in op:
        law_num = str(op.get("law_num") or "").strip()
        if not law_num:
            raise OpError("law_num 不能为空")
        merged["law_num"] = law_num
    elif not str(merged.get("law_num") or "").strip():
        for chunk in group[1:]:
            if str(chunk.get("law_num") or "").strip():
                merged["law_num"] = chunk["law_num"]
                break

    span = _parse_span(op.get("span"))
    spans = [chunk.get("span") for chunk in group]
    merged["manual"] = True
    merged["align"] = "manual"
    if span:
        merged["span"] = span
    elif all(item and len(item) == 2 for item in spans):
        merged["span"] = [min(int(item[0]) for item in spans), max(int(item[1]) for item in spans)]
    else:
        merged["span"] = None

    out = chunks[: indexes[0]] + [merged] + chunks[indexes[-1] + 1 :]
    return _renumber(out, law_name)


def _reindex_parts(chunks: list[dict]) -> list[dict]:
    """删除单个 part 后重排同组剩余 part：编号连续、只剩一个时变回普通块。"""
    out = list(chunks)
    i = 0
    while i < len(out):
        if int(out[i].get("part_total") or 1) <= 1:
            i += 1
            continue
        base = RE_PART_SUFFIX.sub("", str(out[i].get("id") or ""))
        group: list[int] = []
        j = i
        while j < len(out) and RE_PART_SUFFIX.sub("", str(out[j].get("id") or "")) == base:
            group.append(j)
            j += 1
        total = len(group)
        for number, idx in enumerate(group, start=1):
            chunk = out[idx]
            if total == 1:
                chunk["id"] = base
                chunk["part_index"] = 1
                chunk["part_total"] = 1
            else:
                chunk["id"] = f"{base}-p{number}"
                chunk["part_index"] = number
                chunk["part_total"] = total
        i = j
    return out


def _drop(chunks: list[dict], op: dict, law_name: str, reserved=None, source_lines=None) -> list[dict]:
    index = find_index(chunks, op.get("id"))
    out = chunks[:index] + chunks[index + 1 :]
    if int(chunks[index].get("part_total") or 1) > 1:
        out = _reindex_parts(out)
    return out


def _drop_many(chunks: list[dict], op: dict, law_name: str, reserved=None, source_lines=None) -> list[dict]:
    """一次删除多个切片（原子命令，一次 Ctrl+Z 撤销）。"""
    ids = op.get("ids")
    if not isinstance(ids, (list, tuple)) or not ids:
        raise OpError("drop_many 需要 ids（非空数组）")
    wanted = [str(item) for item in ids]
    known = {str(chunk.get("id")) for chunk in chunks}
    missing = [item for item in wanted if item not in known]
    if missing:
        raise OpNotFound("找不到切片：" + "、".join(missing[:3]) + ("…" if len(missing) > 3 else ""))
    drop = set(wanted)
    out = [chunk for chunk in chunks if str(chunk.get("id")) not in drop]
    return _reindex_parts(out)


def _clause_anchor(chunk: dict) -> str:
    """取一块可作为条号参照的 law_num（「第X条」或「5.4.1」）；占位/表号/附录号不算。"""
    candidate = str(chunk.get("law_num") or "").strip()
    if not candidate or candidate == NEW_LAW_NUM:
        return ""
    compact = re.sub(r"\s+", "", candidate)
    if not RE_ANCHOR.match(compact):
        return ""
    # 「A.1 表D.1 第1-2行」这类含表号/附录号的复合值不作参照（A.1 开头已被排除，这里防御性检查）
    if "表" in compact or "附录" in compact:
        return ""
    return compact


def infer_law_num(chunks: list[dict], index: int) -> str:
    """按前后邻居倒推插在第 ``index`` 个位置的新块条号（无绝对把握时返回空串）。

    规则（保守）：
    1. 先看**前一块**（同一块的续写，插在它后面默认仍属于它的条号）；
    2. 前面没有参照时看**后一块**（插在开头，通常属于下一块所在的条）；
    3. 参照只认「第X条」，表号 / 附录号 / （新增）都不拿来推断。
    """
    for j in range(min(index, len(chunks) - 1), -1, -1):
        anchor = _clause_anchor(chunks[j])
        if anchor:
            return anchor
    for j in range(max(0, index + 1), len(chunks)):
        anchor = _clause_anchor(chunks[j])
        if anchor:
            return anchor
    return ""


def _add(chunks: list[dict], op: dict, law_name: str, reserved=None, source_lines=None) -> list[dict]:
    text = str(op.get("text") or "")
    kind = str(op.get("chunk_kind") or "paragraph").strip() or "paragraph"
    if kind not in CHUNK_KINDS:
        raise OpError(f"chunk_kind 非法：{kind!r}（可选 {sorted(CHUNK_KINDS)}）")

    # 插入位置：after_id 之后 / before_id 之前；都不传则追加到末尾
    after_id = op.get("after_id")
    before_id = op.get("before_id")
    if after_id is not None:
        insert_at = find_index(chunks, after_id) + 1
    elif before_id is not None:
        insert_at = find_index(chunks, before_id)
    else:
        insert_at = len(chunks)

    law_num = str(op.get("law_num") or "").strip()
    if not law_num:
        law_num = infer_law_num(chunks, insert_at - 1) or NEW_LAW_NUM

    # 可选：来自原文选区时带上行区间（1-based 闭区间），新增块因此可定位回原文
    span = _parse_span(op.get("span"))

    template = chunks[0] if chunks else {}
    new: dict = {
        "id": next_id(chunks, law_name, reserved),
        "chunk": "",
        "law_name": law_name,
        "law_num": law_num,
        "law_invalid": template.get("law_invalid") or DEFAULT_LAW_INVALID,
        "law_type": template.get("law_type") or "",
        "law_level": template.get("law_level") or "",
        "issuer": template.get("issuer") or "",
        "effective_date": template.get("effective_date") or "",
        "chunk_kind": kind,
        "token_len": 0,
        "source": template.get("source") or "",
        "part_index": 1,
        "part_total": 1,
        "chunk_hash": "",
        "extras": {"region": "body"},
        "span": span,
        "align": "manual",
        "manual": True,
    }
    _derive(new, text=text)
    _require_text(new, "新增")

    # 新增后按当前顺序重排 id：新块 id 反映它在原文中的位置（插入点之后的块序号顺延），
    # 而不是永远拿队尾的 max_seq+1。
    return _renumber(chunks[:insert_at] + [new] + chunks[insert_at:], law_name)


def _patch(chunks: list[dict], op: dict, law_name: str, reserved=None, source_lines=None) -> list[dict]:
    index = find_index(chunks, op.get("id"))
    chunk = _copy(chunks[index])

    changed_text = False
    if "chunk" in op:
        chunk["chunk"] = str(op.get("chunk") or "")
        changed_text = True
    if "law_num" in op:
        law_num = str(op.get("law_num") or "").strip()
        if not law_num:
            raise OpError("law_num 不能为空")
        chunk["law_num"] = law_num
    if "chunk_kind" in op:
        kind = str(op.get("chunk_kind") or "").strip()
        if kind not in CHUNK_KINDS:
            raise OpError(f"chunk_kind 非法：{kind!r}（可选 {sorted(CHUNK_KINDS)}）")
        chunk["chunk_kind"] = kind
    if "part_index" in op:
        chunk["part_index"] = int(op.get("part_index") or 1)
    if "part_total" in op:
        chunk["part_total"] = int(op.get("part_total") or 1)

    _derive(chunk)
    _require_text(chunk, "修改")
    if changed_text:
        chunk["manual"] = True
        chunk["align"] = "manual"

    return chunks[:index] + [chunk] + chunks[index + 1 :]


HANDLERS = {
    "split": _split,
    "merge": _merge,
    "merge_many": _merge_many,
    "extend": _extend,
    "drop": _drop,
    "drop_many": _drop_many,
    "add": _add,
    "patch": _patch,
}


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def apply_ops(
    chunks: list[dict],
    ops: list[dict],
    *,
    law_name: str,
    source_lines: list[str] | None = None,
    materialize: Callable[[list[dict]], list[dict]] | None = None,
) -> list[dict]:
    """按顺序应用一串编辑命令，返回新块列表（输入不变）。

    ``reserved`` 记录基线里出现过的全部 id（每步再并入当前结果），
    保证会话内新 id 永不复用已删块编号。
    ``source_lines`` 为源文件行（只读）；传了就为拆分块补行区间，不传则保持旧行为。
    ``materialize`` 是「自动分 part」的物化钩子（由 Studio 传入，避免本模块
    import autopart 造成循环）：每条命令执行后调用一次，把超长块落成真实 part，
    这样后续命令可以直接按 part id 操作、撤销重放也一致。
    """
    result = [_copy(chunk) for chunk in chunks]
    if materialize is not None:
        result = materialize(result)
    reserved = {str(chunk.get("id")) for chunk in (chunks or [])}
    for position, op in enumerate(ops or []):
        if not isinstance(op, dict):
            raise OpError(f"第 {position + 1} 条编辑命令不是 JSON 对象")
        kind = str(op.get("op") or "")
        handler = HANDLERS.get(kind)
        if handler is None:
            raise OpError(f"未知的编辑命令：{kind!r}（可选 {sorted(HANDLERS)}）")
        try:
            result = handler(result, op, law_name, reserved, source_lines)
        except OpNotFound as exc:
            raise OpNotFound(f"第 {position + 1} 条命令（{kind}）失败：{exc}") from None
        except OpError as exc:
            raise OpError(f"第 {position + 1} 条命令（{kind}）失败：{exc}") from None
        if materialize is not None:
            result = materialize(result)
        reserved |= {str(chunk.get("id")) for chunk in result}
    return result


def _diff_sig(chunk: dict) -> tuple[str, str, str]:
    """用于 LCS 配对的「块内容签名」：正文 + 条号 + 类型。"""
    return (
        normalize_text(chunk.get("chunk")),
        normalize_text(chunk.get("law_num")),
        str(chunk.get("chunk_kind") or ""),
    )


def _diff_meta(chunk: dict) -> tuple[str, str]:
    """次级配对键：条号 + 类型（正文被改写但条号没动时用）。"""
    return (
        normalize_text(chunk.get("law_num")),
        str(chunk.get("chunk_kind") or ""),
    )


def chunk_diff(baseline: list[dict], current: list[dict]) -> dict[str, Any]:
    """对比基线：新增 / 修改 / 删除的数量与 id 列表。

    因为「新增切片」会按原文位置重排 id（插入点之后的块 id 全部顺延），
    不能再用 id 集合直接比对（会把整段顺延误报成删+增）。这里按内容做
    LCS 配对：内容相同的块视为未动，再按「条号+类型」和「正文相似度」
    把剩余块配成修改，真正多出来的才算新增/删除。
    """
    base = list(baseline or [])
    cur = list(current or [])
    base_used = [False] * len(base)
    cur_used = [False] * len(cur)
    modified: list[str] = []

    matcher = SequenceMatcher(
        a=[_diff_sig(chunk) for chunk in base],
        b=[_diff_sig(chunk) for chunk in cur],
        autojunk=False,
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            continue
        for offset in range(i2 - i1):
            base_used[i1 + offset] = True
            cur_used[j1 + offset] = True

    # 第二阶段：正文改了但条号/类型没动 → 修改配对
    meta_index: dict[tuple[str, str], list[int]] = {}
    for index in range(len(base)):
        if not base_used[index]:
            meta_index.setdefault(_diff_meta(base[index]), []).append(index)

    rest_cur: list[int] = []
    for index in range(len(cur)):
        if cur_used[index]:
            continue
        bucket = meta_index.get(_diff_meta(cur[index]))
        if bucket:
            base_index = bucket.pop(0)
            base_used[base_index] = True
            cur_used[index] = True
            modified.append(str(cur[index].get("id")))
        else:
            rest_cur.append(index)

    # 第三阶段：条号/类型也改了但正文大体没动 → 按相似度兜底（数量大时跳过，避免卡顿）
    rest_base = [index for index in range(len(base)) if not base_used[index]]
    if rest_base and rest_cur and len(rest_base) * len(rest_cur) <= 2500:
        pairs: list[tuple[float, int, int]] = []
        for base_index in rest_base:
            left = normalize_text(base[base_index].get("chunk"))
            if not left:
                continue
            for cur_index in rest_cur:
                right = normalize_text(cur[cur_index].get("chunk"))
                if not right:
                    continue
                ratio = SequenceMatcher(None, left, right, autojunk=False).ratio()
                if ratio >= 0.5:
                    pairs.append((ratio, base_index, cur_index))
        pairs.sort(reverse=True)
        for _, base_index, cur_index in pairs:
            if base_used[base_index] or cur_used[cur_index]:
                continue
            base_used[base_index] = True
            cur_used[cur_index] = True
            modified.append(str(cur[cur_index].get("id")))

    added = [str(cur[index].get("id")) for index in range(len(cur)) if not cur_used[index]]
    removed = [str(base[index].get("id")) for index in range(len(base)) if not base_used[index]]
    return {
        "added": len(added),
        "modified": len(modified),
        "removed": len(removed),
        "added_ids": added,
        "modified_ids": modified,
        "removed_ids": removed,
    }
