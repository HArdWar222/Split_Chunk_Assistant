"""编辑命令纯函数测试（P2）：拆分 / 合并 / 删除 / 新增 / 修改 / diff。"""

from __future__ import annotations

import copy
import unittest

from chunk_studio.ops import (
    OpError,
    OpNotFound,
    apply_ops,
    chunk_diff,
    chunk_hash,
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
        make_chunk("测试法-0002", "第二条 测试结束。", span=[6, 6]),
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


class DropAddPatchTest(unittest.TestCase):
    def test_drop(self):
        out = apply_ops(base_chunks(), [{"op": "drop", "id": "测试法-0002"}], law_name=LAW)
        self.assertEqual([c["id"] for c in out], ["测试法-0001", "测试法-0003"])

    def test_add_append_and_insert(self):
        out = apply_ops(base_chunks(), [{"op": "add", "text": "新的块正文"}], law_name=LAW)
        self.assertEqual(len(out), 4)
        new = out[-1]
        self.assertEqual(new["id"], "测试法-0004")
        self.assertEqual(new["law_num"], "（新增）")
        self.assertEqual(new["chunk_kind"], "paragraph")
        self.assertIsNone(new["span"])
        self.assertTrue(new["manual"])
        # 继承模板的 law_type / issuer
        self.assertEqual(new["law_type"], "法律")

        out2 = apply_ops(base_chunks(), [{"op": "add", "text": "插在中间", "after_id": "测试法-0001"}], law_name=LAW)
        self.assertEqual(out2[1]["chunk"], "插在中间")

    def test_add_errors(self):
        with self.assertRaises(OpError):
            apply_ops(base_chunks(), [{"op": "add", "text": "   "}], law_name=LAW)
        with self.assertRaises(OpError):
            apply_ops(base_chunks(), [{"op": "add", "text": "x", "chunk_kind": "nope"}], law_name=LAW)

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
        self.assertIn("测试法-0004", diff["added_ids"])
        self.assertIn("测试法-0003", diff["removed_ids"])
        self.assertIn("测试法-0002", diff["modified_ids"])


if __name__ == "__main__":
    unittest.main()
