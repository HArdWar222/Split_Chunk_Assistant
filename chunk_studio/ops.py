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
- ``{"op": "drop", "id": "..."}``
- ``{"op": "add", "text": "...", "law_num": "...", "chunk_kind": "paragraph", "after_id": "..." | null}``
- ``{"op": "patch", "id": "...", "chunk": "...", "law_num": "...", "chunk_kind": "..."}``（都可选）
"""

from __future__ import annotations

import copy
import hashlib
import re
from typing import Any

from .align import locate_text_span, norm_line

RE_ID = re.compile(r"^.+-(\d{4,})(?:-p(\d+))?$")

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
    if span_a and span_b and not merged["manual"]:
        merged["span"] = [min(int(span_a[0]), int(span_b[0])), max(int(span_a[1]), int(span_b[1]))]
        merged["align"] = "approx"
    else:
        merged["span"] = None
        merged["align"] = "manual"
        merged["manual"] = True

    return chunks[:index] + [merged] + chunks[other + 1 :]


def _drop(chunks: list[dict], op: dict, law_name: str, reserved=None, source_lines=None) -> list[dict]:
    index = find_index(chunks, op.get("id"))
    return chunks[:index] + chunks[index + 1 :]


def _add(chunks: list[dict], op: dict, law_name: str, reserved=None, source_lines=None) -> list[dict]:
    text = str(op.get("text") or "")
    kind = str(op.get("chunk_kind") or "paragraph").strip() or "paragraph"
    if kind not in CHUNK_KINDS:
        raise OpError(f"chunk_kind 非法：{kind!r}（可选 {sorted(CHUNK_KINDS)}）")
    law_num = str(op.get("law_num") or "").strip() or NEW_LAW_NUM

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

    after_id = op.get("after_id")
    if after_id is None:
        return chunks + [new]
    index = find_index(chunks, after_id)
    return chunks[: index + 1] + [new] + chunks[index + 1 :]


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
    "drop": _drop,
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
) -> list[dict]:
    """按顺序应用一串编辑命令，返回新块列表（输入不变）。

    ``reserved`` 记录基线里出现过的全部 id（每步再并入当前结果），
    保证会话内新 id 永不复用已删块编号。
    ``source_lines`` 为源文件行（只读）；传了就为拆分块补行区间，不传则保持旧行为。
    """
    result = [_copy(chunk) for chunk in chunks]
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
        reserved |= {str(chunk.get("id")) for chunk in result}
    return result


def chunk_diff(baseline: list[dict], current: list[dict]) -> dict[str, Any]:
    """对比基线：新增 / 修改 / 删除的数量与 id 列表。"""
    base = {str(chunk.get("id")): chunk for chunk in baseline}
    cur = {str(chunk.get("id")): chunk for chunk in current}

    added = [cid for cid in cur if cid not in base]
    removed = [cid for cid in base if cid not in cur]
    modified = []
    compare_fields = ("chunk", "law_num", "chunk_kind", "part_index", "part_total")
    for cid, chunk in cur.items():
        original = base.get(cid)
        if original is None:
            continue
        if any(chunk.get(field) != original.get(field) for field in compare_fields):
            modified.append(cid)
    return {
        "added": len(added),
        "modified": len(modified),
        "removed": len(removed),
        "added_ids": added,
        "modified_ids": modified,
        "removed_ids": removed,
    }
