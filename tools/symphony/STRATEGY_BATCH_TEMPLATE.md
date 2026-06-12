# 戦略 task 作成メモ

このファイル名は historical に `BATCH_TEMPLATE` だが、bulk 作成用ではない。
Symphony の実行単位は **1 task = 1 strategy idea**。大量に idea がある場合でも、
local task は必ず 1 件ずつ作る。1 件ごとに出典、収益源、ターゲット参加者、
観測 proxy、期待符号、反証条件を確認し、YAML / 重複を検証してから次へ進む。
task の title / description / 最終報告は日本語で書く。論文タイトル、URL、
コマンド、strategy_id などの固有表現は原文のままでよい。

## 目的

以下のテーマから、次に作る **1 件** の研究 task を定義する。

- テーマ:
- 共通する市場メカニズム:
- この task の収益源:
- ターゲット参加者:
- 相手がなぜ不利な価格で売買するか:
- 観測 proxy:
- 期待符号:
- 反証条件:
- 利用データ: price-only / 指定データソース
- Universe: 指定がなければ `hl_all`
- Rebalance: 指定がなければ `W-FRI`
- Mode: 指定がなければ long-short
- Submit: なし / この 1 件だけ pipeline 実行
- Backtest / diagnostic window:
  - BT/report: earliest available .. 2025-12-31
  - IS:
  - OOS:

## task 要件

- `AGENTS.md` の推奨フローに沿って、仮説立案、data 可用性確認、
  target evidence、因子評価の順で進める。target evidence / IC evidence が
  悪い、または不明な idea は、scaffold と `compute_signals` 実装に進めず
  研究結果として完了してよい。
- 必要データがなく target evidence を観測できない idea は、
  `hivefi-factory data request` で不足データ要望を作り、request path を報告する。
- 各 strategy は市場メカニズムから演繹して作る。観測 proxy、期待符号、売買ルール、
  反証条件を書けないものは task 化しない。
  price-only の量産で `market-research` / `factor-research` を軽量化または省略する
  場合も、理由を `## 結果` の `IC` 行に書く。
- `{logic}-{timeframe}-{rebalance}-{exchange}-{universe}-{mode}-v2` 形式で
  strategy_id を 1 つだけ決める。
- evidence gate を通って実装する strategy は `configs/<id>.json` と
  `extensions/<id>.py` を必ず両方持つ。
- 小さなパラメータ差分だけで増やさず、signal source の多様性を優先する。
- すべての extension は HiveFi AST allowlist に収める。
- BT / report は `2025-12-31` で終え、そこまでの利用可能な最長期間を使う。
- 各 extension の docstring に、出典、仮説、crypto への翻訳メモを入れる。
- 報告は `tools/symphony/STRATEGY_REPORT_FORMAT.md` に従い、各 task の最後に
  `## 結果` 1 コメントだけを書く。運用判断や portfolio 判断は書かない。

## 検証

strategy files を作成または変更した task では以下を実行する。

```bash
python tools/symphony/strategy_batch.py --changed
python -m compileall extensions
pytest -q
```

submit / push が指定されている場合、この 1 件だけを対象にする。API key ごとに
1 時間あたり最大 5 件の rate limit を超えない。

## 成果物保存

生成または変更した `configs/` と `extensions/` は local task の成果物として残す。
evidence gate で実装しなかった task は、成果物を `なし (evidence gate で実装せず)`
として報告する。
この workflow では branch / PR を作らない。`after_run` hook が source checkout に
sync する。

## 報告

`tools/symphony/STRATEGY_REPORT_FORMAT.md` を使う。

- `factor-research` は `## 結果` の `分析結果` 行にまとめ、IC mean、R2_mean
  (`IC^2` の平均)、sample_n、t_stat、p_value、補正後 q_value、hit_rate、
  分位 spread、evidence の見え方を記録する。省略時は理由を書く。
  探索や variant 比較を含む場合は、p_value だけで進めず q_value と family size を書く。
- `## 結果` は研究記録にし、長い stderr や詳細表を再掲しない。
- `Strategy IDs created`, `Changed files`, `Local verification results` などの
  重複英語セクションは追加しない。
