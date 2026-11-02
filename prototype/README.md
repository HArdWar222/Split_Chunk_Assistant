# prototype/ · 数据层草稿（不作为最终架构）

`core_prototype.py`（约 610 行）是开工前用来验证可行性的**数据层草稿**，不是最终代码。

## 它已经验证过的事

1. **可精确复现产物**：对《中华人民共和国消防法》调用 `chunk_file_full`，
   与 `data/chunks_v2/` 已落盘产物逐块对比——74 块、id 顺序、正文、`chunk_hash` 全部相同；
2. **对齐启发式可行**：规范化行匹配 + 表格 `table_grid` 行反查，
   实测消防法 74/74、GB 50016 531/531、GB 15499 294/294 = 100%；
   危险化学品目录 11.3%、山东自由裁量 62.7%（表格密集文档的已知缺口）；
3. **覆盖层写入可行**：`edits/<safe>.jsonl` + `.extras.jsonl` + `.meta.json`，
   保存前走 `fac_rag.contracts.Chunk(...).finalize()` 校验；
4. **gate 可复用**：直接调 `chunking_v2.gate_chunks`。

## 它没有的

- 没有 `server.py` / 前端页面（P0–P1 才写）；
- 直接 `sys.path.insert` 依赖 `FAC_ROOT`，未做适配层隔离；
- 对齐算法尚未拆成纯函数模块，也未落缓存。

## 与最终架构的关系

P0 会按 [docs/01-architecture.md](../docs/01-architecture.md) 把它拆分为
`fac_bridge / manifest / source / align / ops / overlay / studio / api / server`，
本文件仅作为参考保留，并可能随实现推进而删除。
