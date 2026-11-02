from __future__ import annotations

import json
import unittest
from pathlib import Path

from chunk_studio.api import App
from chunk_studio.config import Config
from chunk_studio.studio import Studio
from tests.fixtures import make_chunks_dir, remove, temp_dir


class AppTest(unittest.TestCase):
    def setUp(self):
        self._tmp = temp_dir()
        self.base = self._tmp
        self.chunks_dir = make_chunks_dir(self.base)
        self.config_path = self.base / "config.json"
        cfg = Config(
            fac_root=str(self.base),
            chunks_dir=str(self.chunks_dir),
            port=8790,
        )
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
        self.assertEqual(data["phase"], "P0")

    # -- /api/files -------------------------------------------------
    def test_files(self):
        status, data = self.body(self.get("/api/files"))
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["totals"]["n_files"], 2)
        self.assertEqual(data["totals"]["n_chunks"], 3)
        self.assertEqual([g["name"] for g in data["groups"]], ["national_laws", "GB_laws"])
        self.assertIn("config", data)
        self.assertTrue(data["config"]["manifest_ok"])

    def test_files_unavailable(self):
        cfg = Config(
            fac_root=str(self.base),
            chunks_dir=str(self.base / "missing"),
            port=8790,
        )
        studio = Studio(cfg=cfg, config_path=self.config_path)
        app = App(studio)
        status, _, raw = app.handle("GET", "/api/files")
        self.assertEqual(status, 503)
        data = json.loads(raw.decode("utf-8"))
        self.assertFalse(data["ok"])
        self.assertIn("error", data)

    # -- /api/config ------------------------------------------------
    def test_get_config(self):
        status, data = self.body(self.get("/api/config"))
        self.assertEqual(status, 200)
        self.assertEqual(data["port"], 8790)
        self.assertEqual(data["chunks_dir"], str(self.chunks_dir))
        self.assertTrue(data["manifest_ok"])
        self.assertEqual(data["errors"], [])
        self.assertIn("national_laws", data["default_source_roots"])

    def test_post_config_ok(self):
        status, data = self.body(
            self.post(
                "/api/config",
                {"source_roots": {"national_laws": str(self.base / "national_laws")}},
            )
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["message"], "配置已保存")
        # 落盘生效
        saved = Config.load(self.config_path)
        self.assertEqual(saved.source_roots["national_laws"], str(self.base / "national_laws"))
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

    # -- 其它路由 ---------------------------------------------------
    def test_doc_is_p1_only(self):
        status, data = self.body(self.get("/api/doc?key=national_laws/%E6%B5%8B%E8%AF%95%E6%B3%95"))
        self.assertEqual(status, 501)
        self.assertFalse(data["ok"])

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


if __name__ == "__main__":
    unittest.main()
