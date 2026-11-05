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
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .api import App
from .config import Config, ConfigError, DEFAULT_PORT
from .paths import default_config_path, ensure_data_layout, is_frozen
from .studio import Studio
from . import __phase__, __version__

MAX_BODY_BYTES = 64 * 1024 * 1024  # 批量导入原文件（/api/upload）需要比普通 JSON 大得多


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


class _StudioHTTPServer(ThreadingHTTPServer):
    """关闭 SO_REUSEADDR：Windows 下允许复用会让第二个实例静默绑定同一端口。"""

    allow_reuse_address = False
    daemon_threads = True


def build_server(app: App, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    return _StudioHTTPServer((host, port), make_handler(app))


def _open_browser(url: str) -> bool:
    """同步打开默认浏览器，返回是否成功。

    Windows 上用 ``os.startfile`` 最稳（冻结成 exe 后 ``webbrowser`` 偶尔找不到
    浏览器）；其它平台退回 ``webbrowser.open``。
    """
    if os.name == "nt" and hasattr(os, "startfile"):
        try:
            os.startfile(url)  # type: ignore[attr-defined]  # noqa: S606 - 本机文件/URL
            return True
        except OSError:
            pass
    try:
        return bool(webbrowser.open(url))
    except Exception:  # noqa: BLE001 - 打不开浏览器不影响服务
        return False


def _open_browser_later(url: str, delay: float = 0.6) -> None:
    def run() -> None:
        import time

        time.sleep(delay)
        if not _open_browser(url):
            print(f"浏览器没有自动打开，请手动访问：{url}", flush=True)

    threading.Thread(target=run, daemon=True).start()


def _existing_instance(port: int, host: str = "127.0.0.1") -> str | None:
    """如果该端口上已有本工具在跑，返回它的 URL；否则返回 None。"""
    import json as _json
    import urllib.request

    url = f"http://{host}:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=1.0) as resp:  # noqa: S310 - 本机地址
            data = _json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - 连不上/不是本工具都算「没有」
        return None
    if isinstance(data, dict) and data.get("app") == "SplitChunkAssistant":
        return f"http://{host}:{port}/"
    return None


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
    config_path = Path(args.config) if args.config else default_config_path()
    try:
        cfg = Config.load(config_path)
        if cfg.data_root:
            try:
                ensure_data_layout(cfg.data_root)
            except OSError as exc:
                print(f"数据目录不可写：{cfg.data_root}（{exc}）", file=sys.stderr, flush=True)
                print("  请把程序放到有写权限的位置（例如用户目录），或在配置里改 data_root。", file=sys.stderr, flush=True)
                return 2
        studio = Studio(cfg=cfg, config_path=config_path)
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
        print(f"配置错误：{exc}", file=sys.stderr, flush=True)
        return 2

    app = App(studio)
    studio.watch.start()
    running = _existing_instance(port)
    if running:
        print(f"127.0.0.1:{port} 上已经有一个 SplitChunkAssistant 在运行：{running}", flush=True)
        print("  已直接为你打开浏览器；如需再开一个实例，请换端口，例如：", flush=True)
        if is_frozen():
            print(f'  SplitChunkAssistant.exe --port {port + 1}', flush=True)
        else:
            print(f"  python -m chunk_studio.server --port {port + 1}", flush=True)
        if args.open_browser and not _open_browser(running):
            print(f"  浏览器没有自动打开，请手动访问：{running}", flush=True)
        return 0
    try:
        httpd = build_server(app, "127.0.0.1", port)
    except OSError as exc:
        print(f"无法监听 127.0.0.1:{port}：{exc}", file=sys.stderr, flush=True)
        print(f"  该端口可能被其它程序占用，换个端口试试：--port {port + 1}", file=sys.stderr, flush=True)
        return 2

    url = f"http://127.0.0.1:{port}/"
    print(f"SplitChunkAssistant {__version__}（{__phase__}）已启动：{url}", flush=True)
    print(f"  数据目录：{studio.cfg.data_root}", flush=True)
    engine = studio.watch.snapshot().get("engine") or {}
    if engine.get("ok"):
        print(f"  切块引擎：{engine.get('root')}（{engine.get('version') or '?'}）", flush=True)
    else:
        print(f"  [!] 切块引擎不可用：{engine.get('error')}", flush=True)
    files = studio.files_payload()
    if files.get("ok"):
        totals = files.get("totals") or {}
        print(f"  文件 {totals.get('n_files')} 个 / 切块 {totals.get('n_chunks')} 条", flush=True)
    else:
        print(f"  [!] 产物不可用：{files.get('error')}", flush=True)
        print("      可在页面「设置」里修改路径后保存，立即生效。", flush=True)

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
