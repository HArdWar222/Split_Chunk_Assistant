"""跨模块共用的行级正则。

原来 ``probe/units/regions/render/tables/gate/detect`` 各自复制了同一批字面量，
改一处要同步改 2~3 个文件。集中到这里后，行为完全不变（字面量逐字节相同），
只保留「语义确实不同」的本地变体（如 probe 的 ``_HEAD`` 前缀版）。

约定：这里只放**纯字面量**，不放带业务语义的复合规则。
"""
from __future__ import annotations

import re

#: 任何 HTML 标签（粗粒度，用于清洗/特征统计）
RE_TAG = re.compile(r"<[^>]+>")

#: 前言 / 引言（整行匹配）
RE_FRONT = re.compile(r"^(前\s*言|引\s*言)$")

#: 参考文献 / 引用标准名录（整行匹配）
RE_REFS = re.compile(r"^参\s*考\s*文\s*献$")

#: 条文说明（整行，允许「XX（条文说明）」）
RE_COMMENTARY = re.compile(r"^(?:条文说明|.*[（(]\s*条文说明\s*[)）])$")

#: 第X章 / 第X节（行首，只判「像不像」）
RE_CHAPTERISH = re.compile(r"^第[零一二三四五六七八九十百千两0-9]+[章节]")

#: 裸「第X条 …」（无 markdown 前缀）
RE_ARTICLE = re.compile(r"^第([零一二三四五六七八九十百千两0-9]+)条[　\s]*(.*)$")

#: 十进制条款「4.1.2 正文」/ 附录「A.2.1 正文」
RE_CLAUSE = re.compile(r"^(?:([A-Z])\.)?(\d+(?:\.\d+)*)[　\s]+(.+)$")

#: 附录标题（只取字母）
RE_APPENDIX = re.compile(r"^附\s*录\s*([A-Z])")

#: 附录标题（字母 + 可选标题）
RE_APPENDIX_HEAD = re.compile(r"^附\s*录\s*([A-Z])(?:[　\s]+(.*))?$")

#: 只有「附 录 A」这一行
RE_APPENDIX_NO = re.compile(r"^附\s*录\s*[A-Z]$")

#: 一级标题行
RE_LAW_TITLE = re.compile(r"^#\s*(.+?)\s*$")

#: markdown 粗体标记
RE_MD_BOLD = re.compile(r"\*\*")

#: LaTeX 括号转义
RE_LATEX_ESCAPE = re.compile(r"\\[()\[\]]")

#: 续行/续表标记「（续）」
RE_CONT = re.compile(r"[（(]\s*续\s*[)）]\s*$")

#: 目录页码「（12）」
RE_TOC_PAGE_PAREN = re.compile(r"[（(]\s*\d{1,3}\s*[)）]\s*$")

#: 页脚/目录尾巴：点线/省略号 + 页码（v1 口径）
RE_PAGE_TAIL = re.compile(
    r"(?:\.{2,}|…+|⋯+|．{2,}|·{2,})\s*(?:[（(]\s*\d+\s*[)）]|\d+)\s*$"
)

#: markdown 表格行（行首竖线）
RE_TABLE_PIPE = re.compile(r"^\s*\|")

#: HTML 表格标记
RE_TABLE_HTML = re.compile(r"<\s*(?:table|thead|tbody|tr|td|th)\b", re.IGNORECASE)
