"""统一入口：普通启动 = 本地 Web 服务；``--run-chunk-cli`` = 转发给打包进来的切块引擎。

开发态直接用 ``python -m chunk_studio.server`` / ``python -m chunking_v2.cli``；
PyInstaller 冻结后只有一个 exe，chunker 用 ``sys.executable --run-chunk-cli …``
回到这里，再调用 ``chunking_v2.cli.main``（引擎已作为隐藏导入打进 exe）。
"""

from __future__ import annotations

import sys

#: 内部隐藏开关，不要写进用户文档；由 chunker.engine_command() 在冻结态使用。
ENGINE_FLAG = "--run-chunk-cli"


def run_chunk_cli(argv: list[str]) -> int:
    """转发给 SPLITCHUNK 引擎 CLI，返回它的退出码。"""
    try:
        from chunking_v2.cli import main as cli_main
    except ImportError as exc:  # 开发态直接调用 --run-chunk-cli 会走到这里
        print(f"切块引擎不可用：{exc}", file=sys.stderr)
        return 1

    return int(cli_main(list(argv)) or 0)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == ENGINE_FLAG:
        return run_chunk_cli(args[1:])
    from .server import main as server_main

    return server_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
