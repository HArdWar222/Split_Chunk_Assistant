"""运行根目录解析：开发态按仓库目录，打包态（PyInstaller）区分只读资源根与可写应用根。

- **资源根**（resource_root）：随程序分发的只读文件——``web/``、``vendor/splitchunk/``。
  开发态就是仓库根；PyInstaller 里是 ``sys._MEIPASS``（目录版在 ``_internal/`` 下）。
- **应用根**（app_root）：``config.json``、``data/`` 等可写路径所在的目录。
  开发态就是仓库根；打包后是 exe 所在目录（用户可能放在任意可写位置）。

背景：``Path(__file__).parent.parent`` 在 PyInstaller 冻结后会指向临时解压目录，
写进去的数据重启就没了，静态文件/引擎也会找不到，所以统一从这里取路径。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT_CONFIG_NAME = "config.json"
DATA_DIR_NAME = "data"
#: ``data/`` 下需要预先建好的四个子目录。
DATA_SUBDIRS = ("source", "chunks", "draft", "backup")


def is_frozen() -> bool:
    """是否运行在 PyInstaller 等冻结环境里。"""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """只读资源根：打包态是解压目录（``sys._MEIPASS``），开发态是仓库根。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def app_root() -> Path:
    """可写应用根：打包态是 exe 所在目录，开发态是仓库根。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return resource_root()


def default_config_path() -> Path:
    return app_root() / DEFAULT_CONFIG_NAME


def default_data_root() -> Path:
    return app_root() / DATA_DIR_NAME


def web_dir() -> Path:
    """静态页面目录（``index.html`` / ``app.js`` / ``style.css``）。"""
    return resource_root() / "web"


def vendor_root() -> Path:
    """vnedor 的切块引擎根（``chunking_v2/`` 所在目录）。"""
    return resource_root() / "vendor" / "splitchunk"


def fallback_engine_roots() -> tuple[Path, ...]:
    """引擎探测顺序：先打包进来的 vendor，再找开发期兄弟仓库 ``../SPLITCHUNK``。"""
    return (vendor_root(), resource_root().parent / "SPLITCHUNK")


def ensure_data_layout(data_root: str | Path) -> Path:
    """确保数据根下 ``source/ chunks/ draft/ backup/`` 存在；返回数据根 Path。"""
    root = Path(data_root)
    root.mkdir(parents=True, exist_ok=True)
    for name in DATA_SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def ensure_default_config(config_path: str | Path | None = None) -> Path:
    """配置文件不存在时写一份最小配置（``data_root`` 指向应用根下的 data/）。

    只补缺失，不覆盖已有文件；返回配置路径。
    """
    path = Path(config_path) if config_path else default_config_path()
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "data_root": str(default_data_root()),
        "port": 8790,
        "auto_part": False,
        "max_chars": 800,
        "min_part_chars": 200,
        "auto_chunk": True,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
