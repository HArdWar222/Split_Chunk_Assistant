"""server.py：端口已被占用时的「已有实例」识别。"""

from __future__ import annotations

import socket
import threading
import unittest

from chunk_studio import server as srv
from chunk_studio.api import App
from chunk_studio.config import Config
from chunk_studio.studio import Studio
from tests.fixtures import make_chunks_dir, remove, temp_dir


class ExistingInstanceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = temp_dir()
        self.chunks_dir = make_chunks_dir(self._tmp)
        cfg = Config(fac_root=str(self._tmp), chunks_dir=str(self.chunks_dir))
        self.studio = Studio(cfg=cfg, config_path=self._tmp / "config.json")
        self.app = App(self.studio)

    def tearDown(self):
        remove(self._tmp)

    def test_detects_running_instance(self):
        httpd = srv.build_server(self.app, "127.0.0.1", 0)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            url = srv._existing_instance(port)
            self.assertEqual(url, f"http://127.0.0.1:{port}/")
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_returns_none_for_free_port(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        self.assertIsNone(srv._existing_instance(port))

    def test_second_bind_fails(self):
        """同一端口不能静默起第二个实例（Windows 的 SO_REUSEADDR 坑）。"""
        httpd = srv.build_server(self.app, "127.0.0.1", 0)
        port = httpd.server_address[1]
        try:
            with self.assertRaises(OSError):
                second = srv.build_server(self.app, "127.0.0.1", port)
                second.server_close()
        finally:
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
