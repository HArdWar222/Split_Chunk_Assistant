# SplitChunkAssistant

> **RAG 切块的可视化人工切片工作台** —— 自动切完能看、能改、能存、能导出。

给本地 FAC-RAG（默认 `C:\Users\QiuYe\Desktop\FAC-RAG`）的 `chunking_v2` 切块流水线挂一个本地网页工作台：
原文（Markdown）与切片（chunk）双向联动，支持人工 `split / merge / drop / add / 改字`，
支持调整切分参数后重切，人工修改写入覆盖层，导出仍然是原来的 **15 字段契约**。

**当前状态：P1 已实现（2026-11-03）** —— 「原文 ↔ 切片」三栏只读联动可用：
左文件树、中原文 Markdown 源码行（行号/节标题/表格行）、右切片卡片；hover/click 双向高亮，
**在原文里拖选一段文字，右侧覆盖该范围的切片会被高亮**（状态栏显示「原文第 a–b 行 → 对应 N 个切片」），
**原文左侧彩色竖条（范围条）直接标出每个切片覆盖的行区间**——悬停看切片 id 与行范围，点击选中，
同一行被多个切片覆盖时多条竖条并存、可一键开关（偏好存 localStorage），
卡片底部也显示 `第 a–b 行`，深链 `#doc=<key>` 可分享定位；对齐状态如实分档（exact / substr / table_rows / table_label / miss）。
首批验收目标「消防法 74/74」达成；全语料 12 718 块中 12 251 块可定位（96.3%）。
下一步是 P2 人工编辑（split / merge / drop / add / 改字 + 撤销 + diff）。

---

## 它解决什么问题

FAC-RAG 的切块器已经能把 97 份法规/国标 Markdown 切成约 1.27 万块，并带完整元数据与质检，
但切得对不对只能靠翻 JSONL 或跑评测：**看不到原文，也改不了边界**。

SplitChunkAssistant 把「自动切块」变成「可人工复核与修正的切块」：

- 左：文件树（97 个文件、分组、块数、编辑状态、对齐徽章）
- 中：原文 Markdown 源码行（行号 + 切片高亮 + 节导航）
- 右：切片卡片（15 字段 + extras + 对齐状态 + 操作按钮）
- 顶部：参数面板（重切）、diff 计数、保存/重置/导出、gate 质检

---

## 不可动摇的约束

| # | 约束 | 说明 |
|---|---|---|
| 1 | **只处理 Markdown** | 原件是 97 份 `.md`，不引入 Docling / PDF / OCR / torch |
| 2 | **FAC-RAG 只读** | 不改它的代码、不写它的 `data/chunks_v2`、不写源 md |
| 3 | **契约不变** | 输出仍是 15 字段 + extras 侧车；`token_len` / `chunk_hash` 为派生字段 |
| 4 | **覆盖层存储** | 人工修改写 `edits/`，随时可回基线、可审计 |
| 5 | **零第三方依赖** | 后端 Python `http.server`，前端 vanilla JS，双击即可跑 |
| 6 | **本地单机** | 只监听 `127.0.0.1`，无账号、无网络请求 |
| 7 | **对齐如实标注** | `exact / table_rows / approx / miss`，定位不到不假装高亮 |
| 8 | **不接 RAGFlow** | 本期不引入任何知识库平台（理由见 [docs/07-decisions.md](docs/07-decisions.md)） |

---

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/01-architecture.md](docs/01-architecture.md) | 代码架构：分层、目录、模块职责、数据结构、请求流、API、前端架构、测试 |
| [docs/02-interaction.md](docs/02-interaction.md) | 交互设计：三栏布局、联动规则、编辑动作、快捷键、虚拟化与性能、验收 |
| [docs/03-parameters.md](docs/03-parameters.md) | 人工修改时可改的全部参数：块字段 / 边界 / extras / 重切参数 / gate 阈值 |
| [docs/04-data-contract.md](docs/04-data-contract.md) | chunking v2 数据契约：15 字段、extras、record、gate/report、6 个 API |
| [docs/05-alignment.md](docs/05-alignment.md) | 原文↔切片对齐方案：三层算法、实测覆盖率、失败模式、根治路径 |
| [docs/06-roadmap.md](docs/06-roadmap.md) | 分阶段计划 P0–P5：任务清单、验收标准、工期与风险 |
| [docs/07-decisions.md](docs/07-decisions.md) | 决策记录（ADR）：为什么不用 Docling、不用 RAGFlow、不改契约…… |
| [docs/08-references.md](docs/08-references.md) | 参考项目调研：许可证、可借鉴点、不可抄清单 |

---

## 架构一图

```
源 .md（只读） ──► FAC-RAG chunking_v2.chunk_file_full() ──► 15 字段 + extras + gate
                                                              │
                                                   对齐器（chunk ↔ 源行）
                                                              ▼
                   SplitChunkAssistant（本仓库）
   ┌────────────────────── 浏览器单页 ──────────────────────┐
   │  文件树  │  原文行 + 切片高亮  │  切片卡片 + 编辑       │
   └───────────────────────────┬────────────────────────────┘
                               │ JSON over 127.0.0.1:8790
                    server.py ─ api.py ─ studio.py
                               │
              fac_bridge.py（唯一 import fac_rag 的地方）
                               │
              edits/ 覆盖层（人工修改）──► exports/*.jsonl（15 字段）
```

---

## 快速开始（P1 可用）

```bat
:: 前提：本机已装 Python 3.10+，且 FAC-RAG 位于本仓库的兄弟目录
:: 方式一：双击 / 命令行
run_studio.cmd

:: 方式二：直接跑模块
set FAC_ROOT=C:\Users\QiuYe\Desktop\FAC-RAG
python -m chunk_studio.server --port 8790 --open
```

浏览器打开 http://127.0.0.1:8790 ：

- 左侧 97 个法规文件（分组 / 搜索 / 块数 / gate 徽章），点击打开三栏；
- 中间是原文 `.md` 的源码行（行号 gutter、标题/引用/表格/超长行折叠）；
- 右侧是切片卡片（15 字段摘要 + extras + 对齐状态），顶部显示 `对齐 N/N (率) · 各档计数`；
- hover 卡片 → 原文行淡高亮；hover 原文行 → 关联卡片标记；click 卡片 → 滚动并强高亮对应原文行；
- 地址栏形如 `#doc=national_laws%2F中华人民共和国消防法`，可直接收藏/分享定位；
- 「⚙ 设置」可改 `chunks_dir` / `fac_root` / 端口 / 各分组源目录，保存即生效。

首次运行会在仓库根目录生成 `config.json`（已 gitignore）：

```json
{
  "fac_root": "C:\\Users\\QiuYe\\Desktop\\FAC-RAG",
  "chunks_dir": "C:\\Users\\QiuYe\\Desktop\\FAC-RAG\\data\\chunks_v2",
  "source_roots": {},
  "port": 8790
}
```

P1 的验收口径（单测覆盖）：

```bat
python -m unittest discover -s tests -t . -v   :: 65 个测试
```

- `/api/files` 合计 **97 个文件 / 12 718 块**，分组 national_laws 37 + GB_laws 60
- `/api/doc?key=…` 返回原文行 + 切片（含 `span` 与 `align`）+ 统计；缺 key 400，未知 key / 源文件缺失 404
- 消防法：411 行 / 74 块 / 对齐 **74/74（100%）**
- 静态页 `/`、`/static/app.js`、`/static/style.css` 均可直出

全语料对齐实测（2026-11-03，`align_chunks`）：

| 语料 | 块数 | 对齐率 | 主要档位 |
|---|---|---|---|
| 中华人民共和国消防法 | 74 | 100% | substr |
| GB 50016-2018 建筑设计防火规范 | 531 | 100% | substr 466 / table_rows 64 |
| 2025版山东省自由裁量标准 | 836 | 100% | table_rows 378 / table_label 306 |
| DB37T 3135-2018 建筑施工隐患排查 | 739 | 99.9% | table_rows 661 / substr 72 |
| TSG 21-2016 气瓶安全技术规程 | 561 | 100% | substr 466 / exact 64 |
| 全部 97 份 | 12 718 | **96.3%** | 未对齐 467 块（集中在少数含合并单元格复杂表的文件，UI 灰标） |

P1 只消费 FAC-RAG **已切好的 JSON**（`_manifest.json` + `*.jsonl` + `_extras.jsonl`），
不 import `fac_rag`、不调用切块器；参数重切（P4）才会新增裸调用入口。

---

## 目录

```
Split_assistant/
├── README.md                 ← 本文件
├── run_studio.cmd            ← Windows 一键启动（P0）
├── chunk_studio/             ← 后端包（纯 stdlib：config/manifest/fac_bridge/studio/api/server）
├── web/                      ← 前端单页（index.html / app.js / style.css）
├── tests/                    ← 单测（配置 / manifest / 产物桥 / API / 真实语料）
├── docs/                     ← 设计文档（8 篇）
├── prototype/                ← 数据层草稿（P0 起被 chunk_studio 取代）
├── reference/                ← 历史资料：chunking_v2 快照与代码审查（不随仓库发布）
└── scratch/                  ← 一次性探针脚本（不随仓库发布）
```

---

## 依赖 FAC-RAG 的哪些东西

| 用途 | 接口 |
|---|---|
| 切块（含参数覆盖重切） | `fac_rag.chunking_v2.chunk_file_full(path, group=...)` |
| 超长 HTML 行展开 | `fac_rag.chunking_v2.expand_lines(lines)` |
| 契约校验与派生字段 | `fac_rag.contracts.Chunk(...).finalize()`、`chunk_hash`、`estimate_token_len` |
| 质检 | `fac_rag.chunking_v2.gate_chunks(chunks, source_text=...)` |
| 分组/元数据/参数 | `fac_rag.chunking.sources.load_sources(...)`、`SourceGroup.params_for/meta_for` |

运行环境变量：`FAC_ROOT`（默认 `C:\Users\QiuYe\Desktop\FAC-RAG`）。

---

## 参考资料与许可

设计参考了 ChunkViz、Docling-Studio、rag-chunking-playground 等 MIT 项目，
**只借鉴交互与布局，代码自写**；GPL / AGPL / 无 License 的项目一律只读思路。
详见 [docs/08-references.md](docs/08-references.md)。

本仓库许可证：**待定**（建议 MIT；如包含第三方代码片段，将在文件头注明来源与许可证）。
`reference/`、`scratch/` 目录已在 `.gitignore` 中排除，不会被推送到 GitHub。
