"""PyInstaller 打包入口（Windows）。

统一走 ``chunk_studio.launcher``：普通启动=本地 Web 服务，
``--run-chunk-cli``=转发给打包进来的 SPLITCHUNK 引擎。
"""

import sys

from chunk_studio.launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
