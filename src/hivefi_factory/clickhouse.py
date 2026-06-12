"""ClickHouse helpers for reading backtest results and market data.

The multi-tenant API does NOT expose backtest results via REST; users connect
directly to ClickHouse Cloud with their per-tenant ``u_<6hex>`` user. ROW
POLICY ensures each user only sees their own ``user_signals`` /
``backtest_runs`` / ``backtest_jobs`` rows, so SELECT statements do not need
``WHERE owner = ...`` filters.

Lazy import of ``clickhouse_connect`` keeps environments without CH credentials
(e.g. CI) usable for the parts of the CLI that only hit the HTTP API.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable

from .config import Settings, load_settings


def _connect(settings: Settings):
    import clickhouse_connect  # type: ignore[import-not-found]

    host, user, password, port = settings.require_clickhouse()
    return clickhouse_connect.get_client(
        host=host,
        port=port,
        username=user,
        password=password,
        database=settings.ch_database,
        secure=settings.ch_secure,
    )


@dataclass
class JobRow:
    job_id: str
    run_id: str | None
    status: str
    stage: str
    submitted_at: Any
    started_at: Any
    finished_at: Any
    error_message: str | None


class JobNotFoundError(LookupError):
    pass


class JobTimeoutError(TimeoutError):
    pass


class ClickHouseClient:
    """Thin wrapper over ``clickhouse_connect.get_client`` for the factory.

    Methods return plain Python dicts / lists so that scripts and the CLI can
    print them without needing pandas. ``fetch_panel`` returns a pandas
    DataFrame for factor-research workflows.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or load_settings()
        self._client = _connect(self._settings)

    def __enter__(self) -> "ClickHouseClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # noqa: BLE001 - clickhouse-connect may raise heterogeneously
            pass

    # ---- generic SELECT helpers ------------------------------------------

    def query_rows(self, sql: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        result = self._client.query(sql, parameters=parameters or {})
        cols = result.column_names
        return [dict(zip(cols, row)) for row in result.result_rows]

    def query_one(self, sql: str, parameters: dict[str, Any] | None = None) -> dict[str, Any] | None:
        rows = self.query_rows(sql, parameters)
        return rows[0] if rows else None

    # ---- backtest result tables ------------------------------------------

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        # SharedMergeTree on ClickHouse Cloud does not support FINAL.
        return self.query_one(
            "SELECT * FROM backtest_runs WHERE run_id = {run_id:String} LIMIT 1",
            {"run_id": run_id},
        )

    def get_timeseries(self, run_id: str) -> list[dict[str, Any]]:
        return self.query_rows(
            "SELECT time, equity FROM backtest_timeseries "
            "WHERE run_id = {run_id:String} ORDER BY time",
            {"run_id": run_id},
        )

    def get_trades(self, run_id: str) -> list[dict[str, Any]]:
        return self.query_rows(
            "SELECT * FROM backtest_trades "
            "WHERE run_id = {run_id:String} ORDER BY entry_time",
            {"run_id": run_id},
        )

    # ---- jobs / lifecycle -------------------------------------------------

    def list_jobs(self, strategy_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        return self.query_rows(
            "SELECT job_id, run_id, status, stage, submitted_at, started_at, "
            "finished_at, error_message FROM backtest_jobs FINAL "
            "WHERE strategy_id = {strategy_id:String} "
            "ORDER BY submitted_at DESC LIMIT {limit:UInt32}",
            {"strategy_id": strategy_id, "limit": int(limit)},
        )

    def get_job(self, job_id: str) -> JobRow:
        row = self.query_one(
            "SELECT job_id, run_id, status, stage, submitted_at, started_at, "
            "finished_at, error_message FROM backtest_jobs FINAL "
            "WHERE job_id = {job_id:String}",
            {"job_id": job_id},
        )
        if row is None:
            raise JobNotFoundError(f"job_id {job_id} not visible (or not yet replicated)")
        return JobRow(**row)

    def poll_job(
        self,
        job_id: str,
        *,
        timeout: float = 600.0,
        interval: float = 5.0,
        progress: Iterable[str] | None = None,
    ) -> JobRow:
        """Poll a job until it reaches a terminal state or the timeout elapses.

        Terminal statuses: ``succeeded``, ``failed``, ``timeout``.
        """
        deadline = time.monotonic() + timeout
        last: JobRow | None = None
        while True:
            try:
                last = self.get_job(job_id)
            except JobNotFoundError:
                last = None
            if last is not None and last.status in {"succeeded", "failed", "timeout"}:
                return last
            if time.monotonic() >= deadline:
                if last is None:
                    raise JobTimeoutError(f"job {job_id} not visible after {timeout}s")
                raise JobTimeoutError(
                    f"job {job_id} still {last.status!r} after {timeout}s"
                )
            time.sleep(interval)

    # ---- signals ---------------------------------------------------------

    def get_signals(
        self,
        strategy_id: str,
        *,
        since_days: int = 30,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        return self.query_rows(
            "SELECT signal_date, symbol, side, weight FROM user_signals FINAL "
            "WHERE strategy_id = {strategy_id:String} "
            "  AND signal_date >= today() - {since:UInt32} "
            "ORDER BY signal_date DESC, symbol "
            "LIMIT {limit:UInt32}",
            {
                "strategy_id": strategy_id,
                "since": int(since_days),
                "limit": int(limit),
            },
        )

    # ---- market data (factor-research) -----------------------------------

    def fetch_panel(
        self,
        table: str,
        *,
        symbols: list[str] | None = None,
        start: str | None = None,
        end: str | None = None,
        time_col: str = "time",
        symbol_col: str = "symbol",
        value_col: str = "close",
    ):
        """Return a wide DataFrame ``(index=time, columns=symbol, values=value_col)``.

        Caller is responsible for choosing a sensible ``table`` / ``value_col``
        for their universe. This is a research helper, not a strict abstraction.
        """
        import pandas as pd  # local import keeps import cost down for CLI subcommands

        # Whitelist table/column identifiers. We forbid backticks and quotes
        # outright — ClickHouse identifiers accepted by this helper must be
        # plain ``[A-Za-z_][A-Za-z0-9_]*`` so they cannot break out of the
        # backtick-quoted slot below.
        for ident in (table, time_col, symbol_col, value_col):
            if not ident.replace("_", "").isalnum():
                raise ValueError(f"invalid identifier: {ident!r}")

        clauses: list[str] = []
        params: dict[str, Any] = {}
        if symbols:
            clauses.append(f"`{symbol_col}` IN {{symbols:Array(String)}}")
            params["symbols"] = list(symbols)
        if start:
            clauses.append(f"`{time_col}` >= {{start:DateTime}}")
            params["start"] = start
        if end:
            clauses.append(f"`{time_col}` <= {{end:DateTime}}")
            params["end"] = end
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT `{time_col}` AS t, `{symbol_col}` AS s, `{value_col}` AS v "
            f"FROM `{table}` {where} ORDER BY t, s"
        )
        rows = self.query_rows(sql, params)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        return df.pivot(index="t", columns="s", values="v").sort_index()
