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

## 5. 实测覆盖率（2026-11-02 快照）

| 文件 | 块数 | 对齐成功 | 状态分布 |
|---|---:|---:|---|
| 中华人民共和国消防法 | 74 | 74（100%） | exact/substr 全部 |
| GB 50016-2018 建筑设计防火规范 | 531 | 531（100%） | substr 466 / table_rows 64 / exact 1 |
| GB 15499 事故伤害损失工作日标准 | 294 | 294（100%） | 条文 + 表格全命中 |
| 危险化学品目录 | 469 | 53（11.3%） | 表格块大面积 miss |
| 2025 版山东省自由裁量标准 | 836 | 524（62.7%） | 表格块部分命中后游标错位 |

> 结论：**条文型文档接近完美；表格密集型文档是主要缺口**，正是 P5 provenance 改造的动机。

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
