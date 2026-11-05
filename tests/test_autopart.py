"""自动分 part 纯函数测试（P2）：文本均分 / 尾段重排 / 表格按行拆块。"""

from __future__ import annotations

import unittest

from chunk_studio.autopart import (
    auto_part_chunks,
    materialize_chunks,
    split_table_chunk,
    split_text_chunk,
    split_text_parts,
)
from chunk_studio.ops import apply_ops, chunk_hash

LAW = "测试法"


def make_chunk(cid, text, *, law_num="第一条", kind="clause", span=None):
    return {
        "id": cid,
        "chunk": text,
        "law_name": LAW,
        "law_num": law_num,
        "law_invalid": "2030-12-12",
        "law_type": "法律",
        "law_level": "法律",
        "issuer": "",
        "effective_date": "",
        "chunk_kind": kind,
        "token_len": len(text),
        "source": "",
        "part_index": 1,
        "part_total": 1,
        "chunk_hash": chunk_hash(text),
        "extras": {"region": "body"},
        "span": span,
        "align": "exact" if span else "miss",
        "manual": False,
    }


def long_text(lines=40, width=40):
    return "\n".join(f"第{i + 1}行 " + "测试内容" * (width // 4) for i in range(lines))


class TextPartsTest(unittest.TestCase):
    def test_short_text_not_split(self):
        parts = split_text_parts("短正文", max_chars=800, min_chars=200)
        self.assertEqual(parts, ["短正文"])

    def test_long_text_split_and_rejoins(self):
        text = long_text(40, 40)
        self.assertGreater(len(text), 800)
        parts = split_text_parts(text, max_chars=800, min_chars=200)
        self.assertGreaterEqual(len(parts), 2)
        # 不丢字：拼回去与原文（去行尾空白）一致
        joined = "\n".join(parts).replace("\n", "")
        self.assertEqual(joined, text.replace("\n", ""))
        for part in parts:
            self.assertLessEqual(len(part), 920)
        # 除最后一段，其它段不应过小
        for part in parts[:-1]:
            self.assertGreaterEqual(len(part), 200)

    def test_tail_not_too_small(self):
        text = "甲行" * 380 + "\n尾巴一行很短"
        parts = split_text_parts(text, max_chars=800, min_chars=200)
        self.assertGreaterEqual(len(parts[-1]), 200)

    def test_rebalance_does_not_loop_when_prev_is_single_line(self):
        # 长单行被硬切后，尾段过小且上一段只有一行：以前 _rebalance 会
        # 反复 pop 同一段造成死循环，这里确保能返回。
        text = "甲" * 90 + "\n" + "乙" * 150
        parts = split_text_parts(text, max_chars=200, min_chars=50)
        self.assertGreaterEqual(len(parts), 2)
        self.assertEqual("".join(parts).replace("\n", ""), text.replace("\n", ""))

    def test_hard_split_long_line(self):
        line = ("这是很长的一行。" * 120).strip()
        parts = split_text_parts(line, max_chars=400, min_chars=100)
        self.assertGreaterEqual(len(parts), 2)
        self.assertTrue(all(len(part) <= 420 for part in parts))
        self.assertEqual("".join(parts), line)

    def test_breadcrumb_repeated(self):
        body = "测试法 · 第一条\n" + long_text(30, 40)
        parts = split_text_parts(body, max_chars=800, min_chars=200)
        self.assertGreaterEqual(len(parts), 2)
        for part in parts:
            self.assertTrue(part.startswith("测试法 · 第一条\n"), part[:40])

    def test_split_text_chunk_ids(self):
        chunks = [make_chunk("测试法-0003", long_text(40, 40), span=[5, 44])]
        out = split_text_chunk(chunks, 0, 800, 200)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out[0]["id"], "测试法-0003-p1")
        self.assertEqual(out[-1]["id"], f"测试法-0003-p{len(out)}")
        for index, item in enumerate(out, start=1):
            self.assertEqual(item["part_index"], index)
            self.assertEqual(item["part_total"], len(out))
            self.assertEqual(item["law_num"], "第一条")
            self.assertTrue(item["manual"])


class TableSplitTest(unittest.TestCase):
    def table_body(self, rows=6):
        header = "测试法 · 表A.1"
        lines = [header]
        for i in range(rows):
            lines.append(
                f"编号：{i + 1} · 违法行为：测试违法行为{i + 1} · 处罚依据：依据第{i + 1}条"
                + "内容" * 60
            )
        return "\n".join(lines)

    def test_multi_row_table_split_by_rows(self):
        chunks = [
            make_chunk(
                "测试法-0010",
                self.table_body(6),
                law_num="表A.1 第1-6行",
                kind="table",
                span=[20, 20],
            )
        ]
        self.assertGreater(len(chunks[0]["chunk"]), 800)
        out = split_table_chunk(chunks, 0, 800)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertGreaterEqual(len(out), 2)
        self.assertEqual(out[0]["id"], "测试法-0010")
        self.assertEqual(out[0]["chunk_kind"], "table")
        self.assertTrue(out[0]["law_num"].startswith("表A.1 第1-"))
        self.assertTrue(out[-1]["law_num"].startswith("表A.1 第"))
        self.assertTrue(out[-1]["law_num"].endswith("-6行"))
        for item in out:
            self.assertTrue(item["chunk"].startswith("测试法 · 表A.1\n"))
            # 行拆块与整表共享同一行区间（靠 law_num 区分具体行），便于前端高亮
            self.assertEqual(item["span"], [20, 20])

    def test_single_row_table_not_split(self):
        text = "测试法 · 表A.2\n" + "编号：1 · 内容：" + "很长" * 500
        chunks = [make_chunk("测试法-0011", text, law_num="表A.2 第1-1行", kind="table")]
        self.assertIsNone(split_table_chunk(chunks, 0, 800))

    def test_small_table_not_split(self):
        chunks = [make_chunk("测试法-0012", self.table_body(2), law_num="表A.3 第1-2行", kind="table")]
        self.assertIsNone(split_table_chunk(chunks, 0, 5000))


class AutoPartChunksTest(unittest.TestCase):
    def test_text_and_table_together(self):
        chunks = [
            make_chunk("测试法-0001", long_text(40, 40), span=[1, 40]),
            make_chunk("测试法-0002", "短正文", span=[41, 41]),
            make_chunk(
                "测试法-0003",
                TableSplitTest().table_body(6),
                law_num="表A.1 第1-6行",
                kind="table",
                span=[50, 50],
            ),
        ]
        out = auto_part_chunks(chunks, LAW, max_chars=800, min_chars=200)
        self.assertGreater(len(out), 3)
        self.assertTrue(any(item["id"].endswith("-p2") for item in out))
        table_nums = [item["law_num"] for item in out if item["chunk_kind"] == "table"]
        self.assertGreaterEqual(len(table_nums), 2)
        self.assertTrue(table_nums[0].startswith("表A.1 第1-"))
        self.assertTrue(table_nums[-1].endswith("-6行"))
        # 短块原样保留
        self.assertIn("测试法-0002", [item["id"] for item in out])

    def test_no_change_returns_same_object(self):
        chunks = [make_chunk("测试法-0001", "短正文", span=[1, 1])]
        self.assertIs(auto_part_chunks(chunks, LAW, 800, 200), chunks)


class MaterializeTest(unittest.TestCase):
    """``materialize_chunks``：超长块真正拆开（part_span / 表格行拆 / 幂等）。"""

    def test_text_parts_get_part_span(self):
        lines = [
            "第一条 " + "安全生产责任制度应当落实到每一个岗位。" * 2,
            "第二条 " + "生产经营单位应当建立安全生产责任制。" * 2,
            "第三条 " + "从业人员应当接受安全生产教育和培训。" * 2,
        ]
        chunk = make_chunk("测试法-0001", "\n".join(lines), law_num="第一条", span=[1, 3])
        out = materialize_chunks([chunk], max_chars=80, min_chars=30, source_lines=lines)
        self.assertGreaterEqual(len(out), 2)
        self.assertTrue(all(item["id"].startswith("测试法-0001-p") for item in out))
        for item in out:
            self.assertEqual(item["part_total"], len(out))
            self.assertEqual(item["manual"], True)
            self.assertEqual(item["span"], [1, 3])
            self.assertIsNotNone(item["part_span"])
            start, end = item["part_span"]
            self.assertGreaterEqual(start, 1)
            self.assertLessEqual(end, 3)
            self.assertLessEqual(start, end)
        # part_span 依次递进，不重叠
        spans = [item["part_span"] for item in out]
        for left, right in zip(spans, spans[1:]):
            self.assertLessEqual(left[1], right[1])

    def test_short_chunk_and_existing_part_untouched(self):
        chunks = [
            make_chunk("测试法-0001", "短正文", span=[1, 1]),
            make_chunk("测试法-0002-p1", "已经是 part 的正文" * 100, span=[2, 2]),
        ]
        chunks[1]["part_index"] = 1
        chunks[1]["part_total"] = 2
        self.assertIs(materialize_chunks(chunks, 100, 30, []), chunks)

    def test_drop_single_part_reindexes_group(self):
        text = "\n".join("第%d条 " % i + "内容" * 80 for i in range(1, 4))
        chunk = make_chunk("测试法-0001", text, span=[1, 3])
        parts = materialize_chunks([chunk], max_chars=120, min_chars=40, source_lines=[])
        self.assertGreaterEqual(len(parts), 3)
        first = parts[0]["id"]
        out = apply_ops(parts, [{"op": "drop", "id": first}], law_name=LAW)
        self.assertEqual(len(out), len(parts) - 1)
        self.assertEqual(out[0]["id"], "测试法-0001-p1")
        self.assertEqual(out[0]["part_index"], 1)
        self.assertEqual(out[0]["part_total"], len(parts) - 1)


if __name__ == "__main__":
    unittest.main()
