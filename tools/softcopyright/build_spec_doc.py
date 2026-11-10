# -*- coding: utf-8 -*-
"""把 tools/softcopyright/spec_doc.md 排成《软件设计说明书》鉴别材料（.docx / .pdf）。

用法：
    python tools/softcopyright/build_spec_doc.py [--no-pdf]

版面：A4，页边距 上 2.2 / 下 2.2 / 左 2.4 / 右 2.2 cm，正文宋体五号、固定行距，
版心约 45 行/页（满足「文档每页不少于 30 行」）；章标题不单独起页，整篇连续流排，
避免出现只有几行的稀疏页。行距由脚本自动试排，落在 32–36 页。
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import layout  # noqa: E402
import pymupdf as fitz  # noqa: E402
from docx.enum.table import WD_TABLE_ALIGNMENT  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING  # noqa: E402
from docx.oxml import OxmlElement  # noqa: E402
from docx.shared import Cm, Pt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(__file__).resolve().parent / "spec_doc.md"
OUT_DIR = ROOT / "软著申请材料"
DOCX = OUT_DIR / "02_文档鉴别材料_软件设计说明书.docx"

TOP, BOTTOM, LEFT, RIGHT = 2.2, 2.2, 2.4, 2.2
BODY_PT = 10.5        # 五号
H1_PT = 14.0
H2_PT = 11.0
TABLE_PT = 9.5
TABLE_LINE_PT = 12.0
FIRST_LINE_INDENT = BODY_PT * 2

# 试排候选行距：从宽松往紧凑排，取第一个「总页数 ≤40 且每页正文 ≥30 行」的版式
LINE_CANDIDATES = [16.0, 15.5, 15.0, 14.75, 14.5, 14.25, 14.0, 13.5]
TARGET_MIN, TARGET_MAX = 30, 40
MIN_LINES_PER_PAGE = 30

META = {
    "name": layout.SOFTWARE_NAME,
    "doc_title": "软件设计说明书",
    "version": layout.SOFTWARE_VERSION,
    "owner": "武弘正",
    "date": "2026 年 11 月 5 日",
}

_body_line = LINE_CANDIDATES[0]


# ---------------------------------------------------------------- markdown 解析
def clean(text: str) -> str:
    """去 markdown 行内标记（**粗体**、`代码`），本说明书不使用强调语法。"""
    text = text.replace("**", "")
    text = re.sub(r"`([^`]*)`", r"\1", text)
    return text.strip()


def parse(md: str) -> list[tuple[str, object]]:
    blocks: list[tuple[str, object]] = []
    lines = md.splitlines()
    i, n = 0, len(lines)
    while i < n:
        line = lines[i].rstrip()
        if not line.strip():
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
            blocks.append(("p", "".join(buf)))
    return blocks


# ---------------------------------------------------------------- 排版原语
def restyle_heading(doc, name: str, size: float, ea_font: str = "黑体") -> None:
    """把内置标题样式改成中文排版外观，保留其大纲级别（Word 导航窗格可用）。"""
    from docx.enum.style import WD_STYLE_TYPE
    from docx.oxml.ns import qn
    from docx.shared import RGBColor

    try:
        st = doc.styles[name]
    except KeyError:
        st = doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
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


def para(doc, text: str, size: float, line_pt: float, bold: bool = False,
         ea_font: str = "宋体", ascii_font: str = "Times New Roman",
         align=WD_ALIGN_PARAGRAPH.JUSTIFY, first_indent: float = 0.0,
         left_indent: float = 0.0, before: float = 0.0, after: float = 0.0,
         keep_next: bool = False, style: str | None = None):
    p = doc.add_paragraph(style=style) if style else doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    pf.line_spacing = Pt(line_pt)
    pf.first_line_indent = Pt(first_indent)
    pf.left_indent = Pt(left_indent)
    pf.alignment = align
    pf.widow_control = False
    pf.keep_with_next = keep_next
    if text:
        layout.set_run_font(p.add_run(text), ascii_font, ea_font, size, bold=bold)
    return p


def cell_text(cell, text: str, bold: bool = False) -> None:
    cell.text = ""
    p = cell.paragraphs[0]
    pf = p.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    pf.line_spacing = Pt(TABLE_LINE_PT)
    pf.alignment = WD_ALIGN_PARAGRAPH.LEFT
    layout.set_run_font(p.add_run(text), "Times New Roman", "宋体", TABLE_PT, bold=bold)


def add_table(doc, rows: list[list[str]], width_cm: float) -> None:
    cols = max(len(r) for r in rows)
    rows = [r + [""] * (cols - len(r)) for r in rows]
    table = doc.add_table(rows=len(rows), cols=cols)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    weights = []
    for c in range(cols):
        w = max(layout.disp_width(r[c]) for r in rows)
        weights.append(max(6, min(w, 46)))
    total = sum(weights)
    widths = [max(2.0, width_cm * w / total) for w in weights]
    scale = width_cm / sum(widths)
    widths = [w * scale for w in widths]

    for ri, row in enumerate(rows):
        tr = table.rows[ri]
        trpr = tr._tr.get_or_add_trPr()
        trpr.append(OxmlElement("w:cantSplit"))
        for ci, text in enumerate(row):
            cell_text(tr.cells[ci], text, bold=(ri == 0))
            tr.cells[ci].width = Cm(widths[ci])
        if ri == 0:
            trpr.append(OxmlElement("w:tblHeader"))

    tail = doc.add_paragraph()
    tail.paragraph_format.space_before = Pt(0)
    tail.paragraph_format.space_after = Pt(0)
    tail.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    tail.paragraph_format.line_spacing = Pt(4)


def build(total_pages: int, line_pt: float, out: Path = DOCX) -> Path:
    blocks = parse(SRC.read_text(encoding="utf-8"))
    doc = layout.new_document(default_ea_font="宋体", default_size=BODY_PT)
    sec = doc.sections[0]
    layout.setup_page(sec, TOP, BOTTOM, LEFT, RIGHT)
    layout.setup_header_footer(doc, sec, "文档：软件设计说明书", total_pages)
    layout.set_section_page_start(sec, 1)
    width_cm = layout.content_width_cm(sec)

    # 标题使用内置 Heading 样式，便于在 Word 里用导航窗格/大纲视图定位与修改
    restyle_heading(doc, "Heading 1", H1_PT)
    restyle_heading(doc, "Heading 2", H2_PT)
    restyle_heading(doc, "Heading 3", BODY_PT, ea_font="宋体")

    # 首页标题块与正文同页，避免出现只有几行的封面页
    para(doc, META["name"], 15, 22, bold=True, ea_font="黑体", align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc, META["doc_title"], 14, 20, bold=True, ea_font="黑体", align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc, f"版本号：{META['version']}    著作权人：{META['owner']}",
         BODY_PT, line_pt, align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc, f"开发完成日期：{META['date']}", BODY_PT, line_pt,
         align=WD_ALIGN_PARAGRAPH.CENTER, after=6)

    for kind, content in blocks:
        if kind == "h1":
            para(doc, str(content), H1_PT, line_pt + 3, bold=True, ea_font="黑体",
                 align=WD_ALIGN_PARAGRAPH.LEFT, before=9, after=3, keep_next=True,
                 style="Heading 1")
        elif kind == "h2":
            para(doc, str(content), H2_PT, line_pt, bold=True, ea_font="黑体",
                 align=WD_ALIGN_PARAGRAPH.LEFT, before=6, after=1, keep_next=True,
                 style="Heading 2")
        elif kind == "h3":
            para(doc, str(content), BODY_PT, line_pt, bold=True,
                 align=WD_ALIGN_PARAGRAPH.LEFT, before=3, after=1, keep_next=True,
                 style="Heading 3")
        elif kind == "li":
            para(doc, "·　" + str(content), BODY_PT, line_pt,
                 left_indent=BODY_PT * 2, first_indent=-BODY_PT)
        elif kind == "table":
            add_table(doc, content, width_cm)   # type: ignore[arg-type]
        else:
            para(doc, str(content), BODY_PT, line_pt, first_indent=FIRST_LINE_INDENT)

    doc.save(str(out))
    return out


def pdf_pages(pdf: Path) -> int:
    d = fitz.open(pdf)
    n = len(d)
    d.close()
    return n


def measure(pdf: Path) -> tuple[int, int, int]:
    """返回 (总页数, 正文最少行数, 末页正文行数)。排除页眉/页脚带。"""
    import verify

    d = fitz.open(pdf)
    counts = [len(verify.y_clusters(p)) for p in d]
    d.close()
    return len(counts), min(counts), counts[-1]


def main(argv: list[str]) -> None:
    if "--no-pdf" in argv:
        build(34, LINE_CANDIDATES[0])
        print(f"[文档] 已写 {DOCX.name}")
        return

    import to_pdf

    soffice = to_pdf.find_soffice()
    profile = Path(tempfile.gettempdir()) / "softcopyright_lo_profile"

    chosen, pages, best = LINE_CANDIDATES[0], 0, None
    for lp in LINE_CANDIDATES:
        build(34, lp)
        pdf = to_pdf.convert(DOCX, soffice, profile)
        pages, min_lines, last_lines = measure(pdf)
        print(f"[文档] 试排 行距 {lp}pt → {pages} 页（正文最少 {min_lines} 行）", flush=True)
        chosen = lp
        if best is None:
            best = (pages, min_lines, lp)
        if pages <= TARGET_MAX and min_lines >= MIN_LINES_PER_PAGE:
            best = (pages, min_lines, lp)
            break

    pages, min_lines, lp = best
    chosen = lp
    build(pages, chosen)
    pdf = to_pdf.convert(DOCX, soffice, profile)
    pages2, min2, last2 = measure(pdf)
    if pages2 != pages:
        build(pages2, chosen)
        pdf = to_pdf.convert(DOCX, soffice, profile)
        pages2, min2, last2 = measure(pdf)
    print(f"[文档] 完成：{pdf.name} 共 {pages2} 页，正文最少 {min2} 行/页，"
          f"末页 {last2} 行（行距 {chosen}pt）")


if __name__ == "__main__":
    main(sys.argv[1:])
