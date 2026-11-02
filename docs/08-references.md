# 08 · 参考项目调研

> 调研时间：2026-11-02 / 2026-11-03；星级与许可证为 GitHub API 当时快照，会随时间变化。
> 原则：**借鉴交互与结构，代码自写**；MIT / Apache-2.0 可借鉴，GPL / AGPL / 无 License 只读思路。

---

## 1. 直接参考（MIT / Apache-2.0）

| 项目 | 星标（快照） | 许可证 | 最近提交 | 它做什么 | 我们借鉴什么 |
|---|---:|---|---|---|---|
| [gkamradt/ChunkViz](https://github.com/gkamradt/ChunkViz) | 354 | MIT（`LICENSE.txt`；GitHub 标 NOASSERTION 因文件名） | 2026-02 | 不同 text splitter 的切分可视化：原文着色、overlap 绿高亮、Chunk Size/Overlap 滑块、平均块大小统计；React/CRA，demo 在 Railway / Vercel | 原文着色表达、overlap 表达、参数面板布局 |
| [scub-france/Docling-Studio](https://github.com/scub-france/Docling-Studio) | 268 | MIT | 2026-11 | 在 Docling 之上做可视化：页面图 + bbox 叠加、linked view（hover chunk ↔ bbox）、ChunksEditor（内联编辑 / split / merge / drop / add / bulk / diff / push OpenSearch）、参数重切 | 交互范式：linked view、双向高亮、ChunksEditor 动作集合与 diff 计数；不 fork 代码（Vue3+FastAPI+Docling 与零依赖冲突） |
| [ai-agents-buzz/rag-chunking-playground](https://github.com/ai-agents-buzz/rag-chunking-playground) | 14 | MIT | 2026-03 | 单 HTML 零后端：6 种策略并排、质量分级绿/黄/红、hover 块→原文高亮、成本估算、导出 LangChain/LlamaIndex/Haystack 代码 | 单页零依赖架构、并排对比、悬停联动、导出思路 |
| [0xelitesystem/rag-chunk-visualizer](https://github.com/0xelitesystem/rag-chunk-visualizer) | 0 | MIT | 2026-10 | 单 HTML，4 策略并排（fixed/recursive/sentence/paragraph），overlap 高亮，块统计 | 三栏布局与统计卡片 |
| [ljiang9/chunk-visualizer-web](https://github.com/ljiang9/chunk-visualizer-web) | 0 | MIT | 2026-10 | 中文单页，无需服务器；滑块调 chunk_size/overlap，每块标 `[start:end]` 与长度，橙色高亮 overlap | 中文 UI 文案、区间标注样式 |
| [MichalZnalezniak/Chunking-Vis](https://github.com/MichalZnalezniak/Chunking-Vis) | 7 | MIT | 2026-01 | Django + HTMX，6 种切分（character/word/token/recursive/semantic/agentic），Snapshot 并排对比 | Snapshot 对比交互 |
| [PerryLink/RAG-Chunk-Visualizer](https://github.com/PerryLink/RAG-Chunk-Visualizer) | 0 | Apache-2.0 | 2026-08 | Python CLI + Jinja2 生成 HTML 报告，斜纹高亮 overlap + tooltip，自动编码检测 | 报告式输出、中文文档处理 |
| [feyninc/chonkie](https://github.com/feyninc/chonkie) | 4,818 | MIT | 2026-10 | Python 切块库 + `chonkie[viz]` Visualizer（终端打印 / HTML 报告） | 可视化输出思路；不直接用其切块器（我们的语义切块更贴合法规） |

> 注：`Zeus-Kairos/RAG_V`（3★，解析→切片→检索全链路可视化）与 HF Space `m-ric/chunk_visualizer`
> 提供界面思路参考，许可证未逐一核实，**不复制其代码**。

---

## 2. 只读思路、不可抄代码

| 项目 | 星标 | 许可证 | 原因 |
|---|---:|---|---|
| [langchain-ai/text-split-explorer](https://github.com/langchain-ai/text-split-explorer) | 270 | 无 License，已归档 | 无授权不可复制；仅参考 Streamlit 单文件结构 |
| [zirkelc/chunk-visualizer](https://github.com/zirkelc/chunk-visualizer) | 4 | 无 License | 同上 |
| [pandaxbacon/AutoChunker](https://github.com/pandaxbacon/AutoChunker) | 2 | 无 License | 同上；其层级树编辑器交互可参考 |
| [IngestAI/embedditor](https://github.com/IngestAI/embedditor) | 225 | AGPL-3.0 | AGPL 有传染性；2023 年后停更；PHP/Laravel 栈 |
| [Party4Bread/chunker](https://github.com/Party4Bread/chunker) | 0 | GPL-3.0 | GPL 传染性；但「手动编辑切片」交互可参考 |

---

## 3. Docling 与 Docling-Studio（为什么只借界面）

- **Docling**（IBM 开源，MIT）：把 PDF/DOCX/PPTX/XLSX/HTML/图片解析成 `DoclingDocument`，
  内置 TableFormer 表格识别、公式、图片、阅读顺序，**每个元素带 page_no + bbox provenance**，
  可导出 Markdown/JSON，自带 `HybridChunker`。
- **Docling-Studio**：在其上做「页面图 + bbox 叠加 + linked view + ChunksEditor」的可视化编辑台。
- 为什么不用：FAC-RAG 的原件是 **97 份 Markdown**（扫描件已转写为纯文本，无页码/坐标），
  装 Docling 需要 PDF 与重型依赖（torch 等），而对照对象是行号——收益不匹配成本。
- 结论：**只继承交互范式**（双向联动、diff、编辑动作），原文层改为「Markdown 源码行」。

---

## 4. 知识库平台评估（本期不采用）

| 项目 | 星标 | 许可证 | 结论 |
|---|---:|---|---|
| [infiniflow/ragflow](https://github.com/infiniflow/ragflow) | 93k+ | Apache-2.0 | 不接。只有 Token/Title chunker，不懂法规语义；chunk 编辑是列表式文本增删 + 点击跳原文，非双向联动；需要 MySQL/Kvrocks/NATS/MinIO/ClickHouse/ES + Go/CGO 构建；与 FAC-RAG 的 index/retrieve/eval 重复。最多将来作为「对外检索门面」或通过 chunk API 导入 15 字段块。 |
| [Tencent/WeKnora](https://github.com/Tencent/WeKnora) | 36k+ | MIT | 不接。有 chunk 编辑 + 修订历史（diff/回滚），但整体是微信生态的文档理解平台，体量大。 |
| [labring/FastGPT](https://github.com/labring/FastGPT) | 30k+ | 自定义许可（非 OSI） | 不接。支持手动分段/QA 拆分与 chunk 修改删除，但许可证限制商用/分发。 |

---

## 5. 自家仓库里可直接复用的东西（FAC-RAG）

| 位置 | 内容 | 复用方式 |
|---|---|---|
| `tools/serve.py` | stdlib `ThreadingHTTPServer` + `App.handle(method,path,body)` + `STATIC_ROOTS` 白名单 + `--open` | 本工具 `server.py` 的模板 |
| `tools/dump_chunks_browser.py` | 读 manifest + jsonl 生成静态块浏览页（卡片、搜索、展开/收起）+ CSV | 卡片视图与搜索的起点 |
| `web/console.html` | 现有控制台页面（58KB） | 布局与样式参考 |
| `chunking_v2/gate.py` | 质检规则与 metrics | 直接调用，不重写 |
| `data/chunks_v2/_manifest.json` | 97 文件索引、gate、totals | 文件树的初始数据源 |

---

## 6. 许可证策略

1. 只复制 **MIT / Apache-2.0** 项目的代码片段，且文件头注明来源与版权；
2. GPL / AGPL / 无 License 项目：**只阅读、不复制**，需要时按思路重写；
3. 本仓库自身许可证待定（建议 MIT），发布前在根目录放 `LICENSE`；
4. 第三方字体 / 图标：只用系统字体与内嵌 SVG，不引入外部资源。
