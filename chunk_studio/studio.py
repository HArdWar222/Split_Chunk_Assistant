"""数据门面：配置 + 只读切块产物，向 HTTP 层提供干净的载荷。

职责边界
--------
- 持有当前 ``Config`` 与 ``FacBridge``；
- 把 manifest 转成前端要的文件清单结构；
- 负责配置的保存与重新加载（保存后立即生效，不用重启）；
- 这里**没有任何写切块产物的代码**——FAC-RAG 的数据永远只读。
"""

from __future__ import annotations

from pathlib import Path

from .config import Config, ConfigError
from .fac_bridge import FacBridge
from .manifest import ManifestError


class Studio:
    def __init__(
        self,
        cfg: Config | None = None,
        config_path: Path | str | None = None,
    ):
        self.config_path = Path(config_path) if config_path else None
        self.cfg = cfg or Config.load(self.config_path)
        self._bridge: FacBridge | None = None
        self._error: str | None = None
        self._init_bridge()

    # -- 内部 -----------------------------------------------------------
    def _init_bridge(self) -> None:
        self._bridge = None
        self._error = None
        try:
            bridge = FacBridge(self.cfg.chunks_dir)
            _ = bridge.manifest  # 先触发加载，尽早暴露错误
            self._bridge = bridge
        except ManifestError as exc:
            self._error = str(exc)

    # -- 对外 -----------------------------------------------------------
    @property
    def bridge(self) -> FacBridge:
        if self._bridge is None:
            raise ManifestError(self._error or "切块产物不可用")
        return self._bridge

    @property
    def available(self) -> bool:
        return self._bridge is not None

    @property
    def error(self) -> str | None:
        return self._error

    def reload(self) -> None:
        self._init_bridge()

    # -- 载荷 -----------------------------------------------------------
    def files_payload(self) -> dict:
        """文件清单（P0 的主数据）。"""
        if self._bridge is None:
            return {
                "ok": False,
                "chunks_dir": self.cfg.chunks_dir,
                "error": self._error or "切块产物不可用",
                "config": self.config_payload(),
            }
        payload = self._bridge.manifest.payload()
        payload["config"] = self.config_payload()
        return payload

    def config_payload(self) -> dict:
        """配置 + 从 manifest 推导的默认源根目录（设置面板展示用）。"""
        defaults: dict[str, str] = {}
        if self._bridge is not None:
            for name, entries in self._bridge.manifest.groups.items():
                first = entries[0] if entries else None
                if first is not None and first.source_path:
                    defaults[name] = str(Path(first.source_path).parent)
        return {
            "ok": True,
            "fac_root": self.cfg.fac_root,
            "chunks_dir": self.cfg.chunks_dir,
            "source_roots": dict(self.cfg.source_roots),
            "default_source_roots": defaults,
            "groups": list(defaults.keys()),
            "port": self.cfg.port,
            "errors": self.cfg.validate(),
            "manifest_ok": self._bridge is not None,
            "manifest_error": self._error,
        }

    def update_config(self, patch: dict) -> dict:
        """校验 → 落盘 → 重新加载产物。失败抛 ConfigError。"""
        if not isinstance(patch, dict):
            raise ConfigError("配置必须是 JSON 对象")
        merged = self.cfg.merged(patch)
        errors = merged.validate()
        if errors:
            raise ConfigError("；".join(errors))
        merged.save(self.config_path)
        self.cfg = merged
        self.reload()
        if self._bridge is None:
            raise ConfigError(self._error or "改了配置后产物依然不可用")
        return self.config_payload()
