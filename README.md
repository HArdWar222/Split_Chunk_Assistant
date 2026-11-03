# SplitChunkAssistant

> **RAG 切块的可视化人工切片工作台** —— 自动切完能看、能改、能存、能导出。

给本地 FAC-RAG（默认 `C:\Users\QiuYe\Desktop\FAC-RAG`）的 `chunking_v2` 切块流水线挂一个本地网页工作台：
原文（Markdown）与切片（chunk）双向联动，支持人工 `split / merge / drop / add / 改字`，
支持调整切分参数后重切，人工修改写入覆盖层，导出仍然是原来的 **15 字段契约**。

**当前状态：P2 已实现（2026-11-03）** —— P1 的「原文 ↔ 切片」三栏只读联动之上，已支持人工编辑：

- 每个切片的卡片底部有 **拆 / 并↑ / 并↓ / 删 / 改** 五个操作（首尾块的「并」自动禁用）；
- 在原文拖选覆盖 2 个以上切片后右键，菜单里会出现「**⤵ 合并所选 N 个切片**」，一次把选中的连续多块合并成一块（保留第一块 id/条号/kind，行区间取并集；散块不允许，避免误吞）；
- **在原文里拖选一段文字 → 右键**：弹出上下文工作栏，可直接「用选区新增切片 / 拆分此切片 / **✎ 编辑此切片正文·条号** / 并入上一块 / 并入下一块 / 删除此切片 / 定位到切片」，
  执行后右栏切片列表与原文范围条**立即刷新**（无需刷新页面）；
- **右键落在标题/目录/空行等没有切片的原文行**：菜单不再消失，而是显示「ℹ 第 N 行不属于任何切片」，并给出「**✚ 用这一行新增切片**」——把这一行原文带入新增弹层（可改正文、条号、kind）；顶部缺口新增的块会**插到列表最前面**，保持原文顺序；
- **在原文里拖选一段文字，点「＋新增」即可把选区变成新切片**：正文自动带入、行区间一并记录
  （新块带「手工」徽章、`span` 行区间，原文范围条上能看到它，也能点击联动定位）；不选区时仍可手动输入正文新增；
- 顶部工具栏：**未保存徽章**、**diff 计数 `+新增 ~修改 -删除`**、**撤销 / 重做**、切片面板 **新增 / 重置**；
- 拆分支持 **行级（多行块给行单选）**、**字符级（单行块给字符位置）**、**在原文光标/选区处直接拆**（右键工作栏首项「✂ 在此处拆分 / ✂ 在光标处拆分」，按落点在块内第几行自动选行级或字符级）；合并必须相邻、**「并↑」「并↓」两个方向都支持**、保留前块 id；
- **拆开的两块会自动补回原文行区间**（不再显示「未对齐」）：右栏卡片各自显示新的行号范围、左侧范围条立即断成两段；拆分弹层可填「**下半块条号**」（留空时自动识别切点处的条号——「第X条」或「5.4.1」这种标准式层级号，识别不到才继承原块——合并多块再拆开时，下半块会拿回自己的条号），之后再想改条号用卡片「改」或右键「✎ 编辑此切片正文·条号」即可；
- **新增块的条号按前后邻居倒推**：不填 `law_num` 时，优先沿用前一块的条号（「第X条」或「5.4.1」这种标准式，判断为同一块的续写），前面没有就看后一块；新增弹层会实时提示「留空则按前后块倒推：第二条 / 5.4.4」；表号 / 附录号 /「（新增）」不作为倒推参照；
- **合并块显示覆盖条号**：合并多块后，卡片头会出现「**覆盖 第一条–第三条（3 条）**」或「覆盖 5.4.1–5.4.4（4 条）」徽章（从块的行区间 + 原文里的条号倒推，纯 UI 提示，不写入 15 字段契约）；
- **拆分光标反馈**：在原文里点一下就会出现**蓝色拆分光标 + 上方提示气泡**——告诉你落在第几行、会按「行边界」还是「光标处」拆分、拆的是哪个切片；不可拆的位置（块首/块尾/折叠行）会直接提示原因，不属于任何切片的行也会说明；同时右侧对应卡片蓝色描边高亮。点行号列不会误触光标。
- **拆分无反应时先硬刷新一次（Ctrl+F5）**：浏览器标签页可能还缓存着旧版 `app.js`；静态资源的 URL 已带 `?v=` 版本串，正常刷新即可拿到新代码；
- 手改过的正文标 `manual` 徽章、`align = manual`，派生字段（`token_len` / `chunk_hash`）自动重算；
- 快捷键：`Ctrl+Z` 撤销、`Ctrl+Y / Ctrl+Shift+Z` 重做、`Ctrl+Shift+N` 新增、`Ctrl+Enter` 拆当前块、`Delete` 删当前块、`Ctrl+K` 搜索、`Esc` 关弹层；
- P2 的编辑只在内存（按文件隔离、按会话隔离），刷新页面回到基线；**P3 才落盘**。

P1 的只读能力（三栏联动、拖选反查、原文左侧范围条、深链 `#doc=<key>`、对齐如实分档 exact / substr / table_rows / table_label / miss）
全部保留。首批验收目标「消防法 74/74」达成；全语料 12 718 块中 12 251 块可定位（96.3%）。

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

## 快速开始（P2 可用）

```bat
:: 前提：本机已装 Python 3.10+，且 FAC-RAG 位于本仓库的兄弟目录
:: 方式一：双击 / 命令行
run_studio.cmd

:: 方式二：直接跑模块
set FAC_ROOT=C:\Users\QiuYe\Desktop\FAC-RAG
python -m chunk_studio.server --port 8790 --open
```

启动脚本说明（2026-11-03 修复）：

- `run_studio.cmd` 是 **纯 ASCII + CRLF** 的批处理文件。此前版本是 UTF-8 + LF，cmd.exe 按 OEM 代码页读会把中文注释行拆坏、报「'默认端口' 不是内部或外部命令」，现已重写；
- 脚本会自动找 `python`（找不到再试 `py -3`），失败时窗口会 `pause` 停住显示原因，不再一闪而过；
- 如果 8790 上**已经有本工具在跑**，脚本会识别出来并直接打开浏览器（退出码 0），不会报端口占用；
- 想换端口：`run_studio.cmd --port 8801`（或页面「⚙ 设置」里改）。

浏览器打开 http://127.0.0.1:8790 ：

- 左侧 97 个法规文件（分组 / 搜索 / 块数 / gate 徽章），点击打开三栏；
- 中间是原文 `.md` 的源码行（行号 gutter、标题/引用/表格/超长行折叠）；
- 右侧是切片卡片（15 字段摘要 + extras + 对齐状态），顶部显示 `对齐 N/N (率) · 各档计数`；
- hover 卡片 → 原文行淡高亮；hover 原文行 → 关联卡片标记；click 卡片 → 滚动并强高亮对应原文行；
- 地址栏形如 `#doc=national_laws%2F中华人民共和国消防法`，可直接收藏/分享定位；
- 卡片底部有 `拆 / 并↑ / 并↓ / 删 / 改` 操作；弹层里填拆分位置 / 改正文与条号 / 新增正文；
- **拆分也能直接落在光标上**：在原文里把光标放到某个切片内部（或拖选一段），右键工作栏第一项是「✂ 在此处拆分 / ✂ 在光标处拆分」，点击即在该位置拆分，无需再选行/字符；
- 在原文拖选文字后**右键**（或直接在切片行上右键）：弹出工作栏，含「✂ 在此处拆分（光标处）/ ✚ 用选区新增切片 / ✂ 拆分此切片 / ✎ 编辑此切片正文·条号 / ⤴ 并入上一块 / ⤵ 并入下一块 / ✕ 删除此切片 / ◎ 定位到切片」，提交后右栏卡片与原文范围条立即刷新；「并入上一块」「并入下一块」两个方向都可用（合并后保留前一块的 id）；右键在无切片行时给「用这一行新增切片」；
- 原文里拖选一段文字后点「＋新增」：弹层自动带入选区文本并记录行区间，提交后新块出现在原文对应行旁（范围条同步）；
- 顶部 `+a ~m -r` 是相对基线的 diff，`未保存` 徽章表示有内存编辑，撤销/重做可逐步回退；
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

验收 / 自测口径（单测覆盖）：

```bat
python -m unittest discover -s tests -t . -v   :: 115 个测试
```

- `/api/files` 合计 **97 个文件 / 12 718 块**，分组 national_laws 37 + GB_laws 60
- `/api/doc?key=…` 返回原文行 + 切片（含 `span` 与 `align`）+ 统计；缺 key 400，未知 key / 源文件缺失 404
- 消防法：411 行 / 74 块 / 对齐 **74/74（100%）**
- P2 编辑：`POST /api/op`（split / merge / drop / add / patch）、`/api/undo`、`/api/redo`、`/api/reset`；
  `add` 可带 `span:[起始行,结束行]`（原文选区新增，1-based 闭区间）；插入位置用 `after_id`（之后）或 `before_id`（之前），都不传则追加到末尾；
  拆分后块数 +1、diff 同步、新块 id = 该文件最大序号 +1（不回填已删编号）
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
├── run_studio.cmd            ← Windows 一键启动（ASCII + CRLF，可识别已在跑的实例）
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
