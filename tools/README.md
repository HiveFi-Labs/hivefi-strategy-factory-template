# tools/

## e2e_smoke_test.sh

HiveFi Strategy API → Stage 1 (signal-gen) → Lambda signal_inserter →
Stage 2 (backtest) → ClickHouse 結果保存 までの完全パイプラインを **admin
権限なしの一般 tenant credential のみ** で検証する E2E スモークテスト。

bot-2509 Issue #1885 の実環境動作確認で開発した知見を元にしている。

### 使い方

```bash
# default: configs/demo-momentum-D-W-hl-all-ls-v2.json で実行
bash tools/e2e_smoke_test.sh

# 任意の戦略 ID
bash tools/e2e_smoke_test.sh persmom-20d-D-W-hl-all-ls-v2

# タイムアウト調整 (default 600s)
E2E_TIMEOUT=900 bash tools/e2e_smoke_test.sh
```

### 前提

- `.env` に 3 値設定済み (HIVEFI_API_KEY / CLICKHOUSE_USER / CLICKHOUSE_PASSWORD)
- `pip install -e .[dev]` 完了済み
- `configs/<strategy_id>.json` と `extensions/<strategy_id>.py` 両方存在

### 検証ステップ

| Step | 検証内容 |
|---|---|
| 0 | Strategy API health check |
| 1 | `strategy push` で config + code upload (Stage 1 を auto-trigger) |
| 2 | backtest_runs row が現れるまで polling (terminal 判定) |
| 3 | backtest 結果サマリ (total_return / sharpe ratio 等) |
| 4 | user_signals が CLI から見えること (ROW POLICY 経由の sanity check) |

### 終了コード

| Code | 意味 |
|---|---|
| 0 | succeeded — 全段完走、backtest_runs row 確認済み |
| 1 | failed — タイムアウト or status='failed' |
| 2 | setup error — .env / CLI / 戦略 config の不在 |

### 既知の制限

- factory CLI / Strategy API は backtest_jobs SELECT で `FINAL` を使うが
  ClickHouse Cloud の SharedMergeTree は `FINAL` 非対応 (= `ILLEGAL_FINAL`)。
  本スクリプトは status だけでなく **backtest_runs row 存在** を
  authoritative な成功シグナルとして扱うため、この CLI バグを回避する。
- tenant あたりの pending job 上限 (5) があるため、連続実行する場合は
  既存ジョブが完走する (or 失敗する) のを待つ必要がある。

### 例: 出力

```
============================================================
🧪 E2E Smoke Test: HiveFi Strategy API pipeline
============================================================
  Strategy:    demo-momentum-D-W-hl-all-ls-v2
  Timeout:     600s
============================================================

Step 0/4: API health check...
  ✅ API healthy

Step 1/4: Push strategy + upload code (auto-triggers Stage 1)...
created strategy: demo-momentum-D-W-hl-all-ls-v2
uploaded code vv1; job_id=de5583... run_id=851365...
  ✅ Pushed

Step 2/4: Poll backtest result (timeout 600s)...
  [   0s] status=queued    stage=signal_gen
  [  30s] status=running   stage=backtest
  ✅ backtest_runs row found after 90s

Step 3/4: Backtest summary...
    total_return: 35.27
    sharpe:        1.21

Step 4/4: Verify user_signals...
  ✅ user_signals row(s) confirmed

============================================================
🎉 E2E smoke test PASSED
    pipeline: API → Stage 1 → Lambda → Stage 2 → ClickHouse ✅
============================================================
```
