"""chunk_studio.paths 的单元测试：开发态与 PyInstaller 冻结态的路径解析。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

from chunk_studio import paths
from tests.fixtures import remove, temp_dir

REPO_ROOT = Path(__file__).resolve().parent.parent


class DevModeTest(unittest.TestCase):
    """开发态：资源根、应用根都是仓库根。"""

    def test_resource_and_app_root_are_repo_root(self):
        self.assertFalse(paths.is_frozen())
        self.assertEqual(paths.resource_root(), REPO_ROOT)
        self.assertEqual(paths.app_root(), REPO_ROOT)
        self.assertEqual(paths.default_config_path(), REPO_ROOT / "config.json")
        self.assertEqual(paths.default_data_root(), REPO_ROOT / "data")

    def test_web_and_vendor_exist(self):
        self.assertTrue((paths.web_dir() / "index.html").is_file())
        self.assertTrue((paths.vendor_root() / "chunking_v2" / "cli.py").is_file())
        roots = paths.fallback_engine_roots()
        self.assertEqual(roots[0], paths.vendor_root())
        self.assertEqual(roots[-1], REPO_ROOT.parent / "SPLITCHUNK")


class FrozenModeTest(unittest.TestCase):
    """冻结态：资源根是 sys._MEIPASS，应用根是 exe 所在目录。"""

    def setUp(self):
        base = temp_dir()
        self.addCleanup(remove, base)
        self.res = base / "bundle"
        self.app = base / "app"
        self.res.mkdir()
        self.app.mkdir()
        patches = [
            mock.patch.object(sys, "frozen", True, create=True),
            mock.patch.object(sys, "_MEIPASS", str(self.res), create=True),
            mock.patch.object(sys, "executable", str(self.app / "SplitChunkAssistant.exe")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_roots_follow_meipass_and_executable(self):
        self.assertTrue(paths.is_frozen())
        self.assertEqual(paths.resource_root(), self.res)
        self.assertEqual(paths.app_root(), self.app)
        self.assertEqual(paths.default_config_path(), self.app / "config.json")
        self.assertEqual(paths.default_data_root(), self.app / "data")
        self.assertEqual(paths.web_dir(), self.res / "web")
        self.assertEqual(paths.vendor_root(), self.res / "vendor" / "splitchunk")

    def test_ensure_data_layout_creates_subdirs(self):
        root = paths.ensure_data_layout(self.app / "data")
        for name in paths.DATA_SUBDIRS:
            self.assertTrue((root / name).is_dir(), name)

    def test_ensure_default_config_written_once(self):
        path = paths.ensure_default_config()
        self.assertEqual(path, self.app / "config.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["data_root"], str(self.app / "data"))
        self.assertEqual(data["port"], 8790)
        self.assertFalse(data["auto_part"])

        # 已存在时绝不覆盖（用户在页面里改过的配置要保留）。
        data["port"] = 9999
        path.write_text(json.dumps(data), encoding="utf-8")
        again = paths.ensure_default_config()
        self.assertEqual(again, path)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["port"], 9999)


class DefaultConfigTest(unittest.TestCase):
    def test_ensure_default_config_custom_path(self):
        base = temp_dir()
        self.addCleanup(remove, base)
        target = base / "sub" / "my-config.json"
        path = paths.ensure_default_config(target)
        self.assertEqual(path, target)
        self.assertTrue(target.is_file())
        self.assertEqual(
            json.loads(target.read_text(encoding="utf-8"))["data_root"],
            str(paths.default_data_root()),
        )


if __name__ == "__main__":
    unittest.main()
