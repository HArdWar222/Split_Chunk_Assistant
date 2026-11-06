import unittest

from chunk_studio.studio import Studio


class MatchExtrasTest(unittest.TestCase):
    def test_pinned_row_wins_even_if_embed_text_is_stale(self) -> None:
        chunks = [{"id": "x", "chunk": "术语和定义\n下列术语和定义适用于本文件。"}]
        extras = {"x": {"embed_text": "前  言\n本文件按照…", "span": [171, 202]}}
        matched = Studio._match_extras(chunks, extras)
        self.assertEqual(matched["x"]["span"], [171, 202])

    def test_row_without_span_falls_back_to_content_match(self) -> None:
        chunks = [{"id": "y", "chunk": "新正文"}]
        extras = {"z": {"embed_text": "新正文"}}
        matched = Studio._match_extras(chunks, extras)
        self.assertIs(matched["y"], extras["z"])

    def test_row_without_span_and_wrong_text_is_dropped(self) -> None:
        chunks = [{"id": "y", "chunk": "新正文"}]
        extras = {"y": {"embed_text": "旧正文"}}
        matched = Studio._match_extras(chunks, extras)
        self.assertIsNone(matched.get("y"))
