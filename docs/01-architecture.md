# 01 · 代码架构

> 目标：给 FAC-RAG 的 chunking v2 挂一个本地网页工作台，实现「原文 ↔ 切片」双向联动的可视化人工切片。
> 原则：FAC-RAG 只出现在一个适配文件里；其余模块可脱离 FAC-RAG 单测。

---

## 1. 五层架构

```
┌────────────────────────────────────────────────────────────┐
│ L5 表现层：web/（vanilla JS 单页，零 npm 依赖）              │
│   index.html + style.css + app.js + store.js + views/*.js  │
└───────────────▲──────────────────────────┬─────────────────┘
                │ fetch /api/*             │ 静态 / /static/*
┌───────────────┴──────────────────────────▼─────────────────┐
│ L4 传输层：server.py  stdlib ThreadingHTTPServer            │
│   127.0.0.1:8790；静态白名单；gzip；--open                  │
├────────────────────────────────────────────────────────────┤
│ L3 接口层：api.py                                           │
│   路由表、JSON 编解码、参数白名单、异常→HTTP 状态码          │
├────────────────────────────────────────────────────────────┤
│ L2 领域服务层：studio.py                                    │
│   DocView 生命周期、LRU 文档缓存、会话 undo 栈、编排          │
│   ├── ops.py      编辑命令（split/merge/drop/add/patch）    │
│   ├── align.py    原文↔切片对齐（三层算法）                 │
│   ├── overlay.py  覆盖层读写（edits/）                      │
│   ├── manifest.py 产物索引（manifest/_extras/jsonl）        │
│   ├── source.py   源 md 行模型、expand、骨架化              │
│   └── rechunk.py  参数重切（GroupOverride）                 │
├────────────────────────────────────────────────────────────┤
│ L1 适配层：fac_bridge.py  ★ 唯一 import fac_rag 的地方 ★    │
│   chunk_file_full / expand_lines / gate_chunks /            │
│   contracts.Chunk / load_sources / GroupOverride            │
└────────────────────────────────────────────────────────────┘
                          ▼
        C:\Users\QiuYe\Desktop\FAC-RAG （只读依赖）
```

依赖方向严格单向：L5 → L4 → L3 → L2 → L1，**禁止反向 import**。
前端不感知 FAC-RAG 的存在；后端不感知 DOM 的存在。

---

## 2. 目录结构（P0 落地目标）

```
C:\Users\QiuYe\Desktop\Split_assistant\
├── README.md
├── docs/                        设计文档（本目录）
├── chunk_studio/                ← P0 创建
│   ├── __init__.py
│   ├── server.py            (~130 行) HTTP 入口、静态文件、CLI、gzip
│   ├── api.py               (~150 行) 路由与错误映射
│   ├── studio.py            (~200 行) 服务：加载/缓存/undo/保存
│   ├── manifest.py          (~120 行) 产物索引
│   ├── source.py            (~150 行) 源 md 行模型 + expand
│   ├── align.py             (~260 行) 三层对齐
│   ├── ops.py               (~220 行) 编辑命令（含逆操作）
│   ├── overlay.py           (~130 行) edits/ 读写 + sha256 校验
│   ├── rechunk.py           (~120 行) 参数重切
│   ├── fac_bridge.py        (~150 行) FAC-RAG 适配
│   └── web\
│       ├── index.html
│       ├── style.css
│       ├── app.js           (~250 行) 启动、路由、键盘
│       ├── store.js         (~150 行) 单例状态 + 订阅
│       └── views\{tree,source,chunks}.js  (~400 行)
├── tests\
│   ├── test_align.py        消防法 74/74 golden
│   ├── test_ops.py          编辑/撤销不变量
│   ├── test_overlay.py      保存→重开一致性
│   └── test_api.py          随机端口 HTTP 冒烟
├── prototype\                数据层草稿（P0 拆分后废弃）
├── data\                     本地两目录数据源（gitignore：source/ + chunks/）
├── reference\                历史资料（gitignore）
└── scratch\                  一次性脚本（gitignore）
```

后端约 1,600 行，前端约 800 行，合计约 2,400 行，**无任何第三方依赖**。

### 2.1 数据源：清单模式与本地两目录模式（2026-11-04）

`manifest.py` 提供两个同签名实现，`studio.py` 可同时挂两个桥：

| 模式 | 类 | 目录约定 | 元数据 |
|---|---|---|---|
| manifest（FAC-RAG 产物） | `Manifest` | `chunks_dir/_manifest.json` + `<group>__<名>.jsonl` + 全局 `_extras.jsonl` | 完整：gate、warnings、sha256、char_coverage |
| dirs（本地两目录） | `DirManifest` | `<data_root>/source/**.md` + `<data_root>/chunks/<名>.chunks.jsonl`（+ 同名 `.extras.jsonl` 可选） | 现算：块数、kinds、覆盖率；gate/warnings 显示「—」 |

- 子目录即分组（`data/<子目录>`），源与切片按「同名 + 同相对路径」配对；源缺失只记 warning，仍可打开看切片。
- `FacBridge` 接受 `Manifest` 或 `DirManifest` 实例；本地包里缺 `token_len` 时用 `len(chunk)` 补、`part_index/part_total` 转 int。
- `Studio.bridge_for(key)` 先查 manifest 源、再查本地源；`/api/files` 返回 `mode`（both / manifest / dirs）与 `sources`（每个源的块数、目录、generated_at），`/api/config` 额外回 `data_root`、`data_ok`、`data_error`。
- 设置面板可留空其中一种：两者至少一种可用即可启动。本地数据目录默认 `data/`，已加入 `.gitignore`（数据不进仓库）。

---

## 3. 分层职责

| 层 | 模块 | 该做什么 | 不该做什么 |
|---|---|---|---|
| L4 | `server.py` | 绑定 127.0.0.1、路由分发、MIME、gzip、`--open`、优雅退出 | 不做业务判断、不拼路径 |
| L3 | `api.py` | 路径 → 方法映射；JSON 解析与校验；异常转 HTTP；体积上限 | 不触碰底层算法 |
| L2 | `studio.py` | DocView 生命周期、缓存、undo 栈、编排各算法、导出 | 不写底层对齐/编辑逻辑 |
| L2 | `ops.py` / `align.py` / `overlay.py` | 纯函数式领域逻辑，输入输出明确 | 不依赖 HTTP、不打印日志 |
| L1 | `fac_bridge.py` | 把 FAC-RAG 收窄成稳定小接口；版本兼容兜底 | 不让上层看到 `fac_rag.*` 类型 |
| L5 | `web/*` | 渲染与交互、本地视图状态 | 不做契约校验（后端兜底） |

---

## 4. 核心数据结构

后端返回、前端直接消费同一 JSON 形状：

```python
@dataclass
class ChunkView:
    # —— 15 字段契约（原样） ——
    id: str; chunk: str; law_name: str; law_num: str
    law_invalid: str; law_type: str; law_level: str
    issuer: str; effective_date: str; source: str
    chunk_kind: str; token_len: int
    part_index: int; part_total: int; chunk_hash: str
    # —— 视图附加（不进契约） ——
    extras: dict                 # 侧车内容（表格 grid、probe 等）
    span: dict | None            # {"start":127,"end":139,"status":"exact"}
    flags: dict                  # {"manual":False,"dirty":False,"aligned":"exact"}

@dataclass
class DocView:
    key: str                     # "national_laws/national_laws__中华人民共和国消防法"
    group: str                   # national_laws / GB_laws
    law_name: str
    source_path: str
    source_sha256: str
    lines: list[dict]            # [{"n":1,"text":"# 中华人民共和国消防法","src":1}]
    expand: dict | None          # 超长 HTML 行展开信息 {原行, 展开后行数}
    chunks: list[ChunkView]
    params: dict                 # 当前生效的切分参数
    stats: dict                  # 块数/字符数/覆盖率/对齐分布
    manifest: dict               # 基线信息（n_chunks / updated_at / gate）
    edited: bool                 # 是否存在覆盖层

@dataclass
class EditOp:
    kind: str                    # split|merge|drop|add|patch|boundary
    payload: dict                # 操作参数
    before: dict                 # 逆操作快照（撤销用）
```

前端 store（单例，订阅式）：

```js
state = {
  files: [], activeKey: null, doc: null,
  selection: {chunkId: null, line: null},
  filter: "", dirty: false,
  undo: [], redo: [],
  alignStats: {exact: 0, table_rows: 0, approx: 0, miss: 0}
}
```

---

## 5. 请求流程

以「打开消防法 → 拆一块 → 保存」为例：

```
① GET /api/files
   api → manifest.list() → [{key, law_name, n_chunks, n_edits, align:{...}}]

② GET /api/doc?key=national_laws/national_laws__中华人民共和国消防法
   studio.load(key):
     manifest.entry(key)            → source_path / source_sha256
     source.read(path)               → lines[]；expand_lines(lines) → work_lines + 映射
     overlay.exists(key) ? overlay.load() : fac_bridge.chunk_file_full(...)
     align.align(chunks, work_lines, extras) → 每块 span+status（缓存命中则复用）
     stats + manifest 合并           → DocView

③ 前端点击「拆」→ 指定行内切点 lineNo/charIdx
   POST /api/op {key, op:{kind:"split", id:"…-0003", at:{line:221, ch:12}}}
   studio.apply(op):
     ops.split(chunks, op)           → newChunks, inverseOp
     Chunk.finalize() 逐块校验       → 失败返回 409，不落盘
     undo.push(inverseOp)
     → {chunks:[...], diff:{add:1,mod:1,del:0}}

④ POST /api/save {key, chunks, note}
   overlay.save(key, chunks, meta{source_sha256, saved_at, note})
   → edits/<group>__<stem>.jsonl + .extras.jsonl + .meta.json
   → {ok, saved_at, stats}

⑤ POST /api/rechunk {key, params:{max_chunk_chars:400}}
   rechunk.preview(key, params) → GroupOverride 包 SourceGroup
   → fac_bridge.chunk_file_full(path, group=override)（不落盘）
   → 新旧块对比 {added, removed, changed}，前端弹确认
```

---

## 6. API 一览

| 方法 | 路径 | 入参 | 出参 | 说明 |
|---|---|---|---|---|
| GET | `/api/files` | — | 文件列表 | 97 个文件、组、块数、编辑数、对齐分布 |
| GET | `/api/doc` | `key` | `DocView` | 打开文档（含缓存） |
| GET | `/api/tables` | `key` | `{tables: {chunk_id: grid}}` | 表格块的结构化网格（视图层，按文件懒加载、最近 8 个文件缓存） |
| GET | `/api/source` | `key` | 原始 md | 下载/对照用，`text/markdown` |
| POST | `/api/op` | `key, op` | `{chunks, diff}` | 单个编辑命令，返回增量 |
| POST | `/api/save` | `key, chunks, note` | `{ok, saved_at}` | 写覆盖层 |
| POST | `/api/reset` | `key` | `{ok}` | 删覆盖层回基线 |
| POST | `/api/rechunk` | `key, params` | `{preview, diff}` | 参数重切预览（不保存） |
| POST | `/api/gate` | `key`（可选 rules） | gate 结果 | 只质检不重切 |

约定：

- 所有 POST 收 `application/json`，请求体上限 8MB；
- 错误统一 `{"error": {"code": "...", "message": "..."}}` + 合适状态码（400/404/409/500）；
- 静态资源走 `/` 与 `/static/*`；`/api/*` 之外一律按静态文件白名单处理。

---

## 7. 前端架构

- **单页三视图、无框架**：`app.js` 只做启动与事件委托；`store.js` 维护状态并提供 `setState/subscribe`；
- **渲染策略**：
  - 原文栏按**可见行窗口虚拟化**（视口 ±50 行，`translateY` 定位），千行文件只挂约 200 个 DOM 节点；
  - 切片栏**卡片虚拟列表**，836 块也只渲染可见的约 30 张；
  - 高亮 = 给行 div 切 class（`hl-a` / `hl-b` / `is-miss`），**不 innerHTML 重建**，保证滚动不闪；
- **事件**：容器级事件委托，按 `[data-chunk]` / `[data-line]` 定位；`keydown` 处理 Ctrl+Z / Ctrl+Shift+Z / Esc / ↑↓；
- **三栏联动**：hover 卡片 → 对应行加 class 并滚到可见区；点击锁定 → 中栏滚到块首行；点原文行 → 右栏滚到包含该行的第一张卡；
- **行→块查找**：前端持有按 `start` 排序的 chunks 与 `maxEnd` 前缀数组，二分查找，O(log n)。

---

## 8. 对齐模块接口（align.py）

```python
def align_chunks(chunks, work_lines, extras, *, source_line_map=None) -> dict[str, Span]:
    """返回 {chunk_id: Span(start, end, status)}；纯函数，无 IO。"""

# 第 1 层：条文/段落 —— 规范化行序列，游标单调扫描 + 文末回填
#   首行先剥面包屑: text.split(" > ")[-1]
# 第 2 层：表格 —— extras["table_grid"]["rows"] 逐行反查源行
#   源行归一化：去 "|"、去 HTML、去相邻重复单元格
# 第 3 层：表题/表号回填
# status: exact / table_rows / approx / miss
```

算法细节、实测覆盖率和失败模式见 [05-alignment.md](05-alignment.md)。

---

## 9. 覆盖层与基线

- **基线**：`FAC-RAG/data/chunks_v2/*.jsonl`（97 个文件、约 1.27 万块）+ `_extras.jsonl` + `_manifest.json`；
- **覆盖层**：`edits/<group>__<stem>.jsonl`（15 字段快照）+ `.extras.jsonl` + `.meta.json`
  （`source_sha256 / saved_at / note / 每块 span 缓存 / manual 标记 / 改动统计`）；
- 加载优先级：覆盖层 → 基线 → 现场跑 `chunk_file_full` 生成；
- **基线一致性**：`.meta.json` 记录源文件 sha256；源 md 变化时返回 `stale: true`，UI 顶部黄条提示「源已变更，建议重切或重置」；
- **不做增量 diff 文件**：每次保存写整份快照，简单、可审计、可回滚。

---

## 10. 依赖边界、配置与安全

```python
# fac_bridge.py 顶部（唯一耦合点）
try:
    from fac_rag.chunking_v2 import chunk_file_full, gate_chunks, expand_lines
    from fac_rag.contracts import Chunk, chunk_hash, estimate_token_len
    from fac_rag.chunking.sources import load_sources
except ModuleNotFoundError as e:
    raise RuntimeError(
        "需要设置 PYTHONPATH 或 FAC_ROOT 指向 FAC-RAG 仓库"
    ) from e
```

- 启动：`python -m chunk_studio.server --port 8790 --open`；
- 环境变量：`FAC_ROOT`（默认 `C:\Users\QiuYe\Desktop\FAC-RAG`）；CLI 参数覆盖；
- 安全：只绑 `127.0.0.1`；`key` 必须存在于 manifest（防路径穿越）；无 `eval`、无 shell；静态目录白名单；
- Python：3.11+（开发机实测 3.12.10）；只用标准库。

---

## 11. 测试

| 测试 | 内容 |
|---|---|
| `test_align.py` | 消防法 74/74 必须 exact；GB 50016 表格块走 table_rows；危化品 miss 不允许假高亮 |
| `test_ops.py` | split→merge 回到原块（hash/id/part 一致）；undo 10 次后与初始快照完全一致 |
| `test_overlay.py` | 改→存→清缓存→重载，得到同一份块；源 sha 变更触发 stale |
| `test_api.py` | 随机端口起服务；`/api/files` 返回 97 条；非法 key → 404；超长 body → 413 |

前端不引入测试框架；P1 结束给人工验收单（见 [02-interaction.md](02-interaction.md) §9）。

---

## 12. 为什么这样分层

1. **适配层单点隔离**：FAC-RAG 未来改动（例如 P5 给 `Unit` 加 `src_lines`）只影响 `fac_bridge.py`；
2. **算法纯函数化**：对齐与编辑最容易出错，无 IO 才能高频单测；消防法 golden 直接锁回归；
3. **无框架前端**：没有 npm 依赖与构建链，符合「双击就跑」的使用场景；
4. **覆盖层而非改库**：源 md 与 `data/chunks_v2` 永远是干净基线，人工修改可弃、可导出、可审计。
