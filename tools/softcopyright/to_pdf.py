# -*- coding: utf-8 -*-
"""把 软著申请材料 下的 .docx 导出成 .pdf。

首选 LibreOffice（headless，稳定、可批处理）：本机 Word 的 COM 自动化会被
首次运行/激活类对话框阻塞（Documents.Open 永久挂起），因此不作为首选。

用法：
    python tools/softcopyright/to_pdf.py [a.docx b.docx ...]
不传参数时，导出 软著申请材料 目录下全部 .docx。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "软著申请材料"
SOFFICE_CANDIDATES = [
    Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
    Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
]
TIMEOUT_S = 300
# LibreOffice 的用户配置目录固定放在 workspace 内：系统临时目录在沙箱/会话切换后
# 可能被清理或只读，放在这里可跨会话复用，也避免 Temp 目录 ACL 被改写后的清理失败。
LO_PROFILE = OUT_DIR / "_build" / "lo_profile"


def find_soffice() -> Path:
    for p in SOFFICE_CANDIDATES:
        if p.exists():
            return p
    found = shutil.which("soffice") or shutil.which("soffice.com")
    if found:
        return Path(found)
    raise RuntimeError("找不到 LibreOffice（soffice.exe）")


def convert(docx_path: Path, soffice: Path, profile: Path) -> Path:
    pdf_path = docx_path.with_suffix(".pdf")
    # 不用 tempfile.TemporaryDirectory：LibreOffice 会改写目录 ACL，
    # Python 清理时可能 PermissionError（WinError 5）。固定在 workspace 内中转。
    staging = OUT_DIR / "_build" / "pdf_raw"
    staging.mkdir(parents=True, exist_ok=True)
    # 清空整个中转目录：崩溃的转换会留下 LibreOffice 的 .~lock.<pdf># 锁文件
    # （ReadOnly+Hidden），只清 *.pdf 会让下一次转换报 Io Abort(0x11b, code 27)。
    for stale in staging.iterdir():
        if not stale.is_file():
            continue
        try:
            stale.chmod(0o666)
        except OSError:
            pass
        stale.unlink(missing_ok=True)
    cmd = [
        str(soffice),
        f"-env:UserInstallation=file:///{profile.as_posix()}",
        "--headless",
        "--norestore",
        "--convert-to",
        "pdf:writer_pdf_Export",
        "--outdir",
        str(staging),
        str(docx_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=TIMEOUT_S)
    produced = staging / (docx_path.stem + ".pdf")
    if not produced.exists():
        raise RuntimeError(
            f"转换失败（exit={proc.returncode}）：{docx_path.name}\n{proc.stdout}\n{proc.stderr}"
        )
    shutil.move(str(produced), str(pdf_path))
    return pdf_path


def main(argv: list[str]) -> None:
    targets = [Path(a).resolve() for a in argv] or sorted(
        p for p in OUT_DIR.glob("*.docx") if not p.name.startswith("~$"))
    if not targets:
        print("[PDF] 没有待导出的 .docx")
        return
    soffice = find_soffice()
    profile = LO_PROFILE
    for target in targets:
        if not target.exists():
            print(f"[跳过] 不存在：{target}")
            continue
        started = time.time()
        pdf = convert(target, soffice, profile)
        print(
            f"[PDF] {target.name} → {pdf.name}（{time.time() - started:.1f}s，{pdf.stat().st_size} 字节）",
            flush=True,
        )


if __name__ == "__main__":
    main(sys.argv[1:])
