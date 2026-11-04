# -*- coding: utf-8 -*-
"""切块结果契约（15 字段）——切块引擎内置版本。

字段与语义按 FAC-RAG ``fac_rag.contracts`` 对齐后内置，``chunking_v2`` 不再 import
FAC-RAG；两者写出的块逐字节同构。字段分两组：

* **核心 10 字段** ``id chunk law_name law_num law_invalid law_type law_level
  issuer effective_date chunk_kind``
* **派生 5 字段** ``token_len source part_index part_total chunk_hash``

``chunk_hash`` = sha256(规范化正文) 的**前 16 位十六进制**；``law_invalid`` 空值兜底
``2030-12-12``；``to_dict()`` 输出 15 字段，``extras`` 非空时才附加（侧车口径）。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

# 没有失效日期时的兜底日期（沿用旧规矩，别改）
DEFAULT_LAW_INVALID = "2030-12-12"

#: 核心字段（顺序即落库顺序）
CORE_FIELDS: tuple[str, ...] = (
    "id",
    "chunk",
    "law_name",
    "law_num",
    "law_invalid",
    "law_type",
    "law_level",
    "issuer",
    "effective_date",
    "chunk_kind",
)

#: 派生字段
DERIVED_FIELDS: tuple[str, ...] = (
    "token_len",
    "source",
    "part_index",
    "part_total",
    "chunk_hash",
)

ALL_FIELDS: tuple[str, ...] = CORE_FIELDS + DERIVED_FIELDS

#: 合法块种类
CHUNK_KINDS: frozenset[str] = frozenset(
    {"clause", "table", "title", "front", "appendix", "stub", "paragraph"}
)

RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# id 形如「中华人民共和国特种设备安全法-0074」，拆块时会变成「…-0074-p2」
RE_ID = re.compile(r"^.+-(\d{4,})(?:-p(\d+))?$")

#: law_type 六档（D-017）
LAW_TYPES: frozenset[str] = frozenset(
    {"法律", "行政法规", "部门规章", "地方性法规", "国家标准", "行业标准"}
)


class ContractError(ValueError):
    """切块结果不符合契约。"""


def is_date(value: Any) -> bool:
    """是不是 ``YYYY-MM-DD``。"""
    return bool(RE_DATE.match(str(value or "").strip()))


def normalize_date(value: Any, default: str = "") -> str:
    """空串 / 格式不对 → 返回 ``default``（默认保持空，不做假设）。"""
    text = str(value or "").strip()
    return text if RE_DATE.match(text) else default


def estimate_token_len(text: str) -> int:
    """粗估 token 数（中文按 1 字 ≈ 1 token）。"""
    return len(str(text or ""))


def chunk_hash(text: str) -> str:
    """正文指纹（16 位十六进制），用于跨文件去重。"""
    normalized = "\n".join(line.rstrip() for line in str(text or "").strip().splitlines())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def normalize_chunk_text(text: str) -> str:
    """统一换行、去掉每行尾空白；正文内容本身不动。"""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


@dataclass
class Chunk:
    """一个切块单位。构造后调 :meth:`finalize` 再落库。"""

    id: str
    chunk: str
    law_name: str
    law_num: str
    law_invalid: str = DEFAULT_LAW_INVALID
    law_type: str = ""
    law_level: str = ""
    issuer: str = ""
    effective_date: str = ""
    chunk_kind: str = "clause"
    source: str = ""
    part_index: int = 1
    part_total: int = 1
    token_len: int = 0
    chunk_hash: str = ""
    extras: dict[str, Any] = field(default_factory=dict, repr=False)

    # ---------- 装配 ----------

    def finalize(self) -> "Chunk":
        """补齐派生字段并做一次自检；返回自己，方便链式调用。"""
        self.chunk = normalize_chunk_text(self.chunk)
        self.law_invalid = normalize_date(self.law_invalid, DEFAULT_LAW_INVALID)
        self.effective_date = normalize_date(self.effective_date, "")
        self.token_len = estimate_token_len(self.chunk)
        self.chunk_hash = chunk_hash(self.chunk)
        self.part_index = int(self.part_index or 1)
        self.part_total = int(self.part_total or 1)

        problems = validate_chunk(self.to_dict())
        if problems:
            raise ContractError(f"切块 {self.id!r} 不合契约：" + "；".join(problems))
        return self

    def to_dict(self) -> dict[str, Any]:
        """输出给数据库 / 检索层用的 dict（旧字段名保持不变）。"""
        data = {
            "id": self.id,
            "chunk": self.chunk,
            "law_name": self.law_name,
            "law_num": self.law_num,
            "law_invalid": self.law_invalid,
            "law_type": self.law_type,
            "law_level": self.law_level,
            "issuer": self.issuer,
            "effective_date": self.effective_date,
            "chunk_kind": self.chunk_kind,
            "token_len": self.token_len,
            "source": self.source,
            "part_index": self.part_index,
            "part_total": self.part_total,
            "chunk_hash": self.chunk_hash,
        }
        if self.extras:
            data["extras"] = dict(self.extras)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Chunk":
        known = set(ALL_FIELDS) | {"extras"}
        extras = {k: v for k, v in data.items() if k not in known}
        extras.update(data.get("extras") or {})
        return cls(
            id=str(data.get("id", "")),
            chunk=str(data.get("chunk", "")),
            law_name=str(data.get("law_name", "")),
            law_num=str(data.get("law_num", "")),
            law_invalid=str(data.get("law_invalid", "")),
            law_type=str(data.get("law_type", "")),
            law_level=str(data.get("law_level", "")),
            issuer=str(data.get("issuer", "")),
            effective_date=str(data.get("effective_date", "")),
            chunk_kind=str(data.get("chunk_kind", "clause")),
            source=str(data.get("source", "")),
            part_index=int(data.get("part_index", 1) or 1),
            part_total=int(data.get("part_total", 1) or 1),
            token_len=int(data.get("token_len", 0) or 0),
            chunk_hash=str(data.get("chunk_hash", "")),
            extras=extras,
        )


def validate_chunk(data: Mapping[str, Any]) -> list[str]:
    """检查一个块，返回问题清单（空清单 = 合规）。"""
    problems: list[str] = []

    missing = [name for name in CORE_FIELDS if name not in data]
    if missing:
        problems.append(f"缺字段 {missing}")

    cid = str(data.get("id", "")).strip()
    if not cid:
        problems.append("id 为空")
    elif not RE_ID.match(cid):
        problems.append(f"id 形态不对（应为「法规名-0001」或「法规名-0001-p2」）：{cid!r}")

    if not str(data.get("chunk", "")).strip():
        problems.append("chunk 为空")

    if not str(data.get("law_name", "")).strip():
        problems.append("law_name 为空")

    if not str(data.get("law_num", "")).strip():
        problems.append("law_num 为空")

    law_invalid = str(data.get("law_invalid", "")).strip()
    if law_invalid and not is_date(law_invalid):
        problems.append(f"law_invalid 不是 YYYY-MM-DD：{law_invalid!r}")

    effective = str(data.get("effective_date", "")).strip()
    if effective and not is_date(effective):
        problems.append(f"effective_date 不是 YYYY-MM-DD：{effective!r}")

    law_type = str(data.get("law_type", "")).strip()
    if law_type and law_type not in LAW_TYPES:
        problems.append(f"law_type 不在六档内：{law_type!r}（可选 {sorted(LAW_TYPES)}）")

    kind = str(data.get("chunk_kind", "")).strip()
    if kind not in CHUNK_KINDS:
        problems.append(f"chunk_kind 非法：{kind!r}（可选 {sorted(CHUNK_KINDS)}）")

    try:
        part_index, part_total = int(data.get("part_index", 1)), int(data.get("part_total", 1))
    except (TypeError, ValueError):
        problems.append("part_index / part_total 不是整数")
    else:
        if part_total < 1 or part_index < 1 or part_index > part_total:
            problems.append(f"part 编号越界：{part_index}/{part_total}")

    return problems


def validate_chunks(chunks: Iterable[Mapping[str, Any]]) -> None:
    """整批校验；有任何问题就抛 :class:`ContractError`（带上前 5 条）。"""
    all_problems: list[str] = []
    for item in chunks:
        cid = item.get("id", "?")
        for problem in validate_chunk(item):
            all_problems.append(f"{cid}: {problem}")
    if all_problems:
        head = "；".join(all_problems[:5])
        more = f"（共 {len(all_problems)} 条问题）" if len(all_problems) > 5 else ""
        raise ContractError(head + more)


def require_fields(data: Mapping[str, Any], needed: Iterable[str], *, what: str = "") -> None:
    """局部断言：某些字段必须有值（例如做精排时必须知道 law_num）。"""
    empty = [name for name in needed if not str(data.get(name, "")).strip()]
    if empty:
        raise ContractError(f"{what or '数据'} 缺字段：{empty}")


__all__ = [
    "ALL_FIELDS",
    "CHUNK_KINDS",
    "CORE_FIELDS",
    "Chunk",
    "ContractError",
    "DEFAULT_LAW_INVALID",
    "DERIVED_FIELDS",
    "LAW_TYPES",
    "chunk_hash",
    "estimate_token_len",
    "is_date",
    "normalize_chunk_text",
    "normalize_date",
    "require_fields",
    "validate_chunk",
    "validate_chunks",
]
