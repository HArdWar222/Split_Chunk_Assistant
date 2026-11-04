"""units.statute：第X条（statute handler）。"""

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

from .common import (
    RE_CHAPTER,
    RE_LAW_TOC_HEAD,
    RE_SECTION,
    clean_statute_line,
)

def parse_statute(text: str, *, law_name: str = "", profile: Profile | None = None) -> ParseResult:
    result = ParseResult()
    cur: Unit | None = None
    last_no = 0
    chapter, section = "", ""
    in_toc = False

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            result.skipped_lines += 1
            continue

        if RE_LAW_TOC_HEAD.match(line):
            in_toc = True
            result.skipped_lines += 1
            continue

        if line.startswith("#"):
            in_toc = False
            result.skipped_lines += 1
            match = RE_CHAPTER.match(line)
            if match:
                chapter = clean_statute_line(f"第{match.group(1)}章　{match.group(2)}")
                section = ""
                continue
            match = RE_SECTION.match(line)
            if match:
                section = clean_statute_line(f"第{match.group(1)}节　{match.group(2)}")
                continue
            match = RE_LAW_TITLE.match(line)
            if match:
                result.title = clean_statute_line(match.group(1))
                continue
            continue

        if in_toc:
            result.skipped_lines += 1
            continue

        if line.startswith(">"):
            result.skipped_lines += 1
            continue

        match = RE_ARTICLE.match(line)
        if match:
            number = cn2num(match.group(1))
            if number is not None and number > last_no:
                if last_no and number != last_no + 1:
                    result.gaps.append(f"第{last_no}条 之后直接跳到 第{number}条")
                if cur is not None:
                    result.units.append(cur)
                head = clean_statute_line(match.group(2))
                path = tuple(part for part in (chapter, section) if part)
                cur = Unit(
                    kind="clause",
                    ref=f"第{match.group(1)}条",
                    title="",
                    region="body",
                    path=path,
                    lines=[head] if head else [],
                    table=None,
                    meta={"seq": number, "num_cn": match.group(1)},
                )
                last_no = number
                continue

        if cur is None:
            result.skipped_lines += 1
            continue
        body = clean_statute_line(line)
        if body:
            cur.lines.append(body)
        else:
            result.skipped_lines += 1

    if cur is not None:
        result.units.append(cur)
    return result
