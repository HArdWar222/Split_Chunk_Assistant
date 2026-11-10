# -*- coding: utf-8 -*-
"""校验软著鉴别材料 PDF 是否满足登记要求。

源程序材料（01）：60 页（前 30 + 后 30）；正文每页 50 行（末页按实际情况）；
页眉含「软件全称 + 版本号」与材料类型；页脚页码为 1–30 与 (总页数-29)–总页数；
无空白页；首页/末页内容与源程序首行/末行一致。

文档材料（02）：20–40 页；每页 ≥30 行；页眉含软件全称；页码连续。

用法：
    python tools/softcopyright/verify.py [a.pdf b.pdf ...]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "软著申请材料"
NAME = "面向法规领域RAG系统切片审核修改可视化工作台"

LINES_PER_PAGE = 50
HALF_PAGES = 30
MATERIAL_PAGES = HALF_PAGES * 2


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


# 敏感信息扫描：鉴别材料会对外提交，需确认不含个人路径、密钥、联系方式
SENSITIVE = {
    "绝对用户路径": re.compile(r"[A-Za-z]:[\\/]+Users[\\/]", re.I),
    "口令赋值": re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*[\"']?\S"),
    "密钥赋值": re.compile(r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*[\"']?\S"),
    "邮箱": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "手机号": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "身份证号": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
}


def scan_sensitive(pdf: Path) -> dict:
    doc = fitz.open(pdf)
    text = "\n".join(page.get_text("text") for page in doc)
    doc.close()
    hits: dict[str, list[str]] = {}
    for label, pattern in SENSITIVE.items():
        found = pattern.findall(text)
        if found:
            hits[label] = sorted({str(f)[:80] for f in found})[:5]
    return {"hits": hits, "clean": not hits}


def y_clusters(page, tol: float = 3.0) -> list[float]:
    """页面正文（排除页眉页脚带）每一行文字的 y 中心，按 y 升序去重。"""
    height = page.rect.height
    top = height * 0.075
    bottom = height * 0.94
    ys: list[float] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if not span["text"].strip():
                    continue
                cy = (span["bbox"][1] + span["bbox"][3]) / 2
                if top <= cy <= bottom:
                    ys.append(cy)
    ys.sort()
    clusters: list[float] = []
    for y in ys:
        if not clusters or y - clusters[-1] > tol:
            clusters.append(y)
    return clusters


def check_code_pdf(pdf: Path, stats: dict | None) -> dict:
    doc = fitz.open(pdf)
    problems: list[str] = []
    per_page: list[int] = []
    page_numbers: list[int] = []
    header_missing: list[int] = []
    blanks: list[int] = []
    squashed_name = squash(NAME)
    for idx, page in enumerate(doc):
        text = page.get_text("text")
        per_page.append(len(y_clusters(page)))
        if len(text.strip()) < 200:
            blanks.append(idx + 1)
        if squashed_name not in squash(text):
            header_missing.append(idx + 1)
        m = re.search(r"第\s*(\d+)\s*页", text)
        page_numbers.append(int(m.group(1)) if m else -1)
    total = len(doc)
    doc.close()

    back_start = (stats or {}).get("back_page_start", 0)
    expected = list(range(1, HALF_PAGES + 1)) + list(range(back_start, back_start + HALF_PAGES))
    if total != MATERIAL_PAGES:
        problems.append(f"页数 {total}，应为 {MATERIAL_PAGES}")
    if back_start and page_numbers != expected:
        diff = [(i + 1, a, b) for i, (a, b) in enumerate(zip(page_numbers, expected)) if a != b]
        problems.append(f"页码序列不符，共 {len(diff)} 页错位，前几处：{diff[:5]}")
    if blanks:
        problems.append(f"疑似空白页：{blanks}")
    if header_missing:
        problems.append(f"页眉缺软件全称的页数：{len(header_missing)}，例：{header_missing[:5]}")
    last_expected = (stats or {}).get("last_page_lines", LINES_PER_PAGE)
    short = [(i + 1, n) for i, n in enumerate(per_page)
             if n < LINES_PER_PAGE and i + 1 != total]
    over = [(i + 1, n) for i, n in enumerate(per_page) if n > LINES_PER_PAGE]
    if short:
        problems.append(f"行数 < {LINES_PER_PAGE} 的页：{short}")
    if over:
        problems.append(f"行数 > {LINES_PER_PAGE} 的页（折行溢出）：{over}")
    if per_page and per_page[-1] != last_expected:
        problems.append(f"末页行数 {per_page[-1]}，统计应为 {last_expected}")

    if stats:
        first_src = squash(stats.get("first_line", ""))
        last_src = squash(stats.get("last_line", ""))
        d = fitz.open(pdf)
        p1 = squash(d[0].get_text("text"))
        p_last = squash(d[-1].get_text("text"))
        d.close()
        if first_src and first_src not in p1:
            problems.append(f"首页未包含程序第一行：{stats.get('first_line', '')[:60]!r}")
        if last_src and last_src not in p_last:
            problems.append(f"末页未包含程序最后一行：{stats.get('last_line', '')[:60]!r}")

    return {
        "file": pdf.name,
        "pages": total,
        "per_page_lines": {"min": min(per_page), "max": max(per_page), "detail": per_page},
        "page_numbers": page_numbers,
        "blank_pages": blanks,
        "header_missing": header_missing,
        "problems": problems,
        "ok": not problems,
    }


def check_spec_pdf(pdf: Path) -> dict:
    doc = fitz.open(pdf)
    problems: list[str] = []
    per_page: list[int] = []
    page_numbers: list[int] = []
    blanks: list[int] = []
    for idx, page in enumerate(doc):
        text = page.get_text("text")
        per_page.append(len(y_clusters(page)))
        if len(text.strip()) < 150:
            blanks.append(idx + 1)
        m = re.search(r"第\s*(\d+)\s*页", text)
        page_numbers.append(int(m.group(1)) if m else -1)
    total = len(doc)
    doc.close()
    if not 28 <= total <= 40:
        problems.append(f"页数 {total} 不在 28–40 区间")
    short = [(i + 1, n) for i, n in enumerate(per_page) if n < 30]
    if short:
        problems.append(f"行数 < 30 的页：{short}")
    if blanks:
        problems.append(f"疑似空白页：{blanks}")
    if page_numbers != list(range(1, total + 1)):
        problems.append(f"页码不连续：{page_numbers[:5]}…{page_numbers[-3:]}")
    return {
        "file": pdf.name,
        "pages": total,
        "per_page_lines": {"min": min(per_page), "max": max(per_page), "detail": per_page},
        "page_numbers": page_numbers,
        "blank_pages": blanks,
        "problems": problems,
        "ok": not problems,
    }


def main(argv: list[str]) -> None:
    try:  # Windows 控制台默认 GBK，页面文本里的箭头等字符会编码失败
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    stats_path = OUT_DIR / "_build" / "source_stats.json"
    stats = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else None
    targets = [Path(a).resolve() for a in argv] or sorted(
        p for p in OUT_DIR.glob("*.pdf") if p.name.startswith(("01", "02"))
    )
    results = []
    for pdf in targets:
        r = check_code_pdf(pdf, stats) if pdf.name.startswith("01") else check_spec_pdf(pdf)
        r["sensitive"] = scan_sensitive(pdf)
        if not r["sensitive"]["clean"]:
            r["problems"].append(f"疑似敏感信息：{r['sensitive']['hits']}")
            r["ok"] = False
        results.append(r)
        print(f"[{'OK ' if r['ok'] else 'FAIL'}] {r['file']}  页数={r['pages']}  "
              f"每页行数={r['per_page_lines']['min']}–{r['per_page_lines']['max']}  "
              f"页码={r['page_numbers'][:2]}…{r['page_numbers'][-2:]}  空白页={r['blank_pages']}  "
              f"敏感信息={'无' if r['sensitive']['clean'] else r['sensitive']['hits']}")
        for p in r["problems"]:
            print("   - " + p)
    report = OUT_DIR / "_build" / "verify_report.json"
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告：{report}")
    if not results:
        sys.exit("没有找到 PDF")
    if any(not r["ok"] for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
