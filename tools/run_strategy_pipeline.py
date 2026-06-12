#!/usr/bin/env python3
"""End-to-end strategy production pipeline (Symphony Stage 4).

Runs the deterministic chain: local validate → ``hivefi strategy push`` →
``hivefi bt diag`` → ``tools/katsustats_bt_report.py``. Each step is
toggleable so the same script can be re-entered to recover from a partial
run (e.g. push already succeeded but BT diag failed).

Designed to be the single command Symphony's Stage 4 invokes when a task
description opts in to ``Submit: あり`` semantics. Manual invocation also
works once a strategy_id is registered.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _sync_to_source() -> Path | None:
    """rsync configs/, extensions/, artifacts/ from CWD to the source checkout.

    Symphony's after_run hook races against the orchestrator's workspace
    cleanup, so generated strategy code and BT artifacts often disappear
    before the rsync runs. This bypass calls rsync from inside the pipeline
    (still in the workspace) so the source repo gets a copy regardless of
    cleanup timing. Skipped when ``HIVEFI_STRATEGY_FACTORY_SOURCE`` is unset
    (manual run outside Symphony) or when CWD is already the source path.
    """
    src = os.environ.get("HIVEFI_STRATEGY_FACTORY_SOURCE")
    if not src:
        return None
    src_path = Path(src).resolve()
    cwd = Path.cwd().resolve()
    if src_path == cwd:
        return None
    synced: list[str] = []
    for sub in ("configs", "extensions", "artifacts"):
        local_sub = cwd / sub
        if not local_sub.is_dir():
            continue
        target = src_path / sub
        target.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["rsync", "-a", f"{local_sub}/", f"{target}/"],
            check=True,
        )
        synced.append(sub)
    return src_path if synced else None


def _hivefi_path() -> str:
    if shutil.which("hivefi") is None:
        sys.exit(
            "hivefi CLI not found on PATH. Activate the workspace .venv "
            "(see tools/symphony/bootstrap_codex_workspace.sh)."
        )
    return "hivefi"


def _run(args: list[str], *, label: str) -> None:
    print(f"\n=== {label} ===", flush=True)
    print(f"$ {' '.join(args)}", flush=True)
    subprocess.run(args, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy-id", required=True, help="strategy id to run end-to-end")
    parser.add_argument(
        "--benchmark",
        default="BTC",
        help="benchmark symbol passed to katsustats (default BTC, '' to disable)",
    )
    parser.add_argument(
        "--window-days", type=int, default=90, help="hivefi bt diag --window-days"
    )
    parser.add_argument(
        "--step-days", type=int, default=30, help="hivefi bt diag --step-days"
    )
    parser.add_argument(
        "--rf", type=float, default=0.0, help="risk-free rate for katsustats"
    )
    parser.add_argument(
        "--bt-start-date",
        default=None,
        help=(
            "first date included in katsustats report (YYYY-MM-DD). "
            "Default: earliest available."
        ),
    )
    parser.add_argument(
        "--bt-end-date",
        default="2025-12-31",
        help=(
            "last date included in katsustats report (YYYY-MM-DD). "
            "Default: 2025-12-31 to keep 2026+ test period out of BT reports."
        ),
    )
    parser.add_argument(
        "--poll-timeout",
        type=int,
        default=2400,
        help=(
            "hivefi strategy push --poll-timeout (default 2400s/40m, fits "
            "within Symphony's 3600s per-run cap with retry margin; "
            "hivefi default of 900s often times out on longer BT jobs)"
        ),
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=10,
        help="hivefi strategy push --poll-interval (default 10s)",
    )
    parser.add_argument("--no-dark", dest="dark", action="store_false", default=True)
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="skip tools/symphony/strategy_batch.py local check",
    )
    parser.add_argument(
        "--skip-push",
        action="store_true",
        help="skip hivefi strategy push (use latest existing run)",
    )
    parser.add_argument(
        "--skip-diag", action="store_true", help="skip hivefi bt diag"
    )
    parser.add_argument(
        "--skip-report",
        action="store_true",
        help="skip katsustats report generation",
    )
    parser.add_argument(
        "--no-sync-back",
        action="store_true",
        help=(
            "skip the explicit rsync of configs/, extensions/, artifacts/ to "
            "$HIVEFI_STRATEGY_FACTORY_SOURCE. Default is to sync because "
            "Symphony's after_run hook races against workspace cleanup; "
            "disable when running manually inside the source checkout."
        ),
    )
    args = parser.parse_args()

    sid = args.strategy_id
    hivefi = _hivefi_path()

    if not args.skip_validate:
        _run(
            [
                sys.executable,
                str(REPO_ROOT / "tools" / "symphony" / "strategy_batch.py"),
                "--strategy-id",
                sid,
            ],
            label=f"1/4 local validate: {sid}",
        )

    if not args.skip_push:
        _run(
            [
                hivefi,
                "strategy",
                "push",
                sid,
                "--poll-timeout",
                str(args.poll_timeout),
                "--poll-interval",
                str(args.poll_interval),
            ],
            label=f"2/4 hivefi strategy push (Stage 1/2 BT job): {sid}",
        )

    if not args.skip_diag:
        diag_dir = REPO_ROOT / "artifacts" / "diag" / sid
        diag_dir.mkdir(parents=True, exist_ok=True)
        _run(
            [
                hivefi,
                "bt",
                "diag",
                "--strategy-id",
                sid,
                "--window-days",
                str(args.window_days),
                "--step-days",
                str(args.step_days),
                "--csv",
                str(diag_dir / "wf.csv"),
            ],
            label=f"3/4 hivefi bt diag: {sid}",
        )

    if not args.skip_report:
        cmd = [
            sys.executable,
            str(REPO_ROOT / "tools" / "katsustats_bt_report.py"),
            "--strategy-id",
            sid,
            "--rf",
            str(args.rf),
            "--end",
            args.bt_end_date,
        ]
        if args.bt_start_date:
            cmd += ["--start", args.bt_start_date]
        if args.benchmark:
            cmd += ["--benchmark", args.benchmark]
        if args.dark:
            cmd.append("--dark")
        _run(cmd, label=f"4/4 katsustats report: {sid}")

    if not args.no_sync_back:
        synced = _sync_to_source()
        if synced:
            print(f"\n✅ synced configs/extensions/artifacts → {synced}")

    report_html = REPO_ROOT / "artifacts" / "katsustats" / sid / "report.html"
    if report_html.exists():
        print(f"\n✅ pipeline complete: {report_html.relative_to(REPO_ROOT)}")
    else:
        print(
            f"\n⚠️  pipeline finished but report not found at {report_html}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
