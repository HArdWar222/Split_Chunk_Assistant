from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path
from urllib.parse import quote

from chunk_studio.api import App
from chunk_studio.config import Config
from chunk_studio.studio import Studio
from tests.fixtures import make_two_file_data_dir, remove, temp_dir


class AppTest(unittest.TestCase):
    def setUp(self):
        self._tmp = temp_dir()
        self.base = self._tmp
        self.data_root = make_two_file_data_dir(self.base)
        self.config_path = self.base / "config.json"
        cfg = Config(data_root=str(self.data_root), port=8790)
        self.studio = Studio(cfg=cfg, config_path=self.config_path)
        self.app = App(self.studio)

    def tearDown(self):
        remove(self._tmp)

    # -- 工具 -------------------------------------------------------
    def get(self, path: str):
        return self.app.handle("GET", path)

    def post(self, path: str, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return self.app.handle("POST", path, body)

    def body(self, triple):
        status, ctype, raw = triple
        self.assertIn("json", ctype)
        return status, json.loads(raw.decode("utf-8"))

    # -- /api/health ------------------------------------------------
    def test_health(self):
        status, data = self.body(self.get("/api/health"))
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["app"], "SplitChunkAssistant")
        self.assertEqual(data["phase"], "P2")

    # -- /api/files -------------------------------------------------
    def test_files(self):
        status, data = self.body(self.get("/api/files"))
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["totals"]["n_files"], 2)
        self.assertEqual(data["totals"]["n_chunks"], 3)
        self.assertEqual([g["name"] for g in data["groups"]], ["data"])
        self.assertIn("config", data)
        self.assertTrue(data["config"]["data_ok"])

    def test_files_unavailable(self):
        cfg = Config(data_root=str(self.base / "missing"), port=8790)
        studio = Studio(cfg=cfg, config_path=self.config_path)
        app = App(studio)
        status, _, raw = app.handle("GET", "/api/files")
        self.assertEqual(status, 503)
        data = json.loads(raw.decode("utf-8"))
        self.assertFalse(data["ok"])
        self.assertIn("error", data)

    def test_reload_rescans_local_dirs(self):
        """POST /api/reload 重新扫描本地两目录，新放的切块包立刻可见。"""
        from tests.fixtures import make_data_dir

        data_root = make_data_dir(self.base)
        cfg = Config(
            fac_root=str(self.base),
            chunks_dir=str(self.base / "missing"),
            data_root=str(data_root),
            port=8790,
        )
        app = App(Studio(cfg=cfg, config_path=self.config_path))
        status, _, raw = app.handle("GET", "/api/files")
        data = json.loads(raw.decode("utf-8"))
        self.assertEqual(data["mode"], "dirs")
        self.assertEqual(data["totals"]["n_files"], 1)

        name = "补充法规"
        (data_root / "source" / (name + ".md")).write_text(
            "# 补充法规\n\n第一条 补充。\n", encoding="utf-8"
        )
        chunk = {
            "id": name + "-0001",
            "chunk": "第一条 补充。",
            "law_name": name,
            "law_num": "第一条",
            "chunk_kind": "clause",
            "part_index": 1,
            "part_total": 1,
        }
        doc_dir = data_root / "chunks" / name
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / (name + ".chunks.jsonl")).write_text(
            json.dumps(chunk, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        status, _, raw = app.handle("POST", "/api/reload", b"{}")
        body = json.loads(raw.decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["message"], "已重新扫描数据目录")
        self.assertEqual(body["files"]["totals"]["n_files"], 2)
        keys = [f["key"] for g in body["files"]["groups"] for f in g["files"]]
        self.assertIn("data/补充法规", keys)

    # -- /api/config ------------------------------------------------
    def test_get_config(self):
        status, data = self.body(self.get("/api/config"))
        self.assertEqual(status, 200)
        self.assertEqual(data["port"], 8790)
        self.assertEqual(data["data_root"], str(self.data_root))
        self.assertTrue(data["data_ok"])
        self.assertEqual(data["errors"], [])
        self.assertTrue(data["data_source_dir"].endswith("source"))

    def test_post_config_ok(self):
        status, data = self.body(
            self.post(
                "/api/config",
                {"port": 8791},
            )
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["message"], "配置已保存")
        # 落盘生效
        saved = Config.load(self.config_path)
        self.assertEqual(saved.port, 8791)
        # 产物依然可用
        status, files = self.body(self.get("/api/files"))
        self.assertEqual(status, 200)

    def test_post_config_bad_port(self):
        status, data = self.body(self.post("/api/config", {"port": 70000}))
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])
        self.assertIn("端口", data["error"])

    def test_post_config_bad_json(self):
        status, _, raw = self.app.handle("POST", "/api/config", b"{not json")
        self.assertEqual(status, 400)
        self.assertFalse(json.loads(raw.decode("utf-8"))["ok"])

    def test_post_config_not_object(self):
        status, _, raw = self.app.handle("POST", "/api/config", b"[1, 2]")
        self.assertEqual(status, 400)

    # -- /api/doc ---------------------------------------------------
    def doc(self, key: str):
        return self.body(self.get("/api/doc?key=" + quote(key, safe="")))

    def test_doc_ok(self):
        status, data = self.doc("data/测试法")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["key"], "data/测试法")
        self.assertEqual(data["law_name"], "测试法")
        self.assertEqual(len(data["lines"]), 5)
        self.assertEqual(len(data["chunks"]), 2)
        self.assertEqual(data["stats"]["n_chunks"], 2)
        self.assertEqual(data["stats"]["n_aligned"], 2)
        self.assertEqual(data["stats"]["n_miss"], 0)
        self.assertEqual(data["stats"]["align_rate"], 1.0)
        first = data["chunks"][0]
        # 前端用 1-based 闭区间；fixtures 里第一条正文在第 3 行
        self.assertEqual(first["span"], [3, 3])
        self.assertEqual(first["align"], "exact")
        self.assertIn("extras", first)
        self.assertTrue(data["source_chars"] > 0)
        self.assertFalse(data["edited"])

    def test_doc_missing_key(self):
        status, data = self.body(self.get("/api/doc"))
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])

    def test_doc_unknown_key(self):
        status, data = self.doc("data/不存在的法规")
        self.assertEqual(status, 404)
        self.assertFalse(data["ok"])

    def test_doc_source_missing(self):
        # 源 md 被删 → 404「源文件缺失」
        (self.data_root / "source" / "测试法.md").unlink()
        status, data = self.doc("data/测试法")
        self.assertEqual(status, 404)
        self.assertFalse(data["ok"])
        self.assertIn("源文件", data["error"])

    def test_doc_cached(self):
        self.doc("data/测试法")
        status, data = self.doc("data/测试法")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["chunks"]), 2)

    # -- P2 编辑接口 -------------------------------------------------
    KEY = "data/测试法"

    def apply_op(self, op):
        return self.body(self.post("/api/op", {"key": self.KEY, "op": op}))

    def test_op_split_then_doc(self):
        status, data = self.apply_op({"op": "split", "id": "测试法-0001", "at_char": 4})
        self.assertEqual(status, 200)
        self.assertTrue(data["dirty"])
        self.assertEqual(len(data["chunks"]), 3)
        self.assertEqual(data["diff"]["added"], 1)
        self.assertGreaterEqual(data["diff"]["modified"], 1)
        self.assertEqual(data["edits"]["n_ops"], 1)
        self.assertFalse(data["edited"])
        # 再取文档仍带编辑结果
        status, again = self.doc(self.KEY)
        self.assertEqual(status, 200)
        self.assertEqual(len(again["chunks"]), 3)
        self.assertTrue(again["dirty"])

    def test_op_unknown_chunk(self):
        status, data = self.apply_op({"op": "drop", "id": "不存在-0001"})
        self.assertEqual(status, 404)
        self.assertFalse(data["ok"])

    def test_op_bad_command(self):
        status, data = self.apply_op({"op": "nope"})
        self.assertEqual(status, 400)
        self.assertIn("未知的编辑命令", data["error"])

    def test_op_missing_key(self):
        status, data = self.body(self.post("/api/op", {"op": {"op": "drop", "id": "测试法-0001"}}))
        self.assertEqual(status, 400)

    def test_op_patch(self):
        status, data = self.apply_op({"op": "patch", "id": "测试法-0002", "chunk": "第二条 改过了。"})
        self.assertEqual(status, 200)
        self.assertEqual(data["diff"]["modified"], 1)
        patched = [c for c in data["chunks"] if c["id"] == "测试法-0002"][0]
        self.assertEqual(patched["chunk"], "第二条 改过了。")
        self.assertTrue(patched["manual"])
        self.assertEqual(patched["align"], "manual")

    def test_op_add_with_span(self):
        # 原文选区新增：带行区间
        status, data = self.apply_op({"op": "add", "text": "选中新增块", "span": [3, 4]})
        self.assertEqual(status, 200)
        self.assertEqual(len(data["chunks"]), 3)
        new = data["chunks"][-1]
        self.assertEqual(new["span"], [3, 4])
        self.assertTrue(new["manual"])
        self.assertEqual(data["diff"]["added"], 1)

    def test_op_add_bad_span(self):
        status, data = self.apply_op({"op": "add", "text": "x", "span": [4, 2]})
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])

    def test_op_add_before_first_renumbers(self):
        # 插到原文最前：新块拿 -0001，原 0001/0002 顺延成 0002/0003，diff 只报新增
        status, data = self.apply_op(
            {"op": "add", "text": "标题位置新增", "before_id": "测试法-0001", "span": [1, 2]}
        )
        self.assertEqual(status, 200)
        self.assertEqual([c["id"] for c in data["chunks"]], ["测试法-0001", "测试法-0002", "测试法-0003"])
        self.assertEqual(data["diff"]["added"], 1)
        self.assertEqual(data["diff"]["removed"], 0)
        self.assertEqual(data["diff"]["modified"], 0)

    def test_op_add_and_drop(self):
        status, data = self.apply_op({"op": "add", "text": "新增块正文", "chunk_kind": "paragraph"})
        self.assertEqual(status, 200)
        self.assertEqual(len(data["chunks"]), 3)
        new = data["chunks"][-1]
        self.assertEqual(new["id"], "测试法-0003")
        self.assertEqual(new["law_num"], "第二条")  # 追加在末尾 → 沿用最近的「第X条」
        status, data = self.apply_op({"op": "drop", "id": "测试法-0001"})
        self.assertEqual(status, 200)
        self.assertEqual(len(data["chunks"]), 2)
        self.assertEqual(data["diff"]["removed"], 1)

    def test_op_merge_many(self):
        # 选区集体合并：两块并成一块，保留第一块 id
        status, data = self.apply_op({"op": "merge_many", "ids": ["测试法-0001", "测试法-0002"]})
        self.assertEqual(status, 200)
        self.assertEqual(len(data["chunks"]), 1)
        self.assertEqual(data["chunks"][0]["id"], "测试法-0001")
        self.assertEqual(data["chunks"][0]["chunk"], "第一条 为了测试。\n第二条 测试结束。")
        self.assertEqual(data["diff"]["removed"], 1)
        self.assertEqual(data["diff"]["modified"], 1)
        self.assertTrue(data["dirty"])
        # 少于 2 个 id → 400
        status, data = self.apply_op({"op": "merge_many", "ids": ["测试法-0001"]})
        self.assertEqual(status, 400)

    def test_op_add_infers_law_num(self):
        # 不填 law_num 时按前一块倒推：插在「第二条」后面 → 第二条
        status, data = self.apply_op({"op": "add", "text": "续写", "after_id": "测试法-0002"})
        self.assertEqual(status, 200)
        self.assertEqual(data["chunks"][2]["law_num"], "第二条")

    def test_undo_redo(self):
        self.apply_op({"op": "split", "id": "测试法-0001", "at_char": 4})
        status, data = self.body(self.post("/api/undo", {"key": self.KEY}))
        self.assertEqual(status, 200)
        self.assertEqual(len(data["chunks"]), 2)
        self.assertFalse(data["dirty"])
        self.assertEqual(data["edits"]["redo"], 1)
        status, data = self.body(self.post("/api/redo", {"key": self.KEY}))
        self.assertEqual(status, 200)
        self.assertEqual(len(data["chunks"]), 3)
        self.assertTrue(data["dirty"])

    def test_undo_empty(self):
        status, data = self.body(self.post("/api/undo", {"key": self.KEY}))
        self.assertEqual(status, 400)
        self.assertIn("可撤销", data["error"])

    def test_reset(self):
        self.apply_op({"op": "split", "id": "测试法-0001", "at_char": 4})
        status, data = self.body(self.post("/api/reset", {"key": self.KEY}))
        self.assertEqual(status, 200)
        self.assertEqual(len(data["chunks"]), 2)
        self.assertFalse(data["dirty"])
        self.assertEqual(data["edits"]["n_ops"], 0)

    # -- /api/watch、/api/chunk -------------------------------------
    def test_watch_snapshot(self):
        status, data = self.body(self.get("/api/watch"))
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertTrue(data["engine"]["ok"])
        self.assertTrue(data["engine"]["version"])
        self.assertEqual(data["pending"], 0)
        self.assertTrue(data["enabled"])

    def test_watch_toggle(self):
        status, data = self.body(self.post("/api/watch", {"enabled": False}))
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertFalse(data["watch"]["enabled"])
        saved = Config.load(self.config_path)
        self.assertFalse(saved.auto_chunk)

    def test_chunk_without_pending(self):
        status, data = self.body(self.post("/api/chunk", {}))
        self.assertEqual(status, 200)
        self.assertFalse(data["started"])
        self.assertIn("没有待切", data["message"])

    # -- /api/tables ------------------------------------------------
    def tables(self, key: str):
        return self.body(self.get("/api/tables?key=" + quote(key, safe="")))

    def test_tables_empty(self):
        status, data = self.tables("data/测试法")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["n_tables"], 0)
        self.assertEqual(data["tables"], {})

    def test_tables_with_grid(self):
        # 给 测试法-0001 补一个 table_grid（模拟表块），/api/tables 应返回结构化网格
        doc_dir = self.data_root / "chunks" / "测试法"
        jsonl = doc_dir / "测试法.chunks.jsonl"
        rows = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows[0]["chunk_kind"] = "table"
        jsonl.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        extras_path = doc_dir / "测试法.extras.jsonl"
        extras = [json.loads(line) for line in extras_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        entry = next((item for item in extras if item["id"] == rows[0]["id"]), None)
        if entry is None:
            entry = {"id": rows[0]["id"]}
            extras.append(entry)
        entry.update({
            "table_label": "表1",
            "table_grid": {"headers": ["序号", "名称"], "rows": [["1", "甲"], ["2", "乙"]], "header_rows": 1},
        })
        extras_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in extras), encoding="utf-8")
        self.studio.reload()
        status, data = self.tables("data/测试法")
        self.assertEqual(status, 200)
        self.assertEqual(data["n_tables"], 1)
        grid = data["tables"][rows[0]["id"]]
        self.assertEqual(grid["headers"], ["序号", "名称"])
        self.assertEqual(grid["width"], 2)
        self.assertEqual(grid["rows"], [["1", "甲"], ["2", "乙"]])
        self.assertEqual(grid["note_from"], 2)
        self.assertEqual(grid["table_label"], "表1")

    def test_tables_missing_key(self):
        status, _ = self.body(self.get("/api/tables"))
        self.assertEqual(status, 400)

    def test_tables_unknown_key(self):
        status, _ = self.body(self.get("/api/tables?key=" + quote("data/不存在", safe="")))
        self.assertEqual(status, 404)

    def test_index(self):
        status, ctype, raw = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn("SplitChunkAssistant", raw.decode("utf-8"))

    def test_static_js(self):
        status, ctype, raw = self.get("/static/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", ctype)
        self.assertIn(b"loadFiles", raw)

    def test_static_not_allowed(self):
        status, _, _ = self.get("/static/secret.txt")
        self.assertEqual(status, 404)

    def test_unknown_route(self):
        status, _, raw = self.get("/api/nope")
        self.assertEqual(status, 404)

    def test_favicon(self):
        status, _, raw = self.get("/favicon.ico")
        self.assertEqual(status, 204)


class UploadTest(unittest.TestCase):
    """POST /api/upload：批量导入原文件到 data/source。"""

    def setUp(self):
        self._tmp = temp_dir()
        self.base = self._tmp
        self.data_root = make_two_file_data_dir(self.base)
        self.config_path = self.base / "config.json"
        cfg = Config(data_root=str(self.data_root), port=8790)
        self.studio = Studio(cfg=cfg, config_path=self.config_path)
        self.app = App(self.studio)
        self.source_dir = self.data_root / "source"

    def tearDown(self):
        remove(self._tmp)

    def post(self, path: str, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return self.app.handle("POST", path, body)

    def body(self, triple):
        status, ctype, raw = triple
        self.assertIn("json", ctype)
        return status, json.loads(raw.decode("utf-8"))

    def _files(self, *items):
        return {
            "files": [
                {"name": name, "data_b64": base64.b64encode(content.encode("utf-8")).decode("ascii")}
                for name, content in items
            ]
        }

    def test_upload_creates_files_and_reloads(self):
        status, data = self.body(self.post("/api/upload", self._files(("新导入法规.md", "# 新法规\n\n第一条 测试。"))))
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["created"], ["新导入法规.md"])
        self.assertEqual(data["skipped"], [])
        self.assertEqual(data["errors"], [])
        self.assertTrue((self.source_dir / "新导入法规.md").exists())
        self.assertEqual(
            (self.source_dir / "新导入法规.md").read_text(encoding="utf-8"),
            "# 新法规\n\n第一条 测试。",
        )
        # 只有源文件还没有 chunks：它不会出现在文件清单里，等自动切块完成后才会出现
        keys = {f["key"] for g in data["files"]["groups"] for f in g["files"]}
        self.assertEqual(keys, {"data/测试法", "data/GB 0001-2020 测试标准"})

    def test_upload_skips_existing_name(self):
        (self.source_dir / "同名.md").write_text("# 已存在", encoding="utf-8")
        status, data = self.body(self.post("/api/upload", self._files(("同名.md", "# 新内容"))))
        self.assertEqual(status, 200)
        self.assertEqual(data["created"], [])
        self.assertEqual(data["skipped"], ["同名.md"])
        # 同名不覆盖
        self.assertEqual((self.source_dir / "同名.md").read_text(encoding="utf-8"), "# 已存在")

    def test_upload_rejects_path_traversal(self):
        status, data = self.body(self.post("/api/upload", self._files(("../evil.md", "x"))))
        self.assertEqual(status, 200)
        self.assertEqual(data["created"], [])
        self.assertEqual(len(data["errors"]), 1)
        self.assertIn("文件名不合法", data["errors"][0]["error"])
        self.assertFalse((self.base / "evil.md").exists())

    def test_upload_rejects_bad_extension(self):
        status, data = self.body(self.post("/api/upload", self._files(("坏文件.exe", "x"))))
        self.assertEqual(status, 200)
        self.assertEqual(data["created"], [])
        self.assertEqual(len(data["errors"]), 1)
        self.assertIn("只支持", data["errors"][0]["error"])

    def test_upload_rejects_bad_utf8(self):
        payload = {"files": [{"name": "乱码.md", "data_b64": base64.b64encode(b"\xff\xfe\x00").decode("ascii")}]}
        status, data = self.body(self.post("/api/upload", payload))
        self.assertEqual(status, 200)
        self.assertEqual(data["created"], [])
        self.assertIn("UTF-8", data["errors"][0]["error"])


class ReviewApiTest(unittest.TestCase):
    """文件级审核状态：/api/files 与 op / 草稿 / 提交 的流转（D22）。"""

    KEY = "data/测试法"

    def setUp(self):
        self._tmp = temp_dir()
        self.base = self._tmp
        self.data_root = make_two_file_data_dir(self.base)
        cfg = Config(data_root=str(self.data_root), port=8790)
        self.studio = Studio(cfg=cfg, config_path=self.base / "config.json")
        self.app = App(self.studio)

    def tearDown(self):
        remove(self._tmp)

    def get(self, path: str):
        return self.app.handle("GET", path)

    def post(self, path: str, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return self.app.handle("POST", path, body)

    def body(self, triple):
        status, ctype, raw = triple
        self.assertIn("json", ctype)
        return status, json.loads(raw.decode("utf-8"))

    def file_review(self, key: str = KEY) -> dict:
        status, data = self.body(self.get("/api/files"))
        self.assertEqual(status, 200)
        for group in data["groups"]:
            for item in group["files"]:
                if item["key"] == key:
                    return item
        self.fail("文件不在清单里：" + key)

    def status_doc(self):
        path = self.data_root / "status" / "测试法.json"
        if not path.exists():
            return None
        return json.loads(path.read_text("utf-8"))

    def op(self, payload: dict):
        return self.body(self.post("/api/op", {"key": self.KEY, "op": payload}))

    def test_default_none(self):
        item = self.file_review()
        self.assertEqual(item["review_state"], "none")
        self.assertEqual(item["review_label"], "未审核")
        self.assertIsNone(self.status_doc())

    def test_op_marks_in_review(self):
        status, data = self.op({"op": "drop", "id": "测试法-0002"})
        self.assertEqual(status, 200)
        self.assertEqual(data["review"]["state"], "in_review")
        self.assertEqual(data["review"]["label"], "审核中")
        saved = self.status_doc()
        self.assertEqual(saved["state"], "in_review")
        self.assertEqual(self.file_review()["review_state"], "in_review")

    def test_save_and_commit_marks_reviewed(self):
        self.op({"op": "drop", "id": "测试法-0002"})
        status, _ = self.body(self.post("/api/draft/save", {"key": self.KEY}))
        self.assertEqual(status, 200)
        status, data = self.body(self.post("/api/draft/commit", {"key": self.KEY}))
        self.assertEqual(status, 200)
        self.assertEqual(data["review"]["state"], "reviewed")
        saved = self.status_doc()
        self.assertEqual(saved["state"], "reviewed")
        self.assertEqual(self.file_review()["review_state"], "reviewed")

    def test_discard_returns_to_reviewed(self):
        self.op({"op": "drop", "id": "测试法-0002"})
        self.post("/api/draft/save", {"key": self.KEY})
        self.post("/api/draft/commit", {"key": self.KEY})
        self.assertEqual(self.file_review()["review_state"], "reviewed")
        self.op({"op": "drop", "id": "测试法-0001"})
        self.assertEqual(self.file_review()["review_state"], "in_review")
        self.post("/api/draft/discard", {"key": self.KEY})
        self.assertEqual(self.file_review()["review_state"], "reviewed")

    def test_discard_without_prior_returns_none(self):
        self.op({"op": "drop", "id": "测试法-0002"})
        self.assertEqual(self.file_review()["review_state"], "in_review")
        self.post("/api/draft/discard", {"key": self.KEY})
        self.assertEqual(self.file_review()["review_state"], "none")

    def test_external_change_resets_to_none(self):
        self.op({"op": "drop", "id": "测试法-0002"})
        self.post("/api/draft/save", {"key": self.KEY})
        self.post("/api/draft/commit", {"key": self.KEY})
        self.assertEqual(self.file_review()["review_state"], "reviewed")
        chunks = self.data_root / "chunks" / "测试法" / "测试法.chunks.jsonl"
        row = json.loads([line for line in chunks.read_text("utf-8").splitlines() if line.strip()][0])
        row["chunk"] = row["chunk"] + "（外部改动）"
        chunks.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
        self.post("/api/reload", {})
        self.assertEqual(self.file_review()["review_state"], "none")


if __name__ == "__main__":
    unittest.main()
