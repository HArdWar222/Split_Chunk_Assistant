# 03 · 参数清单（人工修改时能改什么）

> 分五层：**块内容 → 块边界 → extras → 文档级重切参数 → gate 阈值**。
> 右侧卡片改字段，中栏拖手柄改边界，顶栏参数面板改重切规则，gate 面板改质检门槛。

---

## 1. 块级 15 字段

| 字段 | 可改 | 界面 | 改后处理 |
|---|---|---|---|
| `chunk` 正文 | ✅ | 卡片内联编辑 | 重算 `chunk_hash`、`token_len`；标 `manual`，原文区间脱钩（span 灰显） |
| `law_num` 条号/表号 | ✅ | 文本框，宽松校验 | 参与 BM25；格式异常给提示但不阻断保存 |
| `chunk_kind` 类型 | ✅ | 下拉 | 决定是否允许拆 part（`table` 不拆 part） |
| `part_index` | ✅ | 数字框 | 保存前校验 `1 ≤ part_index ≤ part_total` |
| `part_total` | ✅ | 数字框 | 提供「规范化 part」按钮自动重排 |
| `id` | ⚠️ 高级模式 | 默认锁定；开启后文本框 + 严格校验 | 必须匹配 `^(.+)-(\d{4,})(?:-p(\d+))?$` 且全局唯一，否则拒存 |
| `law_type` / `law_level` / `issuer` / `effective_date` / `source` / `law_invalid` | ⚠️ 高级面板 | 默认继承 `sources.yaml` 组默认值，可 per-chunk 覆盖 | 与文件默认值不一致时提示，审计记录 override |
| `law_name` | ❌ | 只读 | 文件级身份、id 前缀、目录归属，改了断链 |
| `token_len` | ❌ | 派生 | 自动 = `len(chunk)` |
| `chunk_hash` | ❌ | 派生 | 自动 = 规范化后 sha256 前 16 位 |

---

## 2. 块边界（人工切片的本质动作）

| 操作 | 参数 | 说明 |
|---|---|---|
| 拖手柄调边界 | `start_line` / `end_line` | 中栏 gutter 上/下手柄，拖动后块内容 = 对应源行文本，自动重算正文与 hash |
| 拆分 split（行级） | 切点行号、下半块条号（可选） | 拆成上下两块，分别获得新 id；两半自动补回原文行区间（右栏显示行号、范围条断开）；`second_law_num` 留空时自动识别切点处的「第X条」，识别不到才继承原条号（合并块再拆开时下半块能自动拿回自己的条号） |
| 拆分 split（字符级） | `line` + `char_index`、下半块条号（可选） | 行内 inline span 切点，按字符切两块；两半落在同一源行时行区间相同 |
| 合并 merge | `merge_prev` / `merge_next`（可链式多选） | 相邻块合并，part 自动规范化 |
| 删除 drop | — | 从结果移除（源文不动），可撤销 |
| 新增 add | 插入位置（块后 / 文档末尾）+ 源行区间 | 新块 id = 当前最大 seq + 1；带 `span:[起始行,结束行]` 时表示来自原文选区（P2 已实现，界面即「拖选原文 → ＋新增」），不带则 `span=None` |
| 重划区间（高级） | 直接输入 `start` / `end` | 适用于 miss 块手工定位 |

边界操作自动维护：`id`、`part_index/part_total`、`chunk_hash`、`token_len`、`span`、`manual` 标记、diff 计数。

---

## 3. extras 侧车（表格块 + 元信息）

| extras 字段 | 可改 | 场景 |
|---|---|---|
| `table_label`（如表A.1） | ✅ | 表格块改表号 |
| `table_title` | ✅ | 改表题 |
| `table_mode`（整表 / 打包） | ✅ | 表格渲染模式 |
| `table_grid`（headers / rows / header_rows / width） | ⚠️ P3 起可编辑网格 | 改单元格内容或表头层数 |
| `table_rows` | ✅ | 改 grid 后自动重算 |
| `clause`（所属条号） | ✅ | 表格/段落块挂到哪一条 |
| `region`（body / appendix / front …） | ⚠️ 高级 | 影响渲染与检索分区 |
| `profile` / `namespace` | ❌ | 自动识别结果，人工改会破坏重切一致性 |
| `probe`（family / confidence / heading_mode） | ❌ | 诊断信息，只读 |
| 自定义 `note` / `comment` | ✅ | 覆盖层私有字段，不进 15 字段契约 |

---

## 4. 文档级重切参数

改这些 = 调 `/api/rechunk`，用 `GroupOverride` 重跑 `chunk_file_full`，**生成新块集**（预览不落盘）。
与已有手工编辑冲突时二选一：保留手动编辑 / 全部重切。

| 参数 | 默认值 | 作用 |
|---|---|---|
| `max_chunk_chars` | 800（个别文件 1132） | 单块最大字符数，超出后按行打包 / 硬切 |
| `row_pack_chars` | 700 | 表格打包成一行的最大字符数 |
| `ancestor_levels` | 2 | 标准式面包屑层级（`1 总则 > 1.1 …`） |
| `clean_rules` | latex_escapes / md_bold / html_tags / page_tail / continuation_markers | 渲染清洗规则开关 |
| `profile` | 自动识别 | 解析器配置（national_laws / gb_laws / tabular / generic / statute_*） |
| `tables_mode` | `model` / `legacy` | 表格解析模式 |
| `unlabeled` | false | 无表号表格是否识别为表格 |
| `derive_title` | false | 是否派生表题 |
| `drop_appendix` | "" | 附录处理方式 |
| `keep_front_matter` | false | 是否保留前言 |

---

## 5. gate 质检阈值（不影响切块，只影响判断）

| 参数 | 默认 | 说明 |
|---|---|---|
| `min_line_coverage` | 0.60 | 行覆盖率下限 |
| `max_bad_block_ratio` | 0.02 | 坏块（无条号等）比例上限 |
| `max_dup_ref_ratio` | 0.01 | 重复条号比例上限 |
| `block_count_tolerance` | 0.40 | 块数相对基线波动容忍 |
| `max_warnings` | 5 | 告警条数上限 |

在 gate 面板调整后点「重新质检」→ 只跑 `gate_chunks`，不改现有块；结果展示 metrics 与 failures。

---

## 6. 保存时的硬规则

1. 每块过 `fac_rag.contracts.Chunk(...).finalize()`，非法拒绝保存（返回 409 与具体原因）；
2. `id` 全局唯一 + 格式匹配 `^(.+)-(\d{4,})(?:-p(\d+))?$`；
3. `part_index ≤ part_total`，`chunk_kind == "table"` 的块不参与 part 拆分；
4. `token_len`、`chunk_hash` 一律重算，不接受手填；
5. 改动只写 `edits/` 覆盖层，`data/chunks_v2` 与源 md 不动；
6. diff 计数 `+新增 ~修改 -删除` 实时可见，Ctrl+Z 逐级撤销。

---

## 7. 只读项汇总（及原因）

| 字段 | 原因 |
|---|---|
| `law_name` | 文件级身份：id 前缀、目录归属、检索去重键 |
| `token_len` | 派生自正文，手填会与检索预算不一致 |
| `chunk_hash` | 派生自正文，是去重与审计键 |
| `probe.*` | 识别诊断快照，改了失去可追溯性 |
| `profile` / `namespace` | 重切时由 probe 决定，人工改与重切结果矛盾 |
