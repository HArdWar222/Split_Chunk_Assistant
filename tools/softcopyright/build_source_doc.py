# -*- coding: utf-8 -*-
"""生成《源程序鉴别材料》：前 30 页 + 后 30 页，每页恰好 50 行。

用法：
    python tools/softcopyright/build_source_doc.py

产出：
    软著申请材料/01_源程序鉴别材料.docx
    软著申请材料/_build/source_stats.json   （供校验报告使用）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import layout  # noqa: E402
from docx.enum.section import WD_SECTION  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "软著申请材料"
BUILD_DIR = OUT_DIR / "_build"

# 程序正文的收录顺序：核心算法 → 工作台/草稿 → 基础设施 → 前端 → 打包/启动脚本。
# 核心模块排在最前，使软著要求的「前 30 页」直接呈现对齐、编辑命令、自动拆分等
# 最能体现独创性的代码；入口与打包脚本放在末尾。
# 不含 tests/（验证代码）、prototype/（原型）、vendor/（第三方切块引擎）、data/（语料）。
FILES: list[str] = [
    "chunk_studio/align.py",         # 三层对齐算法（覆盖条 / 表 / 附录）
    "chunk_studio/ops.py",           # 七类编辑命令纯函数
    "chunk_studio/autopart.py",      # 超长单行表与逻辑块自动拆分
    "chunk_studio/studio.py",        # 数据面板、会话内编辑命令、提交
    "chunk_studio/chunker.py",       # 切片契约与切块产物解析
    "chunk_studio/manifest.py",      # 清单、哈希与增量比对
    "chunk_studio/drafts.py",        # 草稿落盘
    "chunk_studio/source.py",        # 原文读取与行号
    "chunk_studio/fac_bridge.py",    # 外部切块引擎桥接
    "chunk_studio/api.py",           # HTTP 路由
    "chunk_studio/server.py",        # 本地服务
    "chunk_studio/config.py",        # 配置
    "chunk_studio/paths.py",         # 路径解析
    "chunk_studio/launcher.py",      # 启动器
    "chunk_studio/__init__.py",
    "web/app.js",                    # 前端主体
    "web/style.css",
    "web/index.html",
    "packaging/run_app.py",
    "packaging/SplitChunkAssistant.spec",
    "packaging/installer.iss",
    "run_studio.cmd",
]

LINES_PER_PAGE = 50
HALF_PAGES = 30                      # 前 30 页 / 后 30 页
WRAP_COLS = 94                       # 等宽 8.5pt 时版心约 97 列，留 3 列余量
CODE_FONT_SIZE = 8.5
CODE_LINE_PT = 13.0                  # 50 × 13pt = 650pt < 版心高 714pt


def read_lines(rel: str) -> list[str]:
    raw = (ROOT / rel).read_bytes()
    text = raw.decode("utf-8-sig")
    out: list[str] = []
    for line in text.splitlines():
        line = line.replace("\t", "    ").rstrip()
        out.append(line)
    return out


def collect_lines() -> tuple[list[str], list[dict]]:
    """收齐正文行。

    空行不打印：软著要求「程序每页不少于 50 行」，空行不构成程序行，
    保留空行会让实际代码密度掉到 35 行/页左右。折行后仍为空的行一并丢弃。
    """
    lines: list[str] = []
    stats: list[dict] = []
    for rel in FILES:
        part = read_lines(rel)
        wrapped: list[str] = []
        for line in part:
            for seg in layout.wrap_line(line, WRAP_COLS):
                if seg.strip():
                    wrapped.append(seg)
        stats.append({
            "file": rel,
            "lines_raw": len(part),
            "lines_blank_dropped": len(part) - len(wrapped),
            "lines_printed": len(wrapped),
            "start_line": len(lines) + 1,
            "end_line": len(lines) + len(wrapped),
        })
        lines.extend(wrapped)
    return lines, stats


def build() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    lines, file_stats = collect_lines()
    total_lines = len(lines)
    total_pages = -(-total_lines // LINES_PER_PAGE)
    need = HALF_PAGES * LINES_PER_PAGE
    if total_lines < need * 2:
        raise SystemExit(f"源码行数不足：{total_lines} 行，无法组成前后各 30 页（需 ≥ {need * 2} 行）")

    front = lines[:need]
    back_page_start = total_pages - HALF_PAGES + 1     # 例如 190 页 → 后 30 页从第 161 页开始
    back = lines[(back_page_start - 1) * LINES_PER_PAGE:]   # 从整页边界起，保证最后 30 页
    footer_total = total_pages

    doc = layout.new_document(default_ea_font="宋体", default_size=CODE_FONT_SIZE)
    sec1 = doc.sections[0]
    layout.setup_page(sec1, 2.5, 2.0, 3.0, 2.0)
    layout.setup_header_footer(doc, sec1, "源程序", footer_total)
    layout.set_section_page_start(sec1, 1)
    _write_block(doc, front)

    sec2 = doc.add_section(WD_SECTION.NEW_PAGE)
    layout.setup_page(sec2, 2.5, 2.0, 3.0, 2.0)
    layout.setup_header_footer(doc, sec2, "源程序", footer_total)
    layout.set_section_page_start(sec2, back_page_start)
    _write_block(doc, back)

    docx_path = OUT_DIR / "01_源程序鉴别材料.docx"
    doc.save(str(docx_path))

    stats = {
        "file_count": len(FILES),
        "total_lines": total_lines,
        "blank_lines_dropped": sum(s["lines_blank_dropped"] for s in file_stats),
        "total_pages": total_pages,
        "lines_per_page": LINES_PER_PAGE,
        "front_pages": HALF_PAGES,
        "back_pages": HALF_PAGES,
        "front_line_range": [1, need],
        "back_line_range": [total_lines - len(back) + 1, total_lines],
        "back_page_start": back_page_start,
        "last_page_lines": len(back) - (HALF_PAGES - 1) * LINES_PER_PAGE,
        "wrap_cols": WRAP_COLS,
        "code_font_size": CODE_FONT_SIZE,
        "code_line_pt": CODE_LINE_PT,
        "first_line": lines[0],
        "last_line": lines[-1],
        "files": file_stats,
        "docx": str(docx_path.relative_to(ROOT)),
    }
    (BUILD_DIR / "source_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[源程序] 文件 {len(FILES)} 个 / 打印 {total_lines} 行"
          f"（丢弃空行 {stats['blank_lines_dropped']} 行）→ 全文 {total_pages} 页")
    print(f"[源程序] 前 30 页 = 第 1-{need} 行；后 30 页 = 第 {stats['back_line_range'][0]}-{total_lines} 行"
          f"（页码 {back_page_start}-{total_pages}，末页 {stats['last_page_lines']} 行）")
    print(f"[源程序] 已写出 {docx_path}")


def _write_block(doc, block: list[str]) -> None:
    """按每页 50 行写入一个 30 页的块：第 1 行不加分页，此后每 50 行用 page_break_before。"""
    for idx, text in enumerate(block):
        p = layout.add_line(doc, text, "Consolas", "宋体", CODE_FONT_SIZE, CODE_LINE_PT)
        if idx and idx % LINES_PER_PAGE == 0:
            p.paragraph_format.page_break_before = True


if __name__ == "__main__":
    build()
