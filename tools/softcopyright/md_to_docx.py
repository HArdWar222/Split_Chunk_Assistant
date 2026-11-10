# -*- coding: utf-8 -*-
"""把 03/04 两份 markdown 转成便于修改的 .docx（无页码/页眉要求，仅供编辑）。

用法：
    python tools/softcopyright/md_to_docx.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import layout  # noqa: E402
from docx.enum.table import WD_TABLE_ALIGNMENT  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING  # noqa: E402
from docx.oxml import OxmlElement  # noqa: E402
from docx.shared import Cm, Pt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "软著申请材料"
JOBS = [
    ("03_软著登记信息表.md", "03_软著登记信息表.docx", "软著登记信息表"),
    ("04_校验报告.md", "04_校验报告.docx", "软著鉴别材料校验报告"),
]

BODY_PT = 11.0
LINE_PT = 20.0


def clean(text: str) -> str:
    text = text.replace("**", "")
    text = re.sub(r"`([^`]*)`", r"\1", text)
    return text.strip()


def join_lines(parts: list[str]) -> str:
    """合并 markdown 软换行：中文之间直接相连，中西文之间补一个空格。"""
    out = ""
    for seg in parts:
        if out and seg:
            a, b = out[-1], seg[0]
            latin_a = a.isascii() and (a.isalnum() or a in ".,;:)]}%")
            if latin_a and not b.isascii():
                out += " "
        out += seg
    return out


def restyle_heading(doc, name: str, size: float, ea_font: str = "黑体") -> None:
    """内置标题样式改成黑色中文外观（默认模板是蓝色主题色），保留大纲级别。"""
    from docx.oxml.ns import qn
    from docx.shared import RGBColor

    st = doc.styles[name]
    st.font.size = Pt(size)
    st.font.bold = True
    st.font.italic = False
    st.font.color.rgb = RGBColor(0, 0, 0)
    rpr = st.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    rfonts.set(qn("w:ascii"), "Times New Roman")
    rfonts.set(qn("w:hAnsi"), "Times New Roman")
    rfonts.set(qn("w:eastAsia"), ea_font)
    rfonts.set(qn("w:cs"), "Times New Roman")


def parse(md: str) -> list[tuple[str, object]]:
    blocks: list[tuple[str, object]] = []
    lines = md.splitlines()
    i, n = 0, len(lines)
    while i < n:
        line = lines[i].rstrip()
        if not line.strip() or line.strip() in ("---", "***"):
            i += 1
            continue
        if line.startswith("### "):
            blocks.append(("h3", clean(line[4:])))
            i += 1
        elif line.startswith("## "):
            blocks.append(("h2", clean(line[3:])))
            i += 1
        elif line.startswith("# "):
            blocks.append(("h1", clean(line[2:])))
            i += 1
        elif line.startswith("```"):
            i += 1
            code: list[str] = []
            while i < n and not lines[i].startswith("```"):
                code.append(lines[i].rstrip())
                i += 1
            i += 1
            if code:
                blocks.append(("code", code))
        elif line.startswith("> "):
            blocks.append(("quote", clean(line[2:])))
            i += 1
        elif line.startswith("|"):
            rows: list[list[str]] = []
            while i < n and lines[i].lstrip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    rows.append([clean(c) for c in cells])
                i += 1
            if rows:
                blocks.append(("table", rows))
        elif re.match(r"^\s*[-*]\s+", line):
            blocks.append(("li", clean(re.sub(r"^\s*[-*]\s+", "", line))))
            i += 1
        else:
            buf = [clean(line)]
            i += 1
            while i < n and lines[i].strip() and not re.match(r"^\s*(#|\||[-*]\s)", lines[i]):
                buf.append(clean(lines[i]))
                i += 1
            blocks.append(("p", join_lines(buf)))
    return blocks


def para(doc, text: str, size: float, bold: bool = False, ea_font: str = "宋体",
         align=WD_ALIGN_PARAGRAPH.JUSTIFY, first_indent: float = 0.0,
         before: float = 0.0, after: float = 0.0, style: str | None = None):
    p = doc.add_paragraph(style=style) if style else doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    pf.line_spacing = Pt(LINE_PT)
    pf.first_line_indent = Pt(first_indent)
    pf.alignment = align
    pf.widow_control = False
    if text:
        layout.set_run_font(p.add_run(text), "Times New Roman", ea_font, size, bold=bold)
    return p


def add_table(doc, rows: list[list[str]], width_cm: float) -> None:
    cols = max(len(r) for r in rows)
    rows = [r + [""] * (cols - len(r)) for r in rows]
    table = doc.add_table(rows=len(rows), cols=cols)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    for ri, row in enumerate(rows):
        for ci, text in enumerate(row):
            cell = table.rows[ri].cells[ci]
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
            p.paragraph_format.line_spacing = Pt(15)
            layout.set_run_font(p.add_run(text), "Times New Roman", "宋体", 9.5,
                                bold=(ri == 0))
    tail = doc.add_paragraph()
    tail.paragraph_format.space_before = Pt(0)
    tail.paragraph_format.space_after = Pt(0)
    tail.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    tail.paragraph_format.line_spacing = Pt(4)


def convert(md_path: Path, out: Path, title: str) -> Path:
    blocks = parse(md_path.read_text(encoding="utf-8"))
    doc = layout.new_document(default_ea_font="宋体", default_size=BODY_PT)
    sec = doc.sections[0]
    layout.setup_page(sec, 2.4, 2.2, 2.6, 2.4)
    width_cm = layout.content_width_cm(sec)

    para(doc, title, 16, bold=True, ea_font="黑体", align=WD_ALIGN_PARAGRAPH.CENTER,
         after=10)
    restyle_heading(doc, "Heading 1", 15)
    restyle_heading(doc, "Heading 2", 12.5)
    restyle_heading(doc, "Heading 3", 11, ea_font="宋体")

    for kind, content in blocks:
        if kind == "h1":
            para(doc, str(content), 15, bold=True, ea_font="黑体",
                 align=WD_ALIGN_PARAGRAPH.LEFT, before=10, after=4, style="Heading 1")
        elif kind == "h2":
            para(doc, str(content), 12.5, bold=True, ea_font="黑体",
                 align=WD_ALIGN_PARAGRAPH.LEFT, before=8, after=3, style="Heading 2")
        elif kind == "h3":
            para(doc, str(content), 11, bold=True,
                 align=WD_ALIGN_PARAGRAPH.LEFT, before=4, after=2, style="Heading 3")
        elif kind == "li":
            p = para(doc, "·　" + str(content), BODY_PT, align=WD_ALIGN_PARAGRAPH.JUSTIFY)
            p.paragraph_format.left_indent = Pt(BODY_PT * 2)
            p.paragraph_format.first_line_indent = Pt(-BODY_PT)
        elif kind == "quote":
            p = para(doc, str(content), 10.5, align=WD_ALIGN_PARAGRAPH.JUSTIFY)
            p.paragraph_format.left_indent = Pt(BODY_PT * 2)
        elif kind == "table":
            add_table(doc, content, width_cm)   # type: ignore[arg-type]
        elif kind == "code":
            for ln in content:   # type: ignore[union-attr]
                p = para(doc, ln, 9.5, align=WD_ALIGN_PARAGRAPH.LEFT)
                for r in p.runs:
                    layout.set_run_font(r, "Consolas", "宋体", 9.5)
        else:
            para(doc, str(content), BODY_PT, first_indent=BODY_PT * 2)

    doc.save(str(out))
    return out


def main() -> None:
    for md_name, docx_name, title in JOBS:
        out = convert(OUT_DIR / md_name, OUT_DIR / docx_name, title)
        print(f"[转换] {md_name} → {out.name}", flush=True)


if __name__ == "__main__":
    main()
