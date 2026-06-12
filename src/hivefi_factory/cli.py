"""argparse-based CLI for the strategy factory.

Subcommands map 1:1 to the workflow steps in
``infrastructure/docs/design/multi-tenant-user-flow.md``:

* ``validate``      — local AST denylist pre-flight (no network)
* ``strategy list``
* ``strategy push <id>``  (POST config + POST code; auto-triggers Stage 1)
* ``strategy show <id>``
* ``strategy delete <id>``
* ``code upload <id> <file>``
* ``bt status <id>``       (jobs list, ClickHouse-backed)
* ``bt result <run_id>``   (runs/timeseries/trades summary, ClickHouse-backed)
* ``bt poll <job_id>``     (poll until terminal status)
* ``data fetch <table>``   (wide panel CSV for factor research)
* ``data request``         (record a local missing-data request)
* ``signals <id>``         (recent user_signals rows)

Skill files (``.claude/skills/`` and ``.agents/skills/``) reference these
exact subcommand strings; ``tests/test_skills.py`` validates the references
against ``hivefi-factory --help`` output.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "configs"
EXTENSIONS_DIR = REPO_ROOT / "extensions"


def _load_config(strategy_id: str) -> dict[str, Any]:
    path = CONFIGS_DIR / f"{strategy_id}.json"
    if not path.exists():
        raise SystemExit(f"config not found: {path.relative_to(REPO_ROOT)}")
    return json.loads(path.read_text(encoding="utf-8"))


def _extension_path(strategy_id: str) -> Path:
    path = EXTENSIONS_DIR / f"{strategy_id}.py"
    if not path.exists():
        raise SystemExit(f"extension not found: {path.relative_to(REPO_ROOT)}")
    return path


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

def _cmd_validate(args: argparse.Namespace) -> int:
    from .validator import ValidationError, validate_file

    if args.all:
        targets = sorted(p.stem for p in EXTENSIONS_DIR.glob("*.py"))
    elif args.strategy_ids:
        targets = list(args.strategy_ids)
    else:
        raise SystemExit("specify --all or strategy ids")
    if not targets:
        print("no strategies found", file=sys.stderr)
        return 2
    fail = 0
    for sid in targets:
        path = EXTENSIONS_DIR / f"{sid}.py"
        if not path.exists():
            print(f"[FAIL] {sid}: extension not found")
            fail += 1
            continue
        try:
            report = validate_file(path)
        except ValidationError as exc:  # validate_file already swallows, but be defensive
            print(f"[FAIL] {sid}: {exc}")
            fail += 1
            continue
        if report.ok:
            print(f"[OK]   {sid}")
        else:
            print(f"[FAIL] {sid}")
            for v in report.violations:
                print(f"       {v}")
            fail += 1
    print(f"\n{len(targets) - fail}/{len(targets)} passed")
    return 0 if fail == 0 else 1


# ---------------------------------------------------------------------------
# smoke (local compute_signals dry-run on synthetic data)
# ---------------------------------------------------------------------------

def _cmd_smoke(args: argparse.Namespace) -> int:
    from .smoke import format_result, run_smoke

    if args.all:
        targets = sorted(p.stem for p in EXTENSIONS_DIR.glob("*.py"))
    elif args.strategy_ids:
        targets = list(args.strategy_ids)
    else:
        raise SystemExit("specify --all or strategy ids")
    if not targets:
        print("no strategies found", file=sys.stderr)
        return 2
    fail = 0
    for sid in targets:
        result = run_smoke(sid)
        print(format_result(result))
        if not result.ok:
            fail += 1
    print(f"\n{len(targets) - fail}/{len(targets)} passed")
    return 0 if fail == 0 else 1


# ---------------------------------------------------------------------------
# strategy
# ---------------------------------------------------------------------------

def _print_table(rows: list[dict[str, Any]], cols: list[str]) -> None:
    if not rows:
        print("(no rows)")
        return
    widths = {c: max(len(c), max(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    header = "  ".join(c.ljust(widths[c]) for c in cols)
    print(header)
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))


def _cmd_strategy_list(_args: argparse.Namespace) -> int:
    from .client import StrategyApiClient

    with StrategyApiClient() as api:
        items = api.list_strategies()
    if not items:
        print("(no strategies registered for this API key)")
        return 0
    cols = ["strategy_id", "title", "rebalance_freq", "universe", "warmup_periods", "updated_at"]
    _print_table(items, cols)
    return 0


def _cmd_strategy_show(args: argparse.Namespace) -> int:
    from .client import StrategyApiClient

    with StrategyApiClient() as api:
        body = api.get_strategy(args.strategy_id)
    print(json.dumps(body, indent=2, sort_keys=True, default=str))
    return 0


def _cmd_strategy_delete(args: argparse.Namespace) -> int:
    from .client import StrategyApiClient

    with StrategyApiClient() as api:
        api.delete_strategy(args.strategy_id)
    print(f"deleted: {args.strategy_id}")
    return 0


def _cmd_strategy_push(args: argparse.Namespace) -> int:
    from .client import StrategyApiClient, StrategyApiError
    from .smoke import format_result, run_smoke
    from .validator import ValidationError, validate_file

    config = _load_config(args.strategy_id)
    code_path = _extension_path(args.strategy_id)

    if not args.skip_validate:
        report = validate_file(code_path)
        if not report.ok:
            print(f"[FAIL] local AST pre-flight rejected {args.strategy_id}:", file=sys.stderr)
            for v in report.violations:
                print(f"  {v}", file=sys.stderr)
            return 2

    if not args.no_smoke:
        smoke_result = run_smoke(args.strategy_id)
        print(format_result(smoke_result))
        if not smoke_result.ok:
            print(
                f"[FAIL] local smoke rejected {args.strategy_id}; "
                "fix compute_signals or pass --no-smoke to override",
                file=sys.stderr,
            )
            return 2

    with StrategyApiClient() as api:
        # 1. config (create or update)
        try:
            saved = api.create_strategy(config)
            print(f"created strategy: {saved['strategy_id']}")
        except StrategyApiError as exc:
            if exc.status_code == 409 and not args.no_update:
                api.update_strategy(args.strategy_id, config)
                print(f"updated existing strategy: {args.strategy_id}")
            else:
                print(f"create failed: {exc}", file=sys.stderr)
                return 1

        # 2. code upload (auto-triggers Stage 1)
        try:
            result = api.upload_code(args.strategy_id, code_path)
        except StrategyApiError as exc:
            print(f"code upload failed: {exc}", file=sys.stderr)
            return 1
        print(
            f"uploaded code v{result.version}; "
            f"job_id={result.job_id} run_id={result.run_id}"
        )
    return 0


# ---------------------------------------------------------------------------
# code
# ---------------------------------------------------------------------------

def _cmd_code_upload(args: argparse.Namespace) -> int:
    from .client import StrategyApiClient
    from .smoke import format_result, run_smoke
    from .validator import validate_file

    code_path = Path(args.file) if args.file else _extension_path(args.strategy_id)
    if not args.skip_validate:
        report = validate_file(code_path)
        if not report.ok:
            print(f"[FAIL] local AST pre-flight rejected {code_path}:", file=sys.stderr)
            for v in report.violations:
                print(f"  {v}", file=sys.stderr)
            return 2

    if not args.no_smoke and args.file is None:
        smoke_result = run_smoke(args.strategy_id)
        print(format_result(smoke_result))
        if not smoke_result.ok:
            print(
                f"[FAIL] local smoke rejected {args.strategy_id}; "
                "fix compute_signals or pass --no-smoke to override",
                file=sys.stderr,
            )
            return 2
    with StrategyApiClient() as api:
        result = api.upload_code(args.strategy_id, code_path)
    print(
        f"uploaded code v{result.version}; "
        f"job_id={result.job_id} run_id={result.run_id}"
    )
    return 0


# ---------------------------------------------------------------------------
# bt (backtest jobs / results)
# ---------------------------------------------------------------------------

def _cmd_bt_status(args: argparse.Namespace) -> int:
    if args.via_api:
        from .client import StrategyApiClient

        with StrategyApiClient() as api:
            rows = api.list_jobs(args.strategy_id, limit=args.limit)
    else:
        from .clickhouse import ClickHouseClient

        with ClickHouseClient() as ch:
            rows = ch.list_jobs(args.strategy_id, limit=args.limit)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    cols = ["job_id", "run_id", "status", "stage", "submitted_at", "finished_at", "error_message"]
    _print_table(rows, cols)
    return 0


def _cmd_bt_poll(args: argparse.Namespace) -> int:
    from .clickhouse import ClickHouseClient, JobTimeoutError

    with ClickHouseClient() as ch:
        try:
            job = ch.poll_job(args.job_id, timeout=args.timeout, interval=args.interval)
        except JobTimeoutError as exc:
            print(str(exc), file=sys.stderr)
            return 3
    print(json.dumps(job.__dict__, indent=2, default=str))
    return 0 if job.status == "succeeded" else 1


def _cmd_bt_result(args: argparse.Namespace) -> int:
    from .clickhouse import ClickHouseClient

    with ClickHouseClient() as ch:
        run = ch.get_run(args.run_id)
        if run is None:
            print(f"run_id {args.run_id} not found (or not yet replicated)", file=sys.stderr)
            return 1
        print("# backtest_runs")
        print(json.dumps(run, indent=2, sort_keys=True, default=str))
        if args.timeseries:
            ts = ch.get_timeseries(args.run_id)
            print(f"\n# backtest_timeseries ({len(ts)} rows)")
            if args.csv:
                _write_csv(sys.stdout, ts)
            else:
                _print_table(ts[:20], list(ts[0].keys()) if ts else [])
                if len(ts) > 20:
                    print(f"... {len(ts) - 20} more rows. Use --csv to dump all.")
        if args.trades:
            trades = ch.get_trades(args.run_id)
            print(f"\n# backtest_trades ({len(trades)} rows)")
            if args.csv:
                _write_csv(sys.stdout, trades)
            else:
                _print_table(trades[:20], list(trades[0].keys()) if trades else [])
                if len(trades) > 20:
                    print(f"... {len(trades) - 20} more rows. Use --csv to dump all.")
    return 0


# ---------------------------------------------------------------------------
# data / signals
# ---------------------------------------------------------------------------

def _cmd_data_fetch(args: argparse.Namespace) -> int:
    from .clickhouse import ClickHouseClient

    with ClickHouseClient() as ch:
        df = ch.fetch_panel(
            args.table,
            symbols=args.symbols,
            start=args.start,
            end=args.end,
            time_col=args.time_col,
            symbol_col=args.symbol_col,
            value_col=args.value_col,
        )
    if df.empty:
        print("(no rows)", file=sys.stderr)
        return 1
    if args.output:
        df.to_csv(args.output)
        print(f"wrote {len(df)} rows × {len(df.columns)} symbols to {args.output}")
    else:
        df.to_csv(sys.stdout)
    return 0


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _cmd_data_request(args: argparse.Namespace) -> int:
    from .data_requests import (
        DataRequestInput,
        format_data_request,
        split_cli_values,
        write_data_request,
    )

    req = DataRequestInput(
        idea=args.idea,
        needed_data=args.needed_data,
        reason=args.reason,
        task_id=args.task_id,
        current_data=args.current_data or [],
        source=args.source,
        symbols=split_cli_values(args.symbols),
        start=args.start,
        end=args.end,
        frequency=args.frequency,
        fields=split_cli_values(args.fields),
        acceptance=args.acceptance or [],
        notes=args.notes or [],
        priority=args.priority,
        state=args.state,
        request_id=args.request_id,
    )

    if args.dry_run:
        print(format_data_request(req))
        return 0

    try:
        path = write_data_request(
            req,
            output_dir=args.output_dir,
            output=args.output,
            overwrite=args.overwrite,
        )
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        print("pass --overwrite or choose --request-id/--output", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"path": str(path), "request_id": path.stem}, indent=2))
    else:
        print(f"wrote data request: {_display_path(path)}")
    return 0


def _cmd_signals(args: argparse.Namespace) -> int:
    from .clickhouse import ClickHouseClient

    with ClickHouseClient() as ch:
        rows = ch.get_signals(args.strategy_id, since_days=args.since_days, limit=args.limit)
    if not rows:
        print("(no signals)")
        return 0
    if args.csv:
        _write_csv(sys.stdout, rows)
    else:
        _print_table(rows, list(rows[0].keys()))
    return 0


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------

def _cmd_health(_args: argparse.Namespace) -> int:
    from .client import StrategyApiClient

    with StrategyApiClient() as api:
        body = api.health()
    print(json.dumps(body, indent=2))
    return 0


def _write_csv(stream, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for r in rows:
        writer.writerow({k: ("" if v is None else v) for k, v in r.items()})


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hivefi-factory",
        description="HiveFi multi-tenant Strategy API client + ClickHouse helpers.",
    )
    parser.add_argument("--version", action="store_true", help="print version and exit")
    sub = parser.add_subparsers(dest="cmd", required=False)

    # validate ---------------------------------------------------------------
    p = sub.add_parser("validate", help="run local AST pre-flight (no network)")
    p.add_argument("strategy_ids", nargs="*", help="strategy ids; or use --all")
    p.add_argument("--all", action="store_true", help="validate every extension/*.py")
    p.set_defaults(func=_cmd_validate)

    # smoke ------------------------------------------------------------------
    sm = sub.add_parser(
        "smoke",
        help="local compute_signals dry-run on synthetic price data (no network)",
    )
    sm.add_argument("strategy_ids", nargs="*", help="strategy ids; or use --all")
    sm.add_argument("--all", action="store_true", help="smoke every extension/*.py")
    sm.set_defaults(func=_cmd_smoke)

    # strategy ---------------------------------------------------------------
    sp = sub.add_parser("strategy", help="manage strategy configs")
    ssub = sp.add_subparsers(dest="strategy_cmd", required=True)

    sp_list = ssub.add_parser("list", help="list strategies registered for this API key")
    sp_list.set_defaults(func=_cmd_strategy_list)

    sp_show = ssub.add_parser("show", help="get one strategy by id")
    sp_show.add_argument("strategy_id")
    sp_show.set_defaults(func=_cmd_strategy_show)

    sp_push = ssub.add_parser(
        "push",
        help="upload config (POST) + code (auto-triggers Stage 1) in one call",
    )
    sp_push.add_argument("strategy_id")
    sp_push.add_argument(
        "--skip-validate",
        action="store_true",
        help="skip local AST pre-flight (NOT recommended; server will reject anyway)",
    )
    sp_push.add_argument(
        "--no-smoke",
        action="store_true",
        help="skip local compute_signals smoke (synthetic data dry-run) before push",
    )
    sp_push.add_argument(
        "--no-update",
        action="store_true",
        help="if config already exists (409), fail instead of PUT update",
    )
    sp_push.set_defaults(func=_cmd_strategy_push)

    sp_del = ssub.add_parser("delete", help="delete a strategy and all its data")
    sp_del.add_argument("strategy_id")
    sp_del.set_defaults(func=_cmd_strategy_delete)

    # code -------------------------------------------------------------------
    cp = sub.add_parser("code", help="manage strategy code uploads")
    csub = cp.add_subparsers(dest="code_cmd", required=True)
    cp_up = csub.add_parser(
        "upload",
        help="upload extensions/<id>.py and auto-trigger a Stage 1 job",
    )
    cp_up.add_argument("strategy_id")
    cp_up.add_argument("file", nargs="?", help="defaults to extensions/<id>.py")
    cp_up.add_argument("--skip-validate", action="store_true")
    cp_up.add_argument(
        "--no-smoke",
        action="store_true",
        help="skip local compute_signals smoke (only runs when uploading the default extension)",
    )
    cp_up.set_defaults(func=_cmd_code_upload)

    # bt ---------------------------------------------------------------------
    bp = sub.add_parser("bt", help="backtest job lifecycle + results")
    bsub = bp.add_subparsers(dest="bt_cmd", required=True)

    bp_st = bsub.add_parser("status", help="list jobs for a strategy")
    bp_st.add_argument("strategy_id")
    bp_st.add_argument("--limit", type=int, default=10)
    bp_st.add_argument(
        "--via-api",
        action="store_true",
        help="use HTTP /v1/strategies/{id}/jobs instead of direct ClickHouse",
    )
    bp_st.add_argument("--json", action="store_true")
    bp_st.set_defaults(func=_cmd_bt_status)

    bp_poll = bsub.add_parser("poll", help="poll one job until terminal state")
    bp_poll.add_argument("job_id")
    bp_poll.add_argument("--timeout", type=float, default=600.0)
    bp_poll.add_argument("--interval", type=float, default=5.0)
    bp_poll.set_defaults(func=_cmd_bt_poll)

    bp_res = bsub.add_parser("result", help="fetch run summary + optional timeseries/trades")
    bp_res.add_argument("run_id")
    bp_res.add_argument("--timeseries", action="store_true", help="include equity curve")
    bp_res.add_argument("--trades", action="store_true", help="include trade history")
    bp_res.add_argument("--csv", action="store_true", help="emit timeseries/trades as CSV")
    bp_res.set_defaults(func=_cmd_bt_result)

    # data -------------------------------------------------------------------
    dp = sub.add_parser("data", help="ClickHouse-backed market data fetch")
    dsub = dp.add_subparsers(dest="data_cmd", required=True)
    dp_fetch = dsub.add_parser("fetch", help="wide panel from a CH table (CSV)")
    dp_fetch.add_argument("table", help="ClickHouse table name (e.g. hyperliquid_kline_1d)")
    dp_fetch.add_argument("--symbols", nargs="+", default=None)
    dp_fetch.add_argument("--start", default=None, help="YYYY-MM-DD")
    dp_fetch.add_argument("--end", default=None, help="YYYY-MM-DD")
    dp_fetch.add_argument("--time-col", default="time")
    dp_fetch.add_argument("--symbol-col", default="symbol")
    dp_fetch.add_argument("--value-col", default="close")
    dp_fetch.add_argument("--output", default=None, help="write CSV here (default: stdout)")
    dp_fetch.set_defaults(func=_cmd_data_fetch)

    dp_req = dsub.add_parser(
        "request",
        help="write a local missing-data request for a blocked idea",
    )
    dp_req.add_argument("--idea", required=True, help="strategy idea blocked by missing data")
    dp_req.add_argument(
        "--needed-data",
        action="append",
        required=True,
        help="missing dataset, field, coverage, or history requirement (repeatable)",
    )
    dp_req.add_argument(
        "--reason",
        required=True,
        help="why existing data cannot support the idea's evidence gate",
    )
    dp_req.add_argument("--task-id", default=None, help="local task identifier, if any")
    dp_req.add_argument(
        "--current-data",
        action="append",
        default=None,
        help="data source already checked and found insufficient (repeatable)",
    )
    dp_req.add_argument("--source", default=None, help="preferred upstream source or venue")
    dp_req.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="symbols/universe; comma-separated chunks are accepted",
    )
    dp_req.add_argument("--start", default=None, help="requested start date, YYYY-MM-DD")
    dp_req.add_argument("--end", default=None, help="requested end date, YYYY-MM-DD")
    dp_req.add_argument("--frequency", default=None, help="requested sampling frequency")
    dp_req.add_argument(
        "--fields",
        nargs="+",
        default=None,
        help="requested columns/metrics; comma-separated chunks are accepted",
    )
    dp_req.add_argument(
        "--acceptance",
        action="append",
        default=None,
        help="criterion that would make the data usable (repeatable)",
    )
    dp_req.add_argument("--notes", action="append", default=None, help="extra context")
    dp_req.add_argument(
        "--priority",
        choices=["low", "medium", "high", "critical"],
        default="medium",
    )
    dp_req.add_argument("--state", default="Open")
    dp_req.add_argument("--request-id", default=None, help="filename stem/id to use")
    dp_req.add_argument("--output-dir", default=None, help="directory for generated Markdown")
    dp_req.add_argument("--output", default=None, help="write to this exact Markdown path")
    dp_req.add_argument("--overwrite", action="store_true")
    dp_req.add_argument("--dry-run", action="store_true", help="print Markdown only")
    dp_req.add_argument("--json", action="store_true", help="print created path as JSON")
    dp_req.set_defaults(func=_cmd_data_request)

    # signals ----------------------------------------------------------------
    sgp = sub.add_parser("signals", help="recent rows from user_signals (this strategy)")
    sgp.add_argument("strategy_id")
    sgp.add_argument("--since-days", type=int, default=30)
    sgp.add_argument("--limit", type=int, default=1000)
    sgp.add_argument("--csv", action="store_true")
    sgp.set_defaults(func=_cmd_signals)

    # health -----------------------------------------------------------------
    hp = sub.add_parser("health", help="GET /health (no auth)")
    hp.set_defaults(func=_cmd_health)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        from . import __version__

        print(f"hivefi-factory {__version__}")
        return 0
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    return int(func(args) or 0)
