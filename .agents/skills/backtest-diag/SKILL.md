---
name: backtest-diag
description: |
  `hivefi-factory bt result` で取得した backtest_runs / timeseries / trades、
  または push 前の strategy code/config を使って、戦略をどう改善すべきか診断する。
  post-BT では KPI、equity / drawdown、walk-forward、regime、cost / funding、
  過学習パターンを総合して判断する。pre-push / BT 不可時は、静的にできる診断と
  公式 BT が必要な診断を明確に分ける。SR / MaxDD の固定閾値だけで採否を決めない。
trigger:
  - user が "BT 結果がおかしい" "この戦略本当に機能する？" "過学習してない？" 等
  - `hivefi-factory bt poll` 完走後にレビュー依頼
  - `hivefi-factory strategy push` 前の最終チェック
  - user が "戦略を改善" "改善候補を出して" "live に耐えるか" と言う
---

# /backtest-diag skill

## 目的

戦略の改善判断を、単一 KPI のラベル付けで終わらせず、次の材料に分解して
提示する。

- 公式 BT 指標: Sharpe / MaxDD / return / total_trades / profit factor / cost / funding
- equity / drawdown の形状
- walk-forward 安定性 (CH の複数 run_id を比較)
- regime sensitivity (window を複数取って push し直す)
- strategy code / config の過学習・look-ahead・実装リスク
- 次に何を変え、どの検証結果なら判断が変わるか

**重要**: SR / MaxDD / profit factor の固定 threshold だけで採否を決めない。
閾値は警戒サインを探すための診断材料であり、最終判断は sample size、
WF、regime、cost/funding、実装リスク、既存 portfolio との独立性を合わせて
説明する。

## 先にモードを決める

依頼を受けたら、最初に次のどれかを明記する。

| モード | 入力 | やること | やってはいけないこと |
|---|---|---|---|
| Post-BT 診断 | `hivefi-factory bt result` で run_id 取得済 | KPI / WF / regime / cost / code scan から改善判断 | SR だけで live 可否を決める |
| Pre-push 静的診断 | strategy code/config はあるが公式 BT はまだ | AST / config / look-ahead / overfit scan、push 前 verification | live / approved 品質を断定する |
| BT 不可 fallback | API key / CLI / ClickHouse credentials がない | 静的診断と「BT が必要な診断」を分離 | BT 済みであるかのように語る |

API key / CH credentials / 公式 BT が使えない場合は、必ず「公式 BT は未確認」と書く。
HiveFi `hivefi-factory` が動かない状態で、戦略が検証済みだと装ってはいけない。

## 成果物保存

診断を実施したら、チャット回答だけで終えず、必ず repo 内に成果物を保存する。
artifact のディレクトリ名は `strategy_id` 固定ではなく `experiment_id` として扱う。
単一戦略の診断なら `experiment_id = strategy_id` でよい。比較実験や再診断なら
`<strategy_id>-postbt-2026-04-23` のように experiment 単位で分けてよい。
開始時点で `experiment_id` を決められない場合は、まず `backtest-diag-2026-04-23` や
`backtest-diag-2026-04-23-draft` のような暫定 slug を使ってよい。`strategy_id` や
比較条件が後で確定したら、必要なら最後にディレクトリ名を rename して report 内の path も合わせる。
artifact 保存を、正式 ID の確定待ちで止めない。

```text
artifacts/<experiment_id>/backtest-diag/
```

最低限、次のファイルを保存する。

```text
artifacts/<experiment_id>/backtest-diag/report.md
```

`report.md` には、ユーザー向けに返す診断内容と同じ主要情報を含める。

- 診断モード
- 使えた入力 / 使えなかった入力
- 結果サマリ
- 評価根拠
- 改善アクション
- 次のコマンド
- 実行した verification と結果
- 作成日時

追加 artifact がある場合は同じディレクトリへ置く。

```text
artifacts/<experiment_id>/backtest-diag/runs.csv
artifacts/<experiment_id>/backtest-diag/equity_curve.csv
artifacts/<experiment_id>/backtest-diag/trades.csv
artifacts/<experiment_id>/backtest-diag/equity_drawdown.png
artifacts/<experiment_id>/backtest-diag/commands.txt
```

`commands.txt` には、実際に実行した shell command とその要約結果を書く。
BT 不可 fallback でも `report.md` と `commands.txt` は作る。公式 BT が実行
できなかった理由（API key なし、CH credentials なし、API 不通など）を
`report.md` に明記する。

## Post-BT 診断

### 1. 公式 BT 指標の取得

`strategy_id` が分かっている場合、まず最近のジョブ一覧を取得する:

```bash
hivefi-factory bt status <strategy_id> --limit 20 --json > runs.json
```

特定の `run_id` の summary + equity + trades:

```bash
hivefi-factory bt result <run_id> --timeseries --trades --csv
```

CSV 化して保存:

```bash
hivefi-factory bt result <run_id> --timeseries --csv > equity_curve.csv
hivefi-factory bt result <run_id> --trades --csv > trades.csv
```

公式 BT をまだ走らせていない場合は、code を再 upload すれば新しい `run_id` で
Stage 1 + Stage 2 が回る (開始日は Orchestrator が data_requirements + pipeline +
filter + 執行 kline の min(time) の最大値 + warmup から runtime で動的算出する):

```bash
hivefi-factory code upload <strategy_id>
hivefi-factory bt poll <返ってくる job_id>
```

記録する最低項目 (`backtest_runs` の column):

- `run_id`
- `sharpe_ratio`
- `max_drawdown`
- `total_return`
- `annual_return`
- `total_trades`
- `profit_factor`
- `win_rate`
- `total_commission`
- `total_slippage`
- `total_funding_pnl`

`total_trades` が 2000 未満なら、KPI 表ではなく diagnostic として扱う。
2000 以上でも独立サンプルとは限らないため、WF / regime を必ず併記する。

### 2. 指標の読み方

以下は「即合否」ではなく、深掘り対象を見つけるための警戒サイン。

| 指標 | 警戒サイン | 追加で見るもの |
|---|---|---|
| SR が高いが total_trades が少ない | 見かけの高 SR | total_trades、期間、WF、同一日/同一銘柄への偏り |
| MaxDD が極端に小さい | look-ahead / 同日 close 約定疑い | strategy code、`ctx.date` と約定タイミング |
| profit_factor が極端に高い | 銘柄固有 fitting / tail 依存 | symbol hardcode、universe robustness |
| 負 SR 窓が多い | regime 依存 | 期間別に再 BT、下落/上昇/横ばい別 KPI |
| funding / commission / slippage が大きい | gross edge が cost に食われる | cost 1.5x / 2x 感度、turnover 近似 |

SR を出す場合は、可能なら不確実性も添える。

```text
sigma_SR ~= sqrt((1 + SR^2 / 2) / N)
95% CI ~= SR +/- 1.96 * sigma_SR
```

`N` に `total_trades` を使うのは近似。trade が独立でない、同一 regime に偏る、
銘柄間相関が高い場合は実効サンプル数が小さくなる。CI は判断材料であり、
WF / regime の矛盾を打ち消すものではない。

### 3. Equity / drawdown / WF

`backtest_timeseries` の CSV (`equity, time`) があれば、最低限次を確認する。

- equity の slope (期間別)
- drawdown 系列 = `1 - equity / equity.cummax()` の peak とその時期
- 直近 1/3 と全体の SR を分けて計算 (rolling 90/180 日)
- 期間別の trade 件数の偏り (`backtest_trades.entry_time` で histogram)
- return が一部期間だけに集中していないか

PNG を作れる場合は、上段 equity、下段 drawdown、別図で rolling SR を出す。
作れない場合は、CSV と表だけでよい。WF は ローカル pandas の rolling で計算する。

例 (`scripts/wf_local.py` を ad-hoc で作って良い):

```python
import pandas as pd
ts = pd.read_csv("equity_curve.csv", parse_dates=["time"]).set_index("time")
ret = ts["equity"].pct_change().dropna()
rolling_sr = (ret.rolling(90).mean() / ret.rolling(90).std()) * (252 ** 0.5)
print(rolling_sr.describe())
```

### 4. Regime sensitivity

公式 API は Orchestrator が runtime resolve した単一の開始日からしか走らない
(data source の min(time) + warmup の最大値) ため、regime ごとの BT を回したい
場合は **`backtest_timeseries` を期間で slice してローカルで再計算する** のが基本。

最低限、以下を分けて読む。

- 下落 / stress 期間
- 上昇 / risk-on 期間
- 横ばい / low-vol 期間
- 直近期間

```python
import pandas as pd
ts = pd.read_csv("equity_curve.csv", parse_dates=["time"]).set_index("time")
windows = {
    "stress_2022H2":   ("2022-06-01", "2022-12-31"),
    "risk_on_2023":    ("2023-06-01", "2024-06-01"),
    "recent_2024":     ("2024-01-01", "2024-12-31"),
}
for name, (start, end) in windows.items():
    sub = ts.loc[start:end]
    ret = sub["equity"].pct_change().dropna()
    sr = ret.mean() / ret.std() * (252 ** 0.5)
    print(f"{name}: SR={sr:.2f}, N={len(ret)}")
```

特定 regime だけ良い場合は「その regime でのみ候補」と書き、汎用 live 候補と
混同しない。

## Pre-push 静的診断

公式 BT が無い場合は、まず code/config を読む。

確認対象:

- `configs/<strategy_id>.json`
- `extensions/<strategy_id>.py`

最低チェック:

1. config の `strategy_id` とファイル名が一致する
2. `exchange`, `universe`, `rebalance_freq`, `warmup_periods` がロジックと矛盾しない
3. `data_requirements` と `ctx.data[...]` の実アクセスが一致する
4. AST denylist 外 import / `eval` / `exec` / `getattr` / file write / network 参照がない (`hivefi-factory validate <id>` で機械的に検査可能)
5. hardcoded symbol list / symbol 固有 `if` / 恣意的日付条件がない
6. `shift(-n)`、全期間集計、future label 参照などの look-ahead がない
7. long / short の symbol overlap が起きない
8. ranking の符号が説明と一致する（positive を long、negative を short 等）
9. 欠損や新規上場銘柄が ranking を歪めない
10. `_LOOKBACK`, `_TOP_N`, threshold などが細かすぎる最適化値になっていない

long / short overlap が起き得る場合は、単に指摘して終わらない。改善案として
少なくとも次のどちらかを提示する。

- long を先に選び、short は `returns.drop(index=longs)` など残り universe
  から選ぶ
- positive / negative など符号別 pool に分け、片側候補が不足する場合は
  無理に反対売買せず、実際に出す signal 数で weight を再計算する

どちらの場合も、候補不足時に weight 合計が 1.0 を超えないか、空 side を許容
するかを report に明記する。

push 前の軽量 verification:

```bash
hivefi-factory validate <strategy_id>
```

```bash
python tools/symphony/strategy_batch.py --strategy-id <strategy_id>
```

`strategy_batch.py` は AST denylist + synthetic compute_signals smoke を実行する
(network 不要、合成 OHLC データで gross exposure ≤ 1.0 を確認)。

push する場合:

```bash
hivefi-factory strategy push <strategy_id>
```

push が成功したら自動で Stage 1 が起動する。`hivefi-factory bt poll <job_id>` で完走を待つ。

## BT 不可 fallback

`hivefi-factory` が動かない (`HIVEFI_API_KEY` 未設定 / API 不通 / CH credentials なし) 場合は、
出力を次の 2 つに分ける。

### 今できる静的診断

- config / code の整合性
- AST denylist リスク (`hivefi-factory validate` は network 不要なので CH なしでも動く)
- look-ahead パターン
- hardcoded symbol / date / threshold
- long/short overlap
- ranking の符号整合性
- warmup と lookback
- 欠損 / 新規上場銘柄への耐性

### 公式環境が必要な診断

- Sharpe / MaxDD / total_return / annual_return
- total_trades が KPI として十分か
- commission / slippage / funding の drag
- WF 安定性
- regime sensitivity
- parameter sensitivity
- factor IC / forward return との対応

このモードでは live 可否を断定しない。最後に「BT 環境が戻ったら実行する
コマンド」と「期待する出力」を必ず書く。

## 改善アクションの出し方

改善提案は抽象論で終えず、次の表にする。

| 改善案 | 根拠 | 変更対象 | 検証方法 | 判断が変わる条件 |
|---|---|---|---|---|
| 例: long/short を符号で分離 | positive trend を short する可能性 | `extensions/<id>.py` ranking 部分 | `code upload` 後 BT + ローカル WF | 全体 SR を落とさず負 SR 窓が減る |
| 例: high-vol regime gate | stress 期で SR が崩れる | exposure / signal filter | regime 別 ローカル計算 | 下落期 DD が下がり、通常期 return が残る |

「判断が変わる条件」は固定閾値だけで書かない。期待する挙動、観測すべき
メトリクス、残るリスクを短く説明する。

公式 API に存在しない stress test（funding 除外、slippage 2x、cost 反転など）は
実行コマンドとして書かない。まず「検証案」として提示し、ローカルの後処理
(`equity_curve.csv` から再計算) で近似できるかを確認する。

## 出力フォーマット

ユーザー向けには必ず日本語で、次を含める。

### 診断モード

- Post-BT / Pre-push / BT 不可 fallback のどれか
- 使えた入力: <bt result / equity csv / trades csv / code / config>
- 使えなかった入力: <あれば明記>

### 結果サマリ

- 採否カテゴリ: 実装候補 / 改善後に再検証 / 診断止まり / 見送り
- 理由: <KPI, WF, regime, cost, code risk を合わせた説明>
- 注意: <BT 未実行、サンプル不足、公式環境なし等>

### 評価根拠

列名は省略しない。全モードで `期待 / 観測 / 判断 / 根拠` を書く。
観測できない項目は `観測=未確認` とし、何が取れれば判断できるかを
`根拠` に書く。

| 項目 | 期待 | 観測 | 判断 | 根拠 |
|---|---|---|---|---|
| sample | KPI として扱えるだけの total_trades | <observed> | <判断> | <理由> |
| WF | 特定 window だけに依存しない | <observed> | <判断> | <理由> |
| regime | 下落/上昇で破綻しない | <observed> | <判断> | <理由> |
| cost | fee/slippage/funding で edge が消えない | <observed> | <判断> | <理由> |
| code | overfit/look-ahead が見えない | <observed> | <判断> | <理由> |

### 改善アクション

| 優先度 | 改善案 | 変更対象 | 検証方法 | 判断が変わる条件 |
|---|---|---|---|---|
| 1 | ... | ... | ... | ... |

### 次のコマンド

```bash
...
```

### 保存先

- `artifacts/<experiment_id>/backtest-diag/report.md`
- `artifacts/<experiment_id>/backtest-diag/commands.txt`
- 追加 artifact: <equity csv / trades csv / png / raw output があれば列挙>

「SR が 1.5 以上だから OK」「MaxDD が小さいから OK」のような説明は禁止。
必ず何が観測され、なぜその判断になり、何が未確認かを書く。

## よくある失敗

- Post-BT 前提なのに、BT 不可環境で live 判定してしまう
- trigger に push 前チェックがあるのに、前提節だけ見て「BT が無いので不可」
  で止めてしまう
- `total_trades` を見ずに sample size を見落とす
- `hl_all` の tail symbol 依存を見ずに、全体 KPI だけで採否を決める
- t-stat / momentum ranking で、全銘柄が同符号の時に弱い positive を short
  してしまう問題を見落とす
- `ctx.data["price"].iloc[-1]` 系の signal が same-bar 約定にならないか確認しない
- `py_compile` などで `__pycache__` を残して作業ツリーを汚す
- チャットだけに診断を書いて `artifacts/<experiment_id>/backtest-diag/report.md`
  を残さない

## 関連 skill

- `/market-research` — データ品質の事前確認
- `/factor-research` — factor 単体の forward return 予測力評価
- `/strategy-scaffold-from-paper` — 改善後の strategy scaffold / variants 作成
- `/submit-flow` — push / Stage 1 auto trigger / 公式 BT
