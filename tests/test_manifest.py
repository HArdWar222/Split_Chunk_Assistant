from __future__ import annotations

import unittest
from pathlib import Path

from chunk_studio.fac_bridge import FacBridge
from chunk_studio.manifest import Manifest, ManifestError, SourceMissingError
from tests.fixtures import make_chunks_dir, remove, temp_dir


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self._tmp = temp_dir()
        self.base = self._tmp
        self.chunks_dir = make_chunks_dir(self.base)

    def tearDown(self):
        remove(self._tmp)

    def test_load_and_counts(self):
        m = Manifest(self.chunks_dir)
        self.assertEqual(m.n_files, 2)
        self.assertEqual(m.n_chunks, 3)
        self.assertEqual(m.generated_at, "2026-11-02T23:55:11")
        self.assertEqual(m.engine, "chunking_v2")
        self.assertTrue(m.gate.get("ok"))

    def test_groups_keep_manifest_order(self):
        m = Manifest(self.chunks_dir)
        self.assertEqual(list(m.groups.keys()), ["national_laws", "GB_laws"])
        self.assertEqual(len(m.groups["national_laws"]), 1)

    def test_payload_shape(self):
        payload = Manifest(self.chunks_dir).payload()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["totals"]["n_files"], 2)
        self.assertEqual(payload["totals"]["n_chunks"], 3)
        self.assertEqual(payload["totals"]["char_coverage"], 0.9123)
        self.assertTrue(payload["totals"]["gate_ok"])
        file0 = payload["groups"][0]["files"][0]
        self.assertEqual(file0["key"], "national_laws/测试法")
        self.assertEqual(len(file0["sha256"]), 12)
        self.assertEqual(file0["kinds"], {"clause": 2})
        self.assertEqual(file0["n_warnings"], 0)
        self.assertTrue(payload["groups"][1]["files"][0]["n_warnings"] == 1)

    def test_get_missing_key(self):
        with self.assertRaises(ManifestError):
            Manifest(self.chunks_dir).get("no/such/key")

    def test_source_text(self):
        m = Manifest(self.chunks_dir)
        text = m.source_text("national_laws/测试法")
        self.assertIn("第一条 为了测试", text)

    def test_source_missing(self):
        m = Manifest(self.chunks_dir)
        entry = m.get("national_laws/测试法")
        Path(entry.source_path).unlink()
        with self.assertRaises(SourceMissingError) as ctx:
            m.source_text("national_laws/测试法")
        self.assertIn("源文件缺失", str(ctx.exception))
        self.assertEqual(ctx.exception.path, entry.source_path)

    def test_missing_manifest(self):
        empty = self.base / "empty"
        empty.mkdir()
        with self.assertRaises(ManifestError):
            Manifest(empty)

    def test_bad_manifest_json(self):
        bad = self.base / "bad"
        bad.mkdir()
        (bad / "_manifest.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(ManifestError):
            Manifest(bad)


class FacBridgeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = temp_dir()
        self.base = self._tmp
        self.chunks_dir = make_chunks_dir(self.base)
        self.bridge = FacBridge(self.chunks_dir)

    def tearDown(self):
        remove(self._tmp)

    def test_chunks(self):
        chunks = self.bridge.chunks("national_laws/测试法")
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["id"], "测试法-0001")
        self.assertEqual(chunks[0]["chunk"], "第一条 为了测试。")

    def test_extras(self):
        extras = self.bridge.extras("national_laws/测试法")
        self.assertEqual(extras["测试法-0001"]["region"], "body")

    def test_load_doc(self):
        entry, chunks, extras = self.bridge.load_doc("GB_laws/GB 0001-2020 测试标准")
        self.assertEqual(entry.n_chunks, 1)
        self.assertEqual(chunks[0]["law_num"], "1")
        self.assertIsInstance(extras, dict)
        self.assertIn("GB 0001-2020 测试标准-0001", extras)

    def test_stats(self):
        stats = self.bridge.stats()
        self.assertEqual(stats["n_files"], 2)
        self.assertEqual(stats["n_chunks"], 3)


if __name__ == "__main__":
    unittest.main()
