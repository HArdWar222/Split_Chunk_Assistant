"""HTTP 服务入口：stdlib ``ThreadingHTTPServer``，只监听 127.0.0.1。

用法
----
    python -m chunk_studio.server              # 默认端口 8790，自动开浏览器
    python -m chunk_studio.server --no-open    # 不自动开浏览器
    python -m chunk_studio.server --port 8801 --chunks-dir "D:\\data\\chunks_v2"
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .api import App
from .config import Config, ConfigError, DEFAULT_PORT
from .studio import Studio

MAX_BODY_BYTES = 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "SplitChunkAssistant/0.1"
    protocol_version = "HTTP/1.1"

    # 由 make_handler() 注入
    app: App | None = None

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- 内部 -----------------------------------------------------------
    def _dispatch(self, method: str) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length < 0 or length > MAX_BODY_BYTES:
            body = json.dumps({"ok": False, "error": "请求体过大"}, ensure_ascii=False).encode("utf-8")
            self._send(413, "application/json; charset=utf-8", body)
            return
        body = self.rfile.read(length) if length else b""
        assert self.app is not None, "Handler.app 未注入"
        status, ctype, payload = self.app.handle(method, self.path, body)
        self._send(status, ctype, payload)

    def _send(self, status: int, ctype: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def log_message(self, fmt: str, *args) -> None:  # 简化日志
        sys.stderr.write("[studio] %s\n" % (fmt % args))


def make_handler(app: App):
    """返回绑定了 App 的 Handler 子类（便于单测/多实例）。"""

    class _Handler(Handler):
        pass

    _Handler.app = app
    return _Handler


def build_server(app: App, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(app))


def _open_browser_later(url: str, delay: float = 0.6) -> None:
    def run() -> None:
        import time

        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - 打不开浏览器不影响服务
            pass

    threading.Thread(target=run, daemon=True).start()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="chunk_studio.server",
        description="SplitChunkAssistant —— 可视化人工切片工作台（P0：文件清单）",
    )
    parser.add_argument("--port", type=int, default=None, help="端口（默认取 config.json，初始 8790）")
    parser.add_argument("--fac-root", default=None, help="覆盖 FAC-RAG 根目录（用于推导默认 chunks_dir）")
    parser.add_argument("--chunks-dir", default=None, help="覆盖切块产物目录（需含 _manifest.json）")
    parser.add_argument("--config", default=None, help="配置文件路径（默认仓库根目录 config.json）")
    parser.add_argument("--open", dest="open_browser", action="store_true", help="启动后自动打开浏览器")
    parser.add_argument("--no-open", dest="open_browser", action="store_false", help="不自动打开浏览器")
    parser.set_defaults(open_browser=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config_path = Path(args.config) if args.config else None
    try:
        studio = Studio(config_path=config_path)
        patch: dict = {}
        if args.fac_root:
            patch["fac_root"] = args.fac_root
        if args.chunks_dir:
            patch["chunks_dir"] = args.chunks_dir
        if patch:
            studio.cfg = studio.cfg.merged(patch)
            studio.reload()
        port = int(args.port or studio.cfg.port)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    app = App(studio)
    try:
        httpd = build_server(app, "127.0.0.1", port)
    except OSError as exc:
        print(f"无法监听 127.0.0.1:{port}：{exc}", file=sys.stderr)
        return 2

    url = f"http://127.0.0.1:{port}/"
    print(f"SplitChunkAssistant P0 已启动：{url}")
    print(f"  切块产物：{studio.cfg.chunks_dir}")
    files = studio.files_payload()
    if files.get("ok"):
        totals = files.get("totals") or {}
        print(f"  文件 {totals.get('n_files')} 个 / 切块 {totals.get('n_chunks')} 条")
    else:
        print(f"  [!] 产物不可用：{files.get('error')}")
        print("      可在页面「设置」里修改路径后保存，立即生效。")

    if args.open_browser:
        _open_browser_later(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
