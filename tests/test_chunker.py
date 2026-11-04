"""切块引擎接入（chunker.py）与 WatchService 的单元测试。

引擎本体来自 vendor/splitchunk（chunking_v2 0.1.4），测试只依赖其存在；
需要真实跑引擎的用例会在没有 vendor 时跳过。
"""

from __future__ import annotations

import hashlib
import json
import time
import unittest
from pathlib import Path

from chunk_studio import chunker
from chunk_studio.config import Config
from chunk_studio.manifest import DirManifest
from tests.fixtures import workspace_tmp


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_data_root(base: Path) -> Path:
    root = base / "data"
    _write(root / "source" / "甲法.md", "# 甲法\n\n第一条 甲。\n\n第二条 乙。\n")
    _write(root / "source" / "乙法.md", "# 乙法\n\n第一条 丙。\n")
    # 甲法已有切片（不算待切）；乙法只有源文件（待切）
    _write(
        root / "chunks" / "甲法.chunks.jsonl",
        json.dumps({"id": "甲法-0001", "chunk": "第一条 甲。", "law_name": "甲法"}, ensure_ascii=False) + "\n",
    )
    return root


class EngineLocateTest(unittest.TestCase):
    def test_resolve_engine_from_vendor(self):
        root = chunker.resolve_engine(None)
        self.assertTrue((Path(root) / "chunking_v2" / "cli.py").is_file())
        self.assertEqual(chunker.engine_version(root), "0.1.4")

    def test_engine_info_reports_version(self):
        info = chunker.engine_info(Config())
        self.assertTrue(info["ok"])
        self.assertTrue(info["version"])
        self.assertTrue(info["root"])

    def test_explicit_chunker_root_must_contain_engine(self):
        with workspace_tmp() as base:
            bogus = base / "nope"
            bogus.mkdir()
            with self.assertRaises(chunker.ChunkerError):
                chunker.resolve_engine(bogus)


class ScanPendingTest(unittest.TestCase):
    def test_new_file_is_pending(self):
        with workspace_tmp() as base:
            root = _make_data_root(base)
            cfg = Config(data_root=str(root))
            items = chunker.scan_pending(cfg, now=time.time() + 10)
            subjects = {it.subject: it.reason for it in items}
            self.assertEqual(subjects, {"data/乙法": "new"})

    def test_just_touched_file_is_skipped(self):
        with workspace_tmp() as base:
            root = _make_data_root(base)
            cfg = Config(data_root=str(root))
            items = chunker.scan_pending(cfg)
            self.assertEqual([it.subject for it in items], [])

    def test_outdated_source_is_pending(self):
        with workspace_tmp() as base:
            root = _make_data_root(base)
            cfg = Config(data_root=str(root))
            # 甲法已有 report，但 source_sha256 对不上 → outdated
            (root / "chunks" / "甲法.report.json").write_text(
                json.dumps({"engine_version": "0.1.4", "source_sha256": "deadbeef"}),
                encoding="utf-8",
            )
            items = chunker.scan_pending(cfg, now=time.time() + 10)
            reasons = {it.subject: it.reason for it in items}
            self.assertEqual(reasons.get("data/甲法"), "outdated")


class RunEngineFileTest(unittest.TestCase):
    def test_run_engine_writes_bundle(self):
        with workspace_tmp() as base:
            root = _make_data_root(base)
            cfg = Config(data_root=str(root))
            engine_root = chunker.resolve_engine(None)
            item = chunker.PendingFile(
                subject="data/乙法",
                source=root / "source" / "乙法.md",
                rel_dir="",
                reason="new",
                detail="",
            )
            result = chunker.run_engine_file(cfg, engine_root, item)
            self.assertTrue(result["ok"], result.get("message"))
            self.assertGreaterEqual(result["n_chunks"], 1)
            self.assertTrue((root / "chunks" / "乙法.chunks.jsonl").is_file())
            self.assertTrue((root / "chunks" / "乙法.report.json").is_file())


class _FakeStudio:
    """只实现 WatchService 需要的接口，避免真跑引擎。"""

    def __init__(self, root: Path):
        self.cfg = Config(data_root=str(root))
        self.reloads = 0

    def reload(self):
        self.reloads += 1


class WatchServiceTest(unittest.TestCase):
    def test_trigger_runs_job_and_refreshes_pending(self):
        with workspace_tmp() as base:
            root = _make_data_root(base)
            studio = _FakeStudio(root)
            watch = chunker.WatchService(studio)
            calls = []

            def fake_run(cfg, engine_root, item):
                calls.append(item.subject)
                # 模拟引擎产物：写 chunks + report，让 reload 后不再 pending
                out = Path(cfg.data_root) / "chunks"
                out.mkdir(parents=True, exist_ok=True)
                name = Path(item.source).stem
                (out / (name + ".chunks.jsonl")).write_text("", encoding="utf-8")
                (out / (name + ".report.json")).write_text(
                    json.dumps({"engine_version": "0.1.4", "source_sha256": "x"}),
                    encoding="utf-8",
                )
                return {
                    "ok": True,
                    "gate_ok": True,
                    "n_chunks": 1,
                    "kinds": {"clause": 1},
                    "failures": [],
                    "message": "",
                }

            original = chunker.run_engine_file
            chunker.run_engine_file = fake_run
            try:
                res = watch.trigger(now=time.time() + 10)
                self.assertTrue(res["ok"])
                self.assertTrue(res["started"])
                watch.wait_idle(timeout=10)
            finally:
                chunker.run_engine_file = original

            self.assertEqual(calls, ["data/乙法"])
            self.assertEqual(studio.reloads, 1)
            last = watch.snapshot()["last"]
            self.assertEqual(last["ok"], 1)
            self.assertEqual(watch.snapshot()["pending"], 0)

    def test_trigger_without_pending_reports_message(self):
        with workspace_tmp() as base:
            root = _make_data_root(base)
            # 把「乙法」也补上切片与 report（sha 必须与源文件一致），使没有任何待切文件
            (root / "chunks" / "乙法.chunks.jsonl").write_text("", encoding="utf-8")
            sha = hashlib.sha256((root / "source" / "乙法.md").read_bytes()).hexdigest()
            (root / "chunks" / "乙法.report.json").write_text(
                json.dumps({"engine_version": "0.1.4", "source_sha256": sha}),
                encoding="utf-8",
            )
            studio = _FakeStudio(root)
            watch = chunker.WatchService(studio)
            res = watch.trigger(now=time.time() + 10)
            self.assertFalse(res["started"])
            self.assertIn("没有待切", res["message"])
            self.assertEqual(studio.reloads, 0)
