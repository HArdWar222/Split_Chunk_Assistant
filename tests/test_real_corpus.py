"""真实语料测试：只在检测到本机 FAC-RAG 产物时运行。

这些断言绑定的是 2026-11-02 生成的那批产物（P0 的验收口径）。
如果另一条对话重跑了切片，数字会变——届时更新期望值即可。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from chunk_studio.config import Config
from chunk_studio.manifest import Manifest

FAC_RAG = Path(r"C:\Users\QiuYe\Desktop\FAC-RAG")
CHUNKS_DIR = FAC_RAG / "data" / "chunks_v2"


@unittest.skipUnless((CHUNKS_DIR / "_manifest.json").is_file(), "本机没有 FAC-RAG 切块产物，跳过")
class RealCorpusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = Manifest(CHUNKS_DIR)

    def test_totals(self):
        self.assertEqual(self.manifest.n_files, 97)
        self.assertEqual(self.manifest.n_chunks, 12718)
        self.assertTrue(self.manifest.gate.get("ok"))

    def test_groups(self):
        groups = self.manifest.groups
        self.assertEqual(len(groups["national_laws"]), 37)
        self.assertEqual(len(groups["GB_laws"]), 60)

    def test_fire_law_74_chunks(self):
        entry = self.manifest.get("national_laws/中华人民共和国消防法")
        self.assertEqual(entry.n_chunks, 74)
        self.assertEqual(entry.kinds.get("clause"), 74)

    def test_config_default_points_here(self):
        cfg = Config(fac_root=str(FAC_RAG))
        self.assertEqual(Path(cfg.chunks_dir), CHUNKS_DIR)
        self.assertEqual(cfg.validate(), [])


if __name__ == "__main__":
    unittest.main()
