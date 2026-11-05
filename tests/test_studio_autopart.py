"""自动分 part 开关下的编辑命令：``-pN`` 展示 id 必须映射回真实块 id。"""

from __future__ import annotations

import json
import unittest

from chunk_studio.config import Config
from chunk_studio.ops import OpError
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

    def test_drop_part_id_removes_whole_logical_chunk(self):
        doc = self.studio.doc_payload(self.key)
        parts = self._part_ids(doc)
        self.assertGreater(len(parts), 1, "开启 auto_part 后应看到多个 part")

        after = self.studio.apply_op(self.key, {"op": "drop", "id": parts[1]})
        self.assertEqual(after["chunks"], [])

        restored = self.studio.undo_op(self.key)
        self.assertGreater(len(self._part_ids(restored)), 1)

    def test_merge_parts_of_same_chunk_is_rejected(self):
        parts = self._part_ids(self.studio.doc_payload(self.key))
        with self.assertRaises(OpError) as ctx:
            self.studio.apply_op(
                self.key, {"op": "merge_many", "ids": [parts[0], parts[1]]}
            )
        self.assertIn("集体合并至少需要 2 个切片 id", str(ctx.exception))

    def test_patch_part_id_edits_whole_logical_chunk(self):
        parts = self._part_ids(self.studio.doc_payload(self.key))
        after = self.studio.apply_op(
            self.key,
            {"op": "patch", "id": parts[-1], "chunk": "第一条 替换后的正文。"},
        )
        texts = [chunk["chunk"] for chunk in after["chunks"]]
        self.assertEqual(texts, ["第一条 替换后的正文。"])


if __name__ == "__main__":
    unittest.main()
