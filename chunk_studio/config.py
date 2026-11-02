"""配置：加载 / 校验 / 保存 config.json。

设计原则
--------
- 配置只放"路径与本地偏好"，不放业务数据；
- 默认值指向 FAC-RAG 的 data/chunks_v2（本机兄弟目录）；
- 配置文件在仓库根目录 ``config.json``（已 gitignore，不入库），首次运行自动生成；
- 路径支持环境变量与 ``~`` 展开，方便换盘 / 换机器。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.json"

# 默认值：优先环境变量 FAC_ROOT，其次仓库的兄弟目录 FAC-RAG
_DEFAULT_FAC_ROOT = os.environ.get("FAC_ROOT") or str(REPO_ROOT.parent / "FAC-RAG")
DEFAULT_FAC_ROOT = Path(_DEFAULT_FAC_ROOT)
DEFAULT_CHUNKS_SUBDIR = Path("data") / "chunks_v2"
DEFAULT_PORT = 8790
MANIFEST_NAME = "_manifest.json"


class ConfigError(ValueError):
    """配置不合法。消息会直接展示给前端，请写成用户能看懂的中文。"""


def _clean_path(value: object) -> str:
    """去引号/空白，并展开 ~ 与环境变量。"""
    text = str(value or "").strip().strip('"').strip("'")
    if not text:
        return ""
    return os.path.expandvars(os.path.expanduser(text))


@dataclass
class Config:
    """本地配置。路径一律用字符串保存，方便直接序列化。"""

    fac_root: str = ""
    chunks_dir: str = ""
    source_roots: dict[str, str] = field(default_factory=dict)
    port: int = DEFAULT_PORT

    def __post_init__(self) -> None:
        if not self.fac_root:
            self.fac_root = str(DEFAULT_FAC_ROOT)
        if not self.chunks_dir:
            self.chunks_dir = str(Path(self.fac_root) / DEFAULT_CHUNKS_SUBDIR)
        self.fac_root = _clean_path(self.fac_root)
        self.chunks_dir = _clean_path(self.chunks_dir)
        self.source_roots = {
            str(k): _clean_path(v) for k, v in dict(self.source_roots or {}).items()
        }
        try:
            self.port = int(self.port)
        except (TypeError, ValueError):
            raise ConfigError(f"端口必须是整数：{self.port!r}") from None

    # -- 序列化 ---------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "fac_root": self.fac_root,
            "chunks_dir": self.chunks_dir,
            "source_roots": dict(self.source_roots),
            "port": self.port,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        data = dict(data or {})
        return cls(
            fac_root=str(data.get("fac_root") or ""),
            chunks_dir=str(data.get("chunks_dir") or ""),
            source_roots=dict(data.get("source_roots") or {}),
            port=data.get("port", DEFAULT_PORT),
        )

    # -- 加载 / 保存 ----------------------------------------------------
    @classmethod
    def load(cls, path: Path | str | None = None) -> "Config":
        p = Path(path) if path else CONFIG_PATH
        if not p.is_file():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"配置文件无法读取：{p}（{exc}）") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"配置文件格式不对：{p}")
        return cls.from_dict(data)

    def save(self, path: Path | str | None = None) -> Path:
        p = Path(path) if path else CONFIG_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(p)
        return p

    # -- 修改 -----------------------------------------------------------
    def merged(self, patch: dict) -> "Config":
        """返回应用 patch 后的新配置（不落盘、不校验）。

        支持的键：fac_root / chunks_dir / source_roots / port。
        只改 fac_root 时，若旧的 chunks_dir 恰好是"fac_root/data/chunks_v2"派生值，
        会自动跟随到新根目录下的默认位置；自定义过 chunks_dir 的则保持不变。
        """
        patch = {k: v for k, v in dict(patch or {}).items() if v is not None}
        old_default_chunks = str(Path(self.fac_root) / DEFAULT_CHUNKS_SUBDIR)

        fac_root = _clean_path(patch.get("fac_root", self.fac_root))
        chunks_dir = patch.get("chunks_dir")
        if chunks_dir is None:
            if fac_root != self.fac_root and self.chunks_dir == old_default_chunks:
                chunks_dir = str(Path(fac_root) / DEFAULT_CHUNKS_SUBDIR)
            else:
                chunks_dir = self.chunks_dir
        source_roots = patch.get("source_roots", self.source_roots)
        port = patch.get("port", self.port)

        return Config(
            fac_root=fac_root,
            chunks_dir=_clean_path(chunks_dir),
            source_roots=dict(source_roots or {}),
            port=port,
        )

    # -- 校验 -----------------------------------------------------------
    def validate(self) -> list[str]:
        """返回错误列表（空列表=通过）。只有硬错误，没有警告。"""
        errors: list[str] = []
        chunks = Path(self.chunks_dir)
        if not chunks.is_dir():
            errors.append(f"切块产物目录不存在：{chunks}")
        elif not (chunks / MANIFEST_NAME).is_file():
            errors.append(f"目录里没有 {MANIFEST_NAME}：{chunks}")
        for group, root in sorted(self.source_roots.items()):
            if not root:
                continue
            if not Path(root).is_dir():
                errors.append(f"源目录不存在（{group}）：{root}")
        if not (0 < self.port < 65536):
            errors.append(f"端口超出范围 1–65535：{self.port}")
        return errors

    def require_valid(self) -> None:
        errors = self.validate()
        if errors:
            raise ConfigError("；".join(errors))
