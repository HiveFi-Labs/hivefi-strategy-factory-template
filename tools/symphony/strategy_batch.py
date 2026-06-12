#!/usr/bin/env python3
"""Batch validation helpers for HiveFi strategy production.

Catches mechanical failures that become expensive when Symphony generates many
strategy files in one run:

- config / extension pair drift
- config strategy_id mismatches
- forbidden imports and high-risk AST nodes (delegated to ``hivefi_factory.validator``)
- runtime exceptions in compute_signals on synthetic price data

The AST denylist is owned by ``hivefi_factory.validator`` so that this script,
the ``hivefi-factory validate`` CLI subcommand, and the server-side validator
in bot-2509 stay aligned.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import re
import subprocess
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hivefi_factory.validator import validate_file  # noqa: E402

CONFIGS_DIR = REPO_ROOT / "configs"
EXTENSIONS_DIR = REPO_ROOT / "extensions"


@dataclass
class StrategyCheck:
    strategy_id: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    signal_count: int | None = None
    gross_exposure: float | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


class Signal:
    def __init__(self, symbol: str, side: str, percentage: float, time: str):
        self.symbol = symbol
        self.side = side
        self.percentage = percentage
        self.time = time


class StrategyContext:
    def __init__(self, data: dict[str, pd.DataFrame], date: dt.date):
        self.data = data
        self.date = date


class StrategyV2:
    pass


def _install_core_stubs() -> None:
    core = types.ModuleType("core")
    base = types.ModuleType("core.base")
    context = types.ModuleType("core.context")
    base.StrategyV2 = StrategyV2
    context.Signal = Signal
    context.StrategyContext = StrategyContext
    sys.modules["core"] = core
    sys.modules["core.base"] = base
    sys.modules["core.context"] = context


def _strategy_ids_from_files() -> set[str]:
    config_ids = {path.stem for path in CONFIGS_DIR.glob("*.json")}
    extension_ids = {path.stem for path in EXTENSIONS_DIR.glob("*.py")}
    return config_ids | extension_ids


def _changed_strategy_ids() -> set[str]:
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    ids: set[str] = set()
    for raw_line in completed.stdout.splitlines():
        path = raw_line[3:].strip()
        if path.startswith("configs/") and path.endswith(".json"):
            ids.add(Path(path).stem)
        if path.startswith("extensions/") and path.endswith(".py"):
            ids.add(Path(path).stem)
    return ids


def _load_config(strategy_id: str, check: StrategyCheck) -> dict[str, Any] | None:
    path = CONFIGS_DIR / f"{strategy_id}.json"
    if not path.exists():
        check.errors.append(f"missing config: {path.relative_to(REPO_ROOT)}")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        check.errors.append(f"invalid JSON config: {exc}")
        return None
    if data.get("strategy_id") != strategy_id:
        check.errors.append(
            f"config strategy_id={data.get('strategy_id')!r} does not match {strategy_id!r}"
        )
    for key in (
        "title",
        "description",
        "exchange",
        "universe",
        "rebalance_freq",
        "rebalance_enabled",
        "auto_close_missing",
        "warmup_periods",
    ):
        if key not in data:
            check.warnings.append(f"config missing recommended key: {key}")
    return data


def _check_extension_ast(strategy_id: str, check: StrategyCheck) -> bool:
    path = EXTENSIONS_DIR / f"{strategy_id}.py"
    if not path.exists():
        check.errors.append(f"missing extension: {path.relative_to(REPO_ROOT)}")
        return False
    report = validate_file(path)
    if not report.ok:
        for v in report.violations:
            check.errors.append(v)
        return False
    return True


def _synthetic_price(days: int = 220) -> pd.DataFrame:
    rng = np.random.default_rng(20260419)
    symbols = [
        "BTC", "ETH", "SOL", "AVAX", "BNB", "ARB", "OP", "MATIC", "DOGE", "XRP",
        "LINK", "AAVE", "UNI", "SUI", "INJ", "TIA", "SEI", "NEAR", "APT", "LTC",
    ]
    common = rng.normal(0.0004, 0.018, size=(days, 1))
    idiosyncratic = rng.normal(0.0002, 0.035, size=(days, len(symbols)))
    returns = common + idiosyncratic
    return pd.DataFrame(100.0 * np.exp(np.cumsum(returns, axis=0)), columns=symbols)


def _load_strategy_module(strategy_id: str):
    path = EXTENSIONS_DIR / f"{strategy_id}.py"
    module_name = "hivefi_strategy_" + re.sub(r"[^0-9a-zA-Z_]", "_", strategy_id)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _smoke_compute_signals(strategy_id: str, check: StrategyCheck) -> None:
    _install_core_stubs()
    module = _load_strategy_module(strategy_id)
    strategy_cls = getattr(module, "Strategy", None)
    if strategy_cls is None:
        check.errors.append("extension does not define class Strategy")
        return

    ctx = StrategyContext({"price": _synthetic_price()}, dt.date(2026, 4, 17))
    signals = strategy_cls().compute_signals(ctx)
    if signals is None:
        check.errors.append("compute_signals returned None")
        return
    if not isinstance(signals, list):
        check.errors.append(f"compute_signals returned {type(signals).__name__}, expected list")
        return

    gross = 0.0
    for index, signal in enumerate(signals):
        for attr in ("symbol", "side", "percentage", "time"):
            if not hasattr(signal, attr):
                check.errors.append(f"signal {index} missing attribute {attr}")
                return
        if signal.side not in {"buy", "sell"}:
            check.errors.append(f"signal {index} has invalid side {signal.side!r}")
        try:
            pct = float(signal.percentage)
        except (TypeError, ValueError):
            check.errors.append(f"signal {index} has non-numeric percentage")
            continue
        if pct <= 0.0:
            check.errors.append(f"signal {index} has non-positive percentage {pct}")
        gross += pct

    check.signal_count = len(signals)
    check.gross_exposure = gross
    if signals and gross > 1.000001:
        check.errors.append(f"gross exposure exceeds 1.0: {gross:.6f}")
    if not signals:
        check.warnings.append("synthetic smoke produced no signals")


def check_strategy(strategy_id: str, *, smoke: bool) -> StrategyCheck:
    check = StrategyCheck(strategy_id=strategy_id)
    _load_config(strategy_id, check)
    ast_ok = _check_extension_ast(strategy_id, check)
    if smoke and ast_ok and not check.errors:
        try:
            _smoke_compute_signals(strategy_id, check)
        except Exception as exc:  # noqa: BLE001 - report strategy runtime failures.
            check.errors.append(f"smoke exception: {exc.__class__.__name__}: {exc}")
    return check


def _print_report(checks: list[StrategyCheck]) -> None:
    ok_count = sum(1 for check in checks if check.ok)
    print(f"strategy batch validation: {ok_count}/{len(checks)} passed")
    for check in checks:
        status = "OK" if check.ok else "FAIL"
        smoke = ""
        if check.signal_count is not None and check.gross_exposure is not None:
            smoke = f" signals={check.signal_count} gross={check.gross_exposure:.4f}"
        print(f"[{status}] {check.strategy_id}{smoke}")
        for error in check.errors:
            print(f"  error: {error}")
        for warning in check.warnings:
            print(f"  warning: {warning}")


def _json_report(checks: list[StrategyCheck]) -> str:
    payload = [
        {
            "strategy_id": check.strategy_id,
            "ok": check.ok,
            "errors": check.errors,
            "warnings": check.warnings,
            "signal_count": check.signal_count,
            "gross_exposure": check.gross_exposure,
        }
        for check in checks
    ]
    return json.dumps(payload, indent=2, sort_keys=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strategy-id",
        action="append",
        dest="strategy_ids",
        help="strategy id to validate; repeatable. Defaults to all strategies.",
    )
    parser.add_argument(
        "--changed",
        action="store_true",
        help="validate only strategies changed in git status.",
    )
    parser.add_argument(
        "--no-smoke",
        action="store_true",
        help="skip synthetic compute_signals smoke tests.",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON report.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.strategy_ids:
        strategy_ids = set(args.strategy_ids)
    elif args.changed:
        strategy_ids = _changed_strategy_ids()
    else:
        strategy_ids = _strategy_ids_from_files()

    if not strategy_ids:
        print("no strategies selected", file=sys.stderr)
        return 2

    checks = [
        check_strategy(strategy_id, smoke=not args.no_smoke)
        for strategy_id in sorted(strategy_ids)
    ]
    if args.json:
        print(_json_report(checks))
    else:
        _print_report(checks)
    return 0 if all(check.ok for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
