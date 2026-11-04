"""测试用工具：在临时目录里造一份最小的 chunking v2 产物。

注意：系统临时目录（%TEMP%）在本机被沙箱限制，不能写；
所以临时目录统一建在仓库内 ``tests/_tmp/``（已 gitignore）。
"""

from __future__ import annotations

import json
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

TMP_ROOT = Path(__file__).resolve().parent / "_tmp"


def temp_dir() -> Path:
    """在仓库内建一个临时目录（可写），返回其 Path。

    不用 tempfile.mkdtemp：本机沙箱下 mkdtemp 建出的目录再建子目录会 WinError 5，
    改成普通 mkdir + 随机名即可。
    """
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = TMP_ROOT / ("t-" + uuid.uuid4().hex[:10])
    path.mkdir()
    return path


def remove(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


@contextmanager
def workspace_tmp():
    d = temp_dir()
    try:
        yield d
    finally:
        remove(d)


def make_chunks_dir(base: Path) -> Path:
    """创建最小产物目录：2 个文件、3 个块、extras 侧车齐全。"""
    chunks_dir = base / "chunks_v2"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    src1 = base / "national_laws" / "测试法.md"
    src1.parent.mkdir(parents=True, exist_ok=True)
    src1.write_text("# 测试法\n\n第一条 为了测试。\n\n第二条 测试结束。\n", encoding="utf-8")

    src2 = base / "GB_laws" / "GB 0001-2020 测试标准.md"
    src2.parent.mkdir(parents=True, exist_ok=True)
    src2.write_text("# 测试标准\n\n1 范围\n本文件规定了测试要求。\n", encoding="utf-8")

    chunks1 = [
        {
            "id": "测试法-0001",
            "chunk": "第一条 为了测试。",
            "law_name": "测试法",
            "law_num": "第一条",
            "law_invalid": "2030-12-12",
            "law_type": "法律",
            "law_level": "法律",
            "issuer": "全国人民代表大会常务委员会",
            "effective_date": "",
            "chunk_kind": "clause",
            "token_len": 9,
            "source": "法律法规",
            "part_index": 1,
            "part_total": 1,
            "chunk_hash": "aaaaaaaaaaaaaaaa",
        },
        {
            "id": "测试法-0002",
            "chunk": "第二条 测试结束。",
            "law_name": "测试法",
            "law_num": "第二条",
            "law_invalid": "2030-12-12",
            "law_type": "法律",
            "law_level": "法律",
            "issuer": "全国人民代表大会常务委员会",
            "effective_date": "",
            "chunk_kind": "clause",
            "token_len": 9,
            "source": "法律法规",
            "part_index": 1,
            "part_total": 1,
            "chunk_hash": "bbbbbbbbbbbbbbbb",
        },
    ]
    chunks2 = [
        {
            "id": "GB 0001-2020 测试标准-0001",
            "chunk": "1 范围\n本文件规定了测试要求。",
            "law_name": "GB 0001-2020 测试标准",
            "law_num": "1",
            "law_invalid": "2030-12-12",
            "law_type": "国家标准",
            "law_level": "GB",
            "issuer": "国家市场监督管理总局、国家标准化管理委员会",
            "effective_date": "",
            "chunk_kind": "clause",
            "token_len": 16,
            "source": "GB",
            "part_index": 1,
            "part_total": 1,
            "chunk_hash": "cccccccccccccccc",
        },
    ]

    jsonl1 = "national_laws__测试法.jsonl"
    jsonl2 = "GB_laws__GB 0001-2020 测试标准.jsonl"
    _write_jsonl(chunks_dir / jsonl1, chunks1)
    _write_jsonl(chunks_dir / jsonl2, chunks2)

    extras = [
        {"id": "测试法-0001", "extras": {"region": "body", "profile": "statute_cn_law"}},
        {"id": "GB 0001-2020 测试标准-0001", "extras": {"region": "body", "probe": {"family": "standard"}}},
    ]
    _write_jsonl(chunks_dir / "_extras.jsonl", extras)

    manifest = {
        "generated_at": "2026-11-02T23:55:11",
        "engine": "chunking_v2",
        "workspace": str(base),
        "fac_root": str(base.parent),
        "gate": {"ok": True, "n_checked": 2},
        "totals": {"n_files": 2, "n_chunks": 3, "kinds": {"clause": 3}, "char_coverage": 0.9123},
        "files": [
            {
                "key": "national_laws/测试法",
                "group": "national_laws",
                "law_name": "测试法",
                "path": str(src1),
                "jsonl": jsonl1,
                "n_chunks": 2,
                "sha256": "a" * 64,
                "sanity": {"n_chunks": 2, "n_law_num_parseable": 2, "law_num_ratio": 1.0},
                "summary": {"kinds": {"clause": 2}, "char_coverage": 0.9},
                "gate": {"ok": True, "failures": []},
                "warnings": [],
                "extras_count": 1,
            },
            {
                "key": "GB_laws/GB 0001-2020 测试标准",
                "group": "GB_laws",
                "law_name": "GB 0001-2020 测试标准",
                "path": str(src2),
                "jsonl": jsonl2,
                "n_chunks": 1,
                "sha256": "b" * 64,
                "sanity": {"n_chunks": 1},
                "summary": {"kinds": {"clause": 1}, "char_coverage": 0.95},
                "gate": {"ok": True, "failures": []},
                "warnings": ["测试警告"],
                "extras_count": 1,
            },
        ],
    }
    (chunks_dir / "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return chunks_dir


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    path.write_text(text, encoding="utf-8")


def make_data_dir(base: Path, extras_style: str = "flat") -> Path:
    """创建本地「两目录」数据源：``<base>/data/source`` + ``<base>/data/chunks``。

    刻意模拟真实切块包：chunk 只有 14 字段（**没有 token_len**），
    part_index/part_total 是字符串；extras_style 控制侧车格式：
    ``flat``（字段平铺）或 ``nested``（``{"id": …, "extras": {…}}``，FAC-RAG 写法）。
    """
    data_root = base / "data"
    source_dir = data_root / "source"
    chunks_dir = data_root / "chunks"
    source_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir.mkdir(parents=True, exist_ok=True)

    name = "测试包法规"
    (source_dir / (name + ".md")).write_text(
        "# 测试包法规\n\n第一条 为测试本地包。\n\n第二条 本地包结束。\n",
        encoding="utf-8",
    )

    chunks = [
        {
            "id": name + "-0001",
            "chunk": "第一条 为测试本地包。",
            "law_name": name,
            "law_num": "第一条",
            "law_invalid": "",
            "law_type": "",
            "law_level": "",
            "issuer": "",
            "effective_date": "",
            "chunk_kind": "clause",
            "source": "",
            "part_index": "1",
            "part_total": "1",
            "chunk_hash": "d" * 16,
        },
        {
            "id": name + "-0002",
            "chunk": "第二条 本地包结束。",
            "law_name": name,
            "law_num": "第二条",
            "law_invalid": "",
            "law_type": "",
            "law_level": "",
            "issuer": "",
            "effective_date": "",
            "chunk_kind": "clause",
            "source": "",
            "part_index": "1",
            "part_total": "1",
            "chunk_hash": "e" * 16,
        },
    ]
    _write_jsonl(chunks_dir / (name + ".chunks.jsonl"), chunks)
    flat_extras = [
        {"id": name + "-0001", "table_label": "", "region": "body", "clause": "第一条"},
        {"id": name + "-0002", "table_label": "", "region": "body", "clause": "第二条"},
    ]
    if extras_style == "nested":
        extras = [
            {
                "id": row["id"],
                "extras": {k: v for k, v in row.items() if k != "id"},
            }
            for row in flat_extras
        ]
    else:
        extras = flat_extras
    _write_jsonl(chunks_dir / (name + ".extras.jsonl"), extras)
    return data_root

