---
name: submit-flow
description: |
  ローカル AST 事前検査 → `hivefi-factory strategy push` で公式登録 (config + code、
  Stage 1 を自動 trigger) → `hivefi-factory bt poll` で完走待ち → `hivefi-factory bt result`
  で KPI 取得、までを 1 flow で完走させる。成功 / 失敗の結果を
  `artifacts/<experiment_id>/submit-flow/report.md` に自然な日本語レポートとして保存する。

trigger:
  - user が "これ push して" "公式 BT まで通して" "submit して" 等
  - 戦略 code が書き終わったタイミング
---

# /submit-flow skill

## 目的

実装済戦略を HiveFi マルチテナント Strategy API
(`https://strategy-api.hivefi.xyz`) に登録し、Stage 1 (signal-gen) → Stage 2
(backtest) を完走させて `backtest_runs` に KPI を残すまで 1 flow で通す。
結果はチャットだけで終わらせず、repo 内に成果物として保存する。
artifact のディレクトリ名は `strategy_id` 固定ではなく `experiment_id` として扱う。
単一戦略の push / BT なら `experiment_id = strategy_id` でよい。比較実験や再実行では
`<strategy_id>-retry-2026-04-23` のように experiment 単位で分けてよい。
開始時点で `experiment_id` を決められない場合は、まず `submit-flow-2026-04-23` や
`submit-flow-2026-04-23-draft` のような暫定 slug を使ってよい。`strategy_id` や
比較条件が後で確定したら、必要なら最後にディレクトリ名を rename して report 内の path も合わせる。
artifact 保存を、正式 ID の確定待ちで止めない。

```text
artifacts/<experiment_id>/submit-flow/report.md
artifacts/<experiment_id>/submit-flow/commands.txt
```

`report.md` は短いログ貼り付けではなく、人間が読んで状態を判断できる自然な日本語レポートにする。新しい report 形式を作るときは、まず対象 run の report を手動で自然に書き、その形を次回以降の参考にする。
参考 canonical example: `artifacts/trendscan-30d-D-W-hl-all-ls-v2/submit-flow/report.md`

## 前提

- `configs/<id>.json` + `extensions/<id>.py` が存在
- `HIVEFI_API_KEY` 設定済 (`hivefi-factory health` で疎通確認可)
- `CLICKHOUSE_USER` + `CLICKHOUSE_PASSWORD` 設定済 (`bt poll` / `bt result` に必要、`strategy push` 自体は API key だけで動く)
- `id` は server-side regex (`{logic}-{timeframe}-{rebalance}-{exchange}-{universe}-{mode}[-f_{filter}]*-v2`) を満たす

## 手順

### 1. ローカル AST 事前検査 (network 不要)

```bash
hivefi-factory validate <id>
```

- exit 0 = AST 違反なし。次へ進める
- exit 1 = denylist hit / SyntaxError あり。詳細メッセージを参加者に説明:

```
ERROR: Forbidden constructs: import os
  → extensions/<id>.py の `import os` を削除してください。
     file 操作が必要なら pandas.read_csv() のように pandas 経由で行ってください。

ERROR: Forbidden constructs: call to getattr()
  → 動的属性アクセスは server-side denylist で reject されます。
     compute_signals 内部での getattr(obj, 'attr') は obj.attr に書き換えてください。
```

修正 → 再 validate のループ。`hivefi-factory validate --all` で全戦略を一括検査も可。

### 1b. ローカル signal smoke (synthetic data で `compute_signals` を 1 回呼ぶ)

```bash
hivefi-factory smoke <id>
```

`extensions/<id>.py` を import して synthetic price (220d × 20 symbols) で
`compute_signals` を 1 リバランス分だけ走らせ、戻り値の型・属性・gross_exposure
(≤ 1.0) を検査する (network 不要、< 1 sec)。

検出できる主な submit 前バグ:
- compute_signals が runtime exception を投げる (KeyError / TypeError / shape mismatch)
- 戻り値が `None` / `str` 等 `list[Signal]` でない
- signal の `side` が `"buy"`/`"sell"` 以外
- `percentage` が非数値 / 非正
- gross exposure が 1.0 を超える (over-leverage)

Method A pipeline 戦略 (`config.pipeline` が定義済) は extensions が
placeholder なので smoke 対象外 = `mode=pipeline` の OK 扱いで skip。

注意: synthetic data には `price` 等の主要 key しか含まれないため、
`oi` / `funding` / `chain_fees` などを `compute_signals` 内で参照する Method C
戦略は smoke で「no signals」warning が出やすい。production data で意味のある
signal が出るかは別途確認すること。

`hivefi-factory strategy push` / `hivefi-factory code upload` を実行すると
自動的に smoke が走り、失敗時は network call 前に exit する。明示的に
skip する場合は `--no-smoke` を付ける (NOT recommended: server-side で同じ
exception が出る場合は Stage 1 で fail する)。

### 2. Push (config + code、Stage 1 を自動 trigger)

```bash
hivefi-factory strategy push <id>
```

内部処理:
1. `configs/<id>.json` を `POST /v1/strategies` に送って 201 (新規) or 409 (既存)。409 の場合は自動で `PUT /v1/strategies/{id}` で update に fallback (`--no-update` で抑制可能)
2. `extensions/<id>.py` を `POST /v1/strategies/{id}/code` に multipart upload。server で AST 再検査 + S3 PUT + Stage 1 ジョブ自動 enqueue
3. レスポンスとして `version` (v1, v2, ...)、`job_id`、`run_id` が表示される

主なエラーパターン:
- **422 Forbidden constructs: ...** → server-side denylist が hit。pre-flight で見落とされたパターンを修正
- **422 SyntaxError: ...** → コードに syntax error。修正
- **409 strategy_id is not available** → 既存戦略 (他テナントを含む)。`--no-update` を使わず再実行すれば自動 PUT に fallback する
- **413 code exceeds 1048576 bytes** → 1 MiB 超過。コードを分割 / 不要な大コメントを削除
- **429 Too Many Requests** → pending ジョブが 5 件超え。`hivefi-factory bt status <id>` で stuck ジョブを確認、必要なら admin に相談
- **401 Invalid API key** → `.env` の `HIVEFI_API_KEY` を見直す
- **500 Internal error (correlation_id=...)** → server 側の障害。`correlation_id` を控えて admin に共有

### 3. Stage 1 + Stage 2 完走待ち

```bash
hivefi-factory bt poll <job_id> --timeout 900 --interval 10
```

ClickHouse `backtest_jobs` を 10 秒間隔で polling し、`succeeded / failed / timeout`
のいずれかになったら exit する (succeeded で 0、failed/timeout で 1)。

通常 5〜10 分で完走する (Stage 1 で 1〜3 分、Stage 2 で数分)。timeout (15 分) を超える場合は
コードのループ / データ量を疑う。`hivefi-factory bt status <id>` で `stage` 列を見ると
`signal_gen` で詰まっているのか `backtest` まで進んでいるのかが分かる。

失敗時の `error_message` 列は CloudWatch Logs に紐づくため、長い traceback は出ない。
HiveFi 管理者に `job_id` を伝えて調査を依頼するのが王道。

### 4. KPI 取得

```bash
hivefi-factory bt result <run_id> --timeseries --trades
```

`backtest_runs` から summary、`backtest_timeseries` から equity curve、`backtest_trades` から
trade 履歴をまとめて取得する (`--csv` でファイル化可能)。

**サンプル数の前提** (CLI/server では validate しない、agent が守る):
- **KPI として提示する BT は `total_trades ≥ 2000` を目安**。未達なら diagnostic 扱い、KPI 表に並べない
- Sharpe の標準誤差 σ_SR ≈ √((1 + SR²/2) / N) は N に依存。window 長より **サンプル数 (trades)** で判定する方が本質的
- 2000 到達ライン: weekly × 6 signals × 4 年 ≈ 2000 / weekly × 20 signals × 1.2 年 ≈ 2000 / daily × 6 signals × 1 年 ≈ 2000
- **IS/OOS の 2 window 構成を推奨**、各 window でも 2000 以上を目標
- 単一 regime 期間の評価は regime-specific な結論に留める

### KPI の読み方 (trades ≥ 2000 の前提で)

**単独 SR / MaxDD に固定 threshold を機械的に当てはめない** (SR > 1.0 は◯、MaxDD > 30% は✗ 等)。最終的な portfolio 構築は **operator 側の MV / RP / MD 最適化**が別途行う前提。参加者は以下に集中する:

- **単独で意味ある alpha** (SR が正 かつ CI がゼロを跨がない、再現性あり)
- **既存 approved 戦略と signal source が異なる** (相関が低くなる) 方向性を優先
- SR を報告する時は σ_SR ≈ √((1+SR²/2)/N) + 95% CI を必ず併記

### KPI の 2 系統 (仕様)

KPI は 2 つの計算系統に分かれる:

| 系統 | 入力 | 反映コスト | 含む KPI |
|---|---|---|---|
| **equity-curve ベース** | 日次 equity (net AUM) の時系列 | **funding + commission + slippage 全て反映後** の net 値 | `sharpe_ratio`, `daily_sharpe_ratio`, `max_drawdown`, `total_return`, `annual_return` |
| **trade-単体ベース** | 各 trade の P&L | funding は **反映されない** (price + commission + slippage のみ) | `win_rate`, `profit_factor`, `total_trades` |

→ **SR / MaxDD / total_return は funding 込みの net 値**。`profit_factor` と `total_return` が乖離する場合は funding が主要因 (profit_factor=1.00 & total_return=-22% なら trade 単体 break-even、損失は funding)。

### 各 KPI の意味

| KPI | 意味 | 解釈の文脈 |
|---|---|---|
| `sharpe_ratio` | 平均リターン / 標準偏差 (equity-curve ベース、**funding/commission/slippage 全て込みの net**) | 絶対値で機械的に切らない。必ず σ_SR (CI) と併読 |
| `max_drawdown` | equity curve の最大下落率 (**net**) | 単独値だけで却下しない (operator 側の合成で緩和されうる) が、50% 超は stand-alone 運用耐性が低いので警戒 |
| `total_return` | 累積幾何リターン (compound、**net**) | `annual_return` と合わせて読む。**SR (算術平均ベース) が正 でも total_return が負**の場合あり → vol drag (AM-GM inequality)。高 vol 戦略で顕著 |
| `annual_return` | 年率 CAGR (**net**) | 複利ベース |
| `win_rate` | 勝ち trade 率 (**trade 単体、funding 除く**) | hit rate。50% 未満でも profit_factor 次第で収益可 |
| `profit_factor` | 勝ち trade 合計 / 負け trade 合計 (**trade 単体、funding 除く**)。**1.0 = break-even** | 1.0 以下は必ず赤字、1.5+ が安定域。`total_return` が負なのに PF=1.0 なら funding が損失要因 |
| `total_trades` | 期間中の total trade count | norm 基準 (≥ 2000) |
| `total_commission` | **取引手数料の累積コスト** (USD、負値として equity から引かれる) | 低 ∝ 頻繁 rebalance でなければ小さい |
| `total_slippage` | **執行滑り (bid-ask spread + impact) の累積コスト** (USD) | `total_commission` と同時に見る。両者を `|coast|` として合算評価 |
| `total_funding_pnl` | perp の **funding 累積 P/L** (USD)。**マイナスは支払い** | long-only perps の慢性赤字要因 |
| `turnover` *(未実装 / 追加要請中)* | 年率化した portfolio の入替率 = Σ\|Δweight\| / 2 / years。例: 5x/年 = 年 5 回 portfolio 全入替相当 | **capacity** と **cost sensitivity** の尺度。高 turnover (≥ 50x/年) は AUM 拡大でコスト劣化。`total_commission` / `total_slippage` と合わせて **commission_bps × turnover / 10000** が年率コスト相当 |

### 整合性チェック (レポート前に必ず確認)

- **SR 正 & total_return 負** → vol drag 可能性。`annual_return` と `std` を見て妥当か判断し、レポートで「vol drag 懸念」と付記
- **total_commission ≈ total_slippage** → 同一ロジックで計算されている可能性 (別 metric のはずなので疑う)。公式 BT の計算式を operator に確認
- **total_funding_pnl の絶対値 > total_return の絶対値** → 戦略の勝敗が funding 決定論的、perp premise 再検討
- **turnover が出力に無い** (現状) → 暫定として `total_commission / commission_rate_bps × 10000 / avg_equity / years` で逆算可能。bt result 出力に `turnover` field が追加されるまでの stop-gap

### 誤用警告 (やらないこと)

- 「SR > 1.0 だから検討に値する」と単独数値で機械的に採択 / 却下しない
- CI がゼロを跨ぐ SR を "alpha あり" と宣言しない
- 単一 window の KPI だけで out-of-sample を推定しない

### 5. report.md と commands.txt を保存

`commands.txt` には、実際に実行した command と要約結果を時系列で残す。secret / token / raw credential / API key / ClickHouse password は書かない。

`report.md` は以下の構成を基本にする。BT が完走した場合も、失敗した場合も、まず「今回わかったこと」を自然文で説明し、その後に必要な表を置く。

1. `# <戦略の短い説明>`
2. `作成日時: YYYY-MM-DD HH:MM:SS TZ`
3. `## 今回わかったこと`
4. `## 実行した対象`
5. `## 提出結果`
6. `## BT 結果`
7. `## 評価できること / できないこと`
8. `## 次の作業`
9. `## 実行ログ`
10. `## 生成物`

書き方のルール:

- 冒頭で push と BT の最終状態を 2〜3 段落で説明する。
- 本文の説明では、API field 名・変数名・内部 status 名を主語にしない。`run_id`, `total_trades`, `sharpe_ratio` のような内部名は、再現用の表・実行ログ・code block に限定する。
- 結果 table の label と状態も日本語にする。例: `succeeded` / `queued` / `running` / `failed` を説明欄の主表にそのまま置かず、「成功」「待機」「実行中」「失敗」と書く。raw status が必要なら `実行ログ` に残す。
- push が成功なら、「手元の事前検査」「提出 ID」「Stage 1 の状態」「Stage 2 の状態」のような人間向け label でまとめる。
- BT が完走した場合は、期間、BT実行ID、状態、取引数、シャープレシオ、最大ドローダウン、累積リターン、年率リターン、勝率、損益倍率、手数料、スリッページ、funding 損益を書く。
- BT が failed / blocked の場合は、KPI を推測で埋めない。「失敗した処理」「失敗理由」「次に必要な作業」を人間向け label で明記する。
- 取引数が 2000 未満の場合は KPI ではなく診断結果と書く。
- SR / MaxDD / return に固定 threshold を当てて機械的に採否を決めない。
- `実行ログ` は raw stdout 全文ではなく、主要 command と要約結果の table にする。
- `生成物` には `report.md` と `commands.txt` を必ず載せる。

BT 完走時の結果文は、数値表の前に自然文で解釈を書く。

例:

```text
提出は成功で完了した。BT も完走し、取引数は 3120 だったため、サンプル数の面では KPI として読める。ただしシャープレシオの信頼区間、期間ごとの偏り、コストと funding の寄与を見るまでは、単独の採用判断にはしない。
```

BT failed 時の結果文は、戦略問題と環境問題を分ける。

例:

```text
バックテスト依頼は API に受理され、待機状態から実行中までは進んだ。しかし Stage 1 が ClickHouse Cloud との接続で失敗した。これは戦略ロジックの実行時エラーや成績悪化ではなく、実行環境側の通信問題である。
```

## 注意

- **書き込み rate limit**: 60 req/min/user。push (config + code) で 2 req 消費するので、batch 投入時は注意
- **同時 pending ジョブ上限**: 5 件 (per-user)。超えると 429。
- **bt poll は長い**: 1 年分の BT で 5〜10 分が目安。polling 途中で agent は wait message を入れる
- **code 再 upload で新版扱い**: 同 strategy_id で code upload を再実行すると `v{N+1}` ディレクトリに保存され、新 `job_id` / `run_id` で Stage 1 が走る
- **削除前 pending check**: `hivefi-factory strategy delete <id>` は pending ジョブが残っていると 409。完了を待つ

## 関連 skill

- `/factor-research`: push 前の仮説検証
- `/backtest-diag`: BT 完走後の多角診断
