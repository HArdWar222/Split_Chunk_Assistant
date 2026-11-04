"""测试本地「两目录」数据源（DirManifest + FacBridge 泛化）。"""

from __future__ import annotations

import unittest
from pathlib import Path

from chunk_studio.fac_bridge import FacBridge
from chunk_studio.manifest import DirManifest, ManifestError

from tests.fixtures import make_data_dir, remove, temp_dir


class DirManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = temp_dir()
        self.data_root = make_data_dir(self.base)

    def tearDown(self) -> None:
        remove(self.base)

    def test_load_files_and_counts(self) -> None:
        manifest = DirManifest(self.data_root)
        self.assertEqual(manifest.n_files, 1)
        self.assertEqual(manifest.n_chunks, 2)
        self.assertEqual(list(manifest.files), ["data/测试包法规"])
        entry = manifest.get("data/测试包法规")
        self.assertTrue(entry.local)
        self.assertTrue(Path(entry.source_path).is_file())
        self.assertEqual(entry.extras_count, 2)
        self.assertEqual(entry.kinds, {"clause": 2})

    def test_payload_has_mode_and_groups(self) -> None:
        payload = DirManifest(self.data_root).payload()
        self.assertEqual(payload["mode"], "dirs")
        self.assertEqual(payload["totals"]["n_files"], 1)
        self.assertEqual(payload["groups"][0]["name"], "data")
        self.assertTrue(payload["groups"][0]["files"][0]["local"])

    def test_subdir_becomes_group(self) -> None:
        sub = self.data_root / "chunks" / "子目录"
        sub.mkdir(parents=True)
        chunk = {
            "id": "子目录法-0001",
            "chunk": "内容",
            "law_name": "子目录法",
            "law_num": "1",
            "chunk_kind": "paragraph",
        }
        (sub / "子目录法.chunks.jsonl").write_text(
            __import__("json").dumps(chunk, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        manifest = DirManifest(self.data_root)
        self.assertIn("data/子目录/子目录法", manifest.files)
        self.assertIn("data/子目录", manifest.groups)
        self.assertTrue(
            any("源文件缺失" in w for w in manifest.get("data/子目录/子目录法").warnings)
        )

    def test_missing_dirs_raises(self) -> None:
        empty = self.base / "empty"
        empty.mkdir()
        with self.assertRaises(ManifestError):
            DirManifest(empty)

    def test_source_text_and_chunk_file(self) -> None:
        manifest = DirManifest(self.data_root)
        self.assertIn("测试包法规", manifest.source_text("data/测试包法规"))
        path = manifest.chunk_file("data/测试包法规")
        self.assertTrue(path.is_file())
        self.assertEqual(path.name, "测试包法规.chunks.jsonl")

    def test_nested_extras_format(self) -> None:
        """FAC-RAG 风格的 {"id","extras"} 嵌套侧车也能解析（不只是平铺）。"""
        base = temp_dir()
        try:
            root = make_data_dir(base, extras_style="nested")
            extras = DirManifest(root).extras_for("data/测试包法规")
            self.assertEqual(extras["测试包法规-0001"]["clause"], "第一条")
            self.assertNotIn("extras", extras["测试包法规-0001"])
        finally:
            remove(base)


class DirBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = temp_dir()
        self.data_root = make_data_dir(self.base)
        self.bridge = FacBridge(DirManifest(self.data_root))

    def tearDown(self) -> None:
        remove(self.base)

    def test_token_len_backfilled_and_part_int(self) -> None:
        chunks = self.bridge.chunks("data/测试包法规")
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["token_len"], len(chunks[0]["chunk"]))
        self.assertIsInstance(chunks[0]["part_index"], int)
        self.assertEqual(chunks[0]["part_index"], 1)

    def test_extras_flat_format(self) -> None:
        extras = self.bridge.extras("data/测试包法规")
        self.assertEqual(set(extras), {"测试包法规-0001", "测试包法规-0002"})
        self.assertEqual(extras["测试包法规-0001"]["clause"], "第一条")

    def test_stats_report_dirs_mode(self) -> None:
        stats = self.bridge.stats()
        self.assertEqual(stats["mode"], "dirs")
        self.assertEqual(stats["n_files"], 1)
        self.assertEqual(stats["n_chunks"], 2)


if __name__ == "__main__":
    unittest.main()
