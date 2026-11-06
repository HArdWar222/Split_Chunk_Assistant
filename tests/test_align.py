"""align.py 的单测（纯函数，不依赖 FAC-RAG / 真实语料）。

覆盖：文本规范化、面包屑剥离、单调扫描、条文/段落对齐、表格行对齐、未对齐分档。
"""

from __future__ import annotations

import unittest

from chunk_studio.align import (
    DEFAULT_CLEAN_RULES,
    align_chunks,
    clean_text,
    norm_line,
    norm_pipe,
    row_variants,
    strip_html,
    summarize_align,
    _scan,
)


class TextNormalizeTest(unittest.TestCase):
    def test_strip_html_unescapes(self):
        self.assertEqual(strip_html("<b>甲&amp;乙</b>"), "甲&乙")

    def test_norm_line_removes_whitespace_and_html(self):
        self.assertEqual(norm_line("  <b>第一条</b>  为了 测试。 "), "第一条为了测试。")

    def test_norm_line_strips_breadcrumb(self):
        # 标准式渲染会把面包屑注入首行：path > 正文
        payload = "1 总　则 > 为了预防建筑火灾，制定本规范。"
        self.assertEqual(norm_line(payload), norm_line("为了预防建筑火灾，制定本规范。"))

    def test_clean_text_latex_escapes(self):
        self.assertEqual(clean_text(r"面积\[m^2\]"), "面积【m^2】")
        self.assertEqual(clean_text("价\\(元\\)"), "价（元）")

    def test_clean_text_optional_rules(self):
        self.assertEqual(clean_text("**粗体**", ("md_bold",)), "粗体")
        self.assertEqual(clean_text("正文...... 12", ("page_tail",)), "正文")
        self.assertEqual(clean_text("第二条（续）", ("continuation_markers",)), "第二条")

    def test_norm_pipe_removes_table_pipes(self):
        self.assertEqual(norm_pipe("| 序号 | 名称 |"), "序号名称")

    def test_row_variants_dedup_adjacent_cells(self):
        variants = row_variants([["1", "1", "阿片", "阿片"], ["2"]])
        self.assertIn("11阿片阿片2", variants)
        self.assertIn("1阿片2", variants)


class ScanTest(unittest.TestCase):
    def test_scan_prefers_first_hit_after_start(self):
        lines = ["附录", "正文", "附录"]
        self.assertEqual(_scan(lines, "附录", 1), 2)

    def test_scan_allows_short_substring(self):
        # 短锚点（长度 ≥2）也要能子串命中：标准式「范围」「术语」这类两字标题
        self.assertEqual(_scan(["1 范围"], "范围", 0), 0)

    def test_scan_miss(self):
        self.assertEqual(_scan(["正文"], "不存在", 0), -1)

    def test_scan_similarity_fallback_for_superscript(self):
        # 源文里的 m<sup>2</sup> 清洗后会多出一个 2，chunk 正文里没有 → 精确/子串都失败
        # （_scan 的入参是 norm_line 之后的字符串，与 align_chunks 内部一致；
        #   真实语料里这类行相似度约 0.98，高于 SIMILAR_RATIO=0.93）
        lines = [
            norm_line("5.2.3 安全出口不应少于 2个，建筑面积不大于 200m<sup>2</sup> 且人数不大于 50 人的单层小购物场所。"),
        ]
        target = norm_line("5.2.3 安全出口不应少于 2个，建筑面积不大于 200m 且人数不大于 50 人的单层小购物场所。")
        self.assertEqual(_scan(lines, target, 0), 0)


class AlignClauseTest(unittest.TestCase):
    SOURCE = [
        "# 测试法",
        "",
        "第一条 为了测试。",
        "",
        "第二条 测试结束。",
    ]

    def chunks(self):
        return [
            {"id": "测试法-0001", "chunk": "第一条 为了测试。", "chunk_kind": "clause"},
            {"id": "测试法-0002", "chunk": "第二条 测试结束。", "chunk_kind": "clause"},
        ]

    def test_exact_alignment(self):
        aligned = align_chunks(self.chunks(), self.SOURCE)
        self.assertEqual([a["span"] for a in aligned], [[2, 3], [4, 5]])
        self.assertEqual([a["status"] for a in aligned], ["exact", "exact"])

    def test_breadcrumb_chunk_matches_body_line(self):
        chunks = [{"id": "c1", "chunk": "1 范围 > 为了测试。", "chunk_kind": "clause"}]
        source = ["# 标准", "", "1 范围", "为了测试。"]
        aligned = align_chunks(chunks, source)
        self.assertEqual(aligned[0]["span"], [3, 4])
        self.assertEqual(aligned[0]["status"], "exact")

    def test_multiline_chunk_spans_multiple_lines(self):
        chunks = [{"id": "c1", "chunk": "第一条 为了测试。\n第二条 测试结束。", "chunk_kind": "clause"}]
        aligned = align_chunks(chunks, self.SOURCE)
        self.assertEqual(aligned[0]["span"], [2, 5])

    def test_lookback_two_lines(self):
        # 游标允许回看 2 行：表题/段首这类信息常落在游标前一两行
        source = ["甲", "重复行", "乙"]
        chunks = [
            {"id": "c1", "chunk": "乙", "chunk_kind": "clause"},
            {"id": "c2", "chunk": "重复行", "chunk_kind": "clause"},
        ]
        aligned = align_chunks(chunks, source)
        self.assertEqual(aligned[0]["span"], [2, 3])
        self.assertEqual(aligned[1]["span"], [1, 2])

    def test_miss_gets_none_span(self):
        chunks = [{"id": "c1", "chunk": "源文件里没有这句话。", "chunk_kind": "clause"}]
        aligned = align_chunks(chunks, self.SOURCE)
        self.assertIsNone(aligned[0]["span"])
        self.assertEqual(aligned[0]["status"], "miss")


class AlignTableTest(unittest.TestCase):
    def test_table_rows_alignment(self):
        source = [
            "# 测试标准",
            "",
            "表A.1 测试表",
            "<table><tr><td>1</td><td>名称</td></tr><tr><td>2</td><td>第二行</td></tr></table>",
        ]
        chunks = [
            {
                "id": "测试标准-0001",
                "chunk": "1\n名称\n2\n第二行",
                "chunk_kind": "table",
            }
        ]
        extras = {
            "测试标准-0001": {
                "table_label": "表A.1",
                "table_title": "测试表",
                "table_grid": {
                    "headers": [],
                    "rows": [["1", "名称"], ["2", "第二行"]],
                    "header_rows": 0,
                    "width": 2,
                },
            }
        }
        aligned = align_chunks(chunks, source, extras)
        self.assertEqual(aligned[0]["status"], "table_rows")
        self.assertIsNotNone(aligned[0]["span"])
        # 命中行是第 4 行（0-based 3），并允许把表题行带进来
        self.assertGreaterEqual(aligned[0]["span"][0], 2)
        self.assertEqual(aligned[0]["span"][1], 4)

    def test_table_without_grid_falls_back_to_label(self):
        source = ["# 标准", "表1 参数表", "正文"]
        chunks = [{"id": "c1", "chunk": "示意图", "chunk_kind": "table"}]
        extras = {"c1": {"table_label": "表1"}}
        aligned = align_chunks(chunks, source, extras)
        self.assertEqual(aligned[0]["span"], [1, 2])
        self.assertEqual(aligned[0]["status"], "table_label")

    def test_single_line_table_below_huge_threshold_reuses_line(self):
        # 整张表压成一行但不足 HUGE_LINE_CHARS(2000)：多行仍应复用同一源行
        table = (
            "<table><tr><td>序号</td><td>名称</td></tr>"
            "<tr><td>1</td><td>甲</td></tr>"
            "<tr><td>2</td><td>乙</td></tr></table>"
        )
        source = ["# 标准", "表A.1 测试表", table]
        chunks = [{"id": "c1", "chunk": "1 甲\n2 乙", "chunk_kind": "table"}]
        extras = {
            "c1": {
                "table_label": "表A.1",
                "table_title": "测试表",
                "table_grid": {
                    "headers": ["序号", "名称"],
                    "rows": [["1", "甲"], ["2", "乙"]],
                    "header_rows": 1,
                    "width": 2,
                },
            }
        }
        aligned = align_chunks(chunks, source, extras)
        self.assertEqual(aligned[0]["status"], "table_rows")
        self.assertEqual(aligned[0]["span"][1], 3)  # 表行是索引 2，span 右端 = 3
        self.assertGreaterEqual(aligned[0]["span"][0], 1)  # 允许把表题带进来

    def test_two_tables_same_first_rows_anchor_to_own_caption(self):
        # 两张表前几行内容完全相同（真实语料里的表C.1/表C.2）：
        # 后一张表的切片不能命中前一张表
        table1 = (
            "<table><tr><td>10</td><td>00</td></tr><tr><td>11</td><td>01</td></tr>"
            "<tr><td>12</td><td>02</td></tr></table>"
        )
        table2 = (
            "<table><tr><td>10</td><td>00</td></tr><tr><td>20</td><td>03</td></tr></table>"
        )
        source = [
            "# 附录 C",
            "表C.1 第一张表",
            table1,
            "",
            "表C.2 第二张表",
            table2,
        ]
        chunks = [
            {"id": "c1", "chunk": "10 00\n20 03", "chunk_kind": "table"},
        ]
        extras = {
            "c1": {
                "table_label": "表C.2",
                "table_title": "第二张表",
                "table_grid": {
                    "headers": [],
                    "rows": [["10", "00"], ["20", "03"]],
                    "header_rows": 0,
                    "width": 2,
                },
            }
        }
        aligned = align_chunks(chunks, source, extras)
        self.assertEqual(aligned[0]["status"], "table_rows")
        self.assertGreaterEqual(aligned[0]["span"][0], 4)  # 至少从表C.2 表题行开始
        self.assertEqual(aligned[0]["span"][1], 6)  # 表C.2 的表行是索引 5


class PinnedSpanTest(unittest.TestCase):
    def test_pinned_span_from_extras(self):
        lines = ["第一条 甲", "第二条 乙", "第三条 丙"]
        chunks = [{"id": "a", "chunk": "完全不相关", "chunk_kind": "paragraph"}]
        out = align_chunks(chunks, lines, {"a": {"span": [1, 3]}})
        self.assertEqual(out[0]["span"], [1, 3])
        self.assertEqual(out[0]["status"], "approx")

    def test_table_label_fallback_reuses_known_span(self):
        lines = ["表1 甲表", "行一", "行二"]
        chunks = [
            {"id": "a", "chunk": "表1 甲表", "chunk_kind": "table", "law_num": "表1 第1-1行"},
            {
                "id": "b",
                "chunk": "第二部分 裁量细则 · 表1\n不存在的行",
                "chunk_kind": "table",
                "law_num": "第二部分 裁量细则 表1 第2-2行",
            },
        ]
        out = align_chunks(chunks, lines, {"a": {"span": [0, 1]}})
        self.assertEqual(out[0]["span"], [0, 1])
        self.assertEqual(out[1]["span"], [0, 1])  # 同表号复用已知区间
        self.assertEqual(out[1]["status"], "table_label")

    def test_prev_single_table_line_fallback(self):
        lines = ["<table><tr><td>甲</td></tr></table>", "其他"]
        chunks = [
            {"id": "a", "chunk": "甲表", "chunk_kind": "table", "law_num": "表1 第1-1行"},
            {"id": "b", "chunk": "无关正文", "chunk_kind": "table", "law_num": "第2行"},
        ]
        out = align_chunks(chunks, lines, {"a": {"span": [0, 1]}})
        self.assertEqual(out[0]["span"], [0, 1])
        self.assertEqual(out[1]["span"], [0, 1])  # 上一块落在单行 <table> 上，沿用该行
        self.assertEqual(out[1]["status"], "table_label")

    def test_caption_chunk_stops_before_next_caption(self):
        lines = ["表A.1 甲表", "行一", "行二", "表A.2 乙表", "行三"]
        chunks = [{"id": "a", "chunk": "表A.1 甲表\n行一\n行二\n行三", "chunk_kind": "paragraph"}]
        out = align_chunks(chunks, lines)
        self.assertEqual(out[0]["span"], [0, 3])
        self.assertEqual(out[0]["status"], "exact")


class SummarizeTest(unittest.TestCase):
    def test_summarize_counts(self):
        aligned = [
            {"span": [0, 1], "status": "exact"},
            {"span": [2, 3], "status": "table_rows"},
            {"span": None, "status": "miss"},
        ]
        result = summarize_align(aligned)
        self.assertEqual(result["n_aligned"], 2)
        self.assertEqual(result["n_miss"], 1)
        self.assertEqual(result["align_kinds"], {"exact": 1, "table_rows": 1, "miss": 1})

    def test_default_rules_are_latex_only(self):
        self.assertIn("latex_escapes", DEFAULT_CLEAN_RULES)
        self.assertNotIn("md_bold", DEFAULT_CLEAN_RULES)


if __name__ == "__main__":
    unittest.main()
