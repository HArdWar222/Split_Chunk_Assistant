"""HTTP 路由层：所有路由集中在 ``App.handle``，可以脱离 HTTP 服务单测。

返回值统一是 ``(status: int, content_type: str, body: bytes)``，
server.py 只负责把三元组写回 socket。
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import __phase__, __version__
from .config import ConfigError
from .manifest import ManifestError, SourceMissingError
from .ops import OpError, OpNotFound
from .studio import Studio

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

STATIC_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}
STATIC_ALLOWED = {"app.js", "style.css"}


class ApiError(Exception):
    """带 HTTP 状态码的业务错误。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _json(obj, status: int = 200) -> tuple[int, str, bytes]:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return status, "application/json; charset=utf-8", body


class App:
    def __init__(self, studio: Studio, web_dir: Path | str | None = None):
        self.studio = studio
        self.web_dir = Path(web_dir) if web_dir else WEB_DIR

    # -- 入口 -----------------------------------------------------------
    def handle(self, method: str, raw_path: str, body: bytes = b"") -> tuple[int, str, bytes]:
        method = (method or "GET").upper()
        parts = urlsplit(raw_path or "/")
        path = parts.path or "/"
        query = parse_qs(parts.query)
        try:
            return self._route(method, path, query, body)
        except ApiError as exc:
            return _json({"ok": False, "error": exc.message}, exc.status)
        except OpNotFound as exc:
            return _json({"ok": False, "error": str(exc)}, 404)
        except OpError as exc:
            return _json({"ok": False, "error": str(exc)}, 400)
        except ConfigError as exc:
            return _json({"ok": False, "error": str(exc)}, 400)
        except ManifestError as exc:
            return _json({"ok": False, "error": str(exc)}, 400)
        except Exception:  # noqa: BLE001 - 兜底，避免服务因单次请求崩溃
            traceback.print_exc()
            return _json({"ok": False, "error": "服务器内部错误（详见控制台日志）"}, 500)

    # -- 路由 -----------------------------------------------------------
    def _route(self, method: str, path: str, query: dict, body: bytes):
        if method == "GET" and path == "/api/health":
            return _json(
                {
                    "ok": True,
                    "app": "SplitChunkAssistant",
                    "version": __version__,
                    "phase": __phase__,
                }
            )

        if method == "GET" and path == "/api/files":
            payload = self.studio.files_payload()
            return _json(payload, 200 if payload.get("ok") else 503)

        if method == "POST" and path == "/api/reload":
            # 重新扫描数据目录（新增/删除本地切块后不必重启服务）
            self.studio.reload()
            payload = self.studio.files_payload()
            ok = bool(payload.get("ok"))
            return _json(
                {
                    "ok": ok,
                    "files": payload,
                    "message": "已重新扫描数据目录",
                    "error": None if ok else (payload.get("error") or "切块产物不可用"),
                },
                200 if ok else 503,
            )

        if method == "GET" and path == "/api/config":
            return _json(self.studio.config_payload())

        if method == "POST" and path == "/api/config":
            patch = self._read_json(body)
            config = self.studio.update_config(patch)
            return _json({"ok": True, "config": config, "message": "配置已保存"})

        if method == "GET" and path == "/api/doc":
            key = (query.get("key") or [""])[0].strip()
            if not key:
                raise ApiError(400, "缺少 key 参数")
            try:
                return _json(self.studio.doc_payload(key))
            except SourceMissingError as exc:
                raise ApiError(404, str(exc)) from None
            except ManifestError as exc:
                raise ApiError(404, str(exc)) from None

        if method == "GET" and path == "/api/tables":
            key = (query.get("key") or [""])[0].strip()
            if not key:
                raise ApiError(400, "缺少 key 参数")
            try:
                return _json(self.studio.tables_payload(key))
            except SourceMissingError as exc:
                raise ApiError(404, str(exc)) from None
            except ManifestError as exc:
                raise ApiError(404, str(exc)) from None

        if method == "POST" and path in {"/api/op", "/api/undo", "/api/redo", "/api/reset"}:
            data = self._read_json(body)
            key = str(data.get("key") or "").strip()
            if not key:
                raise ApiError(400, "缺少 key 参数")
            if path == "/api/op":
                return _json(self.studio.apply_op(key, data.get("op")))
            if path == "/api/undo":
                return _json(self.studio.undo_op(key))
            if path == "/api/redo":
                return _json(self.studio.redo_op(key))
            return _json(self.studio.reset_edits(key))

        if method == "GET" and path in {"", "/", "/index.html"}:
            return self._static_file("index.html")

        if method == "GET" and path.startswith("/static/"):
            name = path[len("/static/") :]
            if name not in STATIC_ALLOWED:
                raise ApiError(404, f"静态资源不存在：{name}")
            return self._static_file(name)

        if method == "GET" and path == "/favicon.ico":
            return 204, "image/x-icon", b""

        raise ApiError(404, f"没有这个路由：{method} {path}")

    # -- 工具 -----------------------------------------------------------
    @staticmethod
    def _read_json(body: bytes) -> dict:
        if not body:
            raise ApiError(400, "请求体为空")
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "请求体不是合法 JSON") from None
        if not isinstance(data, dict):
            raise ApiError(400, "请求体必须是 JSON 对象")
        return data

    def _static_file(self, name: str) -> tuple[int, str, bytes]:
        path = self.web_dir / name
        if not path.is_file():
            raise ApiError(404, f"文件不存在：{name}")
        ctype = STATIC_TYPES.get(name, "application/octet-stream")
        return 200, ctype, path.read_bytes()
