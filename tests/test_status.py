"""文件级审核状态（data/status）单元测试。"""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from chunk_studio import status

REPO_ROOT = Path(__file__).resolve().parent.parent
TMP_ROOT = REPO_ROOT / "tests" / "_tmp" / "status"


def _make_chunks(path: Path, content: str = "chunk-a\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, "utf-8")
    return path


class StatusTest(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(TMP_ROOT, ignore_errors=True)
        self.root = TMP_ROOT / "data"
        (self.root / "chunks" / "测试法").mkdir(parents=True, exist_ok=True)
        self.chunks = _make_chunks(self.root / "chunks" / "测试法" / "测试法.chunks.jsonl")
        self.stem = "测试法"

    def tearDown(self) -> None:
        shutil.rmtree(TMP_ROOT, ignore_errors=True)

    def _sha(self) -> str:
        from chunk_studio.drafts import sha12

        return sha12(self.chunks)

    def test_missing_status_is_none(self) -> None:
        self.assertEqual(status.state_for_chunks(self.root, self.stem, self.chunks)["state"], status.STATE_NONE)

    def test_mark_in_review_and_resolve(self) -> None:
        sha = self._sha()
        status.mark_in_review(self.root, self.stem, sha)
        got = status.state_for_chunks(self.root, self.stem, self.chunks)
        self.assertEqual(got["state"], status.STATE_IN_REVIEW)
        self.assertTrue(status.status_path(self.root, self.stem).is_file())

    def test_in_review_is_idempotent(self) -> None:
        sha = self._sha()
        first = status.mark_in_review(self.root, self.stem, sha)
        second = status.mark_in_review(self.root, self.stem, sha)
        self.assertEqual(first["updated_at"], second["updated_at"])

    def test_reviewed_then_edit_returns_to_in_review_and_discard_restores_reviewed(self) -> None:
        sha = self._sha()
        status.mark_reviewed(self.root, self.stem, sha)
        self.assertEqual(status.state_for_chunks(self.root, self.stem, self.chunks)["state"], status.STATE_REVIEWED)
        status.mark_in_review(self.root, self.stem, sha)
        self.assertEqual(status.state_for_chunks(self.root, self.stem, self.chunks)["state"], status.STATE_IN_REVIEW)
        status.restore_previous(self.root, self.stem, sha)
        self.assertEqual(status.state_for_chunks(self.root, self.stem, self.chunks)["state"], status.STATE_REVIEWED)

    def test_in_review_discard_returns_to_none(self) -> None:
        sha = self._sha()
        status.mark_in_review(self.root, self.stem, sha)
        status.restore_previous(self.root, self.stem, sha)
        self.assertEqual(status.state_for_chunks(self.root, self.stem, self.chunks)["state"], status.STATE_NONE)
        self.assertFalse(status.status_path(self.root, self.stem).exists())

    def test_fingerprint_mismatch_falls_back_to_none(self) -> None:
        sha = self._sha()
        status.mark_reviewed(self.root, self.stem, sha)
        self.chunks.write_text("changed\n", "utf-8")
        self.assertEqual(status.state_for_chunks(self.root, self.stem, self.chunks)["state"], status.STATE_NONE)
