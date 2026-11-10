# 04 · 数据契约（FAC-RAG chunking v2）

> 本工具**不改变契约**：读取、编辑、导出的都是同一种块。以下内容来自对 FAC-RAG
> `fac_rag/contracts.py`、`chunking_v2/*`、`data/chunks_v2/*` 的实际核对（2026-11-02）。

---

## 1. 单块 chunk：15 字段（唯一契约）

| # | 字段 | 类型 | 说明 |
|---|---|---|---|
| 1 | `id` | str | 全局唯一：`{law_name}-{seq:04d}`，多 part 追加 `-p{i}` |
| 2 | `chunk` | str | 块正文（渲染清洗后，可能带面包屑）。**检索唯一消费字段** |
| 3 | `law_name` | str | 法规/标准名（源文件名 stem） |
| 4 | `law_num` | str | 条号/表号：`第一条`、`1`、`A.7 表D.1 第1-2行` |
| 5 | `law_invalid` | str | 失效日期，占位默认 `2030-12-12` |
| 6 | `law_type` | str | 法律 / 行政法规 / 部门规章 / 地方性法规 / 国家标准 / 行业标准 |
| 7 | `law_level` | str | 法律 / 行政法规 / … / 国家标准 / 行业标准 |
| 8 | `issuer` | str | 发布机关 |
| 9 | `effective_date` | str | 生效日期（可空） |
| 10 | `source` | str | 来源类别（文件级常量）：内置 `法律` / `部门规章` / `地方标准` / `规范性文件`，也可在界面自定义；未标注为空串 |
| 11 | `chunk_kind` | enum | `clause` / `table` / `title` / `front` / `appendix` / `stub` / `paragraph` |
| 12 | `token_len` | int | `len(chunk)` 字符数（非 BPE 计数），派生 |
| 13 | `part_index` | int | 分片序号，1 起 |
| 14 | `part_total` | int | 分片总数，≥1 |
| 15 | `chunk_hash` | str | 规范化正文 sha256 前 16 位，派生；去重与审计键 |

**示例**（`national_laws__中华人民共和国消防法.jsonl` 第 1 块）：

```json
{"id":"中华人民共和国消防法-0001","chunk":"为了预防火灾和减少火灾危害，…制定本法。",
 "law_name":"中华人民共和国消防法","law_num":"第一条","law_invalid":"2030-12-12",
 "law_type":"法律","law_level":"法律","issuer":"全国人民代表大会常务委员会",
 "effective_date":"","chunk_kind":"clause","token_len":45,"source":"法律法规",
 "part_index":1,"part_total":1,"chunk_hash":"9cb48b1e904c448d"}
```

**示例**（用户实测的 DB37T 2464 第 1 块，一次性实切，不在 v2 数据集内）：

```json
{"id":"DB37T 2464-2024  “九小场所”消防安全管理要求-0001","chunk":"范围\n本文件规定了…",
 "law_num":"1","law_invalid":"2030-12-12","chunk_kind":"clause","token_len":104,
 "part_index":1,"part_total":1,"chunk_hash":"0d6044dd149f0001"}
```

### id 与 part 规则

- 正则：`^(.+)-(\d{4,})(?:-p(\d+))?$`；
- `seq` 来自 unit 的 `meta["seq"]`：statute 用条号，standard / generic 用块流水号（从 1 递增）；
- `part_total > 1` 时才有 `-p{i}` 后缀；`chunk_kind == "table"` 的块**永不拆 part**；
- **新增块按原文位置重排 id**（P2 已实现，仅 `add`）：插入后对整份列表重排，新块拿它在列表中的序号（首块 `-0001`），插入点之后的块序号顺延；`part_total > 1` 的块与相邻同组 part 继续共用 base 序号（如 `-0003-p1`/`-0003-p2`）。这样 id 序号始终等于该块在原文中的位置，而不是永远拿队尾的 `max_seq+1`；
- 拆分产生新 id（下半块 `max_seq + 1`，暂不重排），合并保留前者 id，撤销栈负责还原。
- 排序与 id：列表顺序恒等于原文行区间顺序（`span[0]` 升序、稳定），无 `span` 的块排最后；每条编辑命令执行后统一「排序 + 按位置重排 id」，所以 `id` 序号始终等于当前位置；排序是确定性的，撤销/重做重放可复现。
- **扩展切片（`extend`，P2 已实现）**：`{op:"extend", id, ids:[目标,…中间块], chunk?, law_num?}` 一个原子命令——校验 `ids[0]==id`、ids 连续唯一，正文按组内顺序拼接（缺 `chunk` 时取原文行），合并后删中间块、保留目标 id、最后按位置重排 id；编辑边界以「行」为单位（文本=源文件行，表格=`tr` 行）。
- **自动分 part（P2 已实现，设置里 `auto_part` 开启；P3 起物化为真实块）**：编辑过程中一产生超长块就立刻拆成真实 `-pN` 块——文本块按行装箱均分（`part_index/part_total` 更新、每段重复面包屑、尾段不足 `min_part_chars` 时与上一段重排），part 继承原块 `span` 与 `align`，并用视图字段 `part_span`（1-based 闭区间）记录它真正覆盖的行段；表格块**只做「多行超长 → 按行区间拆成独立表格块」**（新块不是 part，`part_index=part_total=1`，`law_num` 写 `表X 第a-b行`，`span` 继承整表行，单行超大表格保持整块不拆）。part 是真实块（可被后续命令选中/删除/合并/改正文），删除单个 part 后同组剩余 part 自动重排编号（只剩一个时变回普通块）；**段落块与表格块合并成的超长块按类型边界拆开**——文本段成 paragraph 块、每个表格行成一个 table 块（`law_num`/`span`/`table_grid` 等从原表格块找回，重复的面包屑行丢弃），落盘时仍只写 15 个契约字段（`part_span` 等视图字段不入库）。
- 因为 id 会随插入顺延，`chunk_diff` 不再按 id 集合比较：改为按「正文+条号+类型」做 LCS 配对，未配上的再按「条号+类型」和正文相似度兜底配成修改，剩余才计新增/删除 → 插入只报 `+1 ~0 -0`。
- 视图层 `span`（1-based 闭区间，非 15 字段契约）：拆分时在「原块 span 窗口」内按规范化行顺序定位，两半各自补回行区间；`second_law_num` 可指定下半块条号，留空时按下述优先级自动识别（P2 已实现）。
- **拆分时下半块 `law_num` 的取值优先级**（`ops._split`）：① 命令里显式的 `second_law_num`；② 下半块正文前 4 行的条号（`detect_law_num(second_text)`，先匹配「第X条」正则 `^\s*(?:[#>*\-+]\s*)*第\s*[〇零一二三四五六七八九十百千两0-9]{1,8}\s*条`，再匹配标准式数字层级号 `^\s*(?:[#>*\-+]\s*)*(\d{1,2}(?:\.\d{1,4}){1,5})(?=\s|\u3000|$|[^\d.])`，如 `5.4.1`；带小数点可避免把「2019年」误认成条号）；③ 切点所在原文行往下 4 行内的条号（`detect_law_num(source_lines[second_span[0]])`）；④ 仍找不到才继承原块条号。→ 合并多块后再拆开，下半块会自动识别出它自己的条号（`第二条` 或 `5.4.2`），而不是沿用前一块的。

### 编辑后 15 字段的对应规则（P2 已实现）

把 15 字段按「谁负责维护」分三类，编辑时各按自己的规则走：

| 类别 | 字段 | 规则 |
|---|---|---|
| A. 文件级继承（不随编辑变） | `law_name`、`law_type`、`law_level`、`issuer`、`effective_date`、`source`、`law_invalid` | 来自 `configs/sources.yaml` 分组默认值 + 源文件名；编辑不改、不丢；只有 `add` 从**同文件第一块**继承一份 |
| B. 块级身份（命令决定） | `id`、`chunk`、`law_num`、`chunk_kind`、`part_index`、`part_total` | 见下方矩阵 |
| C. 派生（系统永远重算，禁止手填） | `token_len`=`len(chunk)`、`chunk_hash`=规范化正文 sha256 前 16 | 每次正文变化后由 `_derive()` 重算 |

| 操作 | `id` | `chunk` | `law_num` | `chunk_kind` | `part_*` |
|---|---|---|---|---|---|
| 拆分 split | 上半保留原 id；下半 `max_seq+1` | 上下两段各自裁切；派生字段重算 | 上半保留；下半默认继承，可用 `second_law_num` 指定 | 上下都继承原 kind | 两半都重置为 `1/1` |
| 合并 merge | 保留**前一块** id | 前 + `\n` + 后；派生字段重算 | 保留前一块 | 保留前一块 | 重置 `1/1` |
| 集体合并 merge_many | 保留**第一块** id | 各块正文按文档顺序 `\n` 连接；派生字段重算 | 保留第一块 | 保留第一块 | 重置 `1/1` |
| 删除 drop | 删除 | — | — | — | — |
| 新增 add | 按原文位置重排（新块 = 列表序号；插入点之后顺延；同组 part 共用 base） | 输入正文 | 输入值；留空时按前后邻居倒推（见下），推不到才 `（新增）` | 输入值，默认 `paragraph` | `1/1` |
| 改字/改条号 patch | 不变 | 改字则替换并重算派生字段 | 可只改 `law_num`（正文不动、派生字段不动） | 可改，须在 7 种枚举内 | 可改，须满足 `part_index ≤ part_total`；`table` 块不参与 part |

实测（消防法 74 块）：拆 `-0001`（45 字）→ 上半 10 字/`token_len` 10/hash `accbb4ac…`、下半 `-0075` 35 字/hash `dcf0fd75…`/`law_num=第九九条`；合并 `-0002` 并上 `-0001` → 保留 `-0001`、`token_len` 123、hash `c8cc5226…`；`patch` 只改 `law_num` → `token_len`/`chunk_hash` 不变（正文未动）。

视图层的 `span` / `align` / `manual` **不属于** 15 字段契约：`span` 用于原文高亮与范围条，`manual=true` 表示人工改过（导出时写进 extras 侧车，便于审计），`align` 只是对齐状态（`exact/substr/table_rows/table_label/approximate/manual/miss`）。

**extras 侧车（第 2 层）在编辑时的对应**：拆分下半块与新增块只继承最小集合 `clause / region / ref_title / num_cn`，**`profile` / `namespace` / `probe` 会缺失**；合并保留前块 extras，`patch` 保留原 extras。P3 导出时需要为人工块按文件基线补齐这三个键（`namespace` 默认 `body`，`profile`/`probe` 取源文件 manifest 值），表格块拆分时表格信息（`table_label/table_title/table_rows/table_grid/table_mode`）留在上半块。

**合并块的覆盖条号（视图层提示，不入契约）**：合并后卡片头显示「覆盖 第一条–第三条（3 条）」或「覆盖 5.4.1–5.4.4（4 条）」，由前端遍历该块 `span` 覆盖的原文行、收集其中的条号（「第X条」或 `5.4.1` 数字层级号）得出（`coveredLawNums`）。`law_num` 是检索字段（BM25 会吃），所以不把区间串塞进 15 字段。

### 从原文未覆盖区域新增（P2 当前行为）

原文里未被任何切片覆盖的行（前置说明、目录、章节标题行、空行、注释等）也可以拖选后右键「用选区新增」，或直接在某一行上右键「✚ 用这一行新增切片」。此时 15 字段按以下方式确定：

- 插入位置由 `after_id`（该块之后）/ `before_id`（该块之前）控制；**顶部缺口（比所有切片都靠前）会插到列表最前**（`before_id = 第一块`），使列表顺序仍与原文一致；都不传则追加到末尾；
- `id` 按原文位置重排：新块拿它插入后的列表序号（首块 `-0001`），原有块序号顺延；同组多 part 共用 base 序号。这样顶部插入不会再出现 `-0075` 这种队尾号；`chunk` = 选区/该行原文（**原样，含 Markdown 记号**）；`law_name` = 文件名；
- `law_num` 留空时按前后邻居倒推（`ops.infer_law_num`）：先取**前一块**的条号（「第X条」或标准式数字层级号 `5.4.1`，判断为同一块的续写），前面没有可参照的条号就看**后一块**；参照只认这两种条号，表号 / 附录号 /「（新增）」不作参照，推不到才写 `（新增）`（此时**需人工补**，如 `前言` / `目录` / `第一章`）；
- `chunk_kind` 默认 `paragraph`（弹层可改成 `front` / `title` / `appendix` 等）；
- `law_type` / `law_level` / `issuer` / `effective_date` / `source` / `law_invalid` 继承**同文件第一块**（文件级默认值）；
- `source` 是**文件级人工类别**（`chunk_studio/sources.py`）：左栏右键文件设置后，写 `data/sources.json`（`{"categories": [...类别], "files": {law_name: 类别}}`，类别列表内置四项、可新增自定义项）并把该文件 `chunks.jsonl` 所有行统一改成该类别；有草稿时草稿块同步并按新基线重命名；改类别只动 `source`，不改变审核状态（状态文件按新 chunks 指纹续存）。重新切块后由 `chunker` 按 `sources.json` 回填，不会丢；`source` 不参与 `chunk_hash`；
- `token_len` / `chunk_hash` 按新增正文重算；`part_index=1 / part_total=1`；
- 视图层：`span` = 选区行区间（1-based 闭区间），`align="manual"`、`manual=true`，`extras={"region":"body"}`（P3 导出时补 `profile/namespace/probe`）；
- 纯空白选区会被后端拒绝（「新增切片正文不能为空」）。

### 已知契约级限制（来自代码审查）



- `render.py` 的去重键**只看正文哈希**（不含 `law_num` / `chunk_kind`），
  因此「空正文占位块」与「不同条号但正文相同」可能被误吞；v2 产物中已有 1 块被去重丢弃。
- 工具侧不修此问题（属 FAC-RAG 改造项），但会在 UI 里给「疑似重复」标记。

---

## 2. extras 侧车（不进正文、不进向量）

`data/chunks_v2/_extras.jsonl` 每行：`{"id": "...", "extras": {...}}`；`runs/chunks_preview/*.jsonl`
则把 extras 内联在块对象里（`15 字段 + "extras"`），本工具两者都兼容。

| 键 | 适用 | 含义 |
|---|---|---|
| `clause` | 通用 | 所属条号（表格/段落块挂靠） |
| `region` | 通用 | 区域：`body` / `appendix` / `front` / `text` … |
| `profile` | 通用 | 识别使用的 profile 名 |
| `namespace` | 通用 | 命名空间（与 region 联动） |
| `ref_title` / `num_cn` | 通用 | 条号标题 / 中文数字序号 |
| `probe` | 通用 | `{family, confidence, heading_mode}` 识别诊断 |
| `table_label` | 表格 | 表号（表A.1） |
| `table_title` | 表格 | 表题 |
| `table_rows` | 表格 | 行数 |
| `table_mode` | 表格 | `model`（整表）/ `pack`（打包） |

**手工表格块的展示网格（P2 视图层，不入契约）**：从表格行/表格块新增的切片在正文里保留 `\t`（列）/`\n`（行）分隔，右栏据此还原网格表；表头从同一表格块已有切片的 `table_grid.headers` 继承（列数匹配才用），不写入正文、不写入 extras。因此手工块没有自己的 `table_grid`，`/api/tables` 也不会返回它；P3 落盘时如需让下游复用这份结构，再把网格写进该块的 extras。
| `table_grid` | 表格 | `{headers, rows, header_rows, width}` 表格网格 |

`table_grid` 只用于**视图层**画表：右栏表卡片按它渲染网格表（表头补齐、合并单元格抄平、`note_from` 之后的注释行黄底），原文侧则直接解析源 `.md` 的管道表 / HTML 表渲染；它不进入 15 字段、也不参与检索。人工改过正文的表格块（`manual=true`）前端不再使用旧 grid，避免显示过期内容。

示例：

```json
{"id":"2025版山东省自由裁量标准-0001",
 "extras":{"ref_title":"山东省应急管理 行政处罚自由裁量基准","region":"body",
 "profile":"tabular","namespace":"body",
 "probe":{"family":"tabular","confidence":0.9,"heading_mode":"off"}}}
```

---

## 3. 单文件 record（8 键）

`chunk_file` / `chunk_file_full` 的 record 严格是这 8 个键：

| 键 | 内容 |
|---|---|
| `path` | 源文件绝对路径 |
| `folder` | 所属语料目录 |
| `law_name` | 文件名 stem |
| `chunks` | 15 字段块列表 |
| `parsed` | `ParseResult` **dataclass 对象**（title / units / gaps / warnings / skipped_lines），序列化需要 `dataclasses.asdict` |
| `sanity` | `{n_chunks, n_law_num_parseable, law_num_ratio}`；条号可解析率低于阈值会拒绝整份（generic/tabular 阈值为 0，其余 0.5） |
| `summary` | `{n_chunks, n_units, n_parts_extra, chars_in_chunks, chars_in_source, char_coverage, kinds, n_gaps, n_warnings, max_chunk_chars, over_max_chars}` |
| `source_chars` | 源文件字符数 |

---

## 4. 诊断：gate 与 report

**gate**（`chunking_v2/gate.py`）：

```json
{"ok": true, "n_chunks": 74, "failures": [],
 "metrics": {"line_coverage": 0.94, "toc_leak": 0.0, "bad_block_ratio": 0.0,
             "ref_series": "...", "dup_ref_ratio": 0.0, "table_blocks": 0,
             "warnings": 0, "..."}}
```

metrics 共 26 项，覆盖行覆盖率、目次泄漏、坏块比例、条号序列、重复条号、表格块、
告警数与各类假设检查。阈值来自 `GateRule`：`min_line_coverage=0.60`、
`max_bad_block_ratio=0.02`、`max_dup_ref_ratio=0.01`、`block_count_tolerance=0.40`、`max_warnings=5`。

**report**（probe 识别报告）：`family`（standard / statute / tabular / generic …）、
`confidence`、`features`（16 项行级特征）、`flags`、`heading_mode`（auto/on/off）、
`profile`、`hint`。

---

## 5. 调用家族（6 个入口）

| 函数 | 返回 | 用途 |
|---|---|---|
| `chunk_file(path, group=..., strategy=None, verbose=False)` | `record` | v1 兼容的单文件切块 |
| `chunk_file_full(...)` | `(record, extras_by_id, probe_report)` | 带侧车与识别报告 |
| `chunk_file_gate(...)` | `(record, gate_result)` | 切块 + 质检 |
| `chunk_file_run(...)` | `(record, extras_by_id, gate_result, probe_report)` | 演示/全量入口 |
| `chunk_folder(cfg, sources, folder=None)` | `list[record]` | 按语料目录批量 |
| `gate_chunks(chunks, source_text, ...)` | `gate_result` | 对已有块补跑质检，**不重切** |

本工具实际调用：`chunk_file_full`（读取/重切）、`gate_chunks`（保存后质检）、
`expand_lines`（原文行映射）、`contracts.Chunk.finalize()`（保存校验）。

---

## 6. 下游消费约定

- 检索 / 向量化：只用 `chunk`；
- BM25：`chunk` + `law_name` + `law_num`；
- 展示与过滤：`chunk_kind`、`law_type`、`law_level`、`part_index/part_total`；
- 去重与审计：`chunk_hash`；
- **本工具的编辑不改变上述消费方式**：导出 JSONL 直接可被现有索引/评估流程读取。

## 7. 提交时写入的展示区间（P3）

- 编辑器里的每块行区间（1-based 闭区间，如「第 12–115 行」）在**提交**时写入
  `extras.jsonl` 对应行的 `span` 字段，换算成 **0-based 半开区间** `[start, end)`；
  `part_span` 同理。
- `chunks.jsonl` 仍然只写 15 个契约字段，`span` 不进入正文合约。
- 下次打开文档时，对齐层优先使用 extras 里的 `span`（状态记为 `approx`），不再走
  启发式重猜；没有 `span` 的旧文件仍按启发式对齐。
