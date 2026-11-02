"""SplitChunkAssistant —— FAC-RAG chunking v2 的可视化人工切片工作台。

P0：只读文件清单 + 配置面板。
后续阶段：P1 原文↔切片联动、P2 人工编辑、P3 保存/导出/gate。

模块分层（自下而上）
-------------------
fac_bridge  只读 JSON 访问（唯一与 FAC-RAG 产物接触的地方）
manifest    _manifest.json 内存视图
config      config.json 的加载 / 校验 / 保存
studio      数据门面（配置 + 清单 + 文件清单载荷）
api         路由集中处（可脱离 HTTP 单测）
server      stdlib ThreadingHTTPServer，只监听 127.0.0.1
"""

__version__ = "0.1.0"
__phase__ = "P0"
