# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir 打包配置（Windows）。

用法（在仓库根）：
    python -m PyInstaller --clean --noconfirm packaging/SplitChunkAssistant.spec

产物：``dist/SplitChunkAssistant/SplitChunkAssistant.exe`` + ``_internal/``。
关键点：
- ``web/`` 与 ``vendor/splitchunk/`` 作为**数据目录**放进 ``_internal``，运行时
  ``chunk_studio.paths`` 从 ``sys._MEIPASS`` 找；引擎从磁盘目录导入，升级引擎
  只要替换 ``_internal/vendor/splitchunk``，不必重新打包。
- 引擎依赖 PyYAML（含 ``_yaml`` C 扩展），交给 PyInstaller 的 yaml hook。
- 配置与 data/ 落在 exe 同级（不可写时回退 %LOCALAPPDATA%），不打进安装包。
"""

import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 - SPECPATH 由 PyInstaller 注入

datas = [
    (os.path.join(ROOT, "web"), "web"),
    (os.path.join(ROOT, "vendor", "splitchunk"), os.path.join("vendor", "splitchunk")),
]

a = Analysis(  # noqa: F821
    [os.path.join(SPECPATH, "run_app.py")],  # noqa: F821
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=["yaml"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SplitChunkAssistant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="SplitChunkAssistant",
)
