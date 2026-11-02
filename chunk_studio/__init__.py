"""SplitChunkAssistant：FAC-RAG 切块产物的可视化人工切片工作台。

分层（依赖方向自上而下）：

- ``web/``        零依赖前端单页（文件树 / 原文 / 切片三栏 + 编辑弹层）
- ``server.py``   stdlib ThreadingHTTPServer，127.0.0.1
- ``api.py``      HTTP 路由，``App.handle`` 可脱离 socket 单测
- ``studio.py``   数据门面：配置 + 文件清单 + 文档载荷 + 缓存 + 会话内编辑命令栈
- ``ops.py``      编辑命令纯函数（split / merge / drop / add / patch + diff）
- ``fac_bridge.py`` / ``manifest.py`` / ``source.py`` / ``align.py``
                  只读 JSON 访问、源 md 定位、chunk ↔ 行对齐

**本包不 import ``fac_rag``、不写 FAC-RAG 数据**；P2 的编辑只存在内存里，
P3 起才写 ``edits/`` 覆盖层。
"""

__version__ = "0.3.0"
__phase__ = "P2"
