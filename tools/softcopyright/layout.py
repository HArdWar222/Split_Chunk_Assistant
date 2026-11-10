# -*- coding: utf-8 -*-
"""软著鉴别材料排版引擎。

统一负责：页面尺寸/页边距、页眉（软件全称 + 版本号 + 材料类型）、
页脚（第 X 页 / 共 Y 页，页码用 Word 域）、固定行网格、分页、章节页码起始值。

两类材料共用本模块：
- 源程序鉴别材料：等宽字体 + 固定行距，每页恰好 50 行；
- 文档鉴别材料：宋体小四 + 固定行距，自然流排，每页约 39 行。
"""
from __future__ import annotations

import unicodedata

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

SOFTWARE_NAME = "面向法规领域RAG系统切片审核修改可视化工作台"
SOFTWARE_VERSION = "V1.0"
HEADER_TITLE = f"{SOFTWARE_NAME} {SOFTWARE_VERSION}"

# w:pBdr 在 CT_PPr 里排在 numPr/suppressLineNumbers 之后、shd/tabs/spacing 之前
_PPR_AFTER_PBDR = (
    "w:shd", "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap",
    "w:overflowPunct", "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN",
    "w:bidi", "w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind",
    "w:contextualSpacing", "w:mirrorIndents", "w:suppressOverlap", "w:jc",
    "w:textDirection", "w:textAlignment", "w:textboxTightWrap", "w:outlineLvl",
    "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange",
)


# ---------------------------------------------------------------- 字体与域
def set_run_font(run, ascii_font: str, ea_font: str, size_pt: float, bold: bool = False) -> None:
    """同时设置西文与中文字体（python-docx 默认只管西文）。"""
    run.font.name = ascii_font
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), ascii_font)
    rfonts.set(qn("w:hAnsi"), ascii_font)
    rfonts.set(qn("w:eastAsia"), ea_font)
    rfonts.set(qn("w:cs"), ascii_font)


def add_field(paragraph, instr: str, prefix: str = "", suffix: str = "",
              cached: str = "1", font: tuple[str, str, float] | None = None) -> None:
    """在段落里插入一个 Word 域（PAGE / NUMPAGES 等），可选前后缀文本。

    域必须拆成独立的 run：begin / instrText / separate / 结果 / end。
    把三者塞进同一个 run 虽然部分渲染器能容忍，但不符合 OOXML 语义，
    Word 可能显示为 “错误！未定义书签”。
    """
    if prefix:
        r = paragraph.add_run(prefix)
        if font:
            set_run_font(r, *font)
    seq = [("begin", None), ("instr", f" {instr} "), ("separate", None),
           ("text", cached), ("end", None)]
    for kind, value in seq:
        run = paragraph.add_run()
        if font:
            set_run_font(run, *font)
        if kind == "instr":
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = value
            run._r.append(el)
        elif kind == "text":
            t = OxmlElement("w:t")
            t.set(qn("xml:space"), "preserve")
            t.text = value
            run._r.append(t)
        else:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
            run._r.append(el)
    if suffix:
        r = paragraph.add_run(suffix)
        if font:
            set_run_font(r, *font)


# CT_SectPr 子元素有固定顺序：pgNumType 必须排在 cols / docGrid 之前，
# 否则 Word 判定文件损坏并弹修复对话框（COM 导出会一直卡住）。
_AFTER_PG_NUM = (
    "w:cols",
    "w:formProt",
    "w:vAlign",
    "w:noEndnote",
    "w:titlePg",
    "w:textDirection",
    "w:bidi",
    "w:rtlGutter",
    "w:docGrid",
    "w:printerSettings",
    "w:sectPrChange",
)


def set_section_page_start(section, page_no: int) -> None:
    """让本节页码从 page_no 开始（w:pgNumType@w:start），位置符合 OOXML 顺序。"""
    sect_pr = section._sectPr
    for old in sect_pr.findall(qn("w:pgNumType")):
        sect_pr.remove(old)
    pg = OxmlElement("w:pgNumType")
    pg.set(qn("w:start"), str(page_no))
    for tag in _AFTER_PG_NUM:
        found = sect_pr.find(qn(tag))
        if found is not None:
            found.addprevious(pg)
            return
    sect_pr.append(pg)


# ---------------------------------------------------------------- 页面与版心
def setup_page(section, top_cm: float, bottom_cm: float, left_cm: float, right_cm: float) -> None:
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(top_cm)
    section.bottom_margin = Cm(bottom_cm)
    section.left_margin = Cm(left_cm)
    section.right_margin = Cm(right_cm)
    section.header_distance = Cm(1.2)
    section.footer_distance = Cm(1.0)


def content_width_cm(section) -> float:
    return 21.0 - section.left_margin.cm - section.right_margin.cm


def setup_header_footer(doc, section, material_label: str, total_pages: int) -> None:
    """页眉：左=软件全称+版本号，右=材料类型；页脚：居中 第 X 页 / 共 Y 页。

    「Header」样式自带居中/右制表位，LibreOffice 会优先用样式里的制表位，
    导致右对齐的材料类型标签落在页面中间。必须先清空样式的制表位。
    """
    width = content_width_cm(section)
    try:
        doc.styles["Header"].paragraph_format.tab_stops.clear_all()
    except KeyError:
        pass

    header = section.header
    header.is_linked_to_previous = False
    hp = header.paragraphs[0]
    hp.text = ""
    hp.paragraph_format.space_before = Pt(0)
    hp.paragraph_format.space_after = Pt(0)
    hp.paragraph_format.tab_stops.add_tab_stop(Cm(width), WD_TAB_ALIGNMENT.RIGHT)
    r1 = hp.add_run(HEADER_TITLE)
    set_run_font(r1, "宋体", "宋体", 9)
    r2 = hp.add_run("\t" + material_label)
    set_run_font(r2, "宋体", "宋体", 9)
    # 页眉下边框：必须按 CT_PPr 顺序插到 tabs/spacing 之前，否则标签页对齐会被渲染器忽略
    ppr = hp._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "808080")
    borders.append(bottom)
    ppr.insert_element_before(borders, *_PPR_AFTER_PBDR)

    footer = section.footer
    footer.is_linked_to_previous = False
    fp = footer.paragraphs[0]
    fp.text = ""
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fp.paragraph_format.space_before = Pt(0)
    fp.paragraph_format.space_after = Pt(0)
    add_field(fp, "PAGE", prefix="第 ", suffix=" 页", cached="1",
              font=("宋体", "宋体", 9))
    tail = fp.add_run(f" / 共 {total_pages} 页")
    set_run_font(tail, "宋体", "宋体", 9)
    for run in fp.runs:
        set_run_font(run, "宋体", "宋体", 9)


def new_document(default_ea_font: str = "宋体", default_size: float = 12) -> Document:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "宋体"
    style.font.size = Pt(default_size)
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), "宋体")
    rfonts.set(qn("w:hAnsi"), "宋体")
    rfonts.set(qn("w:eastAsia"), default_ea_font)
    return doc


# ---------------------------------------------------------------- 文本行
def disp_width(text: str) -> int:
    """按显示列宽计数：全角/中文字符算 2 列，其余算 1 列。"""
    total = 0
    for ch in text:
        total += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return total


def wrap_line(text: str, limit: int) -> list[str]:
    """把超过 limit 列的行硬折成多行（折行后每行都按一行的行数计）。"""
    if disp_width(text) <= limit:
        return [text]
    out: list[str] = []
    cur: list[str] = []
    width = 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if width + w > limit and cur:
            out.append("".join(cur))
            cur = []
            width = 0
        cur.append(ch)
        width += w
    out.append("".join(cur))
    return out


def add_line(doc, text: str, ascii_font: str, ea_font: str, size_pt: float,
             line_pt: float, indent_pt: float = 0, bold: bool = False,
             align=WD_ALIGN_PARAGRAPH.LEFT):
    """追加一行：段前段后 0、固定行距、不设首行缩进。"""
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    pf.line_spacing = Pt(line_pt)
    pf.left_indent = Pt(indent_pt)
    pf.alignment = align
    pf.widow_control = False
    run = p.add_run(text)
    set_run_font(run, ascii_font, ea_font, size_pt, bold=bold)
    return p
