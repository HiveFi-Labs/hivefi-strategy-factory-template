#!/usr/bin/env python3
"""Generate katsustats performance reports from a HiveFi BT run.

Pulls equity timeseries via ``hivefi bt timeseries``, derives daily returns,
optionally fetches a benchmark symbol via ``hivefi data fetch``, then writes
a self-contained HTML tearsheet, an LLM-friendly JSON report, and a Markdown
summary into ``artifacts/katsustats/<label>/``.

Designed to run after ``hivefi bt run`` succeeds, or as the final step in
``/backtest-diag``. Not a strategy extension and therefore is allowed to use
``subprocess``, ``pathlib`` etc. — these would be forbidden inside a strategy
``compute_signals`` body.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARK_SOURCE = "binance_spot_price_1d"
MIN_DAILY_ROWS = 30


_ANNOTATION_STYLE_LIGHT = """
        .metric-pos { color: #2e7d32; font-weight: 700; }
        .metric-neg { color: #c62828; font-weight: 700; }
        .metric-warn { color: #ef6c00; font-weight: 700; }
        .analysis { background: #f3f8ff; border-left: 4px solid #2563eb;
            padding: 12px 16px; margin: 8px 0 18px 0; border-radius: 4px;
            font-size: 0.95em; color: #1d2939; min-height: 1.4em; }
        .analysis::before { content: "考察"; display: block;
            font-weight: 700; color: #1a4480; font-size: 0.85em;
            text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 6px; }
        .analysis:empty::after { content: "戦略固有の観察をここに書く"
            " (色付けは <span class=metric-pos>+138%</span> / metric-neg / metric-warn)";
            color: #94a3b8; font-style: italic; font-size: 0.9em; }
"""

_DARK_THEME_STYLE = """
        :root {
            --bg: #15181d !important;
            --surface: #1f232a !important;
            --surface2: #272c34 !important;
            --border: #3a4049 !important;
            --text: #e6e8eb !important;
            --text2: #9aa0a6 !important;
            --accent: #6aa6ff !important;
            --accent2: #36d6b6 !important;
            --positive: #5fdb8a !important;
            --negative: #ff7676 !important;
        }
        body { color-scheme: dark; }
        img.chart-img { background: #1f232a; }
        .metric-pos { color: #5fdb8a !important; font-weight: 700; }
        .metric-neg { color: #ff7676 !important; font-weight: 700; }
        .metric-warn { color: #f0b400 !important; font-weight: 700; }
        .analysis { background: #1c2735; border-left: 4px solid #6aa6ff;
            color: #d4dce6; }
        .analysis::before { color: #82b2f3; }
        .analysis:empty::after { color: #5b6577; }
"""

_SECTION_TITLES = (
    "Cumulative Returns",
    "Daily Return Distribution",
    "Day-of-Week Analysis",
    "Day-of-Week Statistics",
    "Key Performance",
    "Monthly Returns Heatmap",
    "Regime Analysis",
    "Rolling Sharpe",
    "Rolling Volatility",
    "Top Drawdowns",
)


# Mapping of metric name → rule name. Each rule colours ONLY values at the
# extreme ends of the typical range — neutral / middle-ground values stay
# uncoloured so the eye is drawn to actually-noteworthy numbers.
_METRIC_RULES: dict[str, str] = {
    "Total Return": "tr",
    "CAGR": "cagr",
    "Alpha": "alpha",
    "Excess Return": "ex_return",
    "Sharpe Ratio": "sr",
    "Sortino Ratio": "sortino",
    "Calmar Ratio": "calmar",
    "Information Ratio": "ir",
    "Profit Factor": "pf",
    "Win Rate": "wr",
    "Max Drawdown": "drawdown",
    "Worst Day": "loss",
    "Worst Month": "loss",
    "Worst Year": "loss",
    "Daily VaR (95%)": "loss",
    "CVaR (95%)": "loss",
    "Avg Loss": "loss",
    "Best Day": "gain",
    "Best Month": "gain",
    "Best Year": "gain",
    "Avg Win": "gain",
    "Volatility (ann.)": "vol",
    "Volatility": "vol",
    "Skewness": "skew",
    "Kurtosis": "kurt",
    "Beta": "beta",
    "Correlation": "corr",
}


def _parse_metric_value(raw: str) -> float | None:
    text = raw.strip().replace(",", "")
    if not text or text in {"—", "-", "n/a", "N/A"}:
        return None
    try:
        if text.endswith("%"):
            return float(text[:-1]) / 100
        return float(text)
    except ValueError:
        return None


def _classify_metric(rule: str, value: float) -> str | None:
    """Return colour class only for values worth highlighting; None elsewhere."""
    if rule == "tr":
        if value > 1.0:
            return "pos"
        if value < -0.3:
            return "neg"
        return None
    if rule == "cagr":
        if value > 0.3:
            return "pos"
        if value < -0.1:
            return "neg"
        return None
    if rule == "alpha":
        if value > 0.2:
            return "pos"
        if value < -0.1:
            return "neg"
        return None
    if rule == "ex_return":
        if value > 0.5:
            return "pos"
        if value < -0.5:
            return "neg"
        return None
    if rule == "sr":
        if value > 1.0:
            return "pos"
        if value < -0.3:
            return "neg"
        return None
    if rule == "sortino":
        if value > 1.5:
            return "pos"
        if value < -0.3:
            return "neg"
        return None
    if rule == "calmar":
        if value > 1.0:
            return "pos"
        if value < 0:
            return "neg"
        return None
    if rule == "ir":
        if value > 0.5:
            return "pos"
        if value < -0.5:
            return "neg"
        return None
    if rule == "pf":
        if value > 1.5:
            return "pos"
        if value < 0.9:
            return "neg"
        return None
    if rule == "wr":
        if value > 0.55:
            return "pos"
        if value < 0.45:
            return "neg"
        return None
    if rule == "drawdown":
        if value < -0.8:
            return "neg"
        if value < -0.5:
            return "warn"
        return None
    if rule == "loss":
        if value < -0.2:
            return "neg"
        if value < -0.1:
            return "warn"
        return None
    if rule == "gain":
        if value > 0.5:
            return "pos"
        return None
    if rule == "vol":
        if value > 1.0:
            return "neg"
        if value > 0.6:
            return "warn"
        return None
    if rule == "skew":
        if value > 0.5:
            return "pos"
        if value < -0.5:
            return "neg"
        return None
    if rule == "kurt":
        if value > 10:
            return "neg"
        if value > 5:
            return "warn"
        return None
    if rule == "beta":
        if abs(value) > 1.5:
            return "warn"
        if abs(value) < 0.1:
            return "warn"
        return None
    if rule == "corr":
        if abs(value) > 0.7:
            return "warn"
        if abs(value) < 0.1:
            return "warn"
        return None
    return None


_TR_RE = re.compile(r"<tr>(?P<body>(?:<t[dh]>[^<]*</t[dh]>\s*)+)</tr>")
_CELL_RE = re.compile(r"<(?P<tag>t[dh])>(?P<inner>[^<]*)</(?P=tag)>")


def _color_html_tables(html: str) -> str:
    """Wrap value cells in named-metric rows with metric-pos|neg|warn spans."""

    def color_row(match: re.Match[str]) -> str:
        body = match.group("body")
        cells = list(_CELL_RE.finditer(body))
        if not cells or cells[0].group("tag") != "td":
            return match.group(0)
        first = cells[0].group("inner").strip()
        rule = _METRIC_RULES.get(first)
        if rule is None:
            return match.group(0)
        new_body = body
        for cell in cells[1:]:
            inner = cell.group("inner")
            value = _parse_metric_value(inner)
            if value is None:
                continue
            cls = _classify_metric(rule, value)
            if cls is None:
                continue
            new_cell = (
                f"<td><span class=\"metric-{cls}\">{inner.strip()}</span></td>"
            )
            new_body = new_body.replace(cell.group(0), new_cell, 1)
        if new_body == body:
            return match.group(0)
        return f"<tr>{new_body}</tr>"

    return _TR_RE.sub(color_row, html)


def _annotate_html(html: str, *, dark: bool = False, annotate: bool = True) -> str:
    """Inject empty analysis placeholders + colour helper CSS.

    ``annotate`` only adds ``.metric-pos|neg|warn`` colour classes and an empty
    ``<div class="analysis"></div>`` placeholder after each known ``<h2>`` so
    the user can hand-edit the strategy-specific commentary without having to
    set up CSS or markup themselves. No collapsible details, no auto-generated
    explanation text. ``dark`` swaps the katsustats CSS variables for a dark
    palette (matplotlib charts must already be generated under
    ``plt.style.use('dark_background')`` — they are base64-embedded so CSS
    cannot retint them).
    """
    style_addon = ""
    if annotate:
        style_addon += _ANNOTATION_STYLE_LIGHT
    if dark:
        style_addon += _DARK_THEME_STYLE
    if style_addon:
        html = html.replace("</style>", style_addon + "</style>", 1)
    if not annotate:
        return html
    html = _color_html_tables(html)
    for title in _SECTION_TITLES:
        marker = f"<h2>{title}</h2>"
        if marker in html:
            html = html.replace(
                marker, f'{marker}<div class="analysis"></div>', 1
            )
    return html


def _run_hivefi(*args: str) -> None:
    if shutil.which("hivefi") is None:
        sys.exit(
            "hivefi CLI not found on PATH. Activate the workspace .venv "
            "(see tools/symphony/bootstrap_codex_workspace.sh)."
        )
    subprocess.run(["hivefi", *args], check=True)


def _fetch_timeseries(
    strategy_id: str | None,
    run_id: str | None,
    dest: Path,
    *,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
) -> pd.DataFrame:
    args = ["bt", "timeseries", "--format", "csv", "--save", str(dest)]
    if run_id:
        args += ["--run-id", run_id]
    elif strategy_id:
        args += ["--strategy-id", strategy_id]
    else:
        sys.exit("either --strategy-id or --run-id is required")
    if start is not None:
        args += ["--start", str(start.date())]
    if end is not None:
        args += ["--end", str(end.date())]
    _run_hivefi(*args)
    df = pd.read_csv(dest, parse_dates=["time"])
    if df.empty:
        sys.exit("hivefi bt timeseries returned no rows for the given selector")
    if "equity" not in df.columns:
        sys.exit(f"timeseries CSV missing 'equity' column; got {list(df.columns)}")
    if start is not None:
        df = df[df["time"].dt.date >= start.date()]
    if end is not None:
        df = df[df["time"].dt.date <= end.date()]
    if df.empty:
        sys.exit("hivefi bt timeseries returned no rows after date filtering")
    return df.sort_values("time").reset_index(drop=True)


def _to_daily_returns(ts: pd.DataFrame) -> pd.DataFrame:
    ts = ts.copy()
    ts["date"] = ts["time"].dt.date
    daily = ts.groupby("date", as_index=False)["equity"].last()
    daily["returns"] = daily["equity"].pct_change()
    daily = daily.dropna(subset=["returns"]).reset_index(drop=True)
    return daily[["date", "returns"]]


def _fetch_benchmark(
    symbol: str, start: pd.Timestamp, end: pd.Timestamp, dest: Path
) -> pd.DataFrame:
    _run_hivefi(
        "data",
        "fetch",
        DEFAULT_BENCHMARK_SOURCE,
        "--symbol",
        symbol,
        "--fields",
        "time,symbol,close",
        "--start",
        str(start.date()),
        "--end",
        str(end.date()),
        "--format",
        "csv",
        "--save",
        str(dest),
    )
    df = pd.read_csv(dest, parse_dates=["time"])
    if df.empty:
        sys.exit(f"benchmark fetch returned no rows for {symbol}")
    if "symbol" in df.columns:
        df = df[df["symbol"] == symbol]
    df = df.copy()
    df["date"] = df["time"].dt.date
    df = df.sort_values("date").drop_duplicates("date", keep="last")
    df["returns"] = df["close"].pct_change()
    df = df.dropna(subset=["returns"]).reset_index(drop=True)
    return df[["date", "returns"]]


def _write_reports(
    returns: pd.DataFrame,
    benchmark: pd.DataFrame | None,
    *,
    title: str,
    rf: float,
    monte_carlo: bool,
    annotate: bool,
    dark: bool,
    out_dir: Path,
) -> list[Path]:
    try:
        import katsustats
    except ImportError:
        sys.exit(
            "katsustats not installed. Run: uv sync --extra analysis "
            "(or: pip install 'katsustats>=0.6')."
        )
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    common = {
        "title": title,
        "rf": rf,
        "monte_carlo": monte_carlo,
    }
    if benchmark is not None:
        common["benchmark"] = benchmark

    written: list[Path] = []

    html_path = out_dir / "report.html"
    style_ctx = plt.style.context("dark_background") if dark else plt.style.context("default")
    with style_ctx:
        html = katsustats.reports.html(returns, **common)
    if annotate or dark:
        html = _annotate_html(html, dark=dark, annotate=annotate)
    html_path.write_text(html, encoding="utf-8")
    written.append(html_path)

    json_path = out_dir / "report.json"
    katsustats.reports.json(returns, output=str(json_path), **common)
    written.append(json_path)

    md_path = out_dir / "report.md"
    katsustats.reports.markdown(returns, output=str(md_path), **common)
    written.append(md_path)

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strategy-id", help="resolve the latest BT run for this strategy id"
    )
    parser.add_argument(
        "--run-id", help="explicit BT run id (takes precedence over --strategy-id)"
    )
    parser.add_argument(
        "--benchmark",
        default=None,
        help=(
            "benchmark symbol fetched via hivefi data fetch "
            f"{DEFAULT_BENCHMARK_SOURCE} (e.g. BTC). default: no benchmark."
        ),
    )
    parser.add_argument(
        "--rf", type=float, default=0.0, help="annualized risk-free rate (default 0.0)"
    )
    parser.add_argument(
        "--start",
        default=None,
        help="first date included in the report (YYYY-MM-DD; default: earliest available)",
    )
    parser.add_argument(
        "--end",
        default="2025-12-31",
        help="last date included in the report (YYYY-MM-DD; default: 2025-12-31)",
    )
    parser.add_argument(
        "--monte-carlo",
        action="store_true",
        help="include block-bootstrap Monte Carlo simulation in the reports",
    )
    parser.add_argument(
        "--no-annotate",
        action="store_true",
        help="skip the Japanese reading-guide / per-section explanation injection",
    )
    parser.add_argument(
        "--dark",
        action="store_true",
        help="render HTML and matplotlib charts with a dark theme",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="output directory (default: artifacts/katsustats/<run_id|strategy_id>)",
    )
    args = parser.parse_args()

    if not args.strategy_id and not args.run_id:
        parser.error("either --strategy-id or --run-id is required")
    start = pd.Timestamp(args.start) if args.start else None
    end = pd.Timestamp(args.end) if args.end else None

    label = args.run_id or args.strategy_id
    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else REPO_ROOT / "artifacts" / "katsustats" / label
    )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        ts_csv = tmp_path / "ts.csv"
        ts = _fetch_timeseries(args.strategy_id, args.run_id, ts_csv, start=start, end=end)
        returns = _to_daily_returns(ts)
        if len(returns) < MIN_DAILY_ROWS:
            sys.exit(
                f"insufficient daily rows after aggregation: "
                f"{len(returns)} < {MIN_DAILY_ROWS}"
            )

        benchmark = None
        if args.benchmark:
            bm_csv = tmp_path / "bm.csv"
            benchmark = _fetch_benchmark(
                args.benchmark, ts["time"].min(), ts["time"].max(), bm_csv
            )

        title = f"{label} ({len(returns)} daily rows)"
        written = _write_reports(
            returns,
            benchmark,
            title=title,
            rf=args.rf,
            monte_carlo=args.monte_carlo,
            annotate=not args.no_annotate,
            dark=args.dark,
            out_dir=out_dir,
        )

    print(f"katsustats reports: {out_dir}")
    for path in written:
        try:
            display = path.resolve().relative_to(REPO_ROOT)
        except ValueError:
            display = path
        print(f"  - {display}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
