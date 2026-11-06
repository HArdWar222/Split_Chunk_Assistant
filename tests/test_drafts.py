"""P3 草稿层单元测试：保存 / 恢复 / 放弃 / 提交 / 基线过期 / 导出。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from chunk_studio.api import App
from chunk_studio.config import Config
from chunk_studio.studio import Studio

from tests.fixtures import make_data_dir, remove, temp_dir


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class DraftTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.base = temp_dir()
        self.data_root = make_data_dir(self.base)
        self.cfg = Config(data_root=str(self.data_root))
        self.studio = Studio(cfg=self.cfg)
        self.client = App(self.studio)
        self.key = "data/测试包法规"
        self.chunks_path = self.data_root / "chunks" / "测试包法规" / "测试包法规.chunks.jsonl"
        self.draft_dir = self.data_root / "draft"

    def tearDown(self) -> None:
        remove(self.base)

    def body(self, result):
        status, _, raw = result
        return status, json.loads(raw.decode("utf-8"))

    def post(self, path: str, payload: dict):
        return self.body(self.client.handle("POST", path, json.dumps(payload).encode()))

    def edit_first(self) -> None:
        status, _ = self.post(
            "/api/op",
            {"key": self.key, "op": {"op": "patch", "id": "测试包法规-0001", "chunk": "第一条 改。"}},
        )
        self.assertEqual(status, 200)


class SaveDraftTest(DraftTestBase):
    def test_save_writes_draft_and_reports_status(self):
        self.edit_first()
        status, doc = self.post("/api/draft/save", {"key": self.key})
        self.assertEqual(status, 200)
        draft = doc["draft"]
        self.assertTrue(draft["exists"])
        files = sorted(self.draft_dir.glob("测试包法规.*.chunks.jsonl"))
        self.assertEqual(len(files), 1)
        self.assertIn(draft["baseline12"], files[0].name)
        rows = _read_jsonl(files[0])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["chunk"], "第一条 改。")
        self.assertFalse(doc["dirty"])

    def test_delete_other_baseline_drafts(self):
        stale = self.draft_dir / "测试包法规.aaaaaaaaaaaa.chunks.jsonl"
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("", encoding="utf-8")
        self.post("/api/draft/save", {"key": self.key})
        self.assertFalse(stale.exists())

    def test_draft_survives_new_studio(self):
        self.edit_first()
        self.post("/api/draft/save", {"key": self.key})
        fresh = Studio(cfg=self.cfg)
        doc = fresh.doc_payload(self.key)
        self.assertTrue(doc["draft"]["exists"])
        self.assertEqual(doc["chunks"][0]["chunk"], "第一条 改。")
        self.assertFalse(doc["dirty"])

    def test_discard_restores_baseline(self):
        self.edit_first()
        self.post("/api/draft/save", {"key": self.key})
        status, doc = self.post("/api/draft/discard", {"key": self.key})
        self.assertEqual(status, 200)
        self.assertFalse(doc["draft"]["exists"])
        self.assertEqual(doc["chunks"][0]["chunk"], "第一条 为测试本地包。")
        self.assertEqual(list(self.draft_dir.glob("测试包法规.*.chunks.jsonl")), [])
        self.assertFalse(doc["dirty"])


class CommitDraftTest(DraftTestBase):
    def test_commit_overwrites_chunks_and_backs_up(self):
        self.edit_first()
        self.post("/api/draft/save", {"key": self.key})
        status, doc = self.post("/api/draft/commit", {"key": self.key})
        self.assertEqual(status, 200)
        self.assertIn("message", doc)
        rows = _read_jsonl(self.chunks_path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["chunk"], "第一条 改。")
        # 契约字段：不能有 span/align/manual
        self.assertNotIn("span", rows[0])
        self.assertNotIn("manual", rows[0])
        self.assertTrue(rows[0]["token_len"])
        # 草稿删除、备份保留
        self.assertEqual(list(self.draft_dir.glob("测试包法规.*.chunks.jsonl")), [])
        backups = list((self.data_root / "backup").glob("测试包法规.*.jsonl"))
        self.assertEqual(len(backups), 1)
        # 提交后基线刷新：草稿不存在，undo 栈清空
        status, fresh_doc = self.body(self.client.handle("GET", "/api/doc?key=" + self.key))
        self.assertEqual(status, 200)
        self.assertFalse(fresh_doc["draft"]["exists"])
        self.assertFalse(fresh_doc["dirty"])

    def test_commit_writes_view_spans_to_extras(self):
        self.edit_first()
        status, before = self.post("/api/draft/save", {"key": self.key})
        self.assertEqual(status, 200)
        spans_before = {c["id"]: c.get("span") for c in before["chunks"]}
        status, doc = self.post("/api/draft/commit", {"key": self.key})
        self.assertEqual(status, 200)
        spans_after = {c["id"]: c.get("span") for c in doc["chunks"]}
        self.assertEqual(spans_after, spans_before)
        from chunk_studio import drafts as drafts_mod

        extras_file = drafts_mod.extras_path(self.chunks_path, "测试包法规")
        rows = _read_jsonl(extras_file)
        pinned = {r["id"]: (r.get("extras") if isinstance(r.get("extras"), dict) else r).get("span") for r in rows}
        first_id = sorted(spans_before)[0]
        a, b = spans_before[first_id]
        self.assertEqual(pinned.get(first_id), [a - 1, b])  # 侧车里是 0-based 半开区间
        # 正式 chunks 仍只写契约字段
        self.assertNotIn("span", _read_jsonl(self.chunks_path)[0])

    def test_commit_without_draft_fails(self):
        status, doc = self.post("/api/draft/commit", {"key": self.key})
        self.assertEqual(status, 400)
        self.assertIn("没有可提交的草稿", doc["error"])

    def test_stale_draft_is_ignored_and_commit_rejected(self):
        self.post("/api/draft/save", {"key": self.key})
        # 外部改写正式 chunks → 基线变化
        lines = self.chunks_path.read_text(encoding="utf-8").splitlines()
        extra = {
            "id": "测试包法规-9999",
            "chunk": "外部追加。",
            "law_name": "测试包法规",
            "law_num": "外部",
            "chunk_kind": "paragraph",
        }
        self.chunks_path.write_text("\n".join(lines + [json.dumps(extra, ensure_ascii=False)]) + "\n", encoding="utf-8")
        fresh = Studio(cfg=self.cfg)
        doc = fresh.doc_payload(self.key)
        self.assertTrue(doc["draft"]["stale"])
        self.assertFalse(doc["draft"]["exists"])
        # 草稿内容不生效
        self.assertEqual(len(doc["chunks"]), 3)
        status, err = self.body(
            App(fresh).handle("POST", "/api/draft/commit", json.dumps({"key": self.key}).encode())
        )
        self.assertEqual(status, 400)


class ExportTest(DraftTestBase):
    def test_export_returns_jsonl(self):
        self.edit_first()
        status, data = self.body(self.client.handle("GET", "/api/export?key=" + self.key))
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertTrue(data["filename"].endswith(".chunks.jsonl"))
        rows = [json.loads(line) for line in data["content"].splitlines() if line.strip()]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["chunk"], "第一条 改。")
        self.assertNotIn("span", rows[0])


class UndoAfterSaveTest(DraftTestBase):
    """回归：保存草稿后撤销，不能从「已应用全部 ops」的草稿起点重放。"""

    def test_undo_after_save_removes_added_chunk(self):
        status, doc = self.post(
            "/api/op",
            {
                "key": self.key,
                "op": {
                    "op": "add",
                    "text": "补充的一条。",
                    "chunk_kind": "paragraph",
                    "after_id": "测试包法规-0002",
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(doc["chunks"]), 3)
        self.assertEqual(self.post("/api/draft/save", {"key": self.key})[0], 200)
        status, doc = self.post("/api/undo", {"key": self.key})
        self.assertEqual(status, 200)
        self.assertEqual(len(doc["chunks"]), 2)
        self.assertNotIn("补充的一条。", [chunk["chunk"] for chunk in doc["chunks"]])
        self.assertEqual(self.post("/api/redo", {"key": self.key})[0], 200)
        self.assertEqual(len(self.post("/api/undo", {"key": self.key})[1]["chunks"]), 2)

    def test_undo_of_drop_after_save_restores_chunk(self):
        status, _ = self.post(
            "/api/op",
            {"key": self.key, "op": {"op": "drop", "id": "测试包法规-0002"}},
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.post("/api/draft/save", {"key": self.key})[0], 200)
        status, doc = self.post("/api/undo", {"key": self.key})
        self.assertEqual(status, 200)
        self.assertEqual(len(doc["chunks"]), 2)
        self.assertEqual(doc["chunks"][1]["id"], "测试包法规-0002")


if __name__ == "__main__":
    unittest.main()
