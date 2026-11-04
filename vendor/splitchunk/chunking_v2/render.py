# -*- coding: utf-8 -*-
"""fac_rag.chunking_v2.render —— 第④步：装盒（技术版 §8）。

M2 的装盒规则与 v1 :func:`fac_rag.chunking.postprocess.build_chunks` **逐字节
等价**（这是 L1 等价验收的地基）：

* 清洗：图片链接 / ``<img>`` → ``[图示]``，逐行 strip，丢空行；
* 去重：按 ``chunk_hash`` 先见先留（同文档里同文块只留第一块，warning 记被丢者）；
* part 拆分：一律 ``split_text(text, max_chars)``，单行超长才硬切；
  表格块不二次拆（表格渲染时已按行窗口定好）；
* id：``{law_name}-{seq:04d}``，多 part 加 ``-p{i}``；``seq`` 取 ``unit.meta["seq"]``
  （条文式=条号、标准式=原文流水号，与 v1 完全一致）；
* 块本体只出 15 个契约字段（D-040），``extras`` 单独以 ``id → extras`` 返回，
  由 emitter 写 ``_extras.jsonl`` 侧车。

另外把 v1 的 ``check_sane`` / ``summarize`` 一并搬过来（含 0 块抛错语义），
只给 ``is_law_num_parseable`` 加了 ``附 录 A`` 形态（D-042 relabel 后的 law_num）。
"""

from __future__ import annotations

from .patterns import RE_CONT as RE_CONTINUATION, RE_MD_BOLD

import re
from typing import Any, Iterable

from . import contracts

from .model import ChunkingError, ParseResult, Unit

RE_MD_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
RE_IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
IMAGE_PLACEHOLDER = "[图示]"

#: §8 清洗规则（profile.render.clean 里的名字 → 正则/替换）
RE_PAIRED_TAG = re.compile(r"<([A-Za-z][A-Za-z0-9-]*)\b[^>]*>.*?</\1\s*>", re.DOTALL)
RE_PAGE_TAIL = re.compile(
    # 只认「空白 + 点线/省略号 + 页码」这种目录/页脚形态；要求点线前有空白，
    # 避免把公式编号「L＞v（t₁＋t₂）……………………（1）」当成页码删掉。
    r"(?<=[ \t　])(?:\.{2,}|…{2,}|⋯{2,}|．{2,}|·{2,})\s*"
    r"(?:[（(]\s*\d{1,4}\s*[)）]|\d{1,4}|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]{1,4})\s*$"
)
LATEX_ESCAPES: tuple[tuple[str, str], ...] = (
    (r"\(", "（"),
    (r"\)", "）"),
    (r"\[", "【"),
    (r"\]", "】"),
)
CLEAN_RULES = (
    "latex_escapes",
    "md_bold",
    "html_tags",
    "page_tail",
    "continuation_markers",
)

DEFAULT_CLEAN_RULES: tuple[str, ...] = ("latex_escapes",)

DEFAULT_MAX_CHUNK_CHARS = 800

RE_ARTICLE_NO = re.compile(r"^第[零一二三四五六七八九十百千两0-9]+条$")
RE_STD_CLAUSE = re.compile(r"^(?:[A-Z]\.)?\d+(?:\.\d+)*$")
RE_TABLE_ROW = re.compile(
    r"^(?:(?:(?:[A-Z]\.)?\d+(?:\.\d+)*|附\s*录\s*[A-Z])\s+)?表\s*[0-9A-Z]+(?:\.[0-9]+)?(?:[-—–]\d+)?"
    r"(?:\s+第\s*\d+(?:\s*-\s*\d+)?\s*行|\s+注\s*\d+)?$"
)
#: D-042 relabel 后的附录引用键：「附 录 A」；D4b 附录表块：「附 录 A 表A.1」
RE_APPENDIX_NO = re.compile(r"^附\s*录\s*[A-Z](?:\s+表\s*\S+)?$")


# ---------------------------------------------------------------------------
# 清洗 / 拆分（v1 postprocess.py 等价移植）
# ---------------------------------------------------------------------------


def strip_media(line: str) -> str:
    line = RE_MD_IMG.sub(IMAGE_PLACEHOLDER, line)
    line = RE_IMG_TAG.sub(IMAGE_PLACEHOLDER, line)
    return line


def clean_lines(lines: Iterable[str]) -> list[str]:
    out: list[str] = []
    for raw in lines:
        line = strip_media(str(raw)).strip()
        if line:
            out.append(line)
    return out


def clean_text(text: str, rules: Iterable[str] | None = None) -> str:
    r"""按 §8 规则清洗块文本（M2 只启用 latex_escapes，其余规则留 M3 与表格一起启用）。

    * ``latex_escapes``：``\(``→``（``、``\)``→``）``、``\[``→``【``、``\]``→``】``；
    * ``md_bold``：删 ``**``；
    * ``html_tags``：只删**成对**标签（``<b>…</b>``），保留比较符裸 ``<``/``>``；
    * ``page_tail``：删行尾点线页码 / 括号页码；
    * ``continuation_markers``：删行尾「（续）」。
    """
    selected = tuple(rules) if rules else ()
    unknown = [rule for rule in selected if rule not in CLEAN_RULES]
    if unknown:
        raise ValueError(f"未知的 render.clean 规则：{unknown}（可用：{list(CLEAN_RULES)}）")
    out = str(text)
    lines = out.split("\n")
    cleaned: list[str] = []
    for line in lines:
        if "latex_escapes" in selected:
            for old, new in LATEX_ESCAPES:
                line = line.replace(old, new)
        if "md_bold" in selected:
            line = RE_MD_BOLD.sub("", line)
        if "html_tags" in selected:
            line = RE_PAIRED_TAG.sub("", line)
        if "page_tail" in selected and "=" not in line:
            # 含「=」的是公式行（如「h = m / 20 ……（1）」「S = q₁/Q₁ + … ≥ 1 ……（1）」），
            # 行尾「（1）」是公式编号而不是页码，不删。
            line = RE_PAGE_TAIL.sub("", line).rstrip()
        if "continuation_markers" in selected:
            line = RE_CONTINUATION.sub("", line).rstrip()
        cleaned.append(line)
    return "\n".join(cleaned)


def _hard_split(text: str, max_chars: int) -> list[str]:
    parts: list[str] = []
    rest = text
    while len(rest) > max_chars:
        window = rest[:max_chars]
        cut = max(window.rfind("。"), window.rfind("；"), window.rfind("，"))
        cut = cut + 1 if cut >= max_chars // 2 else max_chars
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        parts.append(rest)
    return parts


#: Phase 2 默认切分点优先级（高 → 低）：段落 → 换行 → 中文句末标点 → 分句标点。
DEFAULT_SEPARATORS: tuple[str, ...] = (
    "\n\n",
    "\n",
    "。",
    "！",
    "？",
    "；",
    "：",
    "，",
    "、",
    " ",
)

#: 代码围栏起始行（``` 或 ~~~，3 个以上）
RE_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


def _fence_segments(text: str) -> list[tuple[bool, str]]:
    """按 ``` / ~~~ 围栏把文本切段，返回 ``[(is_fence, 段文本)]``。

    未闭合的围栏一直算到文末（按围栏段处理），保证不会被切成半截。
    """
    segments: list[tuple[bool, str]] = []
    buf: list[str] = []
    fence: str | None = None
    for line in text.split("\n"):
        match = RE_FENCE.match(line)
        if fence is None and match:
            if buf:
                segments.append((False, "\n".join(buf)))
                buf = []
            fence = match.group(1)
            buf.append(line)
        elif fence is not None and match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
            buf.append(line)
            segments.append((True, "\n".join(buf)))
            buf = []
            fence = None
        else:
            buf.append(line)
    if buf:
        segments.append((fence is not None, "\n".join(buf)))
    return segments


def _cut_point(text: str, max_chars: int, seps: Iterable[str]) -> int:
    """在 ``text[:max_chars]`` 里按优先级找切点（>= 半窗才认），找不到返回 max_chars。"""
    window = text[:max_chars]
    half = max_chars // 2
    for sep in seps:
        idx = window.rfind(sep)
        if idx >= half:
            return idx + len(sep)
    return max_chars


def _split_plain(
    text: str,
    max_chars: int,
    soft_max_chars: int,
    seps: tuple[str, ...],
) -> list[str]:
    """按分隔符优先级递归切分 + 打包成块（Phase 2 高级路径，默认参数不走这里）。"""
    body = text.strip("\n")
    if not body.strip():
        return []
    if len(body) <= max_chars:
        return [body]
    soft = min(soft_max_chars, max_chars) if soft_max_chars else 0

    for index, sep in enumerate(seps):
        if not sep or sep not in body:
            continue
        raw = body.split(sep)
        pieces = [part + sep for part in raw[:-1]]
        if raw[-1]:
            pieces.append(raw[-1])
        out: list[str] = []
        pack: list[str] = []
        size = 0
        for piece in pieces:
            if len(piece) > max_chars:
                if pack:
                    out.append("".join(pack))
                    pack, size = [], 0
                out.extend(_split_plain(piece, max_chars, 0, seps[index + 1:]))
                continue
            if pack and (size + len(piece) > max_chars or (soft and size >= soft)):
                out.append("".join(pack))
                pack, size = [], 0
            pack.append(piece)
            size += len(piece)
        if pack:
            out.append("".join(pack))
        return [item.strip() for item in out if item.strip()]

    # 没有任何分隔符可用：按标点优先硬切，再不行按字符硬切。
    out = []
    rest = body
    while len(rest) > max_chars:
        cut = _cut_point(rest, max_chars, ("。", "；", "，", "、", " "))
        out.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        out.append(rest)
    return [item for item in out if item]


def _wrap_fence(segment: str, max_chars: int, seps: tuple[str, ...]) -> list[str]:
    """超长代码围栏：按内容重打成多块，每块自带围栏起止行（仍是合法 md）。"""
    lines = segment.split("\n")
    if len(lines) <= 2:  # 只有起止行（空围栏）或单行围栏
        return [segment] if len(segment) <= max_chars else _split_plain(segment, max_chars, 0, seps)
    open_line, close_line = lines[0], lines[-1]
    body = "\n".join(lines[1:-1])
    budget = max_chars - len(open_line) - len(close_line) - 2
    if budget < 20:  # 围栏行本身极长：放弃围栏语法，退化为普通切分
        return _split_plain(segment, max_chars, 0, seps)
    pieces = _split_plain(body, budget, 0, seps)
    return [f"{open_line}\n{piece}\n{close_line}" for piece in pieces]


def _combine_chunks(
    items: list[tuple[str, bool]], max_chars: int, combine_under_chars: int
) -> list[str]:
    """小段回吸：短于阈值的相邻块尽量合并（不合并围栏块，避免破坏围栏）。"""
    out: list[tuple[str, bool]] = []
    for text, is_fence in items:
        if (
            out
            and not is_fence
            and not out[-1][1]
            and len(out[-1][0]) + 1 + len(text) <= max_chars
            and (len(out[-1][0]) < combine_under_chars or len(text) < combine_under_chars)
        ):
            out[-1] = (out[-1][0] + "\n" + text, False)
        else:
            out.append((text, is_fence))
    return [text for text, _ in out]


def split_text(
    text: str,
    max_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    *,
    soft_max_chars: int = 0,
    combine_under_chars: int = 0,
    separators: Iterable[str] = (),
    protect_fences: bool = False,
) -> list[str]:
    """超长文本拆块。

    默认参数走**旧路径**（逐行打包 + 单行超长按「。；，」硬切），与 v1/v2
    Phase 1 逐字节等价。只有显式给定 Phase 2 选项时才走新的分隔符优先级 /
    软上限 / 小段回吸 / 围栏保护路径。
    """
    if not (soft_max_chars or combine_under_chars or separators or protect_fences):
        if len(text) <= max_chars:
            return [text]
        parts: list[str] = []
        pack: list[str] = []
        size = 0
        for line in text.split("\n"):
            if len(line) > max_chars:
                if pack:
                    parts.append("\n".join(pack))
                    pack, size = [], 0
                parts.extend(_hard_split(line, max_chars))
                continue
            if size + len(line) + 1 > max_chars and pack:
                parts.append("\n".join(pack))
                pack, size = [], 0
            pack.append(line)
            size += len(line) + 1
        if pack:
            parts.append("\n".join(pack))
        return parts

    seps = tuple(s for s in separators if s) or DEFAULT_SEPARATORS
    items: list[tuple[str, bool]] = []
    segments = _fence_segments(text) if protect_fences else [(False, text)]
    for is_fence, segment in segments:
        if not segment.strip():
            continue
        if is_fence:
            for piece in _wrap_fence(segment, max_chars, seps):
                items.append((piece, True))
        else:
            for piece in _split_plain(segment, max_chars, soft_max_chars, seps):
                items.append((piece, False))
    if combine_under_chars:
        return _combine_chunks(items, max_chars, combine_under_chars)
    return [piece for piece, _ in items]


# ---------------------------------------------------------------------------
# Unit → 契约块
# ---------------------------------------------------------------------------


def render_units(
    parsed: ParseResult,
    *,
    law_name: str,
    law_invalid: str = "",
    source: str = "",
    law_type: str = "",
    law_level: str = "",
    issuer: str = "",
    effective_date: str = "",
    default_kind: str = "clause",
    max_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    soft_max_chars: int = 0,
    combine_under_chars: int = 0,
    separators: Iterable[str] = (),
    protect_fences: bool = False,
    dedupe: bool = True,
    clean_rules: Iterable[str] | None = DEFAULT_CLEAN_RULES,
    split_override: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """把解析出的 Unit 装成 15 字段契约块 + ``id → extras`` 侧车字典。

    返回 ``(chunks, extras_by_id)``；``extras_by_id`` 与 ``chunks`` 的 id 一一对应。

    ``split_override``（P1 尺寸自适应）：``{unit.kind: {max_chars, soft_max_chars,
    combine_under_chars, separators}}``。给了就按 kind 覆盖默认切分参数；不给时
    与旧行为逐字节等价。表块本来就不二次切，忽略该表的配置。
    """
    chunks: list[dict[str, Any]] = []
    extras_by_id: dict[str, dict[str, Any]] = {}
    prepared: list[tuple[Unit, str, str]] = []
    seen: dict[str, Unit] = {}
    #: 正文重复去重记录（D-072）：逐条记会顶爆 gate 的 warnings 阈值（DB37/T 2464
    #: 原文本身有 7 处重复句 → warnings 8>5 假红），这里先收集、末尾聚合成 1 条。
    dedupe_drops: list[tuple[str, str]] = []

    for unit in parsed.units:
        lines = clean_lines(unit.lines)
        kind = unit.kind or default_kind
        if not lines and kind == "clause":
            kind = "stub"
        text = "\n".join(lines) or "（本条无正文）"
        if clean_rules:
            text = clean_text(text, clean_rules)

        if dedupe:
            digest = contracts.chunk_hash(text)
            if digest in seen:
                other = seen[digest]
                dedupe_drops.append((unit.ref, other.ref))
                continue
            seen[digest] = unit
        prepared.append((unit, text, kind))

    if dedupe_drops:
        sample = "、".join(f"{ref} 与 {keep}" for ref, keep in dedupe_drops[:3])
        more = " 等" if len(dedupe_drops) > 3 else ""
        parsed.warnings.append(
            f"正文相同去重 {len(dedupe_drops)} 处，已丢弃后出现者（{sample}{more}）"
        )

    for unit, text, kind in prepared:
        override = (split_override or {}).get(kind) or {}
        unit_max = int(override.get("max_chars", max_chars))
        unit_soft = int(override.get("soft_max_chars", soft_max_chars))
        unit_combine = int(override.get("combine_under_chars", combine_under_chars))
        unit_separators = tuple(override.get("separators", separators) or ())
        parts = (
            [text]
            if kind == "table"
            else split_text(
                text,
                unit_max,
                soft_max_chars=unit_soft,
                combine_under_chars=unit_combine,
                separators=unit_separators,
                protect_fences=protect_fences,
            )
        )
        sequence = int(unit.meta.get("seq", 0)) or 0
        base_id = f"{law_name}-{sequence:04d}"
        for index, part in enumerate(parts, start=1):
            cid = base_id if len(parts) == 1 else f"{base_id}-p{index}"
            chunk = contracts.Chunk(
                id=cid,
                chunk=part,
                law_name=law_name,
                law_num=unit.ref,
                law_invalid=law_invalid or contracts.DEFAULT_LAW_INVALID,
                law_type=law_type,
                law_level=law_level,
                issuer=issuer,
                effective_date=effective_date,
                chunk_kind=kind,
                source=source,
                part_index=index,
                part_total=len(parts),
                extras={},
            ).finalize()
            chunks.append(chunk.to_dict())
            # 侧车 extras：v1 的单元附加信息 + region/appendix（D-040 / D-042）
            extras = {key: value for key, value in dict(unit.meta).items() if key != "seq"}
            if "region" not in extras:
                extras["region"] = unit.region
            # Phase 4：检索增强字段（只进 _extras.jsonl 侧车，不进 15 字段契约，
            # 不参与 chunk_hash）。header_path=章节面包屑；embed_text=面包屑+正文，
            # 供向量化时带上层级上下文。
            header_path = " > ".join(str(item) for item in (getattr(unit, "path", ()) or ()))
            if not header_path and kind == "table":
                # 表单元的 path 为空：用「所属条款 + 表号表题」拼面包屑，
                # 与表块正文首行（_table_breadcrumb）同构，检索侧可直接对齐。
                label_title = (
                    f"{extras.get('table_label') or ''} "
                    f"{extras.get('table_title') or ''}"
                ).strip()
                header_path = " · ".join(
                    item
                    for item in (str(extras.get("clause") or ""), label_title)
                    if item
                )
            extras["header_path"] = header_path
            # 标题/表块正文本身已带面包屑时不再重复前缀（part 以 header_path 开头即视为已带）。
            head = part.strip()
            already = head == header_path.strip() or head.startswith(
                header_path.strip() + "\n"
            )
            extras["embed_text"] = (
                part if not header_path or already else f"{header_path}\n{part}"
            )
            extras_by_id[cid] = extras

    # Phase 4：相邻块 id 链（检索时可整段回拼，不必依赖落盘顺序扫描）。
    for index, chunk in enumerate(chunks):
        extras = extras_by_id.get(str(chunk.get("id") or ""))
        if extras is None:
            continue
        extras["neighbor_prev"] = (
            str(chunks[index - 1].get("id", "")) if index > 0 else ""
        )
        extras["neighbor_next"] = (
            str(chunks[index + 1].get("id", "")) if index + 1 < len(chunks) else ""
        )
    return chunks, extras_by_id


# ---------------------------------------------------------------------------
# 体检与报表（v1 postprocess.py 等价移植 + 附录 law_num 形态）
# ---------------------------------------------------------------------------


def is_law_num_parseable(law_num: str) -> bool:
    text = str(law_num or "").strip()
    if not text:
        return False
    return bool(
        RE_ARTICLE_NO.match(text)
        or RE_STD_CLAUSE.match(text)
        or RE_TABLE_ROW.search(text)
        or RE_APPENDIX_NO.match(text)
    )


def check_sane(chunks: list[dict[str, Any]], *, what: str = "这份文件", min_ratio: float = 0.5) -> dict:
    total = len(chunks)
    parseable = sum(1 for c in chunks if is_law_num_parseable(c.get("law_num", "")))
    ratio = (parseable / total) if total else 0.0
    stats = {"n_chunks": total, "n_law_num_parseable": parseable, "law_num_ratio": ratio}

    if total == 0:
        raise ChunkingError(f"{what} 一块都没切出来——检查是否放错文件夹，或文本里没有可识别的条款号")
    if ratio < min_ratio:
        sample = ", ".join(str(c.get("law_num", "")) for c in chunks[:3])
        raise ChunkingError(
            f"{what} 的条款号只有 {ratio:.0%} 能识别（样例：{sample}）——"
            "多半是放错文件夹（切块刀与文件格式不匹配）"
        )
    return stats


def summarize(chunks: list[dict[str, Any]], parsed: ParseResult, source_chars: int) -> dict:
    chars = sum(len(c.get("chunk", "")) for c in chunks)
    kinds: dict[str, int] = {}
    for item in chunks:
        kinds[item.get("chunk_kind", "")] = kinds.get(item.get("chunk_kind", ""), 0) + 1
    parts = sum(1 for c in chunks if int(c.get("part_total", 1)) > 1)
    return {
        "n_chunks": len(chunks),
        "n_units": len(parsed.units),
        "n_parts_extra": parts,
        "chars_in_chunks": chars,
        "chars_in_source": source_chars,
        "char_coverage": round(chars / source_chars, 4) if source_chars else 0.0,
        "kinds": kinds,
        "n_gaps": len(parsed.gaps),
        "n_warnings": len(parsed.warnings),
        "max_chunk_chars": max((len(c.get("chunk", "")) for c in chunks), default=0),
        "over_max_chars": sum(
            1 for c in chunks if int(c.get("token_len", 0)) > DEFAULT_MAX_CHUNK_CHARS
        ),
    }


__all__ = [
    "CLEAN_RULES",
    "DEFAULT_CLEAN_RULES",
    "check_sane",
    "clean_lines",
    "clean_text",
    "is_law_num_parseable",
    "render_units",
    "split_text",
    "strip_media",
    "summarize",
]
