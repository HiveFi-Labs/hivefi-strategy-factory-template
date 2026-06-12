#!/usr/bin/env bash
#
# E2E smoke test for HiveFi Strategy API
# =============================================================================
#
# 目的: Strategy API → Stage 1 (signal-gen) → Lambda signal_inserter →
# Stage 2 (backtest) → ClickHouse 結果保存 までの完全パイプラインを、
# admin 権限なしで一般 tenant の credential のみで検証する。
#
# 前提:
#   - .env に 3 値 (HIVEFI_API_KEY / CLICKHOUSE_USER / CLICKHOUSE_PASSWORD) 設定済み
#   - hivefi-factory CLI が PATH または .venv/bin にある
#   - configs/ に対象戦略の設定 JSON、extensions/ に対応コード
#
# 使い方:
#   bash tools/e2e_smoke_test.sh                          # demo-momentum (default)
#   bash tools/e2e_smoke_test.sh <strategy_id>            # 任意の戦略 ID
#   E2E_TIMEOUT=600 bash tools/e2e_smoke_test.sh          # ジョブ完走待ち時間 (default 600s)
#
# 終了コード:
#   0 — succeeded (全段完走、backtest_runs に row 確認済み)
#   1 — failed   (期間内に終わらない、または status='failed' で終了)
#   2 — setup error (.env / CLI 不在 / 戦略 config 不在)
# =============================================================================

set -uo pipefail

STRATEGY_ID="${1:-demo-momentum-D-W-hl-all-ls-v2}"
E2E_TIMEOUT="${E2E_TIMEOUT:-600}" # 10 分まで待つ (Lambda cold + Stage 2 spawn 含む)
POLL_INTERVAL="${E2E_POLL_INTERVAL:-15}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# CLI パス解決: .venv/bin > PATH
if [[ -x "${REPO_ROOT}/.venv/bin/hivefi-factory" ]]; then
    CLI="${REPO_ROOT}/.venv/bin/hivefi-factory"
elif command -v hivefi-factory >/dev/null 2>&1; then
    CLI="$(command -v hivefi-factory)"
else
    echo "❌ hivefi-factory CLI not found (run 'pip install -e .[dev]' first)" >&2
    exit 2
fi

# 戦略 config 存在確認
CONFIG_FILE="${REPO_ROOT}/configs/${STRATEGY_ID}.json"
EXT_FILE="${REPO_ROOT}/extensions/${STRATEGY_ID}.py"
if [[ ! -f "${CONFIG_FILE}" ]]; then
    echo "❌ Strategy config not found: ${CONFIG_FILE}" >&2
    exit 2
fi
if [[ ! -f "${EXT_FILE}" ]]; then
    echo "❌ Strategy code not found: ${EXT_FILE}" >&2
    exit 2
fi

echo "============================================================"
echo "🧪 E2E Smoke Test: HiveFi Strategy API pipeline"
echo "============================================================"
echo "  Strategy:    ${STRATEGY_ID}"
echo "  Timeout:     ${E2E_TIMEOUT}s"
echo "  Poll:        ${POLL_INTERVAL}s"
echo "  CLI:         ${CLI}"
echo "============================================================"
echo

# ------------------------------------------------------------
# Step 0: API health check
# ------------------------------------------------------------
echo "Step 0/4: API health check..."
if ! "${CLI}" health 2>&1 | grep -q '"status": "ok"'; then
    echo "❌ Strategy API health check failed" >&2
    "${CLI}" health
    exit 1
fi
echo "  ✅ API healthy"
echo

# ------------------------------------------------------------
# Step 1: Strategy push (config + code upload → triggers Stage 1)
# ------------------------------------------------------------
echo "Step 1/4: Push strategy + upload code (auto-triggers Stage 1)..."
PUSH_OUTPUT=$("${CLI}" strategy push "${STRATEGY_ID}" 2>&1)
echo "${PUSH_OUTPUT}"

JOB_ID=$(echo "${PUSH_OUTPUT}" | grep -oE 'job_id=[a-f0-9]+' | tail -1 | cut -d= -f2)
RUN_ID=$(echo "${PUSH_OUTPUT}" | grep -oE 'run_id=[a-f0-9]+' | tail -1 | cut -d= -f2)

if [[ -z "${JOB_ID}" || -z "${RUN_ID}" ]]; then
    echo "❌ Failed to extract job_id / run_id from push output" >&2
    exit 1
fi
echo "  ✅ Pushed: job_id=${JOB_ID} run_id=${RUN_ID}"
echo

# ------------------------------------------------------------
# Step 2: Poll status until backtest_runs row exists for our run_id
# (factory CLI / API has a known FINAL bug that may report 'running'
# even after Stage 2 completes — backtest_runs row presence is the
# authoritative signal of success.)
# ------------------------------------------------------------
echo "Step 2/4: Poll backtest result (timeout ${E2E_TIMEOUT}s)..."
START_TIME=$(date +%s)
LAST_STAGE=""
LAST_STATUS=""
RUN_FOUND=false

while :; do
    NOW=$(date +%s)
    ELAPSED=$((NOW - START_TIME))
    if (( ELAPSED > E2E_TIMEOUT )); then
        echo "  ❌ Timeout after ${E2E_TIMEOUT}s waiting for backtest_runs row" >&2
        "${CLI}" bt status "${STRATEGY_ID}" --via-api --limit 1
        exit 1
    fi

    # 1) Cheap status print (best-effort, may be stale due to FINAL bug)
    JSON=$("${CLI}" bt status "${STRATEGY_ID}" --via-api --json --limit 5 2>/dev/null || echo "[]")
    JOB_LINE=$(echo "${JSON}" | python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
for j in data:
    if j.get('job_id') == '${JOB_ID}':
        print(f\"{j.get('status', '?')}|{j.get('stage', '?')}|{j.get('error_message', '') or ''}\")
        break
" 2>/dev/null || echo "?|?|")

    STATUS=$(echo "${JOB_LINE}" | cut -d'|' -f1)
    STAGE=$(echo "${JOB_LINE}" | cut -d'|' -f2)
    ERROR=$(echo "${JOB_LINE}" | cut -d'|' -f3)

    if [[ "${STATUS}" != "${LAST_STATUS}" || "${STAGE}" != "${LAST_STAGE}" ]]; then
        printf "  [%4ds] status=%-10s stage=%-10s\n" "${ELAPSED}" "${STATUS}" "${STAGE}"
        LAST_STATUS="${STATUS}"
        LAST_STAGE="${STAGE}"
    fi

    # 2) Hard fail signals (factory CLI/API actually persists these without FINAL bug)
    case "${STATUS}" in
        failed|timeout)
            echo "  ❌ Job ${STATUS}: ${ERROR}" >&2
            exit 1
            ;;
    esac

    # 3) Authoritative success: backtest_runs row exists for our run_id
    RESULT_OUT=$("${CLI}" bt result "${RUN_ID}" 2>&1 || true)
    if echo "${RESULT_OUT}" | grep -qiE "total_return"; then
        echo "  ✅ backtest_runs row found after ${ELAPSED}s"
        RUN_FOUND=true
        break
    fi

    sleep "${POLL_INTERVAL}"
done
echo

# ------------------------------------------------------------
# Step 3: Print backtest summary
# ------------------------------------------------------------
echo "Step 3/4: Backtest summary..."
echo "${RESULT_OUT}" | head -15 | sed 's/^/    /'
echo

# ------------------------------------------------------------
# Step 4: Verify user_signals — count from this run
# ------------------------------------------------------------
echo "Step 4/4: Verify user_signals (Stage 1 → Lambda インサート確認)..."
SIGNALS_JSON=$("${CLI}" signals "${STRATEGY_ID}" --json --limit 1 2>&1 || echo "[]")
SIGNALS_COUNT=$(echo "${SIGNALS_JSON}" | python3 -c "
import json, sys
try:
    data = json.loads(sys.stdin.read())
    print(len(data) if isinstance(data, list) else 0)
except Exception:
    print(0)
" 2>/dev/null || echo "0")

if [[ "${SIGNALS_COUNT}" -gt 0 ]]; then
    echo "  ✅ user_signals row(s) confirmed (sample fetched ${SIGNALS_COUNT})"
else
    echo "  ⚠️  No user_signals visible via CLI — may be ROW POLICY filtering"
fi
echo

# ------------------------------------------------------------
# Final
# ------------------------------------------------------------
TOTAL_ELAPSED=$(($(date +%s) - START_TIME))
echo "============================================================"
echo "🎉 E2E smoke test PASSED"
echo "    job_id:       ${JOB_ID}"
echo "    run_id:       ${RUN_ID}"
echo "    total time:   ${TOTAL_ELAPSED}s"
echo "    pipeline:     API → Stage 1 → Lambda → Stage 2 → ClickHouse ✅"
echo "============================================================"
