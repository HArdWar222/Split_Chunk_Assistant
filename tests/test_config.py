from __future__ import annotations

import unittest
from pathlib import Path

from chunk_studio.config import (
    DEFAULT_CHUNKS_SUBDIR,
    DEFAULT_PORT,
    Config,
    ConfigError,
)
from tests.fixtures import make_data_dir, workspace_tmp


class ConfigDefaultsTest(unittest.TestCase):
    def test_defaults_point_at_fac_rag(self):
        cfg = Config()
        self.assertTrue(cfg.fac_root.endswith("FAC-RAG"), cfg.fac_root)
        self.assertEqual(Path(cfg.chunks_dir), Path(cfg.fac_root) / DEFAULT_CHUNKS_SUBDIR)
        self.assertEqual(cfg.port, DEFAULT_PORT)
        self.assertEqual(cfg.source_roots, {})

    def test_roundtrip(self):
        cfg = Config(fac_root=r"D:\FAC-RAG", chunks_dir=r"D:\FAC-RAG\data\chunks_v2", port=8801)
        back = Config.from_dict(cfg.to_dict())
        self.assertEqual(back.to_dict(), cfg.to_dict())

    def test_bad_port(self):
        with self.assertRaises(ConfigError):
            Config(port="not-a-port")

    def test_clean_path_strips_quotes_and_expands_user(self):
        cfg = Config(chunks_dir='"~/mydir"')
        self.assertTrue(cfg.chunks_dir.endswith("mydir"))
        self.assertFalse(cfg.chunks_dir.startswith('"'))


class ConfigMergedTest(unittest.TestCase):
    def test_fac_root_follows_default_chunks(self):
        cfg = Config(fac_root=r"D:\OldRAG")
        merged = cfg.merged({"fac_root": r"D:\NewRAG"})
        self.assertEqual(merged.fac_root, r"D:\NewRAG")
        self.assertEqual(Path(merged.chunks_dir), Path(r"D:\NewRAG") / DEFAULT_CHUNKS_SUBDIR)

    def test_custom_chunks_dir_survives_fac_root_change(self):
        cfg = Config(fac_root=r"D:\OldRAG", chunks_dir=r"E:\custom\chunks")
        merged = cfg.merged({"fac_root": r"D:\NewRAG"})
        self.assertEqual(merged.chunks_dir, r"E:\custom\chunks")

    def test_explicit_chunks_dir_wins(self):
        cfg = Config()
        merged = cfg.merged({"chunks_dir": r"E:\other\chunks", "port": 9001})
        self.assertEqual(merged.chunks_dir, r"E:\other\chunks")
        self.assertEqual(merged.port, 9001)


class ConfigValidateTest(unittest.TestCase):
    def test_missing_data_root(self):
        errors = Config().validate()
        self.assertTrue(any("未配置本地数据目录" in e for e in errors), errors)

    def test_missing_data_dir(self):
        with workspace_tmp() as tmp:
            cfg = Config(data_root=str(Path(tmp) / "missing"))
            self.assertTrue(any("本地数据目录不存在" in e for e in cfg.validate()))

    def test_incomplete_data_dir(self):
        with workspace_tmp() as tmp:
            data = Path(tmp) / "data"
            (data / "source").mkdir(parents=True)
            cfg = Config(data_root=str(data))
            self.assertTrue(any("不完整" in e for e in cfg.validate()))

    def test_ok_with_data_fixture(self):
        with workspace_tmp() as tmp:
            data = make_data_dir(Path(tmp))
            cfg = Config(data_root=str(data), port=8790)
            self.assertEqual(cfg.validate(), [])
            cfg.require_valid()

    def test_save_and_load(self):
        with workspace_tmp() as tmp:
            path = Path(tmp) / "config.json"
            data = make_data_dir(Path(tmp))
            cfg = Config(data_root=str(data), port=8899)
            saved = cfg.save(path)
            self.assertEqual(saved, path)
            loaded = Config.load(path)
            self.assertEqual(loaded.port, 8899)
            self.assertEqual(loaded.data_root, str(data))


class ConfigDataRootTest(unittest.TestCase):
    def test_relative_data_root_resolves_to_repo(self):
        from chunk_studio.config import REPO_ROOT

        cfg = Config(data_root="data")
        self.assertEqual(Path(cfg.data_root), Path(REPO_ROOT) / "data")

    def test_roundtrip_keeps_data_root(self):
        with workspace_tmp() as tmp:
            data = make_data_dir(Path(tmp))
            cfg = Config(data_root=str(data))
            back = Config.from_dict(cfg.to_dict())
            self.assertEqual(back.data_root, cfg.data_root)

    def test_data_available_detects_dirs(self):
        with workspace_tmp() as tmp:
            data = make_data_dir(Path(tmp))
            self.assertTrue(Config(data_root=str(data)).data_available())
            self.assertFalse(Config().data_available())
            self.assertFalse(Config(data_root=str(Path(tmp) / "nope")).data_available())

    def test_validate_ok_with_data_only(self):
        """FAC-RAG manifest 不可用、本地两目录可用时，配置也算有效。"""
        with workspace_tmp() as tmp:
            data = make_data_dir(Path(tmp))
            cfg = Config(
                fac_root=tmp,
                chunks_dir=str(Path(tmp) / "nope"),
                data_root=str(data),
            )
            self.assertEqual(cfg.validate(), [])

    def test_validate_reports_incomplete_data_dir(self):
        with workspace_tmp() as tmp:
            data = Path(tmp) / "data"
            (data / "source").mkdir(parents=True)
            cfg = Config(
                fac_root=tmp,
                chunks_dir=str(Path(tmp) / "nope"),
                data_root=str(data),
            )
            errors = cfg.validate()
            self.assertTrue(any("不完整" in e for e in errors), errors)

    def test_validate_missing_data_dir(self):
        with workspace_tmp() as tmp:
            cfg = Config(
                fac_root=tmp,
                chunks_dir=str(Path(tmp) / "nope"),
                data_root=str(Path(tmp) / "missing"),
            )
            self.assertTrue(any("本地数据目录不存在" in e for e in cfg.validate()))

    def test_merged_updates_data_root(self):
        cfg = Config(data_root="data")
        merged = cfg.merged({"data_root": "D:\\chunk-data"})
        self.assertEqual(merged.data_root, "D:\\chunk-data")


if __name__ == "__main__":
    unittest.main()
