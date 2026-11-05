"""SPLITCHUNK 引擎接入：子进程切块 + 目录轮询 watch。

设计
----
- 引擎以 vendored 副本形式放在 ``vendor/splitchunk``（见 ``UPSTREAM.md``），
  工作台本身仍保持纯标准库：每次切块起一个 ``python -m chunking_v2.cli`` 子进程。
- 引擎产物落进「每个文档一个文件夹」的产物目录：``data/chunks/<子目录>/<名>/``
  下的 ``chunks.jsonl`` / ``extras.jsonl`` / ``report.json``；CLI 先写临时目录，
  成功后把三件套原子搬进文档目录。
- watch 线程按 ``chunker_poll`` 秒轮询：``data/source`` 下有 md 而
  ``data/chunks/<名>/chunks.jsonl`` 不存在（或 report 里的 source_sha256 与原件
  不一致）时，视为待切；``auto_chunk`` 打开就自动切，关闭时只提示、等手动按钮。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import REPO_ROOT, Config
from .manifest import CHUNKS_NAME, EXTRAS_NAME_LOCAL, REPORT_NAME
ENGINE_PACKAGE = "chunking_v2"
CLI_MODULE = "chunking_v2.cli"
VENDOR_ROOT = REPO_ROOT / "vendor" / "splitchunk"
FALLBACK_ROOTS = (VENDOR_ROOT, REPO_ROOT.parent / "SPLITCHUNK")

CHUNKS_SUFFIX = ".chunks.jsonl"
EXTRAS_SUFFIX = ".extras.jsonl"
REPORT_SUFFIX = ".report.json"

DEBOUNCE_SECONDS = 1.5  # 刚写入/仍在拷贝的文件先跳过
DEFAULT_TIMEOUT = 300  # 单文件切块超时（秒）
_VERSION_RE = re.compile(r"__version__\s*=\s*['\"]([^'\"]+)['\"]")


class ChunkerError(Exception):
    """切块引擎缺失或调用失败。消息直接给前端。"""


def resolve_engine(root: str | None = None) -> Path:
    """找引擎仓库根（含 ``chunking_v2/cli.py`` 的目录）。

    显式配置了 ``chunker_root`` 时只认它（找不到直接报错，避免静默用到
    别的副本）；未配置时依次尝试 vendor/splitchunk 与同级 SPLITCHUNK。
    """
    if root:
        cand = Path(root)
        if (cand / ENGINE_PACKAGE / "cli.py").is_file():
            return cand.resolve()
        raise ChunkerError(
            f"配置的切块引擎目录里没有 {ENGINE_PACKAGE}/cli.py：{cand}"
        )
    for cand in FALLBACK_ROOTS:
        try:
            if (cand / ENGINE_PACKAGE / "cli.py").is_file():
                return cand.resolve()
        except OSError:
            continue
    raise ChunkerError(
        f"找不到切片引擎（需要 {ENGINE_PACKAGE}/cli.py）：默认找 {VENDOR_ROOT}"
    )


def engine_version(engine_root: Path) -> str:
    init = engine_root / ENGINE_PACKAGE / "__init__.py"
    try:
        text = init.read_text(encoding="utf-8")
    except OSError:
        return ""
    m = _VERSION_RE.search(text)
    return m.group(1) if m else ""


def engine_info(cfg: Config) -> dict:
    try:
        root = resolve_engine(cfg.chunker_root or None)
    except ChunkerError as exc:
        return {"ok": False, "root": "", "version": "", "error": str(exc)}
    return {
        "ok": True,
        "root": str(root),
        "version": engine_version(root),
        "error": "",
    }


# -- 待切扫描 ---------------------------------------------------------------
@dataclass
class PendingFile:
    subject: str  # 给前端看的 key（data/<rel>/<name>）
    source: Path
    rel_dir: str
    reason: str  # new | outdated
    detail: str = ""


def _sha256_text(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _report_sha256(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("source_sha256") or "")


def scan_pending(cfg: Config, now: float | None = None) -> list[PendingFile]:
    """扫描 ``data/source`` 下需要（重新）切块的文件。"""
    if not cfg.data_root:
        return []
    root = Path(cfg.data_root)
    source_dir = root / "source"
    chunks_dir = root / "chunks"
    if not source_dir.is_dir():
        return []
    now = time.time() if now is None else now
    out: list[PendingFile] = []
    for source in sorted(source_dir.rglob("*.md")):
        if not source.is_file() or source.name.startswith("."):
            continue
        try:
            if now - source.stat().st_mtime < DEBOUNCE_SECONDS:
                continue  # 可能还在拷贝/写入
        except OSError:
            continue
        rel = source.parent.relative_to(source_dir)
        rel_dir = "" if rel.as_posix() == "." else rel.as_posix()
        stem = source.name[: -len(".md")]
        doc_dir = chunks_dir / rel / stem
        chunk_path = _existing_chunks(doc_dir, stem)
        subject = "data/" + stem if not rel_dir else f"data/{rel_dir}/{stem}"
        if chunk_path is None:
            out.append(PendingFile(subject, source, rel_dir, "new"))
            continue
        report = _existing_report(doc_dir, stem)
        recorded = _report_sha256(report) if report else ""
        if recorded:
            current = _sha256_text(source)
            if current and current != recorded:
                out.append(PendingFile(subject, source, rel_dir, "outdated"))
    return out


# -- 单文件切块 -------------------------------------------------------------
def run_engine_file(cfg: Config, engine_root: Path, item: PendingFile) -> dict:
    """起子进程切一个文件，返回结果字典（不抛异常，失败也返回）。

    引擎按源文件名把三件套平铺写进 ``--out-dir``；这里先落到
    ``<data_root>/.tmp_chunk/<名>/``，成功后原子搬进
    ``<data_root>/chunks/<分组>/<名>/{chunks,extras,report}.jsonl``。
    """
    root = Path(cfg.data_root)
    source_dir = root / "source"
    chunks_root = root / "chunks"
    doc_dir = (
        chunks_root / item.source.stem
        if not item.rel_dir
        else chunks_root / item.rel_dir / item.source.stem
    )
    tmp_dir = root / ".tmp_chunk" / item.source.stem
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        for stale in tmp_dir.iterdir():
            if stale.is_file():
                stale.unlink()
    except OSError as exc:
        return {
            "subject": item.subject,
            "ok": False,
            "gate_ok": False,
            "n_chunks": 0,
            "kinds": {},
            "failures": [],
            "message": f"无法创建临时切块目录：{exc}",
            "seconds": 0.0,
        }
    cmd = [
        sys.executable,
        "-m",
        CLI_MODULE,
        str(item.source),
        "--out-dir",
        str(tmp_dir),
        "--source-root",
        str(source_dir),
        "--strategy",
        cfg.chunker_strategy or "auto",
        "--json",
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(engine_root) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    started = time.time()
    result: dict = {
        "subject": item.subject,
        "ok": False,
        "gate_ok": False,
        "n_chunks": 0,
        "kinds": {},
        "failures": [],
        "message": "",
        "seconds": 0.0,
    }
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(engine_root),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DEFAULT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        result["message"] = f"切块超时（>{DEFAULT_TIMEOUT}s）"
        result["seconds"] = round(time.time() - started, 2)
        _cleanup_tmp(tmp_dir)
        return result
    except OSError as exc:
        result["message"] = f"无法启动切块引擎：{exc}"
        _cleanup_tmp(tmp_dir)
        return result

    result["seconds"] = round(time.time() - started, 2)
    payload = None
    if proc.stdout:
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = None
    file_info = {}
    if isinstance(payload, dict):
        files = payload.get("files")
        if isinstance(files, list) and files:
            file_info = files[0] if isinstance(files[0], dict) else {}
    if file_info:
        result["ok"] = bool(file_info.get("ok", proc.returncode in (0, 2)))
        result["gate_ok"] = bool(file_info.get("gate_ok", True))
        try:
            result["n_chunks"] = int(file_info.get("n_chunks") or 0)
        except (TypeError, ValueError):
            pass
        kinds = file_info.get("kinds")
        if isinstance(kinds, dict):
            result["kinds"] = {str(k): int(v) for k, v in kinds.items()}
        failures = file_info.get("gate_failures")
        if isinstance(failures, list):
            result["failures"] = [str(x) for x in failures]
    else:
        result["ok"] = proc.returncode == 0
        if proc.returncode not in (0, 2):
            result["message"] = (proc.stderr or proc.stdout or "").strip()[-400:]
    if proc.returncode == 2 and not result["failures"]:
        result["message"] = "gate 未通过"
    if result["ok"]:
        err = _publish_tmp(tmp_dir, doc_dir, item.source.stem)
        if err:
            result["ok"] = False
            result["message"] = err
    _cleanup_tmp(tmp_dir)
    return result


def _publish_tmp(tmp_dir: Path, doc_dir: Path, stem: str) -> str:
    """把临时目录里的三件套搬进文档目录（保留文档名）。

    新约定：``<名>.chunks.jsonl`` / ``<名>.extras.jsonl`` / ``<名>.report.json``；
    同时删掉早期统一命名的 ``chunks.jsonl`` 等，避免同一目录出现两份。
    返回错误消息（空串=成功）。
    """
    try:
        doc_dir.mkdir(parents=True, exist_ok=True)
        for suffix in (CHUNKS_SUFFIX, EXTRAS_SUFFIX, REPORT_SUFFIX):
            src = tmp_dir / (stem + suffix)
            if src.is_file():
                os.replace(src, doc_dir / (stem + suffix))
        for legacy in (CHUNKS_NAME, EXTRAS_NAME_LOCAL, REPORT_NAME):
            path = doc_dir / legacy
            if path.is_file():
                path.unlink()
    except OSError as exc:
        return f"切块产物落位失败：{exc}"
    return ""


def _existing_chunks(doc_dir: Path, stem: str) -> Path | None:
    """文档目录里已存在的切块文件（新命名优先，兼容旧统一命名）。"""
    for name in (stem + CHUNKS_SUFFIX, CHUNKS_NAME):
        path = doc_dir / name
        if path.is_file():
            return path
    return None


def _existing_report(doc_dir: Path, stem: str) -> Path | None:
    for name in (stem + REPORT_SUFFIX, REPORT_NAME):
        path = doc_dir / name
        if path.is_file():
            return path
    return None



def _cleanup_tmp(tmp_dir: Path) -> None:
    shutil.rmtree(tmp_dir, ignore_errors=True)


# -- 自动/手动队列 ----------------------------------------------------------
class WatchService:
    """轮询 + 单 worker 队列；手动按钮与自动扫描共用。"""

    def __init__(self, studio):
        self.studio = studio
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._job: threading.Thread | None = None
        self._running = False
        self._current = ""
        self._total = 0
        self._done = 0
        self._last: dict = {}
        self._last_pending: list[dict] = []
        self._last_scan_at = 0.0

    # -- 生命周期 ------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="chunk-watch", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # -- 查询 ----------------------------------------------------------
    def snapshot(self) -> dict:
        cfg = self.studio.cfg
        with self._lock:
            pending = [dict(p) for p in self._last_pending]
            return {
                "ok": True,
                "engine": engine_info(cfg),
                "enabled": bool(cfg.auto_chunk),
                "poll": cfg.chunker_poll,
                "strategy": cfg.chunker_strategy,
                "running": self._running,
                "current": self._current,
                "done": self._done,
                "total": self._total,
                "pending": len(pending),
                "pending_items": pending[:50],
                "last": dict(self._last) if self._last else None,
                "scan_at": self._last_scan_at,
            }

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """等待当前切块任务结束；返回 True 表示已空闲。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self._job
            if job is None or not job.is_alive():
                return True
            job.join(0.2)
        return not self._running

    def trigger(
        self,
        only_pending: bool = True,
        now: float | None = None,
        subjects: list[str] | None = None,
    ) -> dict:
        """手动触发：启动后台线程处理待切列表（可只处理指定 subject）。"""
        with self._lock:
            if self._running:
                return {"ok": True, "started": False, "message": "正在切块中"}
            items = scan_pending(self.studio.cfg, now)
            if subjects:
                wanted = set(subjects)
                items = [it for it in items if it.subject in wanted]
                if not items:
                    return {
                        "ok": True,
                        "started": False,
                        "message": "选中的文件没有变化，不需要重切",
                    }
            self._last_pending = [
                {"subject": it.subject, "reason": it.reason} for it in items
            ]
            if not items:
                return {"ok": True, "started": False, "message": "没有待切块的文件"}
            self._running = True
            self._total = len(items)
            self._done = 0
            self._current = ""
            self._job = threading.Thread(
                target=self._run_job, args=(items,), name="chunk-job", daemon=True
            )
            self._job.start()
            return {"ok": True, "started": True, "total": len(items)}

    # -- 内部 ----------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            cfg = self.studio.cfg
            wait = max(1, int(cfg.chunker_poll or 3))
            try:
                items = scan_pending(cfg, time.time())
            except Exception:  # noqa: BLE001 - watcher 不能因扫描异常退出
                items = []
            self._last_scan_at = time.time()
            with self._lock:
                if not self._running:
                    self._last_pending = [
                        {"subject": it.subject, "reason": it.reason} for it in items
                    ]
            if items and cfg.auto_chunk and not self._running:
                with self._lock:
                    if not self._running:
                        self._running = True
                        self._total = len(items)
                        self._done = 0
                        self._current = ""
                        self._job = threading.Thread(
                            target=self._run_job, args=(items,), name="chunk-job", daemon=True
                        )
                        self._job.start()
            self._stop.wait(wait)

    def _run_job(self, items: list[PendingFile]) -> None:
        cfg = self.studio.cfg
        engine_error = ""
        try:
            engine_root = resolve_engine(cfg.chunker_root or None)
        except ChunkerError as exc:
            engine_root = None
            engine_error = str(exc)
        results: list[dict] = []
        for item in items:
            with self._lock:
                self._current = item.subject
            if engine_root is None:
                result = {
                    "subject": item.subject,
                    "ok": False,
                    "gate_ok": False,
                    "n_chunks": 0,
                    "failures": [],
                    "message": engine_error,
                    "seconds": 0.0,
                }
            else:
                result = run_engine_file(cfg, engine_root, item)
            results.append(result)
            with self._lock:
                self._done += 1
        ok = [r for r in results if r.get("ok")]
        failed = [r for r in results if not r.get("ok")]
        gate = [r for r in ok if not r.get("gate_ok")]
        try:
            self.studio.reload()
        except Exception:  # noqa: BLE001 - reload 失败保留旧快照
            pass
        try:
            remaining = [
                {"subject": it.subject, "reason": it.reason}
                for it in scan_pending(self.studio.cfg)
            ]
        except Exception:  # noqa: BLE001 - 扫描失败不影响结果
            remaining = []
        with self._lock:
            self._last_pending = remaining
            self._last = {
                "ts": time.time(),
                "ok": len(ok),
                "failed": len(failed),
                "gate": len(gate),
                "chunks": sum(int(r.get("n_chunks") or 0) for r in ok),
                "items": results,
                "message": self._summary(ok, failed, gate),
            }
            self._running = False
            self._current = ""
            self._total = 0
            self._done = 0
            self._job = None

    @staticmethod
    def _summary(ok: list[dict], failed: list[dict], gate: list[dict]) -> str:
        if not ok and not failed:
            return "没有待切块的文件"
        parts = [f"已切块 {len(ok)} 份"]
        if gate:
            parts.append(f"{len(gate)} 份 gate 警告")
        if failed:
            parts.append(f"{len(failed)} 份失败")
        return "，".join(parts)
