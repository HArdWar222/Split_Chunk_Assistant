"""文件级「审核状态」：未审核（无状态文件 / 指纹对不上）、审核中、已审核。

设计要点（D22）：
- 状态存在 ``data/status/<stem>.json``，是一个很小的辅助文件，不写进 chunks/extras 契约；
- 状态里记录 ``baseline12``（当时正式 chunks 文件内容 sha256 的前 12 位）；
- 读状态时用当前 chunks 指纹校验：对不上就当作「未审核」，自动切块/外部覆盖后旧状态失效；
- 编辑（op / 保存草稿）→ 审核中（记住编辑前是不是「已审核」，供「放弃/重置」回退）；
- 提交 → 已审核（用提交后的新指纹）；
- 放弃/重置 → 回到编辑前状态（已审核仍是已审核，否则回到未审核）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import drafts

STATUS_DIR = "status"
STATE_NONE = "none"
STATE_IN_REVIEW = "in_review"
STATE_REVIEWED = "reviewed"

_LABELS = {
    STATE_NONE: "未审核",
    STATE_IN_REVIEW: "审核中",
    STATE_REVIEWED: "已审核",
}


def label(state: str) -> str:
    return _LABELS.get(state or STATE_NONE, "未审核")


def _status_dir(data_root: Path | str) -> Path:
    return Path(data_root) / STATUS_DIR


def status_path(data_root: Path | str, stem: str) -> Path:
    return _status_dir(data_root) / f"{stem}.json"


def _read(data_root: Path | str, stem: str) -> dict | None:
    path = status_path(data_root, stem)
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def load(data_root: Path | str, stem: str) -> dict:
    """读取原始状态（不校验指纹）。"""
    raw = _read(data_root, stem) or {}
    return {
        "state": str(raw.get("state") or STATE_NONE),
        "prior": str(raw.get("prior") or STATE_NONE),
        "baseline12": str(raw.get("baseline12") or ""),
        "updated_at": float(raw.get("updated_at") or 0),
    }


def resolve(data_root: Path | str, stem: str, current12: str) -> dict:
    """读取并校验指纹：指纹对不上视为「未审核」。"""
    raw = load(data_root, stem)
    if raw["state"] not in (STATE_IN_REVIEW, STATE_REVIEWED):
        return {"state": STATE_NONE, "updated_at": 0}
    if not current12 or raw["baseline12"] != current12:
        return {"state": STATE_NONE, "updated_at": 0}
    return {"state": raw["state"], "updated_at": raw["updated_at"]}


def _write(data_root: Path | str, stem: str, payload: dict) -> None:
    path = status_path(data_root, stem)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)


def mark_in_review(data_root: Path | str, stem: str, current12: str) -> dict:
    """进入「审核中」；记住进入前是不是「已审核」（同一基线下）。"""
    existing = load(data_root, stem)
    same_baseline = bool(current12) and existing["baseline12"] == current12
    if same_baseline and existing["state"] == STATE_IN_REVIEW:
        return existing  # 已经是审核中，不重复写盘
    prior = STATE_REVIEWED if (same_baseline and existing["state"] == STATE_REVIEWED) else STATE_NONE
    payload = {
        "state": STATE_IN_REVIEW,
        "prior": prior,
        "baseline12": current12,
        "updated_at": time.time(),
    }
    _write(data_root, stem, payload)
    return payload


def mark_reviewed(data_root: Path | str, stem: str, current12: str) -> dict:
    payload = {
        "state": STATE_REVIEWED,
        "prior": STATE_NONE,
        "baseline12": current12,
        "updated_at": time.time(),
    }
    _write(data_root, stem, payload)
    return payload


def restore_previous(data_root: Path | str, stem: str, current12: str) -> dict:
    """放弃/重置：回到进入「审核中」之前的状态。"""
    existing = load(data_root, stem)
    if existing["state"] != STATE_IN_REVIEW or not current12 or existing["baseline12"] != current12:
        return resolve(data_root, stem, current12)
    if existing["prior"] == STATE_REVIEWED:
        mark_reviewed(data_root, stem, current12)
        return {"state": STATE_REVIEWED, "updated_at": time.time()}
    clear(data_root, stem)
    return {"state": STATE_NONE, "updated_at": 0}


def clear(data_root: Path | str, stem: str) -> None:
    path = status_path(data_root, stem)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def state_for_chunks(data_root: Path | str, stem: str, chunks_path: Path | str) -> dict:
    """便捷函数：根据文件算指纹并解析状态。"""
    path = Path(chunks_path)
    current12 = drafts.sha12(path) if path.is_file() else ""
    return resolve(data_root, stem, current12)
