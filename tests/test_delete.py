"""文件删除（P3 物理删除）的单测。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from chunk_studio import drafts
from chunk_studio import status as review
from chunk_studio.config import Config
from chunk_studio.manifest import ManifestError
from chunk_studio.studio import Studio
from tests.fixtures import make_two_file_data_dir

KEY = "data/测试法"
OTHER = "data/GB 0001-2020 测试标准"


class DeleteFilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.data_root = make_two_file_data_dir(self.base)
        self.studio = Studio(cfg=Config(data_root=str(self.data_root), port=8790))
        self.entry = self.studio.bridge.manifest.get(KEY)
        self.chunks_path = Path(self.entry.jsonl)
        self.stem = self.studio._stem_for(KEY)
        self.sha = drafts.sha12(self.chunks_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _make_draft_backup_status(self) -> tuple[Path, Path, Path]:
        draft = drafts.draft_file(self.data_root / "draft", self.stem, self.sha)
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text("{}\n", encoding="utf-8")
        backup = self.data_root / "backup" / (self.stem + ".20260101-000000.jsonl")
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text("{}\n", encoding="utf-8")
        review.mark_in_review(self.data_root, self.stem, self.sha)
        status = review.status_path(self.data_root, self.stem)
        self.assertTrue(status.is_file())
        return draft, backup, status

    def test_delete_removes_file_artifacts(self) -> None:
        draft, backup, status = self._make_draft_backup_status()
        result = self.studio.delete_files([KEY])
        self.assertTrue(result["ok"])
        self.assertEqual(result["deleted"], [KEY])
        self.assertEqual(result["failed"], [])
        self.assertFalse(self.chunks_path.parent.exists())
        self.assertFalse(Path(self.entry.source_path).exists())
        self.assertFalse(draft.exists())
        self.assertFalse(backup.exists())
        self.assertFalse(status.exists())
        # 其它文件不受影响
        self.assertIn(OTHER, self.studio.bridge.manifest.files)
        self.assertTrue(Path(self.studio.bridge.manifest.get(OTHER).jsonl).is_file())

    def test_delete_unknown_key_is_reported_not_raised(self) -> None:
        result = self.studio.delete_files(["data/不存在"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["deleted"], [])
        self.assertIn("没有这个文件", result["failed"][0]["error"])

    def test_delete_empty_selection_raises(self) -> None:
        with self.assertRaises(ManifestError):
            self.studio.delete_files([])
        with self.assertRaises(ManifestError):
            self.studio.delete_files(["   "])

    def test_delete_dedupes_keys(self) -> None:
        result = self.studio.delete_files([KEY, KEY])
        self.assertTrue(result["ok"])
        self.assertEqual(result["deleted"], [KEY])


if __name__ == "__main__":
    unittest.main()
