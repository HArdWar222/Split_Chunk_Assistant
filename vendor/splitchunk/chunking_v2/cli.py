# -*- coding: utf-8 -*-
"""``splitassistant-chunk`` 命令行入口。

SplitAssistant 前置自动切块用这个 CLI 最省事：

    splitassistant-chunk <file.md> [more.md ...] --out-dir <dir> --json

产物（与 SplitAssistant 侧约定的一一对应命名，可用 ``--*-suffix`` 改后缀）：

    <out-dir>/{stem}.chunks.jsonl     # 15 字段契约块，每行一个 JSON
    <out-dir>/{stem}.extras.jsonl     # {"id", "extras"}，检索/审核用的侧车
    <out-dir>/{stem}.report.json      # 决策日志 + gate + summary

接入友好的几个细节：

- 三个产物都**先写 ``.tmp`` 再 ``os.replace``**，目录监听方不会读到半个文件；
  ``report.json`` 最后落盘，可当「本次切块完成」的信号；
- ``--source-root`` 给出后，15 字段里的 ``source`` 写成相对该目录的 posix 路径
  （如 ``source/xx.md``），审核端能直接定位回原文；
- ``report.json`` 带 ``engine_version`` / ``source_sha256`` / ``source_relative``，
  工作台可据此判断「产物是否当前版本 / 源文件是否已改动」，不必依赖 mtime；
- ``--chunks-suffix/--extras-suffix/--report-suffix`` 用于对齐工作台已有的命名约定。
- ``--data-root <data>`` 一次对齐工作台布局：产物目录默认 ``<data>/chunks``、源目录默认
  ``<data>/source``；不给 inputs 时递归扫描 ``source/**/*.md``。自动扫描模式默认**增量**：
  ``report.json`` 的 ``engine_version`` + ``source_sha256`` 与当前一致就跳过，
  ``--force`` 强制重切。

退出码：

    0  全部成功且 gate 通过
    2  产物可用，但至少一份 gate 不通过（审核重点看 report.gate.failures）
    1  至少一份硬失败（文件不存在 / 编码不对 / profile 错 / 一块都没切出来）

``--json`` 时 stdout 输出一个 JSON 文档（含 files[] 与 summary），日志仍走 stderr。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import DEFAULT_PROFILES_DIR, __version__, chunk_file_run, load_profiles

STRATEGIES = ("auto", "profile")
EXIT_OK = 0
EXIT_FATAL = 1
EXIT_GATE = 2


def _group(name: str, profile: str, meta: dict[str, Any] | None = None) -> SimpleNamespace:
    """``SourceGroup`` 的鸭子类型替身（引擎只用这三个属性）。"""
    meta = dict(meta or {})
    return SimpleNamespace(
        name=name,
        profile=profile,
        meta_for=lambda *args, **kwargs: dict(meta),
        params_for=lambda *args, **kwargs: {},
    )


def _relative_source(target: Path, source_root: Path | None) -> str:
    """15 字段的 ``source``：相对 ``--source-root`` 的 posix 路径（不给则空串）。"""
    if source_root is None:
        return ""
    try:
        return target.resolve().relative_to(source_root).as_posix()
    except ValueError:
        return target.name


def _file_sha256(target: Path) -> str:
    """源文件字节 sha256（给工作台判断「源改没改」用）。"""
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    """同目录写 ``.tmp`` 再 ``os.replace``：目录监听方只会看到完整文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    _atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))
    return len(lines)


def _report_payload(
    target: Path,
    strategy: str,
    report: dict[str, Any],
    gate: dict[str, Any] | None,
    chunks: list[dict[str, Any]],
    *,
    source_relative: str,
    source_sha256: str,
) -> dict[str, Any]:
    """落盘用的 report.json（与沙盒 build_chunks 同构 + 接入用指纹字段）。"""
    gate = gate or {}
    decisions = list(report.get("decisions") or [])
    return {
        "engine_version": __version__,
        "source": str(target),
        "source_relative": source_relative,
        "source_sha256": source_sha256,
        "strategy": strategy,
        "profile": report.get("profile"),
        "probe": {
            "family": report.get("family"),
            "confidence": report.get("confidence"),
            "heading_mode": report.get("heading_mode"),
        },
        "decisions": decisions,
        "sizing": [item for item in decisions if item.get("region") == "sizing"],
        "fallback_chain": list(report.get("fallback_chain") or []),
        "n_chunks": len(chunks),
        "kinds": dict(Counter(str(chunk.get("chunk_kind") or "") for chunk in chunks)),
        "gate": {
            "ok": gate.get("ok"),
            "metrics": gate.get("metrics") or {},
            "failures": gate.get("failures") or [],
        },
        "summary": report.get("summary") or {},
        "warnings": list(report.get("warnings") or []),
    }


def _is_up_to_date(target: Path, out_dir: Path, report_suffix: str) -> bool:
    """``--data-root`` 自动扫描时的增量判定：版本 + 源文件字节指纹都一致才算最新。"""
    report_path = out_dir / f"{target.stem}{report_suffix}"
    if not report_path.is_file():
        return False
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        source_sha256 = _file_sha256(target)
    except Exception:  # noqa: BLE001 - 报告损坏/源读不到 → 当作需要重切
        return False
    return (
        payload.get("engine_version") == __version__
        and payload.get("source_sha256") == source_sha256
    )


def _process(
    target: Path,
    *,
    out_dir: Path,
    strategy: str,
    profile: str,
    name: str,
    profiles: dict[str, Any],
    source_root: Path | None,
    chunks_suffix: str,
    extras_suffix: str,
    report_suffix: str,
) -> dict[str, Any]:
    """切一份文件并落盘，返回该文件的摘要（不抛异常给调用方）。"""
    source_relative = _relative_source(target, source_root)
    try:
        source_sha256 = _file_sha256(target)
    except OSError:
        source_sha256 = ""
    meta = {"source": source_relative} if source_relative else {}
    try:
        record, extras, gate, report = chunk_file_run(
            target,
            group=_group(name, profile, meta),
            strategy=strategy,
            profiles=profiles,
        )
    except Exception as exc:  # noqa: BLE001 - CLI 边界统一转成 JSON 错误
        return {
            "file": str(target),
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "exit_code": EXIT_FATAL,
        }

    chunks = list(record.get("chunks") or [])
    report["summary"] = record.get("summary") or {}
    payload = _report_payload(
        target,
        strategy,
        report,
        gate,
        chunks,
        source_relative=source_relative,
        source_sha256=source_sha256,
    )

    stem = target.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks_path = out_dir / f"{stem}{chunks_suffix}"
    extras_path = out_dir / f"{stem}{extras_suffix}"
    report_path = out_dir / f"{stem}{report_suffix}"
    _write_jsonl(chunks_path, chunks)
    _write_jsonl(
        extras_path,
        [
            {"id": str(chunk["id"]), "extras": extras[str(chunk["id"])]}
            for chunk in chunks
            if str(chunk.get("id")) in extras
        ],
    )
    _atomic_write_text(report_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    gate_ok = bool((gate or {}).get("ok"))
    return {
        "file": str(target),
        "ok": True,
        "exit_code": EXIT_OK if gate_ok else EXIT_GATE,
        "gate_ok": gate_ok,
        "n_chunks": len(chunks),
        "kinds": payload["kinds"],
        "max_len": max((len(str(c.get("chunk") or "")) for c in chunks), default=0),
        "over_max": sum(1 for c in chunks if len(str(c.get("chunk") or "")) > 800),
        "profile": payload["profile"],
        "strategy": strategy,
        "source_relative": source_relative,
        "source_sha256": source_sha256,
        "gate_failures": payload["gate"]["failures"],
        "out": {
            "chunks": str(chunks_path),
            "extras": str(extras_path),
            "report": str(report_path),
        },
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="splitassistant-chunk",
        description="法规/标准 Markdown 自动切块（15 字段契约 + 表格一等公民）。",
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="待切分的 .md 文件路径（可多个）；配合 --data-root 可省略，自动扫描 <data-root>/source/**/*.md",
    )
    parser.add_argument(
        "--data-root",
        default=None,
        help="工作台 data 目录（内含 source/ 与 chunks/）；给出后自动推导 out-dir/source-root，可省略 inputs",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="产物目录，默认 ./chunks；给了 --data-root 时默认 <data-root>/chunks",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="配合 --data-root：即使 report.json 显示已是最新（版本+源指纹一致）也重切",
    )
    parser.add_argument(
        "--strategy",
        choices=STRATEGIES,
        default="auto",
        help="auto=逐区域按证据自动选切分单位（默认）；profile=按 probe 族选 handler",
    )
    parser.add_argument(
        "--profile-dir",
        default=None,
        help=f"profile YAML 目录，默认包内 {DEFAULT_PROFILES_DIR}",
    )
    parser.add_argument(
        "--profile",
        default="generic",
        help="group 使用的 profile 名，默认 generic（兜底档）",
    )
    parser.add_argument("--name", default="default", help="group 名，写进记录，默认 default")
    parser.add_argument(
        "--source-root",
        default=None,
        help="源文件根目录；给出时 15 字段 source 写成相对该目录的 posix 路径",
    )
    parser.add_argument(
        "--chunks-suffix",
        default=".chunks.jsonl",
        help="块文件名后缀，默认 .chunks.jsonl",
    )
    parser.add_argument(
        "--extras-suffix",
        default=".extras.jsonl",
        help="侧车文件名后缀，默认 .extras.jsonl",
    )
    parser.add_argument(
        "--report-suffix",
        default=".report.json",
        help="报告文件名后缀，默认 .report.json",
    )
    parser.add_argument("--json", action="store_true", help="stdout 输出单个 JSON 文档")
    parser.add_argument("--version", action="version", version=f"splitassistant-chunk {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        profiles = load_profiles(args.profile_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"profile 加载失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FATAL

    suffixes = (args.chunks_suffix, args.extras_suffix, args.report_suffix)
    if any((not suffix) or ("/" in suffix) or ("\\" in suffix) for suffix in suffixes):
        print("产物后缀不能为空，也不能包含路径分隔符", file=sys.stderr)
        return EXIT_FATAL

    data_root = Path(args.data_root).resolve() if args.data_root else None
    if data_root is not None and not data_root.is_dir():
        print(f"--data-root 不是目录：{data_root}", file=sys.stderr)
        return EXIT_FATAL
    if args.source_root:
        source_root = Path(args.source_root).resolve()
    elif data_root is not None:
        source_root = data_root / "source"
    else:
        source_root = None
    if source_root is not None and not source_root.is_dir():
        print(f"source 目录不存在：{source_root}", file=sys.stderr)
        return EXIT_FATAL
    if args.out_dir:
        out_dir = Path(args.out_dir).resolve()
    elif data_root is not None:
        out_dir = data_root / "chunks"
    else:
        out_dir = Path("chunks").resolve()

    targets = [Path(raw) for raw in args.inputs]
    if not targets:
        if source_root is None:
            print(
                "没有输入文件：请给 .md 路径，或用 --data-root <dir> 自动扫描 <data-root>/source/",
                file=sys.stderr,
            )
            return EXIT_FATAL
        targets = sorted(path for path in source_root.rglob("*.md") if path.is_file())
        if not targets:
            print(f"source 目录下没有 .md：{source_root}", file=sys.stderr)
            return EXIT_FATAL
        by_stem: dict[str, list[Path]] = {}
        for path in targets:
            by_stem.setdefault(path.stem, []).append(path)
        duplicated = {stem: paths for stem, paths in by_stem.items() if len(paths) > 1}
        if duplicated:
            print(
                f"source 里存在同名文件（产物会互相覆盖），请先改名或分目录：{duplicated}",
                file=sys.stderr,
            )
            return EXIT_FATAL

    results: list[dict[str, Any]] = []
    for target in targets:
        if data_root is not None and not args.force and _is_up_to_date(
            target, out_dir, args.report_suffix
        ):
            results.append(
                {
                    "file": str(target),
                    "ok": True,
                    "skipped": True,
                    "reason": "up-to-date",
                    "exit_code": EXIT_OK,
                }
            )
            continue
        results.append(
            _process(
                target,
                out_dir=out_dir,
                strategy=args.strategy,
                profile=args.profile,
                name=args.name,
                profiles=profiles,
                source_root=source_root,
                chunks_suffix=args.chunks_suffix,
                extras_suffix=args.extras_suffix,
                report_suffix=args.report_suffix,
            )
        )

    hard_failures = [item for item in results if not item["ok"]]
    gate_failures = [
        item for item in results if item["ok"] and not item.get("skipped") and not item["gate_ok"]
    ]
    skipped = [item for item in results if item.get("skipped")]
    exit_code = EXIT_FATAL if hard_failures else (EXIT_GATE if gate_failures else EXIT_OK)

    summary = {
        "version": __version__,
        "strategy": args.strategy,
        "out_dir": str(out_dir),
        "n_files": len(results),
        "n_processed": len(results) - len(skipped),
        "n_skipped": len(skipped),
        "n_chunks": sum(
            int(item.get("n_chunks") or 0) for item in results if item["ok"] and not item.get("skipped")
        ),
        "n_hard_failures": len(hard_failures),
        "n_gate_failures": len(gate_failures),
        "exit_code": exit_code,
    }

    if args.json:
        print(json.dumps({"summary": summary, "files": results}, ensure_ascii=False))
    else:
        for item in results:
            if not item["ok"]:
                print(f"FAIL {item['file']}: {item['error']}", file=sys.stderr)
                continue
            if item.get("skipped"):
                print(f"SKIP {item['file']}: {item.get('reason')}")
                continue
            gate = "ok" if item["gate_ok"] else "FAILED"
            print(
                f"{item['file']}: chunks={item['n_chunks']} kinds={item['kinds']} "
                f"max_len={item['max_len']} over800={item['over_max']} gate={gate}"
            )
            for path in item["out"].values():
                print(f"  out: {path}")
        print(f"summary: {json.dumps(summary, ensure_ascii=False)}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
