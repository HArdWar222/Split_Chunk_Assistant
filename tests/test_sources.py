"""来源类别（P3 文件级人工标注）的单测。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chunk_studio import drafts, sources, status as review
from chunk_studio.config import Config
from chunk_studio.manifest import ManifestError
from chunk_studio.studio import Studio
from tests.fixtures import make_two_file_data_dir

KEY = "data/测试法"
OTHER = "data/GB 0001-2020 测试标准"


class SourcesModuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_validate_accepts_categories_and_empty(self) -> None:
        for category in sources.CATEGORIES:
            self.assertEqual(sources.validate(category), category)
        self.assertEqual(sources.validate(""), "")
        self.assertEqual(sources.validate(None), "")

    def test_validate_accepts_custom_and_rejects_bad_names(self) -> None:
        self.assertEqual(sources.validate("行政法规"), "行政法规")
        self.assertEqual(sources.validate("  行业标准  "), "行业标准")
        with self.assertRaises(sources.SourceError):
            sources.validate("a/b")
        with self.assertRaises(sources.SourceError):
            sources.validate("行" * (sources.MAX_NAME_LEN + 1))

    def test_load_missing_is_empty(self) -> None:
        self.assertEqual(sources.load(self.base), {})

    def test_save_load_roundtrip_keeps_custom_categories(self) -> None:
        sources.save(self.base, {"甲法": "法律", "乙法": "行政法规", "": "地方标准"})
        self.assertEqual(sources.load(self.base), {"甲法": "法律", "乙法": "行政法规"})
        self.assertIn("行政法规", sources.list_categories(self.base))
        registry = json.loads(sources.sources_path(self.base).read_text(encoding="utf-8"))
        self.assertIsInstance(registry.get("categories"), list)
        self.assertIsInstance(registry.get("files"), dict)
        # 损坏的 json 返回空表而不是抛错
        path = sources.sources_path(self.base)
        path.write_text("{oops", encoding="utf-8")
        self.assertEqual(sources.load(self.base), {})

    def test_legacy_flat_format_still_loads(self) -> None:
        path = sources.sources_path(self.base)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"甲法": "法律"}, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(sources.load(self.base), {"甲法": "法律"})
        self.assertIn("法律", sources.list_categories(self.base))

    def test_add_category_is_idempotent(self) -> None:
        categories = sources.add_category(self.base, "行政法规")
        self.assertIn("行政法规", categories)
        categories = sources.add_category(self.base, "行政法规")
        self.assertEqual(categories.count("行政法规"), 1)
        self.assertIn("法律", categories)

    def test_apply_to_chunks_file_rewrites_every_row(self) -> None:
        data_root = make_two_file_data_dir(self.base)
        studio = Studio(cfg=Config(data_root=str(data_root), port=8790))
        chunks_path = Path(studio.bridge.manifest.get(KEY).jsonl)
        n = sources.apply_to_chunks_file(chunks_path, "规范性文件")
        rows = [json.loads(line) for line in chunks_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(n, len(rows))
        self.assertEqual({row["source"] for row in rows}, {"规范性文件"})


class SetFileSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.data_root = make_two_file_data_dir(self.base)
        self.studio = Studio(cfg=Config(data_root=str(self.data_root), port=8790))
        self.entry = self.studio.bridge.manifest.get(KEY)
        self.chunks_path = Path(self.entry.jsonl)
        self.stem = self.studio._stem_for(KEY)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _sources_of_file(self) -> set[str]:
        rows = [
            json.loads(line)
            for line in self.chunks_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return {str(row.get("source") or "") for row in rows}

    def test_set_source_updates_chunks_and_sources_json(self) -> None:
        result = self.studio.set_file_source(KEY, "地方标准")
        self.assertTrue(result["ok"])
        self.assertEqual(result["n_chunks"], 2)
        self.assertEqual(self._sources_of_file(), {"地方标准"})
        self.assertEqual(sources.load(self.data_root), {self.entry.law_name: "地方标准"})
        # 其它文件不变
        other = self.studio.bridge.manifest.get(OTHER)
        other_rows = [
            json.loads(line)
            for line in Path(other.jsonl).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertNotIn("地方标准", {str(row.get("source") or "") for row in other_rows})

    def test_files_payload_exposes_category(self) -> None:
        self.studio.set_file_source(KEY, "部门规章")
        payload = self.studio.files_payload()
        found = {}
        for group in payload["groups"]:
            for item in group["files"]:
                found[item["key"]] = item.get("source_category")
        self.assertEqual(found[KEY], "部门规章")
        self.assertEqual(found[OTHER], "")

    def test_clear_source_removes_mapping(self) -> None:
        self.studio.set_file_source(KEY, "法律")
        result = self.studio.set_file_source(KEY, "")
        self.assertEqual(result["label"], sources.UNSET_LABEL)
        self.assertEqual(self._sources_of_file(), {""})
        self.assertEqual(sources.load(self.data_root), {})

    def test_invalid_category_is_rejected_without_writing(self) -> None:
        with self.assertRaises(sources.SourceError):
            self.studio.set_file_source(KEY, "a/b")
        self.assertEqual(self._sources_of_file(), {""})
        self.assertEqual(sources.load(self.data_root), {})

    def test_custom_category_is_added_and_exposed(self) -> None:
        result = self.studio.set_file_source(KEY, "行政法规")
        self.assertEqual(result["label"], "行政法规")
        self.assertIn("行政法规", result["categories"])
        self.assertEqual(self._sources_of_file(), {"行政法规"})
        self.assertEqual(sources.load(self.data_root), {self.entry.law_name: "行政法规"})
        payload = self.studio.files_payload()
        self.assertIn("行政法规", payload["source_categories"])

    def test_unknown_key_is_rejected(self) -> None:
        with self.assertRaises(ManifestError):
            self.studio.set_file_source("data/不存在", "法律")

    def test_draft_is_synced_and_renamed_to_new_baseline(self) -> None:
        self.studio.doc_payload(KEY)
        self.studio.save_draft(KEY)
        old_sha = drafts.sha12(self.chunks_path)
        old_draft = drafts.draft_file(self.data_root / "draft", self.stem, old_sha)
        self.assertTrue(old_draft.is_file())

        self.studio.set_file_source(KEY, "规范性文件")
        new_sha = drafts.sha12(self.chunks_path)
        self.assertNotEqual(new_sha, old_sha)
        files = drafts.draft_files(self.data_root / "draft", self.stem)
        self.assertEqual(len(files), 1)
        self.assertEqual(drafts.parse_draft_name(files[0].name), (self.stem, new_sha))
        draft_rows = drafts.read_chunks(files[0])
        self.assertEqual({str(row.get("source") or "") for row in draft_rows}, {"规范性文件"})
        # 草稿仍可正常提交（基线一致）
        doc = self.studio.commit_draft(KEY)
        self.assertIn("committed", doc)

    # -- 设置 source 不应改变审核状态 -------------------------------------
    def _review_state(self) -> str:
        return self.studio._review_for(KEY)["state"]

    def test_set_source_keeps_reviewed_state(self) -> None:
        review.mark_reviewed(self.data_root, self.stem, drafts.sha12(self.chunks_path))
        self.assertEqual(self._review_state(), review.STATE_REVIEWED)

        self.studio.set_file_source(KEY, "地方标准")

        self.assertEqual(self._review_state(), review.STATE_REVIEWED)
        # 指纹要跟着新 chunks 更新，否则下次打开会显示未审核
        fresh = review.resolve(self.data_root, self.stem, drafts.sha12(self.chunks_path))
        self.assertEqual(fresh["state"], review.STATE_REVIEWED)

    def test_set_source_keeps_in_review_state_and_prior(self) -> None:
        review.mark_reviewed(self.data_root, self.stem, drafts.sha12(self.chunks_path))
        review.mark_in_review(self.data_root, self.stem, drafts.sha12(self.chunks_path))
        self.assertEqual(self._review_state(), review.STATE_IN_REVIEW)

        self.studio.set_file_source(KEY, "部门规章")

        self.assertEqual(self._review_state(), review.STATE_IN_REVIEW)
        raw = review.load(self.data_root, self.stem)
        self.assertEqual(raw["prior"], review.STATE_REVIEWED)
        # 放弃编辑后仍可回到「已审核」
        self.studio.discard_draft(KEY)
        self.assertEqual(self._review_state(), review.STATE_REVIEWED)

    def test_set_source_on_unreviewed_file_stays_unreviewed(self) -> None:
        self.assertEqual(self._review_state(), review.STATE_NONE)

        self.studio.set_file_source(KEY, "法律")

        self.assertEqual(self._review_state(), review.STATE_NONE)

    # -- 批量设置来源 -----------------------------------------------------
    def test_batch_set_source_updates_all_files(self) -> None:
        review.mark_reviewed(self.data_root, self.stem, drafts.sha12(self.chunks_path))

        result = self.studio.set_files_source([KEY, OTHER], "部门规章")

        self.assertTrue(result["ok"])
        self.assertEqual(result["n_files"], 2)
        self.assertEqual(result["label"], "部门规章")
        self.assertEqual(
            sources.load(self.data_root),
            {self.entry.law_name: "部门规章", "GB 0001-2020 测试标准": "部门规章"},
        )
        self.assertEqual(self._sources_of_file(), {"部门规章"})
        other = self.studio.bridge.manifest.get(OTHER)
        other_rows = [
            json.loads(line)
            for line in Path(other.jsonl).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual({str(row.get("source") or "") for row in other_rows}, {"部门规章"})
        # 审核状态延续：已审核的仍是已审核，未审核的仍是未审核
        self.assertEqual(self.studio._review_for(KEY)["state"], review.STATE_REVIEWED)
        self.assertEqual(self.studio._review_for(OTHER)["state"], review.STATE_NONE)

    def test_batch_set_source_rejects_unknown_key_before_writing(self) -> None:
        with self.assertRaises(ManifestError):
            self.studio.set_files_source([KEY, "data/不存在"], "法律")
        self.assertEqual(sources.load(self.data_root), {})
        self.assertEqual(self._sources_of_file(), {""})


if __name__ == "__main__":
    unittest.main()
