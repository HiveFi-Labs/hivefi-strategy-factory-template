# ローカル単一戦略 task テンプレート

このファイルを `tools/symphony/local_tasks/<id>.md` にコピーして使う。
`state: Todo` の task が Symphony の default local workflow に拾われる。
1 task は 1 strategy だけを作る。
task は bulk 作成しない。大量に issue 化する場合でも、1 件ずつ出典、
収益源、ターゲット参加者、観測 proxy、期待符号、反証条件を確認してから
このテンプレートで 1 ファイルだけ作り、YAML / 重複を確認して次に進む。

```md
---
id: local-price-path-alpha-001
identifier: LOCAL-001
title: price-only パス形状 alpha 単一戦略
state: Todo
priority: 3
labels:
  - local
  - scaffold-only
created_at: "2026-05-08T00:00:00Z"
---

## 目的

以下のテーマで 1 本の戦略を生成する。

- テーマ:
- 想定メカニズム:
- 観測 proxy:
- 期待符号:
- 反証条件:
- strategy_id: 未定なら agent が規則に沿って 1 つ決める
- 利用データ: price-only
- Universe: hl_all
- Rebalance: W-FRI
- Mode: long-short
- Submit: なし
- Backtest / diagnostic window:
  - BT/report: earliest available .. 2025-12-31
  - Optional IS/OOS split: IS ends before 2025, OOS is within 2025 only

## 戦略要件

- `AGENTS.md` の推奨フローに沿って、仮説立案、data 可用性確認、
  target evidence、因子評価の順で進める。target evidence / IC evidence が
  evidence gate を満たさない、または測定不能な場合は、scaffold と `compute_signals` 実装に進まず、
  研究結果として `Done` にしてよい。
- 必要データがなく target evidence を観測できない場合は、
  `hivefi-factory data request` で `tools/symphony/data_requests/` に要望を作り、
  `## 結果` に request path と不足している dataset / fields / coverage を書く。
- strategy は `市場メカニズム -> 観測 proxy -> 期待符号 -> 売買ルール -> 反証条件`
  の演繹 chain から作る。parameter sweep で後付けしない。
- `{logic}-{timeframe}-{rebalance}-{exchange}-{universe}-{mode}-v2` 形式で
  strategy_id を 1 つだけ作る。
- evidence gate を通って code を作る場合だけ、`configs/<id>.json` と
  `extensions/<id>.py` を必ず両方持つ。
- 追加案を同じ task 内で作らない。別案は次 task の研究案としてコメントに残す。
- extension は HiveFi AST allowlist に収める。
- BT / report は `2025-12-31` で終え、そこまでの利用可能な最長期間を使う。
- `compute_signals` 冒頭に 2026 ガードを必ず入れる:
  ```python
  import datetime as dt
  ...
  if pd.Timestamp(ctx.date).date() >= dt.date(2026, 1, 1):
      return []
  ```
  `.date()` で tz を捨てるのは server BT で `ctx.date` が tz-aware だと
  `Cannot compare tz-naive and tz-aware timestamps` で例外を吐くため。
  hivefi-api `routers/data.py:_TEST_PERIOD_START` と整合させ、BT が test
  period (2026+) で signals を出さないようにする。

## 検証

```bash
python tools/symphony/strategy_batch.py --changed
python -m compileall extensions
pytest -q
```
```

## 全自動 pipeline を opt-in する場合

公式 BT + katsustats レポートまで Symphony agent が自走するには、上記
template の `## 目的` セクションで `Submit: なし` の行を以下のように書き換える:

```md
- Submit: あり (pipeline 実行)
  - `python tools/run_strategy_pipeline.py --strategy-id <id> --benchmark BTC --bt-end-date 2025-12-31`
  - rate limit 5/h を 1 件消費。完走後 `artifacts/katsustats/<id>/report.html` が出る
```

agent はこの記述を見て WORKFLOW.md の pipeline step で公式評価を呼び出し、
local validate → `hivefi strategy push` → `hivefi bt diag` → katsustats
report までを 1 連で実行する。最終報告では BT metrics を再掲せず、
`artifacts/katsustats/<id>/report.html` の path だけを書く。途中で失敗した場合は
`--skip-validate` / `--skip-push` / `--skip-diag` / `--skip-report` で部分再開できる。
