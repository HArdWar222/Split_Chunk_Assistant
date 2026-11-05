"""自动分 part 开关下的编辑命令：物化后的 part 是真实块，命令直接落在 part 上。"""

from __future__ import annotations

import json
import unittest

from chunk_studio.config import Config
from chunk_studio.studio import Studio

from tests.fixtures import remove, temp_dir

# 一条很长的正文，开启 auto_part 后会被切成多个 part。
LONG_TEXT = "第一条 " + "安全生产责任制度应当落实到每一个岗位和每一名从业人员，" * 8


class AutoPartCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = temp_dir()
        self.data_root = self.base / "data"
        source_dir = self.data_root / "source"
        doc_dir = self.data_root / "chunks" / "长文法"
        source_dir.mkdir(parents=True)
        doc_dir.mkdir(parents=True)
        (source_dir / "长文法.md").write_text("第一条 内容\n", encoding="utf-8")
        chunk = {
            "id": "长文法-0001",
            "chunk": LONG_TEXT,
            "law_name": "长文法",
            "law_num": "1",
            "law_invalid": "",
            "law_type": "",
            "law_level": "",
            "issuer": "",
            "effective_date": "",
            "source": "",
            "chunk_kind": "clause",
            "token_len": len(LONG_TEXT),
            "part_index": 1,
            "part_total": 1,
            "chunk_hash": "test",
        }
        (doc_dir / "长文法.chunks.jsonl").write_text(
            json.dumps(chunk, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        self.cfg = Config(
            data_root=str(self.data_root),
            auto_part=True,
            max_chars=120,
            min_part_chars=40,
        )
        self.studio = Studio(cfg=self.cfg)
        self.key = "data/长文法"

    def tearDown(self) -> None:
        remove(self.base)

    def _part_ids(self, doc: dict) -> list[str]:
        return [chunk["id"] for chunk in doc["chunks"] if chunk["id"].endswith(tuple(f"-p{i}" for i in range(1, 10)))]

    def test_drop_part_id_removes_single_part(self):
        """物化之后 part 是真实块：删分块卡只删这一块，同组其余 part 重排编号。"""
        doc = self.studio.doc_payload(self.key)
        parts = self._part_ids(doc)
        self.assertGreater(len(parts), 2, "开启 auto_part 后应看到多个 part")

        after = self.studio.apply_op(self.key, {"op": "drop", "id": parts[1]})
        remaining = self._part_ids(after)
        self.assertEqual(len(remaining), len(parts) - 1)
        self.assertEqual(
            [chunk["part_index"] for chunk in after["chunks"]],
            list(range(1, len(parts))),
        )
        self.assertEqual(after["chunks"][0]["part_total"], len(parts) - 1)

        restored = self.studio.undo_op(self.key)
        self.assertEqual(len(self._part_ids(restored)), len(parts))

    def test_patch_part_id_edits_only_that_part(self):
        parts = self._part_ids(self.studio.doc_payload(self.key))
        after = self.studio.apply_op(
            self.key,
            {"op": "patch", "id": parts[-1], "chunk": "第一条 替换后的正文。"},
        )
        texts = [chunk["chunk"] for chunk in after["chunks"]]
        self.assertEqual(texts[-1], "第一条 替换后的正文。")
        self.assertEqual(len(texts), len(parts))
        self.assertNotIn(LONG_TEXT, texts)


if __name__ == "__main__":
    unittest.main()
