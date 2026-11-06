# 05 · 原文 ↔ 切片对齐方案

> 问题：chunking v2 的契约里**没有源行号 / 字符偏移**，且 chunk 正文是「渲染清洗后的文本」，
> 不是源文件的逐字子串。因此不能在原文里直接搜字符串——必须做规范化匹配。

---

## 1. 为什么难

`chunk` 的生成链路（`chunking_v2`）：

```
源 .md
  → expand_lines()        超长单行 HTML 表被展开成多工作行（source_line_map 记录映射）
  → detect_regions()      掩码目次/前言等区域
  → parse_document()      按 family 解析成 Unit（lines 已 strip + clean_*_line 清洗）
  → render_units()        clean_lines（图片→[图示]、去空行）→ clean_text（latex/md_bold/html/page_tail…）
                          → 按 chunk_hash 去重 → 按 max_chars 拆 part → id
```

因此：

- `unit.lines` 是 `raw.strip()` 后去掉列表符、`**`、`#`、HTML 标签的结果；
- 标准式（GB）还会在块首行注入面包屑（`1 总则 > 1.1 …`）；
- 表格块正文是整张表的渲染文本（`表A.1 ...` + grid 行），与源行不是逐字关系；
- 直接做「chunk ∈ source」搜索 → **0 命中**（实测：消防法 74/74 全失败）。

---

## 2. 三套坐标

| 坐标 | 定义 | 用途 |
|---|---|---|
| 源行号 `src_line` | 1-based，原始 `.md` 的行 | 中栏显示、高亮区间 |
| 工作行号 `work_line` | 0-based，`expand_lines` 之后的行 | 对齐算法的搜索空间 |
| 字符偏移 `[start, end)` | 原文全文（按 `\n` 连接）的偏移 | 前端行内拆分、跨块合并 |

映射：`source_line_map[i] = 原始行号`（仅当发生展开时非空；未展开 = 恒等）。
源行 → 工作行需要在展开信息里反查。

---

## 3. 三层对齐算法

```python
def align_chunks(chunks, work_lines, extras, *, source_line_map=None) -> dict[str, Span]:
    """返回 {chunk_id: Span(start_line, end_line, status)}；纯函数、无 IO。"""

    # ── 第 1 层：条文 / 段落 / 附录块 ────────────────────────────────
    # 1) 拆分 chunk 正文为行；取首个非空行，剥面包屑 text.split(" > ")[-1]
    # 2) 规范化：去 HTML 标签、去全角空格/多余空白、统一小写
    # 3) 从游标位置向后线性扫描 work_lines 找首个命中行
    # 4) 命中后按行数向后对齐剩余行（允许 ±1 行漂移）
    # 5) 扫描到文末仍未命中 → 回卷到文首再扫一遍（处理并表/重排）
    #    status = exact（整段命中） / approx（部分命中）

    # ── 第 2 层：表格块（chunk_kind == "table"）──────────────────────
    # 1) 取 extras["table_grid"]["rows"]，每行拼成规范化字符串
    #    规则：去 "|"、去 HTML、去空白、去相邻重复单元格（合并单元格展开产物）
    # 2) 在 work_lines 上找连续命中窗口 → start/end
    #    status = table_rows

    # ── 第 3 层：表题/表号回填 ─────────────────────────────────────
    # 用 extras.table_label / table_title 在已知区块附近（前后 200 行）搜索
    # 命中则补全或修正 span（status 保持 table_rows / 升级 exact）

    # 未命中 → status = miss，span = None（前端灰标，不伪造高亮）
```

对齐结果按 `source_sha256` 缓存到 `cache/align/<sha256>.json`；源变更即失效。

---

## 4. 规范化函数（对齐成败的关键）

```python
def _norm_line(s: str) -> str:
    s = RE_TAG.sub("", s)                    # HTML 标签
    s = s.replace("　", " ").replace("\u00a0", " ")  # 全角/不换行空格
    s = re.sub(r"\s+", "", s)               # 一切空白
    return s.strip().lower()

def _norm_row(cells: list[str]) -> str:
    # 合并单元格会在 grid 里产生相邻重复列：["1","1","阿片","阿片",…]
    deduped = [c for i, c in enumerate(cells) if i == 0 or c != cells[i - 1]]
    return _norm_line("".join(deduped))
```

源行侧对应：`_norm_line(line.replace("|", ""))`，再与 chunk 行规范化结果比较。

---

## 5. 实测覆盖率（2026-11-04 快照）

| 文件 | 块数 | 对齐成功 | 状态分布 |
|---|---:|---:|---|
| 中华人民共和国消防法 | 74 | 74（100%） | exact/substr 全部 |
| GB 50016-2018 建筑设计防火规范 | 531 | 531（100%） | substr 466 / table_rows 64 / exact 1 |
| GB 15499 事故伤害损失工作日标准 | 294 | 294（100%） | 条文 + 表格全命中 |
| 危险化学品目录 | 469 | 468（99.8%） | table_rows 415 / exact 48 / substr 5 |
| 2025 版山东省自由裁量标准（FAC-RAG） | 836 | 836（100%） | table_rows 684 / exact 146 / substr 6 |
| DB37T 2464-2024 「九小场所」消防安全管理要求（本地包） | 104 | **104（100%）** | substr 84 / exact 17 / table_rows 3 |
| DB37T 2972-2026 非煤矿山双重预防机制建设规范（本地包） | 185 | **185（100%）** | substr 79 / table_rows 71 / exact 35 |
| 全部 97 份（FAC-RAG 清单） | 12 718 | **12 626（99.28%）** | 未对齐 92 块 |
| 全部本地 `data/` 源 | 1 046 | 1 045（99.9%） | 未对齐 1 块 |

> 结论：早期版本的表格密集文档缺口（危化品 11.3%、山东 62.7%）已由下面的三项改进解决；
> 剩余 miss 集中在少数合并单元格/重排复杂的表格，UI 灰标、不伪造高亮。

### 5.1 三项改进（2026-11-04）

| 改进 | 问题 | 做法 | 效果 |
|---|---|---|---|
| 整表单行复用 | 整张表被压成一行 HTML（`len >= 2000` 才回退游标），< 2000 的单行表第一行命中后游标被推过整行，后续行无法再命中 | `_is_table_line(raw)` = 长度 ≥ `HUGE_LINE_CHARS` **或** 行内含 `<table`；命中后 `j = hit if _is_table_line(...) else hit+1` | 2972 从 160/185 → 185/185 |
| 表题锚定 | 两张表前几行内容相同（例：表C.1 与表C.2 都以 `10 00 1000 其他 其他` 开头），后表的切片先命中前表整行 → span 跨两张表 | `_caption_candidates()` 在 `(cursor-2 .. last+10)` 找匹配 `table_label` 的 `^表\s*[A-Za-z]?\s*[.．]?\s*\d` 行；以「表题下一行 ~ 下一条 caption 前」为窗口整表重匹配，全部行命中才采纳，span 从表题行开始 | 跨表 span 9 → 5；2972 `-0142` span [724,731] → [727,731] |
| 相似度兜底 | 源文里 `m<sup>2</sup>` 清洗成 `200m2`，chunk 正文是 `200m` → 精确/子串都失败 | 精确/子串失败后在 `start..start+600` 行内做 `difflib.SequenceMatcher`，跳过长度差 > `max(8, len//4)` 的行，`ratio >= 0.93` 取最佳 | 2464 从 99/104 → 104/104；全量 +6 块 |

> 三项组合后全量：13 764 块 / 13 671 可定位（**99.32%**），
> 档位 `substr 8 720 / exact 716 / table_rows 4 206 / table_label 29 / miss 93`。

### 已知失败模式与对策

| 失败模式 | 表现 | 当前对策 | 根治 |
|---|---|---|---|
| 面包屑注入 | 标准式块首行多出 `1 总则 >` 前缀 | 匹配前 `split(" > ")[-1]` | P5 记 `src_lines` |
| HTML 单行超长表被展开 | 112 源行 → 3,112 工作行 | 折叠显示 + `source_line_map` 反查 | 同上 |
| 合并单元格重复列 | grid 行 `1 1 阿片 阿片 …` vs 源行 `1阿片鸦片…` | `_norm_row` 去相邻重复 | 同上 |
| 表号误配 | `表3.2.1` 在条文说明再次出现，游标跳错 | 扫描文末 + 回填 + 去重 | 用表格 bbox/行号确定性定位 |
| 游标单调推进 | 一次 miss 后后续连续 miss | 允许回卷重扫 | 同上 |
| 手改正文 | 与原文脱钩 | 标 `manual`、span 置灰 | 设计允许 |

---

## 6. 根治方案：给 Unit 加 `src_lines`（P5，改 FAC-RAG）

不做进 15 字段契约，只进 extras 侧车，因此**对下游检索零影响**：

1. `chunking_v2/model.py`：`Unit` 增加 `src_lines: tuple[int, int] | None`（工作行区间，0-based）；
2. `chunking_v2/units.py` 三个 handler（`parse_statute` / `_parse_standard_blocks→_to_units` /
   `_table_block_to_unit`）在构造 `Unit` 时记录当前源行号；
   - statute 已有循环变量 `lineno`（`units.py:204`）可直接用；
   - standard 的 blocks 需要沿 `masked` 行号回溯；
   - 表格块由表格行范围直接给出（`tables.py` 解析时已知道行下标）；
3. `chunking_v2/render.py`：把 `unit.src_lines` 写进 `extras_by_id[id]["_src_lines"]`，
   多 part 时按 `split_text` 的行分组给出每 part 的子区间；
4. 展开映射：若该文档触发过 `expand_lines`，用 `source_line_map` 把工作行还原成源行；
5. 本工具 `fac_bridge.py` 优先读 `extras["_src_lines"]`，缺失时回落到启发式对齐。

工作量约 1 天（含回归：`tools/run_chunking.py` 全量重跑 + gate 结果不变 + 97 文件产物 diff）。

---

## 7. 验收标准

- [ ] 消防法 74/74 块 `exact`，点击任意块中栏滚动并高亮到正确行；
- [ ] GB 50016 的 64 个表格块显示 `table_rows` 且高亮到对应表格区域；
- [ ] 危险化学品目录：可达 11 个百分点的块正常高亮，其余显示 `miss` 灰标（不伪造）；
- [ ] 山东自由裁量：>=62% 命中，且 miss 块可手动拖选区间后转为 `manual`；
- [ ] 任意文档对齐耗时 < 2s（97 文件全量缓存后 < 0.2s）。

## 6. 提交区间钉住与表题锚定（2026-11-07）

- **钉住 span**：`extras.jsonl` 行里若有 `span: [start, end)`（0-based 半开），`align_chunks` 直接采用并把状态记为 `approx`，不再扫描正文。
- **表题锚定**：块正文首行匹配 `^表\s*[A-Za-z]?\s*[.．]?\s*\d` 时，先定位表题行，再只为「表题下一行到下一张表题之前」的行做顺序匹配；起点不早于游标，避免两个块从同一张表题开始而重叠。
- 效果：DB37T 1915 的 5 处 span 重叠（提交后重新对齐导致）降为 0；新提交的文件打开后行区间与提交前一致（81 块对照差异 0）。

## 7. 旧版产物的兼容兜底（2026-11-07，山东自由裁量）

早期版本提交时只写 15 字段，`span` 不落盘；同时界面按位置重编了块的 id，而 `extras.jsonl` 里仍是引擎原始 id。这类文件打开后会先「重新启发式对齐」，大表格常对不上。为此加了三层兜底：

1. **extras 内容回挂**：`studio._match_extras()` 先按 id 取；当 extras 行有 `embed_text` 且与块正文（去空白）不一致时，用 `embed_text` 建索引按正文重匹配；`/api/doc` 与 `/api/tables` 都走这条路径。
2. **表号复用区间**：对齐时记录「表号 → 已知 span」。没有 `table_grid`、正文也匹配不上的表格块，按 `law_num` / `table_label` 里的表号复用同表已知区间（状态 `table_label`）。
3. **同一张表行**：若上一已知 span 是「单行且该行含 `<table`」，说明整张表压在这一行上，后续拆出来的块也归到这一行。

效果（用户实际文件 `2025版山东省自由裁量标准(1)`，708 块）：46 块无 span → 内容回挂后 41 → 表号复用后 6 → 单行表格兜底后 **0**，全部 708 块对齐；表格网格按内容重挂后 646 张可见（其余块的正文被人工改过，无法与引擎侧车对应，卡片退回「正文 + 原文表格」展示）。

提交时 `drafts.merge_extras_spans()` 也按内容认领旧行：id 对不上但 `embed_text` 相同的行会被改写为新 id 并写入 `span`，不会重复追加行。
