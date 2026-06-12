#!/usr/bin/env python3
"""Fetch HiveFi data through a small shared CSV cache for Symphony workers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_CACHE_DIR = Path("/tmp/hivefi-strategy-data-cache")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cached wrapper around `hivefi-factory data fetch` for CSV data."
    )
    parser.add_argument("source_key", help="ClickHouse table name")
    parser.add_argument("--symbol", help="comma-separated symbols (legacy alias)")
    parser.add_argument("--symbols", nargs="+", help="space-separated symbols")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument(
        "--fields",
        default=None,
        help="legacy comma-separated fields; the last field is used as --value-col",
    )
    parser.add_argument("--time-col", default="time")
    parser.add_argument("--symbol-col", default="symbol")
    parser.add_argument("--value-col", default="close")
    parser.add_argument("--limit", default="0")
    parser.add_argument("--format", choices=["csv"], default="csv")
    parser.add_argument("--save", required=True, help="destination CSV path")
    parser.add_argument(
        "--cache-dir",
        default=os.environ.get("HIVEFI_DATA_CACHE_DIR", str(DEFAULT_CACHE_DIR)),
    )
    parser.add_argument("--refresh", action="store_true", help="ignore cached CSV")
    parser.add_argument("--lock-timeout", type=float, default=300.0)
    return parser.parse_args()


def _symbols(args: argparse.Namespace) -> list[str]:
    if args.symbols:
        return args.symbols
    if args.symbol:
        return [s.strip() for s in args.symbol.split(",") if s.strip()]
    return []


def _value_col(args: argparse.Namespace) -> str:
    if args.fields and args.value_col == "close":
        fields = [f.strip() for f in args.fields.split(",") if f.strip()]
        if fields:
            return fields[-1]
    return args.value_col


def _cache_key(args: argparse.Namespace) -> str:
    payload = {
        "version": 2,
        "source_key": args.source_key,
        "symbols": _symbols(args),
        "start": args.start,
        "end": args.end,
        "time_col": args.time_col,
        "symbol_col": args.symbol_col,
        "value_col": _value_col(args),
        "format": args.format,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:24]


def _copy_cached(cache_path: Path, save_path: Path) -> None:
    save_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cache_path, save_path)


def _acquire_lock(lock_dir: Path, cache_path: Path, args: argparse.Namespace) -> bool:
    deadline = time.monotonic() + args.lock_timeout
    while True:
        try:
            lock_dir.mkdir(parents=True)
            return True
        except FileExistsError:
            if cache_path.exists() and not args.refresh:
                return False
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for cache lock: {lock_dir}")
            time.sleep(0.25)


def _fetch_to_cache(cache_path: Path, args: argparse.Namespace) -> None:
    tmp_path = cache_path.with_suffix(f".{os.getpid()}.tmp")
    cmd = [
        "hivefi-factory",
        "data",
        "fetch",
        args.source_key,
        "--start",
        args.start,
        "--end",
        args.end,
        "--time-col",
        args.time_col,
        "--symbol-col",
        args.symbol_col,
        "--value-col",
        _value_col(args),
        "--output",
        str(tmp_path),
    ]
    symbols = _symbols(args)
    if symbols:
        cmd[4:4] = ["--symbols", *symbols]

    try:
        subprocess.run(cmd, check=True)
        if not tmp_path.exists():
            raise FileNotFoundError(f"hivefi-factory did not create {tmp_path}")
        os.replace(tmp_path, cache_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def main() -> int:
    args = _parse_args()
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{_cache_key(args)}.csv"
    save_path = Path(args.save)

    if cache_path.exists() and not args.refresh:
        _copy_cached(cache_path, save_path)
        print(f"cache hit: {cache_path} -> {save_path}")
        return 0

    lock_dir = cache_path.with_suffix(".lock")
    acquired = _acquire_lock(lock_dir, cache_path, args)
    if not acquired:
        _copy_cached(cache_path, save_path)
        print(f"cache hit after wait: {cache_path} -> {save_path}")
        return 0

    try:
        if cache_path.exists() and not args.refresh:
            print(f"cache hit after lock: {cache_path} -> {save_path}")
        else:
            _fetch_to_cache(cache_path, args)
            print(f"cache miss fetched: {cache_path}")
        _copy_cached(cache_path, save_path)
        return 0
    finally:
        try:
            lock_dir.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
