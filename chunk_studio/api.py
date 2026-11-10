"""HTTP 路由层：所有路由集中在 ``App.handle``，可以脱离 HTTP 服务单测。

返回值统一是 ``(status: int, content_type: str, body: bytes)``，
server.py 只负责把三元组写回 socket。
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import traceback
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import __phase__, __version__, drafts
from . import sources as source_tags
from .chunker import ChunkerError
from .config import ConfigError
from .manifest import ManifestError, SourceMissingError
from .ops import OpError, OpNotFound
from .paths import web_dir
from .studio import Studio

WEB_DIR = web_dir()

STATIC_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}
STATIC_ALLOWED = {"app.js", "style.css"}

# 批量导入原文件：单文件 ≤16MB、整批 ≤64MB（server.MAX_BODY_BYTES 会按整批放大）。
UPLOAD_MAX_FILE = 16 * 1024 * 1024
UPLOAD_MAX_TOTAL = 64 * 1024 * 1024
UPLOAD_EXTS = {".md", ".markdown", ".txt"}


class ApiError(Exception):
    """带 HTTP 状态码的业务错误。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _json(obj, status: int = 200) -> tuple[int, str, bytes]:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return status, "application/json; charset=utf-8", body


def _safe_upload_name(raw: object) -> str:
    """校验上传文件名并统一成 ``<stem>.md``（拒绝路径穿越 / 控制字符 / 非法扩展名）。"""
    name = str(raw or "").strip()
    if not name:
        raise ApiError(400, "文件名不能为空")
    if "/" in name or "\\" in name or ".." in name or Path(name).name != name:
        raise ApiError(400, f"文件名不合法：{raw}")
    if any(ord(ch) < 32 for ch in name):
        raise ApiError(400, f"文件名里含有控制字符：{raw}")
    suffix = Path(name).suffix.lower()
    if suffix not in UPLOAD_EXTS:
        raise ApiError(400, f"只支持 .md / .markdown / .txt：{name}")
    stem = Path(name).stem.strip()
    if not stem or stem in {".", ".."}:
        raise ApiError(400, f"文件名不合法：{raw}")
    return stem + ".md"


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
        except drafts.DraftError as exc:
            return _json({"ok": False, "error": str(exc)}, 400)
        except ChunkerError as exc:
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

        if method == "POST" and path == "/api/upload":
            # 批量导入原文件到 data/source/（只接受 UTF-8 文本；同名不覆盖）
            data = self._read_json(body)
            items = data.get("files")
            if not isinstance(items, list) or not items:
                raise ApiError(400, "files 不能为空")
            source_dir = Path(self.studio.cfg.data_root) / "source"
            if not source_dir.is_dir():
                raise ApiError(503, f"本地数据目录缺少 source/：{source_dir}")
            created: list[str] = []
            skipped: list[str] = []
            errors: list[dict] = []
            total = 0
            for item in items:
                if not isinstance(item, dict):
                    errors.append({"name": "", "error": "条目不是对象"})
                    continue
                raw_name = str(item.get("name") or "")
                try:
                    name = _safe_upload_name(raw_name)
                except ApiError as exc:
                    errors.append({"name": raw_name, "error": exc.message})
                    continue
                b64 = item.get("data_b64")
                if not isinstance(b64, str) or not b64:
                    errors.append({"name": raw_name, "error": "内容为空"})
                    continue
                try:
                    raw = base64.b64decode(b64, validate=True)
                except (binascii.Error, ValueError):
                    errors.append({"name": raw_name, "error": "内容不是合法 base64"})
                    continue
                if len(raw) > UPLOAD_MAX_FILE:
                    errors.append({"name": raw_name, "error": "单文件超过 16MB"})
                    continue
                total += len(raw)
                if total > UPLOAD_MAX_TOTAL:
                    errors.append({"name": raw_name, "error": "整批超过 64MB，已停止"})
                    break
                try:
                    text = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    errors.append({"name": raw_name, "error": "不是 UTF-8 编码（请先转码）"})
                    continue
                if not text.strip():
                    errors.append({"name": raw_name, "error": "内容为空"})
                    continue
                target = source_dir / name
                if target.exists():
                    skipped.append(name)
                    continue
                tmp = target.with_name(target.name + ".uploading")
                tmp.write_text(text, encoding="utf-8", newline="")
                os.replace(tmp, target)
                created.append(name)
            if created:
                self.studio.reload()
            return _json(
                {
                    "ok": True,
                    "created": created,
                    "skipped": skipped,
                    "errors": errors,
                    "files": self.studio.files_payload() if created else None,
                    "message": "已导入 {} 个文件".format(len(created)),
                }
            )

        if method == "POST" and path == "/api/files/delete":
            # 物理删除选中的文件（源文件 + 切块目录 + 草稿 + 备份 + 审核状态）
            data = self._read_json(body)
            keys = data.get("keys")
            if not isinstance(keys, list) or not keys:
                raise ApiError(400, "keys 不能为空")
            try:
                result = self.studio.delete_files([str(k) for k in keys])
            except ManifestError as exc:
                raise ApiError(400, str(exc)) from exc
            status = 200 if result.get("ok") else 409
            if result.get("n_deleted"):
                result["files"] = self.studio.files_payload()
            return _json(result, status)

        if method == "POST" and path == "/api/files/source":
            # 文件级来源类别（人工标注）：写 sources.json + 重写该文件所有块
            data = self._read_json(body)
            key = str(data.get("key") or "").strip()
            if not key:
                raise ApiError(400, "key 不能为空")
            try:
                result = self.studio.set_file_source(key, data.get("source"))
            except source_tags.SourceError as exc:
                raise ApiError(400, str(exc)) from exc
            except ManifestError as exc:
                raise ApiError(400, str(exc)) from exc
            result["files"] = self.studio.files_payload()
            return _json(result)

        if method == "GET" and path == "/api/watch":
            return _json(self.studio.watch.snapshot())

        if method == "POST" and path == "/api/watch":
            data = self._read_json(body)
            if "enabled" in data:
                self.studio.update_config({"auto_chunk": bool(data.get("enabled"))})
            snapshot = self.studio.watch.snapshot()
            return _json(
                {
                    "ok": True,
                    "watch": snapshot,
                    "message": "已开启自动切块" if snapshot.get("enabled") else "已关闭自动切块",
                }
            )

        if method == "POST" and path == "/api/chunk":
            data = self._read_json(body)
            key = str(data.get("key") or "").strip()
            result = self.studio.watch.trigger(subjects=[key] if key else None)
            status = 200 if result.get("ok") else 409
            return _json({**result, "watch": self.studio.watch.snapshot()}, status)

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

        if method == "POST" and path in {"/api/draft/save", "/api/draft/discard", "/api/draft/commit"}:
            data = self._read_json(body)
            key = str(data.get("key") or "").strip()
            if not key:
                raise ApiError(400, "缺少 key 参数")
            try:
                if path == "/api/draft/save":
                    doc = self.studio.save_draft(key)
                    return _json({**doc, "message": "草稿已保存"})
                if path == "/api/draft/discard":
                    doc = self.studio.discard_draft(key)
                    return _json({**doc, "message": "已放弃草稿"})
                doc = self.studio.commit_draft(key)
                n = (doc.get("committed") or {}).get("n_chunks") or len(doc.get("chunks") or [])
                return _json({**doc, "message": f"已提交 {n} 个切片"})
            except SourceMissingError as exc:
                raise ApiError(404, str(exc)) from None
            except ManifestError as exc:
                raise ApiError(404, str(exc)) from None

        if method == "GET" and path == "/api/export":
            key = (query.get("key") or [""])[0].strip()
            if not key:
                raise ApiError(400, "缺少 key 参数")
            try:
                doc = self.studio.doc_payload(key)
            except SourceMissingError as exc:
                raise ApiError(404, str(exc)) from None
            except ManifestError as exc:
                raise ApiError(404, str(exc)) from None
            chunks = drafts.contract_chunks(doc.get("chunks") or [])
            content = "".join(
                json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in chunks
            )
            filename = f"{doc.get('law_name') or 'chunks'}.chunks.jsonl"
            return _json(
                {
                    "ok": True,
                    "filename": filename,
                    "n_chunks": len(chunks),
                    "content": content,
                }
            )

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
