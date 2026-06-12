#!/usr/bin/env bash
# E2E onboarding test — 参加者シナリオ自動化
#
# hivefi-strategy-factory を fresh clone した状態から
#   1. パッケージ install
#   2. ローカル AST validate (network 不要)
#   3. API 疎通 (hivefi-factory health)
#   4. demo 戦略を strategy push (Stage 1 が auto trigger される)
#   5. ジョブの polling
#   6. backtest 結果の取得
# を自動で走らせて alpha tester onboarding の defect を検知する。
#
# 前提: HIVEFI_API_KEY と CLICKHOUSE_USER + CLICKHOUSE_PASSWORD が export 済み。
#
# Usage:
#   HIVEFI_API_KEY=hvf_prod_... \
#   CLICKHOUSE_USER=u_xxxxxx CLICKHOUSE_PASSWORD=... \
#     bash tests/e2e_onboarding.sh
#
# Exit code:
#   0 = 全 step 成功
#   1 = any step 失敗

set -u

WORKSPACE="${E2E_WORKSPACE:-/tmp/hivefi-e2e-onboarding-$$}"
REPO_URL="${E2E_TEMPLATE_REPO:-https://github.com/HiveFi-Labs/hivefi-strategy-factory.git}"
DEMO_ID="${E2E_DEMO_STRATEGY:-demo-momentum-D-W-hl-all-ls-v2}"
POLL_TIMEOUT="${E2E_POLL_TIMEOUT:-900}"

fail() { echo "❌ FAIL: $1" >&2; exit 1; }
ok()   { echo "✅ $1"; }

# ---------------------------------------------------------------------------
# Step 0: env check
# ---------------------------------------------------------------------------
[ -n "${HIVEFI_API_KEY:-}" ] || fail "HIVEFI_API_KEY is not set"
[ -n "${CLICKHOUSE_USER:-}" ] || fail "CLICKHOUSE_USER is not set"
[ -n "${CLICKHOUSE_PASSWORD:-}" ] || fail "CLICKHOUSE_PASSWORD is not set"
ok "Step 0: env credentials present"

# ---------------------------------------------------------------------------
# Step 1: fresh clone + install
# ---------------------------------------------------------------------------
rm -rf "$WORKSPACE" 2>/dev/null
git clone --depth 1 "$REPO_URL" "$WORKSPACE" >/dev/null 2>&1 \
    || fail "git clone $REPO_URL → $WORKSPACE failed"
cd "$WORKSPACE" || fail "cd $WORKSPACE failed"
[ -f CLAUDE.md ] || fail "CLAUDE.md missing after clone"
[ -f AGENTS.md ] || fail "AGENTS.md missing after clone"
[ -d .claude/skills ] || fail ".claude/skills/ missing after clone"
[ -d src/hivefi_factory ] || fail "src/hivefi_factory/ missing after clone"
[ -f "configs/${DEMO_ID}.json" ] || fail "demo config missing: configs/${DEMO_ID}.json"
[ -f "extensions/${DEMO_ID}.py" ] || fail "demo code missing: extensions/${DEMO_ID}.py"

python -m venv .venv >/dev/null 2>&1 || fail "python -m venv .venv failed"
. .venv/bin/activate
pip install --quiet --upgrade pip >/dev/null \
    || fail "pip upgrade failed"
pip install --quiet -e ".[dev]" >/dev/null \
    || fail "pip install -e .[dev] failed"
ok "Step 1: fresh clone + pip install -e .[dev]"

# ---------------------------------------------------------------------------
# Step 2: local AST validate (no network)
# ---------------------------------------------------------------------------
hivefi-factory --version >/dev/null \
    || fail "hivefi-factory CLI did not start"
hivefi-factory validate "$DEMO_ID" >/dev/null \
    || fail "hivefi-factory validate $DEMO_ID failed"
hivefi-factory validate --all >/dev/null \
    || fail "hivefi-factory validate --all failed"
ok "Step 2: local AST validate OK"

# ---------------------------------------------------------------------------
# Step 3: API health check
# ---------------------------------------------------------------------------
hivefi-factory health >/dev/null 2>&1 \
    || fail "hivefi-factory health failed (API key or endpoint?)"
ok "Step 3: API health OK"

# ---------------------------------------------------------------------------
# Step 4: push (config + code, Stage 1 auto trigger)
# ---------------------------------------------------------------------------
push_out=$(hivefi-factory strategy push "$DEMO_ID" 2>&1) \
    || fail "strategy push failed:\n$push_out"
job_id=$(echo "$push_out" | grep -oE 'job_id=[a-f0-9]+' | head -1 | cut -d= -f2)
run_id=$(echo "$push_out" | grep -oE 'run_id=[a-f0-9]+' | head -1 | cut -d= -f2)
[ -n "$job_id" ] || fail "could not parse job_id from push output:\n$push_out"
[ -n "$run_id" ] || fail "could not parse run_id from push output:\n$push_out"
ok "Step 4: strategy push → job_id=$job_id run_id=$run_id"

# ---------------------------------------------------------------------------
# Step 5: poll until terminal state
# ---------------------------------------------------------------------------
hivefi-factory bt poll "$job_id" --timeout "$POLL_TIMEOUT" --interval 10 >/dev/null \
    || fail "bt poll did not reach succeeded within ${POLL_TIMEOUT}s"
ok "Step 5: Stage 1 + Stage 2 succeeded"

# ---------------------------------------------------------------------------
# Step 6: fetch result
# ---------------------------------------------------------------------------
result_out=$(hivefi-factory bt result "$run_id" 2>&1) \
    || fail "bt result failed:\n$result_out"
echo "$result_out" | grep -q "sharpe_ratio" \
    || fail "bt result did not include sharpe_ratio"
echo "$result_out" | grep -q "max_drawdown" \
    || fail "bt result did not include max_drawdown"
echo "$result_out" | grep -q "total_trades" \
    || fail "bt result did not include total_trades"
ok "Step 6: backtest_runs row visible (KPI included)"

# ---------------------------------------------------------------------------
# 完了
# ---------------------------------------------------------------------------
echo
echo "🎉 onboarding E2E 完走 ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
echo "  workspace : $WORKSPACE"
echo "  strategy  : $DEMO_ID"
echo "  job_id    : $job_id"
echo "  run_id    : $run_id"
exit 0
