# 07 · 决策记录（ADR）

> 记录「为什么这样做」，避免后续重新争论已定事项。编号 D1–D16，按定稿时间排列。
> 决策时间：2026-11-02 / 2026-11-03；基于 FAC-RAG chunking v2 实测数据。

---

## D1 · 工具独立建在 Split_assistant，FAC-RAG 只读

- **背景**：FAC-RAG 是成熟的项目（97 文件、12.7k 块、有测试与评测），工具的职责是「看与改」，不是「重新切块」。
- **决定**：新工具独立目录 `Split_assistant/chunk_studio/`；FAC-RAG 只作为 Python 包被 import 与读取。
- **理由**：不污染生产仓库；工具可单独迭代、单独发 GitHub；FAC-RAG 升级（含 P5 provenance）不影响工具主线。
- **后果**：需要处理跨仓库依赖（`FAC_ROOT` 环境变量）与契约版本核对；P5 要回 FAC-RAG 仓库开 PR。

## D2 · 只处理 Markdown，不引入 Docling / PDF / OCR

- **背景**：用户最初看到的「原文+切块」可视化范例是 Docling-Studio（基于 PDF 页图 + bbox）。
- **决定**：放弃 PDF 路线；原文直接用 Markdown 源码行，不装 Docling / torch / OCR。
- **理由**：FAC-RAG 的 97 份原件全部是 `.md`（其中 GB 类是从扫描件转写的纯文本，无页码/坐标 provenance）；装 Docling 要 PDF + 重型依赖，收益只是「页图对照」，而我们的对照对象是行号。
- **后果**：失去「bbox 矩形高亮」，改用「行区间高亮」；对齐难度更高（见 [05-alignment.md](05-alignment.md)）。

## D3 · 不接 RAGFlow（也不接其它知识库平台）

- **背景**：用户曾问是否把 RAGFlow 接进来做 chunk 管理。
- **决定**：本期不接；工具自带管理功能。
- **理由**：RAGFlow 的 chunker 不懂法规语义（只有 Token/Title）；chunk 编辑是列表式文本增删，只有「点击跳原文」而非双向联动；需要 MySQL/Kvrocks/NATS/MinIO/ClickHouse/ES + Go/CGO 构建；且与 FAC-RAG 已有的 index/retrieve/eval 功能重复。
- **后果**：放弃现成的多用户/权限/检索 UI；但保留了轻量、可控、零部署成本。

## D4 · 零第三方依赖（stdlib + vanilla JS）

- **决定**：后端只用 Python 标准库 `http.server`；前端只用原生 HTML/CSS/JS，无 npm、无框架、无 CDN。
- **理由**：用户环境是 Windows 桌面、要「双击就跑」；避免 npm 供应链与构建链；FAC-RAG 的 `tools/serve.py` 已证明 stdlib 方案够用。
- **后果**：没有前端框架的响应式便利，需要手写 store 与虚拟列表（约 800 行）。

## D5 · 人工修改写覆盖层，不改基线

- **决定**：所有编辑写 `edits/`（块快照 + extras + meta），`data/chunks_v2` 与源 `.md` 永不被修改。
- **理由**：随时可回滚、可审计、可复现；避免污染生产数据集；便于对比「自动 vs 人工」。
- **后果**：加载需要「覆盖层优先」逻辑；需要 stale 检测（sha256）。

## D6 · 15 字段契约不变，派生字段不可手改

- **决定**：导出仍是 15 字段 + extras；`token_len`、`chunk_hash` 由工具重算。
- **理由**：下游索引/评测零改动；契约字段乱改会破坏可比性与去重。
- **后果**：手改正文的块 hash 变化，与原块不同（这正是审计所需）。

## D7 · 对齐采用启发式 + 状态标注，miss 不伪造高亮

- **决定**：三套坐标、三层算法、四种状态（exact / table_rows / approx / miss）；miss 灰标。
- **理由**：契约没有 offset，硬做「猜」会把错的位置显示得像对的，误导人工复核。
- **后果**：表格密集文档（危化品 11%、山东 63%）大量灰标，用户需手动拖选；P5 根治。

## D8 · 界面范式继承 Docling-Studio，代码不 fork

- **决定**：借鉴 linked view / ChunksEditor 的交互范式与设计文档思路；不整体 fork。
- **理由**：Docling-Studio 是 Vue3 + FastAPI + Docling 重型栈，与本项目「零依赖 + Markdown 行模型」冲突；范式比代码更有价值。
- **后果**：交互细节需自行实现（约 800 行前端）。

## D9 · 可借鉴 MIT 项目，只读 GPL/AGPL/无 License

- **决定**：ChunkViz（MIT）、rag-chunking-playground（MIT）、0xelitesystem/ljiang9（MIT）等可借鉴代码思路；Docling-Studio（MIT）借鉴设计。
- **理由**：许可证合规；避免把 AGPL 代码带进用户仓库。
- **后果**：GPL/AGPL/无 License 项目（text-split-explorer、zirkelc、AutoChunker、embedditor、Party4Bread/chunker）只看思路不复制代码。

## D10 · id 规则：未改保留、新块 max_seq+1、合并保留前者

- **决定**：不重新编号；仅在新增块时取当前最大 seq + 1。
- **理由**：保持与基线/索引的稳定对应关系；避免全量 id 漂移。
- **后果**：可能出现「已删除的 id 空洞」，可接受；撤销栈保证回退。

## D11 · 手改正文的块标记 manual，与原文脱钩

- **决定**：编辑正文后 `flags.manual=true`、span 置灰、不再双向高亮。
- **理由**：手动编辑后正文不再等于任何源行切片，继续显示原高亮会误导。
- **后果**：用户可手动重新拖选区间恢复定位；保存的 `.meta.json` 保留旧 span 供参考。

## D12 · 保存整份快照，不做增量 diff 文件

- **决定**：每个文件保存时写完整块快照 + meta。
- **理由**：简单、可重建、可回滚；97 文件 × 数百块体积完全可控（总产物约 12MB 级）。
- **后果**：每次保存产生全量文件；git 里若跟踪会有大 diff（因此 `edits/` 默认 gitignore）。

## D13 · 分组先支持 national_laws 与 GB_laws

- **决定**：只支持 `sources.yaml` 已注册的两个组。
- **理由**：当前 manifest 也只有这两组（37 + 60 文件）。
- **后果**：新增语料组时需同步 `sources.yaml` 与工具的分组枚举。

## D14 · 高亮用行 div + class 切换，不重建 DOM

- **决定**：每行一个 div，高亮/选中只切 class；行与卡片都虚拟化。
- **理由**：1200+ 行文档与 836 块列表要保持滚动流畅；`innerHTML` 重建会闪烁并丢失滚动位置。
- **后果**：前端需要维护行↔块索引与视口计算。

## D15 · 对齐结果缓存按 source sha256

- **决定**：`cache/align/<sha256>.json` 缓存，源变更即失效。
- **理由**：97 文件全量对齐耗时可观；缓存后打开是常数时间。
- **后果**：缓存需随 `source_line_map` 变化（P5 后）失效重算。

## D16 · P5 改造只进 extras，不进 15 字段

- **决定**：`src_lines` 写 `extras["_src_lines"]`，契约字段不变。
- **理由**：下游消费（向量/BM25/评测）不感知；避免契约版本升级。
- **后果**：extras 体积增加（每块多 2 个整数）；侧车文件仍可控。

## D17 · 改动前先提案、经用户确认再动手（2026-11-03 起）

- **决定**：所有会改文件的动作（代码、文档、探针脚本、提交与推送）都先给出「现状/根因 → 改哪几个文件 → 具体改法 → 影响面 → 验证方式 → 风险与边界」的提案，等用户明确同意后再执行；只读排查（读代码、查数据、grep）不受限。
- **理由**：用户需要控制改动范围和节奏，避免未经确认的重构或扩大改动；每轮改动都应可预期、可回滚。
- **后果**：每轮改动的沟通多一步；因此提案要简短可执行，验证口径必须在动手前就约定好。

---

## 待定问题

| # | 问题 | 倾向 |
|---|---|---|
| Q1 | 仓库许可证选 MIT 还是 Apache-2.0 | MIT（与借鉴项目一致，简单） |
| Q2 | 是否支持渲染视图（非源码行模式） | P5 之后再说 |
| Q3 | 人工修改是否要纳入 git 版本管理 | 默认 gitignore；需要时手工提交 |
| Q4 | 多人协作 / 权限 | 本期不做，单人本地工具 |
| Q5 | 是否把 P5 patch 直接合入 FAC-RAG 主线 | 先在 FAC-RAG 开 PR，独立评审 |
