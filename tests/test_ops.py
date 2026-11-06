"""编辑命令纯函数测试（P2）：拆分 / 合并 / 删除 / 新增 / 修改 / diff。"""

from __future__ import annotations

import copy
import unittest

from chunk_studio import ops
from chunk_studio.ops import (
    OpError,
    OpNotFound,
    apply_ops,
    chunk_diff,
    chunk_hash,
    detect_law_num,
    estimate_token_len,
    max_seq,
    next_id,
    normalize_text,
)

LAW = "测试法"


def make_chunk(cid: str, text: str, *, law_num: str = "第一条", kind: str = "clause", span=None, manual=False):
    return {
        "id": cid,
        "chunk": text,
        "law_name": LAW,
        "law_num": law_num,
        "law_invalid": "2030-12-12",
        "law_type": "法律",
        "law_level": "法律",
        "issuer": "全国人民代表大会常务委员会",
        "effective_date": "",
        "chunk_kind": kind,
        "token_len": len(text),
        "source": "法律法规",
        "part_index": 1,
        "part_total": 1,
        "chunk_hash": chunk_hash(text),
        "extras": {"region": "body"},
        "span": span,
        "align": "exact" if span else "miss",
        "manual": manual,
    }


def base_chunks():
    return [
        make_chunk("测试法-0001", "第一条 为了测试。\n这是第二行。", span=[3, 4]),
        make_chunk("测试法-0002", "第二条 测试结束。", law_num="第二条", span=[6, 6]),
        make_chunk("测试法-0003", "（资料性附录）\n附录内容。", law_num="A.1", kind="appendix", span=[9, 10]),
    ]


class TextTest(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_text("  a\r\nb  \r\n\r\n"), "  a\nb")
        self.assertEqual(normalize_text(None), "")

    def test_hash_and_len(self):
        self.assertEqual(len(chunk_hash("abc")), 16)
        self.assertEqual(chunk_hash("abc"), chunk_hash("abc"))
        self.assertNotEqual(chunk_hash("abc"), chunk_hash("abd"))
        self.assertEqual(estimate_token_len("你好"), 2)

    def test_ids(self):
        chunks = base_chunks()
        self.assertEqual(max_seq(chunks), 3)
        self.assertEqual(next_id(chunks, LAW), "测试法-0004")
        # 撞号时继续递增
        chunks.append(make_chunk("测试法-0004", "占位"))
        self.assertEqual(next_id(chunks, LAW), "测试法-0005")


class SplitTest(unittest.TestCase):
    def test_split_by_line(self):
        chunks = base_chunks()
        out = apply_ops(chunks, [{"op": "split", "id": "测试法-0001", "at_line": 1}], law_name=LAW)
        self.assertEqual(len(out), 4)
        first, second = out[0], out[1]
        self.assertEqual(first["id"], "测试法-0001")
        self.assertEqual(first["chunk"], "第一条 为了测试。")
        self.assertEqual(second["id"], "测试法-0004")
        self.assertEqual(second["chunk"], "这是第二行。")
        self.assertTrue(first["manual"] and second["manual"])
        self.assertEqual(first["align"], "manual")
        self.assertIsNone(second["span"])
        # 派生字段重算
        self.assertEqual(first["token_len"], len("第一条 为了测试。"))
        self.assertEqual(first["chunk_hash"], chunk_hash("第一条 为了测试。"))
        # 原列表未被修改
        self.assertEqual(len(chunks), 3)

    def test_split_by_char(self):
        out = apply_ops(
            base_chunks(), [{"op": "split", "id": "测试法-0002", "at_char": 4}], law_name=LAW
        )
        self.assertEqual(len(out), 4)
        self.assertEqual(out[1]["chunk"], "第二条")
        self.assertEqual(out[2]["chunk"], "测试结束。")
        self.assertEqual(out[2]["id"], "测试法-0004")

    def test_split_extras_inherited(self):
        out = apply_ops(
            base_chunks(), [{"op": "split", "id": "测试法-0001", "at_line": 1}], law_name=LAW
        )
        self.assertEqual(out[1]["extras"], {"region": "body"})

    def test_split_errors(self):
        with self.assertRaises(OpError):
            apply_ops(base_chunks(), [{"op": "split", "id": "测试法-0001", "at_line": 0}], law_name=LAW)
        with self.assertRaises(OpError):
            apply_ops(base_chunks(), [{"op": "split", "id": "测试法-0001", "at_line": 9}], law_name=LAW)
        with self.assertRaises(OpError):
            apply_ops(base_chunks(), [{"op": "split", "id": "测试法-0001"}], law_name=LAW)
        with self.assertRaises(OpNotFound):
            apply_ops(base_chunks(), [{"op": "split", "id": "不存在-0001", "at_line": 1}], law_name=LAW)

    def test_split_with_source_lines_restores_spans(self):
        """服务端传入源行时：拆开的两半都各自补回行区间（右栏显示行号、范围条断开）。"""
        source = ["# 测试法", "", "第一条 为了测试。", "这是第二行。", "", "第二条 测试结束。"]
        out = apply_ops(
            base_chunks(),
            [{"op": "split", "id": "测试法-0001", "at_line": 1}],
            law_name=LAW,
            source_lines=source,
        )
        first, second = out[0], out[1]
        self.assertEqual(first["span"], [3, 3])
        self.assertEqual(second["span"], [4, 4])
        self.assertEqual(first["align"], "manual")
        self.assertEqual(second["align"], "manual")

    def test_split_second_law_num(self):
        out = apply_ops(
            base_chunks(),
            [{"op": "split", "id": "测试法-0001", "at_line": 1, "second_law_num": "第二条"}],
            law_name=LAW,
        )
        self.assertEqual(out[0]["law_num"], "第一条")
        self.assertEqual(out[1]["law_num"], "第二条")

    def test_split_without_source_lines_keeps_old_behavior(self):
        out = apply_ops(base_chunks(), [{"op": "split", "id": "测试法-0001", "at_line": 1}], law_name=LAW)
        self.assertIsNone(out[1]["span"])

    def test_split_auto_detects_second_law_num_from_text(self):
        """合并过的块再拆开：下半块正文以「第二条」开头时自动识别条号。"""
        merged = make_chunk("测试法-0001", "第一条 为了测试。\n第二条 测试结束。", law_num="第一条", span=[3, 5])
        out = apply_ops([merged], [{"op": "split", "id": "测试法-0001", "at_line": 1}], law_name=LAW)
        self.assertEqual(out[0]["law_num"], "第一条")
        self.assertEqual(out[1]["law_num"], "第二条")

    def test_split_auto_detects_second_law_num_from_source_line(self):
        """正文不带条号时，用切点所在原文行（「第二条 …」）识别下半块条号。"""
        source = ["# 测试法", "", "第一条 为了测试。", "", "第二条 测试结束。"]
        merged = make_chunk("测试法-0001", "为了测试。\n测试结束。", law_num="第一条", span=[3, 5])
        out = apply_ops(
            [merged],
            [{"op": "split", "id": "测试法-0001", "at_line": 1}],
            law_name=LAW,
            source_lines=source,
        )
        self.assertEqual(out[0]["law_num"], "第一条")
        self.assertEqual(out[1]["law_num"], "第二条")
        self.assertEqual(out[1]["span"], [5, 5])

    def test_split_explicit_law_num_beats_auto_detect(self):
        merged = make_chunk("测试法-0001", "第一条 为了测试。\n第二条 测试结束。", law_num="第一条", span=[3, 5])
        out = apply_ops(
            [merged],
            [{"op": "split", "id": "测试法-0001", "at_line": 1, "second_law_num": "第八条"}],
            law_name=LAW,
        )
        self.assertEqual(out[1]["law_num"], "第八条")


class MergeTest(unittest.TestCase):
    def test_merge_adjacent_restores_text(self):
        out = apply_ops(base_chunks(), [{"op": "merge", "id": "测试法-0001", "with": "测试法-0002"}], law_name=LAW)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["id"], "测试法-0001")
        self.assertEqual(out[0]["chunk"], "第一条 为了测试。\n这是第二行。\n第二条 测试结束。")
        # 两个 span 都在 → 合并区间
        self.assertEqual(out[0]["span"], [3, 6])
        self.assertEqual(out[0]["align"], "approx")

    def test_split_then_merge_roundtrip(self):
        chunks = base_chunks()
        text_before = chunks[0]["chunk"]
        sp = [{"op": "split", "id": "测试法-0001", "at_line": 1}]
        out = apply_ops(chunks, sp, law_name=LAW)
        # 拆出来的两块 id 分别是 0001 / 0004，合并后正文还原
        out = apply_ops(out, [{"op": "merge", "id": "测试法-0001", "with": "测试法-0004"}], law_name=LAW)
        self.assertEqual(out[0]["chunk"], text_before)
        self.assertTrue(out[0]["manual"])  # 手工合并后仍标 manual
        self.assertIsNone(out[0]["span"])

    def test_merge_non_adjacent_error(self):
        with self.assertRaises(OpError):
            apply_ops(base_chunks(), [{"op": "merge", "id": "测试法-0001", "with": "测试法-0003"}], law_name=LAW)

    def test_merge_reversed_order(self):
        # 「并↑」写法：id 是后一块、with 是前一块，结果保留前一块 id
        out = apply_ops(base_chunks(), [{"op": "merge", "id": "测试法-0002", "with": "测试法-0001"}], law_name=LAW)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["id"], "测试法-0001")
        self.assertEqual(out[0]["chunk"], "第一条 为了测试。\n这是第二行。\n第二条 测试结束。")
        self.assertEqual(out[0]["span"], [3, 6])

    def test_merge_with_manual_chunk_keeps_span(self):
        # 手工改过的块与邻近块合并：两边都有行区间时仍应保留覆盖区间（不再变成「未对齐」）
        chunks = base_chunks()
        chunks[0]["manual"] = True
        out = apply_ops(chunks, [{"op": "merge", "id": "测试法-0001", "with": "测试法-0002"}], law_name=LAW)
        self.assertEqual(out[0]["span"], [3, 6])
        self.assertEqual(out[0]["align"], "approx")
        self.assertTrue(out[0]["manual"])


class MergeManyTest(unittest.TestCase):
    def test_merge_three_adjacent(self):
        out = apply_ops(
            base_chunks(),
            [{"op": "merge_many", "ids": ["测试法-0001", "测试法-0002", "测试法-0003"]}],
            law_name=LAW,
        )
        self.assertEqual(len(out), 1)
        merged = out[0]
        self.assertEqual(merged["id"], "测试法-0001")
        self.assertEqual(merged["law_num"], "第一条")
        self.assertEqual(merged["chunk_kind"], "clause")
        self.assertEqual(
            merged["chunk"],
            "第一条 为了测试。\n这是第二行。\n第二条 测试结束。\n（资料性附录）\n附录内容。",
        )
        self.assertEqual(merged["span"], [3, 10])
        self.assertEqual(merged["align"], "approx")
        self.assertFalse(merged["manual"])

    def test_merge_many_any_order(self):
        # 传入顺序无关，按文档顺序合并
        out = apply_ops(
            base_chunks(),
            [{"op": "merge_many", "ids": ["测试法-0003", "测试法-0001", "测试法-0002"]}],
            law_name=LAW,
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], "测试法-0001")

    def test_merge_many_with_manual_chunk_keeps_span(self):
        # 手工改过的块只要两边都有行区间，合并后仍应给出覆盖区间（不再退化成「未对齐」）
        chunks = base_chunks()
        chunks[1]["manual"] = True
        out = apply_ops(
            chunks,
            [{"op": "merge_many", "ids": ["测试法-0001", "测试法-0002", "测试法-0003"]}],
            law_name=LAW,
        )
        self.assertEqual(out[0]["span"], [3, 10])
        self.assertEqual(out[0]["align"], "approx")
        self.assertTrue(out[0]["manual"])

    def test_merge_many_with_missing_span_stays_manual(self):
        chunks = base_chunks()
        chunks[1]["span"] = None
        out = apply_ops(
            chunks,
            [{"op": "merge_many", "ids": ["测试法-0001", "测试法-0002", "测试法-0003"]}],
            law_name=LAW,
        )
        self.assertIsNone(out[0]["span"])
        self.assertEqual(out[0]["align"], "manual")

    def test_merge_many_errors(self):
        chunks = base_chunks()
        with self.assertRaises(OpError):
            apply_ops(chunks, [{"op": "merge_many", "ids": ["测试法-0001"]}], law_name=LAW)
        with self.assertRaises(OpError):
            apply_ops(chunks, [{"op": "merge_many", "ids": []}], law_name=LAW)
        with self.assertRaises(OpError):
            apply_ops(chunks, [{"op": "merge_many", "ids": ["测试法-0001", "测试法-0002", "测试法-0002"]}], law_name=LAW)
        with self.assertRaises(OpNotFound):
            apply_ops(chunks, [{"op": "merge_many", "ids": ["测试法-0001", "测试法-9999"]}], law_name=LAW)

    def test_merge_many_non_adjacent_error(self):
        # 跳过中间块（0001 与 0003）不允许，避免把未选中的块吞掉
        with self.assertRaises(OpError):
            apply_ops(
                base_chunks(),
                [{"op": "merge_many", "ids": ["测试法-0001", "测试法-0003"]}],
                law_name=LAW,
            )

    def test_merge_many_diff(self):
        chunks = base_chunks()
        out = apply_ops(
            chunks,
            [{"op": "merge_many", "ids": ["测试法-0001", "测试法-0002", "测试法-0003"]}],
            law_name=LAW,
        )
        diff = chunk_diff(chunks, out)
        self.assertEqual(diff["removed"], 2)
        self.assertEqual(diff["added"], 0)
        self.assertEqual(diff["modified"], 1)


class DropAddPatchTest(unittest.TestCase):
    def test_drop(self):
        out = apply_ops(base_chunks(), [{"op": "drop", "id": "测试法-0002"}], law_name=LAW)
        self.assertEqual([c["id"] for c in out], ["测试法-0001", "测试法-0003"])

    def test_drop_many_is_one_command(self):
        out = apply_ops(
            base_chunks(),
            [{"op": "drop_many", "ids": ["测试法-0001", "测试法-0003"]}],
            law_name=LAW,
        )
        self.assertEqual([c["id"] for c in out], ["测试法-0002"])
        self.assertEqual(out[0]["chunk"], "第二条 测试结束。")

    def test_drop_many_missing_id_raises(self):
        with self.assertRaises(OpNotFound) as ctx:
            apply_ops(base_chunks(), [{"op": "drop_many", "ids": ["测试法-0001", "没有-0009"]}], law_name=LAW)
        self.assertIn("找不到切片", str(ctx.exception))

    def test_drop_many_reindexes_part_group(self):
        chunks = [
            make_chunk("测试法-0001", "第一条 开始。"),
            make_chunk("测试法-0002-p1", "第二条 上。"),
            make_chunk("测试法-0002-p2", "第二条 中。"),
            make_chunk("测试法-0002-p3", "第二条 下。"),
        ]
        for index, chunk in enumerate(chunks[1:], start=1):
            chunk["part_index"], chunk["part_total"] = index, 3
        out = apply_ops(
            chunks,
            [{"op": "drop_many", "ids": ["测试法-0002-p2"]}],
            law_name=LAW,
        )
        self.assertEqual([c["id"] for c in out], ["测试法-0001", "测试法-0002-p1", "测试法-0002-p2"])
        self.assertEqual([c["part_total"] for c in out[1:]], [2, 2])

    def test_add_append_and_insert(self):
        out = apply_ops(base_chunks(), [{"op": "add", "text": "新的块正文"}], law_name=LAW)
        self.assertEqual(len(out), 4)
        new = out[-1]
        self.assertEqual(new["id"], "测试法-0004")
        self.assertEqual(new["law_num"], "第二条")  # 追加在附录后，条号取最近的「第X条」邻居
        self.assertEqual(new["chunk_kind"], "paragraph")
        self.assertIsNone(new["span"])
        self.assertTrue(new["manual"])
        # 继承模板的 law_type / issuer
        self.assertEqual(new["law_type"], "法律")

        # 插到第一块后面：新块拿 -0002，原先的 0002/0003 顺延成 0003/0004
        out2 = apply_ops(base_chunks(), [{"op": "add", "text": "插在中间", "after_id": "测试法-0001"}], law_name=LAW)
        self.assertEqual(out2[1]["chunk"], "插在中间")
        self.assertEqual(out2[1]["id"], "测试法-0002")
        self.assertEqual(out2[2]["id"], "测试法-0003")
        self.assertEqual(out2[3]["id"], "测试法-0004")

    def test_add_renumbers_part_group(self):
        part1 = make_chunk("测试法-0002-p1", "第二条 上半段。", law_num="第二条")
        part1["part_index"], part1["part_total"] = 1, 2
        part2 = make_chunk("测试法-0002-p2", "第二条 下半段。", law_num="第二条")
        part2["part_index"], part2["part_total"] = 2, 2
        chunks = [make_chunk("测试法-0001", "第一条 开始。"), part1, part2]
        out = apply_ops(chunks, [{"op": "add", "text": "追加的正文"}], law_name=LAW)
        self.assertEqual([c["id"] for c in out], ["测试法-0001", "测试法-0002-p1", "测试法-0002-p2", "测试法-0003"])

    def test_add_before_first(self):
        # 原文顶部缺口新增：插到第一块之前，新块拿 -0001，原有块整体顺延
        out = apply_ops(
            base_chunks(),
            [{"op": "add", "text": "标题位置新增", "before_id": "测试法-0001", "span": [1, 2]}],
            law_name=LAW,
        )
        self.assertEqual(len(out), 4)
        self.assertEqual(out[0]["chunk"], "标题位置新增")
        self.assertEqual(out[0]["id"], "测试法-0001")
        self.assertEqual(out[0]["span"], [1, 2])
        self.assertEqual(out[1]["id"], "测试法-0002")
        self.assertEqual(out[3]["id"], "测试法-0004")

    def test_add_errors(self):
        with self.assertRaises(OpError):
            apply_ops(base_chunks(), [{"op": "add", "text": "   "}], law_name=LAW)
        with self.assertRaises(OpError):
            apply_ops(base_chunks(), [{"op": "add", "text": "x", "chunk_kind": "nope"}], law_name=LAW)

    def test_add_with_span(self):
        # 原文选区新增：带行区间，可定位回原文；条号按前一块倒推（插在第二条后面 → 第二条）
        out = apply_ops(
            base_chunks(),
            [{"op": "add", "text": "从原文选中新增", "span": [7, 9], "after_id": "测试法-0002"}],
            law_name=LAW,
        )
        new = out[2]
        self.assertEqual(new["chunk"], "从原文选中新增")
        self.assertEqual(new["id"], "测试法-0003")
        self.assertEqual(new["span"], [7, 9])
        self.assertTrue(new["manual"])
        self.assertEqual(new["align"], "manual")
        self.assertEqual(new["law_num"], "第二条")

    def test_add_infers_law_num_from_neighbors(self):
        # 插在「第二条」后面 → 默认沿用第二条（同一块的续写）
        out = apply_ops(base_chunks(), [{"op": "add", "text": "续写", "after_id": "测试法-0002"}], law_name=LAW)
        self.assertEqual(out[2]["law_num"], "第二条")
        # 显式写的条号优先
        out2 = apply_ops(
            base_chunks(),
            [{"op": "add", "text": "续写", "law_num": "第三条", "after_id": "测试法-0002"}],
            law_name=LAW,
        )
        self.assertEqual(out2[2]["law_num"], "第三条")
        # 前一块不是「第X条」（附录 A.1）→ 不硬套，继续往前取最近的条号邻居（第二条）
        out3 = apply_ops(base_chunks(), [{"op": "add", "text": "附注", "after_id": "测试法-0003"}], law_name=LAW)
        self.assertEqual(out3[-1]["law_num"], "第二条")

    def test_add_span_errors(self):
        for bad in ([3], ["a", "b"], [0, 2], [5, 4], "3-4"):
            with self.assertRaises(OpError):
                apply_ops(base_chunks(), [{"op": "add", "text": "x", "span": bad}], law_name=LAW)

    def test_patch(self):
        out = apply_ops(
            base_chunks(),
            [{"op": "patch", "id": "测试法-0002", "chunk": "第二条 改过了。", "law_num": "第二条"}],
            law_name=LAW,
        )
        self.assertEqual(out[1]["chunk"], "第二条 改过了。")
        self.assertEqual(out[1]["token_len"], len("第二条 改过了。"))
        self.assertEqual(out[1]["chunk_hash"], chunk_hash("第二条 改过了。"))
        self.assertTrue(out[1]["manual"])
        self.assertEqual(out[1]["align"], "manual")

    def test_patch_errors(self):
        with self.assertRaises(OpError):
            apply_ops(base_chunks(), [{"op": "patch", "id": "测试法-0002", "law_num": "  "}], law_name=LAW)
        with self.assertRaises(OpError):
            apply_ops(base_chunks(), [{"op": "patch", "id": "测试法-0002", "chunk_kind": "bad"}], law_name=LAW)
        with self.assertRaises(OpError):
            apply_ops(base_chunks(), [{"op": "patch", "id": "测试法-0002", "chunk": "  "}], law_name=LAW)


class StandardClauseNumTest(unittest.TestCase):
    """标准式条号（5.4.1）的识别与倒推。"""

    def test_detect_standard_clause(self):
        self.assertEqual(detect_law_num("5.4.2 作业场所应设置氨的安全警示标志"), "5.4.2")
        self.assertEqual(detect_law_num("## 5.4 安全警示标识"), "5.4")
        self.assertEqual(detect_law_num("2019年发布"), "")        # 纯数字不带点，不误吞
        self.assertEqual(detect_law_num("表A.1 安全标志"), "")     # 表号不作条号

    def test_split_merged_standard_clause(self):
        merged = make_chunk(
            "标准-0001",
            "5.4.1 现场应在醒目位置高低搭配设置风向标。\n5.4.2 作业场所应设置氨的安全警示标志。",
            law_num="5.4.1",
            span=[356, 358],
        )
        out = apply_ops([merged], [{"op": "split", "id": "标准-0001", "at_line": 1}], law_name="标准")
        self.assertEqual(out[0]["law_num"], "5.4.1")
        self.assertEqual(out[1]["law_num"], "5.4.2")

    def test_add_after_standard_clause(self):
        first = make_chunk("标准-0001", "5.4.1 现场设置风向标。", law_num="5.4.1", span=[356, 356])
        table = make_chunk("标准-0002", "表A.1 安全标志", law_num="表A.1", kind="table", span=[357, 357])
        out = apply_ops(
            [first, table], [{"op": "add", "text": "补充说明", "after_id": "标准-0002"}], law_name="标准"
        )
        self.assertEqual(out[-1]["law_num"], "5.4.1")  # 跳过表号，取最近的「5.4.1」


class ExtendTest(unittest.TestCase):
    def test_extend_merges_into_target_keeping_id(self):
        chunks = base_chunks()
        body = chunks[0]["chunk"] + "\n" + chunks[1]["chunk"] + "\n多选出来的尾巴行"
        out = apply_ops(
            chunks,
            [
                {
                    "op": "extend",
                    "id": "测试法-0001",
                    "ids": ["测试法-0001", "测试法-0002"],
                    "chunk": body,
                    "span": [3, 6],
                }
            ],
            law_name=LAW,
        )
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["id"], "测试法-0001")
        self.assertEqual(out[0]["chunk"], body)
        self.assertEqual(out[0]["law_num"], "第一条")
        self.assertEqual(out[0]["span"], [3, 6])
        self.assertTrue(out[0]["manual"])
        # 后面的附录块按位置重排 id 顺延
        self.assertEqual(out[1]["id"], "测试法-0002")
        self.assertEqual(out[1]["chunk_kind"], "appendix")

    def test_extend_without_chunk_concatenates(self):
        out = apply_ops(
            base_chunks(),
            [{"op": "extend", "id": "测试法-0001", "ids": ["测试法-0001", "测试法-0002"]}],
            law_name=LAW,
        )
        self.assertIn("第二条 测试结束。", out[0]["chunk"])
        self.assertEqual(out[0]["span"], [3, 6])
        self.assertEqual(out[0]["align"], "manual")

    def test_extend_errors(self):
        chunks = base_chunks()
        with self.assertRaises(OpError):
            apply_ops(chunks, [{"op": "extend", "ids": ["测试法-0001"]}], law_name=LAW)
        with self.assertRaises(OpError):
            apply_ops(
                chunks,
                [{"op": "extend", "id": "测试法-0002", "ids": ["测试法-0001", "测试法-0002"]}],
                law_name=LAW,
            )
        with self.assertRaises(OpError):
            apply_ops(
                chunks,
                [{"op": "extend", "id": "测试法-0001", "ids": ["测试法-0001", "测试法-0003"]}],
                law_name=LAW,
            )
        with self.assertRaises(OpError):
            apply_ops(
                chunks,
                [{"op": "extend", "id": "测试法-0001", "ids": ["测试法-0001"], "chunk": "   "}],
                law_name=LAW,
            )
        with self.assertRaises(OpError):
            apply_ops(
                chunks,
                [{"op": "extend", "id": "测试法-0001", "ids": ["测试法-0001"], "law_num": ""}],
                law_name=LAW,
            )

    def test_extend_diff(self):
        chunks = base_chunks()
        out = apply_ops(
            chunks,
            [{"op": "extend", "id": "测试法-0001", "ids": ["测试法-0001", "测试法-0002"]}],
            law_name=LAW,
        )
        diff = chunk_diff(chunks, out)
        self.assertEqual(diff["removed"], 1)
        self.assertEqual(diff["added"], 0)
        self.assertEqual(diff["modified"], 1)


class OrderTest(unittest.TestCase):
    def test_sort_by_span_puts_unsorted_chunks_in_document_order(self):
        chunks = [
            {"id": "a-0001", "span": [10, 12]},
            {"id": "a-0002", "span": None},
            {"id": "a-0003", "span": [3, 5]},
            {"id": "a-0004", "span": [3, 4]},
        ]
        out = ops.sort_by_span(chunks)
        self.assertEqual([c["id"] for c in out], ["a-0004", "a-0003", "a-0001", "a-0002"])

    def test_normalize_chunks_renumbers_by_position(self):
        chunks = [
            {"id": "a-0009", "span": [10, 12], "chunk": "x"},
            {"id": "a-0007", "span": [3, 5], "chunk": "y"},
        ]
        out = ops.normalize_chunks(chunks, "a")
        self.assertEqual([c["id"] for c in out], ["a-0001", "a-0002"])
        self.assertEqual([c["span"] for c in out], [[3, 5], [10, 12]])


class ApplyAndDiffTest(unittest.TestCase):
    def test_input_not_mutated(self):
        chunks = base_chunks()
        snapshot = copy.deepcopy(chunks)
        apply_ops(chunks, [{"op": "split", "id": "测试法-0001", "at_line": 1}], law_name=LAW)
        self.assertEqual(chunks, snapshot)

    def test_unknown_op_and_prefix(self):
        with self.assertRaises(OpError) as ctx:
            apply_ops(base_chunks(), [{"op": "nope"}], law_name=LAW)
        self.assertIn("未知的编辑命令", str(ctx.exception))
        with self.assertRaises(OpError) as ctx2:
            apply_ops(base_chunks(), [{"op": "drop", "id": "missing"}], law_name=LAW)
        self.assertIn("第 1 条命令", str(ctx2.exception))

    def test_diff(self):
        baseline = base_chunks()
        current = apply_ops(
            baseline,
            [
                {"op": "patch", "id": "测试法-0002", "chunk": "改过"},
                {"op": "drop", "id": "测试法-0003"},
                {"op": "add", "text": "新增的"},
            ],
            law_name=LAW,
        )
        diff = chunk_diff(baseline, current)
        self.assertEqual(diff["added"], 1)
        self.assertEqual(diff["removed"], 1)
        self.assertEqual(diff["modified"], 1)
        # 插入后按位置重排：新块拿到 0003，同时原附录块 0003 被删掉，id 字面相同但含义不同
        self.assertIn("测试法-0003", diff["added_ids"])
        self.assertIn("测试法-0003", diff["removed_ids"])
        self.assertIn("测试法-0002", diff["modified_ids"])


if __name__ == "__main__":
    unittest.main()
