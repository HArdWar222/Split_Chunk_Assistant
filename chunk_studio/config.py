"""配置：加载 / 校验 / 保存 config.json。

设计原则
--------
- 配置只放"路径与本地偏好"，不放业务数据；
- 默认值指向 FAC-RAG 的 data/chunks_v2（本机兄弟目录）；
- ``data_root`` 可选：本地「两目录约定」数据根（下含 ``source/`` 与 ``chunks/``），
  与 FAC-RAG manifest 数据源可以同时启用；
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

#: 本地「两目录约定」数据根下的子目录名。
DIR_SOURCE = "source"
DIR_CHUNKS = "chunks"


class ConfigError(ValueError):
    """配置不合法。消息会直接展示给前端，请写成用户能看懂的中文。"""


def _clean_path(value: object) -> str:
    """去引号/空白，并展开 ~ 与环境变量。"""
    text = str(value or "").strip().strip('"').strip("'")
    if not text:
        return ""
    return os.path.expandvars(os.path.expanduser(text))


def _abs_path(value: str) -> str:
    """空串原样返回；相对路径按仓库根目录解析成绝对路径。"""
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return str(path)


@dataclass
class Config:
    """本地配置。路径一律用字符串保存，方便直接序列化。"""

    fac_root: str = ""
    chunks_dir: str = ""
    data_root: str = ""
    source_roots: dict[str, str] = field(default_factory=dict)
    port: int = DEFAULT_PORT
    # 编辑后自动分 part（文本按字符均分；表格只按行区间拆独立块）
    auto_part: bool = False
    max_chars: int = 800
    min_part_chars: int = 200
    # 自动切块：发现 source/ 下没有 chunks 的 md 时调用 vendored 引擎子进程
    auto_chunk: bool = True
    chunker_root: str = ""  # 空=自动探测 vendor/splitchunk，其次 ../SPLITCHUNK
    chunker_poll: int = 3  # 轮询秒数
    chunker_strategy: str = "auto"  # auto | profile

    def __post_init__(self) -> None:
        if not self.fac_root:
            self.fac_root = str(DEFAULT_FAC_ROOT)
        if not self.chunks_dir:
            self.chunks_dir = str(Path(self.fac_root) / DEFAULT_CHUNKS_SUBDIR)
        self.fac_root = _clean_path(self.fac_root)
        self.chunks_dir = _clean_path(self.chunks_dir)
        self.data_root = _abs_path(_clean_path(self.data_root))
        self.source_roots = {
            str(k): _clean_path(v) for k, v in dict(self.source_roots or {}).items()
        }
        try:
            self.port = int(self.port)
        except (TypeError, ValueError):
            raise ConfigError(f"端口必须是整数：{self.port!r}") from None
        self.auto_part = bool(self.auto_part)
        try:
            self.max_chars = int(self.max_chars)
            self.min_part_chars = int(self.min_part_chars)
        except (TypeError, ValueError):
            raise ConfigError("max_chars / min_part_chars 必须是整数") from None
        self.auto_chunk = bool(self.auto_chunk)
        self.chunker_root = _abs_path(_clean_path(self.chunker_root))
        try:
            self.chunker_poll = int(self.chunker_poll)
        except (TypeError, ValueError):
            raise ConfigError(f"chunker_poll 必须是整数：{self.chunker_poll!r}") from None
        self.chunker_strategy = str(self.chunker_strategy or "auto")

    # -- 序列化 ---------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "fac_root": self.fac_root,
            "chunks_dir": self.chunks_dir,
            "data_root": self.data_root,
            "source_roots": dict(self.source_roots),
            "port": self.port,
            "auto_part": self.auto_part,
            "max_chars": self.max_chars,
            "min_part_chars": self.min_part_chars,
            "auto_chunk": self.auto_chunk,
            "chunker_root": self.chunker_root,
            "chunker_poll": self.chunker_poll,
            "chunker_strategy": self.chunker_strategy,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        data = dict(data or {})
        return cls(
            fac_root=str(data.get("fac_root") or ""),
            chunks_dir=str(data.get("chunks_dir") or ""),
            data_root=str(data.get("data_root") or ""),
            source_roots=dict(data.get("source_roots") or {}),
            port=data.get("port", DEFAULT_PORT),
            auto_part=bool(data.get("auto_part", False)),
            max_chars=data.get("max_chars", 800),
            min_part_chars=data.get("min_part_chars", 200),
            auto_chunk=bool(data.get("auto_chunk", True)),
            chunker_root=str(data.get("chunker_root") or ""),
            chunker_poll=data.get("chunker_poll", 3),
            chunker_strategy=str(data.get("chunker_strategy") or "auto"),
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

        支持的键：fac_root / chunks_dir / data_root / source_roots / port。
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
        data_root = patch.get("data_root", self.data_root)
        source_roots = patch.get("source_roots", self.source_roots)
        port = patch.get("port", self.port)

        return Config(
            fac_root=fac_root,
            chunks_dir=_clean_path(chunks_dir),
            data_root=_clean_path(data_root),
            source_roots=dict(source_roots or {}),
            port=port,
            auto_part=patch.get("auto_part", self.auto_part),
            max_chars=patch.get("max_chars", self.max_chars),
            min_part_chars=patch.get("min_part_chars", self.min_part_chars),
            auto_chunk=patch.get("auto_chunk", self.auto_chunk),
            chunker_root=patch.get("chunker_root", self.chunker_root),
            chunker_poll=patch.get("chunker_poll", self.chunker_poll),
            chunker_strategy=patch.get("chunker_strategy", self.chunker_strategy),
        )

    # -- 校验 -----------------------------------------------------------
    def manifest_available(self) -> bool:
        """FAC-RAG 产物目录是否可用（目录存在且带 ``_manifest.json``）。"""
        chunks = Path(self.chunks_dir)
        return chunks.is_dir() and (chunks / MANIFEST_NAME).is_file()

    def data_available(self) -> bool:
        """本地两目录数据根是否可用（``source/`` 与 ``chunks/`` 都存在）。"""
        if not self.data_root:
            return False
        root = Path(self.data_root)
        return (root / DIR_SOURCE).is_dir() and (root / DIR_CHUNKS).is_dir()

    def validate(self) -> list[str]:
        """返回错误列表（空列表=通过）。只有硬错误，没有警告。

        唯一数据源是本地「两目录」数据根 ``data_root``（下含 ``source/`` 与
        ``chunks/``）；``fac_root``/``chunks_dir``/``source_roots`` 只为兼容旧
        配置文件保留，不再参与校验。
        """
        errors: list[str] = []
        if not self.data_root:
            errors.append("未配置本地数据目录 data_root（下需含 source/ 与 chunks/）")
        else:
            root = Path(self.data_root)
            if not root.is_dir():
                errors.append(f"本地数据目录不存在：{root}")
            elif not self.data_available():
                errors.append(
                    f"本地数据目录不完整（需含 {DIR_SOURCE}/ 与 {DIR_CHUNKS}/）：{root}"
                )
        if not (0 < self.port < 65536):
            errors.append(f"端口超出范围 1–65535：{self.port}")
        if self.max_chars < 200:
            errors.append(f"max_chars 太小（至少 200）：{self.max_chars}")
        if not (0 < self.min_part_chars <= self.max_chars):
            errors.append(
                f"min_part_chars 必须在 1–max_chars 之间：{self.min_part_chars}"
            )
        if not (1 <= self.chunker_poll <= 60):
            errors.append(f"chunker_poll 必须在 1–60 秒之间：{self.chunker_poll}")
        if self.chunker_strategy not in ("auto", "profile"):
            errors.append(f"chunker_strategy 只能是 auto 或 profile：{self.chunker_strategy}")
        return errors

    def require_valid(self) -> None:
        errors = self.validate()
        if errors:
            raise ConfigError("；".join(errors))
