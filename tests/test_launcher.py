"""打包入口与引擎命令构造的单元测试（冻结态不依赖真实 PyInstaller）。"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from chunk_studio import launcher
from chunk_studio.chunker import engine_command


class EngineCommandTest(unittest.TestCase):
    def _cmd(self, frozen: bool) -> list[str]:
        return engine_command(
            Path("C:/data/source/某法规.md"),
            Path("C:/data/.tmp_chunk/某法规"),
            Path("C:/data/source"),
            "auto",
            frozen=frozen,
        )

    def test_development_uses_python_m(self):
        cmd = self._cmd(frozen=False)
        self.assertEqual(cmd[0], sys.executable)
        self.assertEqual(cmd[1:3], ["-m", "chunking_v2.cli"])
        self.assertIn("--json", cmd)
        self.assertTrue(cmd[3].endswith("某法规.md"))

    def test_frozen_uses_hidden_entry(self):
        cmd = self._cmd(frozen=True)
        self.assertEqual(cmd[0], sys.executable)
        self.assertEqual(cmd[1], launcher.ENGINE_FLAG)
        self.assertTrue(cmd[2].endswith("某法规.md"))


class LauncherDispatchTest(unittest.TestCase):
    def test_run_chunk_cli_forwards_args_and_return_code(self):
        calls: list[list[str]] = []

        fake_cli = types.ModuleType("chunking_v2.cli")

        def fake_main(argv):
            calls.append(list(argv))
            return 7

        fake_cli.main = fake_main
        fake_pkg = types.ModuleType("chunking_v2")
        fake_pkg.cli = fake_cli

        with mock.patch.dict(sys.modules, {"chunking_v2": fake_pkg, "chunking_v2.cli": fake_cli}):
            code = launcher.run_chunk_cli(["--out-dir", "x", "--json"])

        self.assertEqual(code, 7)
        self.assertEqual(calls, [["--out-dir", "x", "--json"]])

    def test_main_dispatches_engine_flag(self):
        seen: list[list[str]] = []

        def fake_run(argv):
            seen.append(list(argv))
            return 3

        with mock.patch.object(launcher, "run_chunk_cli", fake_run):
            code = launcher.main([launcher.ENGINE_FLAG, "a.md", "--json"])

        self.assertEqual(code, 3)
        self.assertEqual(seen, [["a.md", "--json"]])


if __name__ == "__main__":
    unittest.main()
