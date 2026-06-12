# Symphony Integration

This directory contains repository-local helpers for running the external
Symphony orchestrator against `hivefi-strategy-factory`.

The orchestrator itself is not vendored here. Use the local Symphony checkout,
usually `~/symphony/elixir`, and point it at this repo's `WORKFLOW.md`.

The default workflow is local-only. It reads Markdown tasks from
`tools/symphony/local_tasks/`, writes report comments to
`tools/symphony/local_comments/`, and never requires Linear or GitHub.

## Start

From this repository:

```bash
cd /path/to/hivefi-strategy-factory
set -a
. ./.env
set +a
export HIVEFI_STRATEGY_FACTORY_SOURCE="$PWD"
export HIVEFI_STRATEGY_FACTORY_TASKS_DIR="$PWD/tools/symphony/local_tasks"
export HIVEFI_STRATEGY_FACTORY_COMMENTS_DIR="$PWD/tools/symphony/local_comments"
cd ~/symphony/elixir
mise exec -- ./bin/symphony "$HIVEFI_STRATEGY_FACTORY_SOURCE/WORKFLOW.md" --port 4000 \
  --i-understand-that-this-will-be-running-without-the-usual-guardrails
```

`WORKFLOW.md` reads the local checkout and file-tracker directories from
environment variables. Override them when testing another checkout:

```bash
export HIVEFI_STRATEGY_FACTORY_SOURCE=/path/to/hivefi-strategy-factory
export HIVEFI_STRATEGY_FACTORY_TASKS_DIR="$HIVEFI_STRATEGY_FACTORY_SOURCE/tools/symphony/local_tasks"
export HIVEFI_STRATEGY_FACTORY_COMMENTS_DIR="$HIVEFI_STRATEGY_FACTORY_SOURCE/tools/symphony/local_comments"
```

## Workspace Bootstrap

`bootstrap_codex_workspace.sh` creates `.venv`, installs this package in editable
mode (which pulls in `httpx`, `clickhouse-connect`, `pandas`, etc.), and exposes
the `hivefi-factory` console script. The HiveFi multi-tenant Strategy API client
is the same package — there is no separate CLI to install.

Symphony strategy work requires API and ClickHouse access. `WORKFLOW.md` fails
fast before starting Codex unless `hivefi-factory health`,
`hivefi-factory validate --all`, and `tools/symphony/check_data_access.sh`
succeed. The data smoke runs a tiny `hivefi-factory data fetch` against
`hyperliquid_kline_1d` and caches the successful result for 5 minutes. It
requires exported `HIVEFI_API_KEY`, `CLICKHOUSE_USER`, and
`CLICKHOUSE_PASSWORD`.

## Strategy Issue Mode

This workflow is tuned for strategy research, not single-file maintenance.
Each local task should handle at most one strategy idea. If target evidence or
IC evidence is weak, the task can finish as a research result without creating
strategy files.

Do not bulk-create local tasks. Even when turning many ideas into issues, create
one `tools/symphony/local_tasks/<id>.md` at a time: confirm the source, edge,
target participant, observable proxy, expected sign, and falsification
condition; write that single file; validate YAML and duplicate IDs; then move to
the next idea. Do not use one-shot generation scripts, giant patches, or
template-variable mass fill.

Use `LOCAL_TASK_TEMPLATE.md` when creating local tasks. Save each task as
`tools/symphony/local_tasks/<id>.md` with `state: Todo`.

After generation, run the validator:

```bash
python tools/symphony/strategy_batch.py --changed
```

The validator checks:

- `configs/<id>.json` and `extensions/<id>.py` pair consistency
- `strategy_id` field matches the filename
- forbidden imports / calls that would violate the HiveFi AST denylist
  (delegated to `hivefi_factory.validator`, kept in sync with the server)
- `compute_signals` smoke execution against synthetic `price` data

For repeated `hivefi-factory data fetch` calls during IC / data checks, use the
shared CSV cache wrapper so parallel local tasks do not fetch the same
ClickHouse slice again:

```bash
python tools/symphony/fetch_data_cached.py hyperliquid_kline_1d \
  --start 2022-01-01 --end 2025-12-31 \
  --symbols BTC ETH SOL \
  --time-col time --symbol-col symbol --value-col close \
  --save data/hl_kline_1d_2022_2025.csv
```

The cache defaults to `/tmp/hivefi-strategy-data-cache` and can be overridden
with `HIVEFI_DATA_CACHE_DIR`.

If a clear strategy idea cannot be evaluated because the needed dataset, field,
or history is unavailable, file a local data request instead of inventing a weak
proxy:

```bash
hivefi-factory data request \
  --idea "order-flow imbalance fade" \
  --needed-data "signed taker buy/sell volume by symbol" \
  --reason "OHLCV cannot identify aggressor-side flow, so target evidence is missing" \
  --current-data "hyperliquid_kline_1d: OHLCV only" \
  --source Hyperliquid \
  --symbols hl_all \
  --start 2022-01-01 \
  --end 2025-12-31 \
  --frequency 1h
```

Requests are written under `tools/symphony/data_requests/` and should be linked
from the task's final `## 結果` comment.

## Operational Notes

- Keep API tokens in the environment only.
- Use local task labels or titles to group related strategy cohorts.
- Submitting many tasks must respect the HiveFi multi-tenant API limits:
  60 write requests/min/user and at most 5 pending backtest jobs per user.
- For strategy factory work, a completed Symphony run should leave a single
  research record comment. New / rework tasks leave one `configs/` and one
  `extensions/` pair only when the evidence gate is met and code was actually
  created or changed.
- The cross-task result list is `STRATEGY_STATUS.md`; regenerate it with
  `python tools/refresh_strategy_status.py --no-refresh` when reviewing local
  task outcomes without refreshing server BT data. The list exposes one
  user-facing evaluation column. It is a research index, not an operations or
  portfolio decision.
- Strategy creation should follow the repository's existing recommended flow in
  `AGENTS.md`: hypothesis, data availability, factor evidence, scaffold,
  `compute_signals`, local verification, then submit/BT only when requested.
- BT reports end at `2025-12-31` and use the longest available history up to
  that date. The pipeline passes this bound to the katsustats report step so
  2026+ test-period data is not included in reported BT evidence.
- Local reporting uses `tracker_comment`, which writes to
  `tools/symphony/local_comments/<task>.md`. Post one Japanese `## 結果`
  comment only. Keep strategy_id, what was done, research purpose, deductive
  chain, target evidence, IC direction, R2_mean, sample_n, p_value, corrected
  q_value, Q5-Q1, pipeline report path, files, and next check in that short card.
  Do not use good / bad labels or operations decisions. Do not claim file outputs
  unless those files exist in the workspace. Do not use
  `## 工程レポート`. The result comment must be posted before moving the local
  task to `Done`.
- Workflow result checks should be run against one selected task first. Do not
  bulk-move every task to `Rework` unless the user explicitly asks for a full
  rerun.
- A task is complete when scoped work is done, the final `## 結果` comment
  exists, required minimal outputs for that scope are synced back by the
  `after_run` hook, and the local task is moved to `Done`. Poor evaluations
  still complete as `Done`; only missing operator input should use `On Hold`.
- When testing unmerged workflow changes, the default local workflow copies the
  current dirty checkout into workspaces after cloning, so no remote push is
  required.
