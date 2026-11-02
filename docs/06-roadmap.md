# 06 · 分阶段计划（P0–P5）

> 总目标：让 FAC-RAG 的自动切块变成「可视化、可人工修正、可导出」的流程。
> MVP = P0 + P1 + P2 + P3（约 2.5–3 天），第一个可演示点 = P1 结束。

---

## 1. 阶段总览

| 阶段 | 目标 | 工期 | 交付物 |
|---|---|---|---|
| **P0** | 服务跑起来，能看到 97 个文件 | 0.5 天 | `python -m chunk_studio.server` 可用 |
| **P1** | 三栏只读可视化 + 双向联动 | 1 天 | 消防法 74 块可点、可看、可定位 |
| **P2** | 编辑动作 + 撤销重做 | 1 天 | 可 split/merge/drop/add/改字 |
| **P3** | 保存/重置/导出/gate/stale | 0.5–1 天 | 改完能存、能导出 15 字段 |
| P4 | 参数重切与新旧对比 | 0.5 天（可选） | 调参后预览、采纳 |
| P5 | 给 Unit 加 `src_lines` | 1 天（可选，改 FAC-RAG） | 表格文档对齐率接近 100% |

顺序依赖：`P0 → P1 → P2 → P3 → P4`，P5 可独立并行（改 FAC-RAG 仓库）。

---

## 2. P0 · 骨架（0.5 天）

| ID | 任务 | 验收 |
|---|---|---|
| T0.1 | 清理工作区、建 `chunk_studio/` 包骨架、更新 README 状态 | 目录与文档一致 |
| T0.2 | `fac_bridge.py`：import 包装、`FAC_ROOT` 校验、`GroupOverride` | 缺 FAC-RAG 时报错信息清晰；能取到一个文件的 record |
| T0.3 | `manifest.py`：读 `_manifest.json` / `_extras.jsonl` / 各 jsonl，建 97 文件索引 | 返回 97 条，含 group/law_name/n_chunks/对齐字段占位 |
| T0.4 | `server.py` + `api.py`：`/api/files`、静态页、gzip、错误处理、`--open` | 浏览器打开看到文件树 |
| T0.5 | 页面骨架：三栏布局 + 文件树渲染 + 空态 | 点击文件不报错（DocView 占位） |

**演示**：`python -m chunk_studio.server --port 8790 --open` → 左侧 97 个文件，点开显示块数。

---

## 3. P1 · 只读可视化（1 天）

**状态：已完成（2026-11-03）** —— 消防法 74/74（100%）、GB 50016 531/531（100%）、
全语料 12 718 块中 12 251 块可定位（96.3%）；实现细节见 [README](../README.md) 与 [05-alignment.md](05-alignment.md)。
下列任务 ID 保留作为验收对照。

| ID | 任务 | 验收 |
|---|---|---|
| T1.1 | `source.py`：源 md 行模型、`expand_lines` 映射、字符偏移表 | 危化品目录能加载 3,112 工作行 |
| T1.2 | `align.py` 条文/段落层：规范化行扫描 + 游标 + 回卷 + 缓存 | 消防法 74/74 exact |
| T1.3 | `align.py` 表格层：`table_grid` 行反查 + 去重单元格 + 表题回填 | GB 50016 表块 100% `table_rows` |
| T1.4 | 中栏渲染：行号、轻量格式化、块着色、节导航、虚拟化 | 千行文档滚动流畅 |
| T1.5 | 右栏卡片：15 字段摘要、状态点、虚拟列表 | 836 块文件打开 < 2s |
| T1.6 | 双向联动：hover 描边、click 锁定、点行反查、自动滚动 | 双向定位正确、无闪烁 |
| T1.7 | 对齐徽章、状态栏统计、`miss` 灰标 | 与审计数据一致 |

**演示**：消防法三栏联动；GB 50016 表格块可定位。

---

## 4. P2 · 编辑（1 天）

| ID | 任务 | 验收 |
|---|---|---|
| T2.1 | `ops.py` 命令接口 + 逆操作 + 会话 undo/redo 栈 | 10 次操作后 undo 10 次回初始快照 |
| T2.2 | split：行级 + 字符级（inline span） | 拆完两块内容拼回原块 |
| T2.3 | merge / drop / add | 合并保留前者 id；新增 id = max_seq+1 |
| T2.4 | gutter 拖边界手柄 + 拆分预览 | 拖动实时预览，松手生效 |
| T2.5 | 快捷键、diff 计数 `+新增 ~修改 -删除` | 计数与块集一致 |

**演示**：改 3 块、撤销 3 次、diff 归零。

---

## 5. P3 · 持久化（0.5–1 天）

| ID | 任务 | 验收 |
|---|---|---|
| T3.1 | `overlay.py`：写 `edits/*.jsonl` + `.extras.jsonl` + `.meta.json`，sha256 校验 | 保存后重开一致 |
| T3.2 | `/api/save`、`/api/reset`、`/api/export`（15 字段 JSONL 下载） | 导出文件可被 `contracts.Chunk` 解析 |
| T3.3 | stale 检测与黄条提示 | 改源文件后提示「源已变更」 |
| T3.4 | gate 面板：`gate_chunks` + 阈值调整 | 质检结果与 FAC-RAG 一致 |
| T3.5 | `test_overlay.py` / `test_api.py` | pytest 全绿 |

**演示**：改→保存→重启进程→再次打开还是改后的版本；导出 JSONL 可跑索引流程。

---

## 6. P4 · 参数重切（0.5 天，可选）

| ID | 任务 | 验收 |
|---|---|---|
| T4.1 | `rechunk.py`：`GroupOverride` 覆盖 params，跑 `chunk_file_full`（不落盘） | 800→400 生成新块集 |
| T4.2 | 新旧对比弹层：新增/删除/修改块列表，采纳或放弃 | 采纳后覆盖层更新、diff 计数正确 |

---

## 7. P5 · provenance 改造（1 天，可选，改 FAC-RAG）

| ID | 任务 | 验收 |
|---|---|---|
| T5.1 | `model.Unit` 加 `src_lines`（只进 extras，不进 15 字段） | 序列化兼容 |
| T5.2 | `units.py` 三个 handler 记录源行号（含 expand 映射） | 单测覆盖 statute/standard/table |
| T5.3 | `render.py` 写 `extras["_src_lines"]`（多 part 按行分组） | 74/531 文件 extras 有值 |
| T5.4 | 全量重跑 `tools/run_chunking.py`，gate 结果不变、产物 diff 可解释 | 97 文件产物仅 extras 增加 |
| T5.5 | 本工具 `fac_bridge` 优先读 `_src_lines`，回落到启发式 | 危化品/山东对齐率 >95% |

> P5 是唯一需要修改 FAC-RAG 的环节，单独走 FAC-RAG 仓库的 PR；不做也不影响 P0–P4。

---

## 8. 工期与里程碑

```
D1 上午  P0 骨架        → 服务能起、97 文件可见
D1 下午  T1.1–T1.3     → 对齐算法跑通（消防法 100%）
D2 上午  T1.4–T1.6     → 三栏联动可用 ★ 可演示点
D2 下午  T1.7 + P2 开始 → 编辑动作
D3 上午  P2 收尾       → 撤销/diff
D3 下午  P3 持久化     → MVP 完成
D4（可选）P4 / P5
```

---

## 9. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 表格/HTML 文档对齐率低（危化品 11%、山东 63%） | 高亮不准 | `miss` 如实灰标 + 手动拖选；P5 根治 |
| FAC-RAG 产物更新导致 id/顺序变化 | 覆盖层 id 对不上 | 覆盖层存 sha256，源变更提示 stale；保存前按 id 校验 |
| `parsed` 是 dataclass 对象 | 直接 JSON 序列化失败 | `asdict()` 包装；`fac_bridge` 统一出口 |
| 大文档首屏慢 | 体验差 | 行/卡片虚拟化、文档 LRU、gzip |
| 契约校验不过导致无法保存 | 编辑丢失 | 保存前前端预检 + 明确错误定位 + 覆盖层先写临时文件 |
| 双人同时改同一文件 | 覆盖 | 本期单用户；`.meta.json` 记录 mtime/sha 防静默覆盖 |

---

## 10. 交付物清单

- [ ] `chunk_studio/` 可运行代码（P0–P3，可选 P4）
- [ ] `tests/` 四个测试文件全绿
- [ ] `README.md` 更新为「可运行」状态与截图说明
- [ ] `edits/`、`exports/` 运行时目录（gitignore）
- [ ] 人工验收单（见 [02-interaction.md](02-interaction.md) §9）逐条勾选
- [ ] 一份实测对齐报告（新增 P1 结束数据）
