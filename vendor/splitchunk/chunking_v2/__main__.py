"""``python -m chunking_v2`` 入口（等价于 ``splitassistant-chunk``）。"""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
