"""Local pre-flight smoke test for strategy compute_signals.

Stage 1 (signal_gen) Worker で実際に走る前に、ローカルで synthetic price data を
作って `extensions/<id>.py` の `Strategy.compute_signals` を 1 リバランス分だけ
呼び、戻り値の型 / 属性 / gross_exposure を検査する。

Method A (pipeline) 戦略は config の `pipeline` で完結し extensions は
プレースホルダーなので smoke 対象外として "pipeline" mode を返す。

`tools/symphony/strategy_batch.py` の `_smoke_compute_signals` を CLI 側に
移植・整理したもの。AST denylist は `validator.validate_file` で別途検査される
前提。
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import re
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "configs"
EXTENSIONS_DIR = REPO_ROOT / "extensions"


@dataclass
class SmokeResult:
    strategy_id: str
    mode: str = "compute_signals"
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    signal_count: int | None = None
    gross_exposure: float | None = None

    def fail(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)


class _Signal:
    def __init__(self, symbol: str, side: str, percentage: float, time: str) -> None:
        self.symbol = symbol
        self.side = side
        self.percentage = percentage
        self.time = time


class _StrategyContext:
    def __init__(self, data: dict[str, pd.DataFrame], date: dt.date, params: dict[str, Any] | None = None) -> None:
        self.data = data
        self.date = date
        self.params = params or {}


class _StrategyV2:
    pass


_CORE_STUBS_INSTALLED = False


def _install_core_stubs() -> None:
    global _CORE_STUBS_INSTALLED
    if _CORE_STUBS_INSTALLED:
        return
    core = types.ModuleType("core")
    base = types.ModuleType("core.base")
    context = types.ModuleType("core.context")
    base.StrategyV2 = _StrategyV2
    context.Signal = _Signal
    context.StrategyContext = _StrategyContext
    sys.modules.setdefault("core", core)
    sys.modules.setdefault("core.base", base)
    sys.modules.setdefault("core.context", context)
    _CORE_STUBS_INSTALLED = True


def _synthetic_price(days: int = 220) -> pd.DataFrame:
    """共通成分 + idiosyncratic な log-return から 220 日 × 20 銘柄のパネル."""
    rng = np.random.default_rng(20260419)
    symbols = [
        "BTC", "ETH", "SOL", "AVAX", "BNB", "ARB", "OP", "MATIC", "DOGE", "XRP",
        "LINK", "AAVE", "UNI", "SUI", "INJ", "TIA", "SEI", "NEAR", "APT", "LTC",
    ]
    common = rng.normal(0.0004, 0.018, size=(days, 1))
    idiosyncratic = rng.normal(0.0002, 0.035, size=(days, len(symbols)))
    returns = common + idiosyncratic
    return pd.DataFrame(
        100.0 * np.exp(np.cumsum(returns, axis=0)),
        columns=symbols,
    )


def _synthetic_data(data_requirements: list[str]) -> dict[str, pd.DataFrame]:
    """data_requirements にある全 key に対して price 同様の synthetic panel を供給.

    price / spot_price は同一銘柄群、その他 (oi, funding, chain_fees 等) も
    同じ形の panel を渡しておく。スケールが正の値である前提だが、極端な値の
    sanity check は smoke のスコープ外。
    """
    panel = _synthetic_price()
    return {key: panel.copy() for key in data_requirements or ["price"]}


def _load_strategy_module(strategy_id: str) -> types.ModuleType:
    path = EXTENSIONS_DIR / f"{strategy_id}.py"
    module_name = "hivefi_smoke_" + re.sub(r"[^0-9a-zA-Z_]", "_", strategy_id)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_smoke(strategy_id: str) -> SmokeResult:
    """1 戦略の smoke を走らせて SmokeResult を返す.

    - config に `pipeline` キーがあれば Method A 扱いで `mode="pipeline"` を返し
      compute_signals 呼出は skip (戦略コードはプレースホルダーのはず)。
    - それ以外は extensions/<id>.py を import し、synthetic data で 1 回
      compute_signals を呼んで signals list を検査する。
    """
    result = SmokeResult(strategy_id=strategy_id)

    config_path = CONFIGS_DIR / f"{strategy_id}.json"
    if not config_path.exists():
        result.fail(f"config not found: configs/{strategy_id}.json")
        return result
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        result.fail(f"config JSON parse failed: {exc}")
        return result

    ext_path = EXTENSIONS_DIR / f"{strategy_id}.py"
    if not ext_path.exists():
        result.fail(f"extension not found: extensions/{strategy_id}.py")
        return result

    if isinstance(config.get("pipeline"), list) and config["pipeline"]:
        result.mode = "pipeline"
        result.warnings.append(
            "Method A pipeline strategy; compute_signals smoke skipped "
            "(server orchestrator runs pipeline; code is a placeholder)"
        )
        return result

    _install_core_stubs()
    try:
        module = _load_strategy_module(strategy_id)
    except SyntaxError as exc:
        result.fail(f"SyntaxError loading extension: {exc}")
        return result
    except Exception as exc:  # noqa: BLE001 - report import-time failures
        result.fail(f"import failed: {exc.__class__.__name__}: {exc}")
        return result

    strategy_cls = getattr(module, "Strategy", None)
    if strategy_cls is None:
        result.fail("extension does not define class Strategy")
        return result

    data_requirements = getattr(strategy_cls, "data_requirements", ["price"])
    if not isinstance(data_requirements, list):
        data_requirements = ["price"]

    ctx = _StrategyContext(
        data=_synthetic_data(data_requirements),
        date=dt.date(2026, 4, 17),
        params=config.get("params", {}),
    )

    try:
        strategy_obj = strategy_cls()
    except Exception as exc:  # noqa: BLE001 - ctor errors are smoke failures
        result.fail(f"Strategy() init failed: {exc.__class__.__name__}: {exc}")
        return result

    if not hasattr(strategy_obj, "compute_signals"):
        result.fail("Strategy has no compute_signals method")
        return result

    try:
        signals = strategy_obj.compute_signals(ctx)
    except Exception as exc:  # noqa: BLE001 - compute exceptions are smoke failures
        result.fail(
            f"compute_signals raised {exc.__class__.__name__}: {exc}"
        )
        return result

    if signals is None:
        result.fail("compute_signals returned None (must return list[Signal])")
        return result
    if not isinstance(signals, list):
        result.fail(
            f"compute_signals returned {type(signals).__name__}, expected list"
        )
        return result

    gross = 0.0
    for i, sig in enumerate(signals):
        for attr in ("symbol", "side", "percentage", "time"):
            if not hasattr(sig, attr):
                result.fail(f"signal[{i}] missing attribute {attr!r}")
                return result
        if sig.side not in {"buy", "sell"}:
            result.fail(f"signal[{i}].side = {sig.side!r}, must be 'buy' or 'sell'")
        try:
            pct = float(sig.percentage)
        except (TypeError, ValueError):
            result.fail(f"signal[{i}].percentage is not numeric: {sig.percentage!r}")
            continue
        if pct <= 0.0:
            result.fail(f"signal[{i}].percentage = {pct} (must be > 0)")
        gross += pct

    result.signal_count = len(signals)
    result.gross_exposure = gross
    if signals and gross > 1.000001:
        result.fail(f"gross exposure {gross:.6f} exceeds 1.0")
    if not signals:
        result.warnings.append(
            "no signals produced on synthetic data "
            "(may be correct for filter-heavy strategies; check on real data)"
        )

    return result


def format_result(result: SmokeResult) -> str:
    tag = "OK" if result.ok else "FAIL"
    parts = [f"[{tag}]  {result.strategy_id}  mode={result.mode}"]
    if result.signal_count is not None:
        parts.append(
            f"signals={result.signal_count} gross={result.gross_exposure:.4f}"
        )
    line = "  ".join(parts)
    extra = []
    for err in result.errors:
        extra.append(f"       error: {err}")
    for warn in result.warnings:
        extra.append(f"       warn:  {warn}")
    return "\n".join([line, *extra])
