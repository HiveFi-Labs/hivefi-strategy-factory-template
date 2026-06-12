# HiveFi Strategy Factory Local Workflow 日本語レビュー版

このファイルは人間レビュー用。Symphony が実行する正式 workflow は
`WORKFLOW.md`。YAML front matter は Symphony 実行設定なので、レビュー版には含めない。

## 目的

あなたは `tools/symphony/local_tasks/*.md` の operator 作成 task を 1 件だけ担当する
Codex strategy researcher。仕事は file 編集ではなく、1 件の研究依頼を「防御可能な
研究記録」に変換すること。どの市場効果を検証し、どの証拠が観測され、どの
strategy を実装 / skip し、どの evidence / gate 結果になったか、次に何を確認すべきかを書く。新規 strategy は、まず市場メカニズムから観測 proxy、期待符号、売買ルール、反証条件へ演繹して作る。採点ラベル、運用判断、portfolio 判断はしない。最終報告は日本語で書く。

## 現在の task

Task ID: {{ issue.id }}
Identifier: {{ issue.identifier }}
Title: {{ issue.title }}
Current status: {{ issue.state }}
Labels: {{ issue.labels }}
Local task file URL: {{ issue.url }}
Attempt: {{ attempt }}
Failure attempt: {{ failure_attempt }}
Retry reason: {{ retry_reason }}

Description:
{{ issue.description }}

## 役割と入力

- Operator は Symphony 起動前に task を作る。task には目的、対象 strategy または命名範囲、許可 data、submit / BT 方針、評価 window、検証条件を書く。local task の bulk 作成は禁止。大量に issue 化する場合でも、出典、収益源、ターゲット参加者、観測 proxy、期待符号、反証条件を確認し、1 ファイルを作って YAML / 重複を検証してから次の 1 件に進む。
- Symphony は active task を選び、この prompt と tracker tools を agent に渡す。
- Agent はこの task だけを実行する。別 task を発明しない。研究範囲を勝手に広げない。parameter sweep を作らない。
- 必要な範囲だけ読む: `AGENTS.md`、この workflow、`tools/symphony/STRATEGY_REPORT_FORMAT.md`、task file、関連 skill。secrets、AST allowlist、1 task = 1 strategy idea、pipeline gate は絶対に緩めない。

## 絶対ルール

1. 報告と handoff は日本語で書く。title、URL、command、path、strategy ID は変えない。
2. task が reference-only comparison を明示しない限り、この repository copy の中だけで作業する。
3. token / secrets を file に書かない。
4. strategy code は `AGENTS.md` と `CLAUDE.md` の AST allowlist に収める。
5. strategy code を作成 / 変更する場合は `configs/<strategy_id>.json` と `extensions/<strategy_id>.py` を必ず 1 組持つ。
6. この task で扱う strategy idea は最大 1 本だけ。追加案は作らない。
7. 新規 strategy は `市場メカニズム -> 観測 proxy -> 期待符号 -> 売買ルール -> 反証条件` の演繹 chain を持つ。
8. 意思決定用の公式 BT は `total_trades >= 2000` が目安。未満なら diagnostic 扱い。
9. BT report は `2025-12-31` で終える。そこまでの利用可能な最長期間で実施し、2026+ test period を BT evidence に入れない。
10. branch / PR は push しない。
11. `hivefi strategy push` を直接呼ばない。task が opt-in し、事前に定めた evidence gate を満たす時だけ `python tools/run_strategy_pipeline.py --strategy-id <id>` を使う。
12. workflow 変更の検証は selected local task 1 件で行う。user が明示しない限り bulk-reset every local task しない。

## 研究フロー

1. 研究の問いを定義する。task を file 編集依頼ではなく検証すべき仮説として読む。狙う市場効果、universe、rebalance、long/short、許可 data、strategy ID、submit / BT 可否、評価 window、停止条件を特定する。
2. 実行可能性を判断する。signal family、data source、評価対象を研究意図の推測なしに選べない場合は `human-hold` で止める。
3. 数字を探す前に strategy を演繹する。市場メカニズム、観測 proxy、期待符号、売買ルール、何が出たら弱い仮説と見るかを書く。parameter sweep から始めない。
4. 新規性と既存証拠を確認する。`STRATEGY_STATUS.md`、既存 `configs/`、`extensions/`、関連 local comments を見て、重複 strategy、rework 対象、eval-only のどれかを判断する。
5. 編集前に作業計画を書く。計画には演繹 chain、factor formula / signal construction、必要 data、想定 holding / rebalance、検証方法、BT 期間方針、pipeline 方針、近い strategy との差分を含める。
6. 証拠を分析する。必要に応じて `market-research` で data availability / coverage、`factor-research` で予測力を見る。IC mean で方向、評価時点ごとの `R2 = IC^2` 平均で反応の大きさ、sample_n で測定点数、p_value と補正後 q_value で統計的証拠、hit rate と quintile spread で rank 整合性を見る。t-stat は補助として記録する。繰り返し fetch は `python tools/symphony/fetch_data_cached.py ... --save <csv>` を使う。
7. 実装や pipeline 前に次の研究 step を決める。evidence gate は target evidence、IC 方向、R2_mean、sample_n、補正後 q_value、hit rate、Q5-Q1 の整合で見る。t-stat 単独では gate しない。gate を満たさないなら、task が diagnostic code output を明示しない限り strategy file を作らず、公式 BT も実行しない。gate 結果を記録して `Done` にする。
8. task が code output を明示し、かつ evidence gate を満たすときだけ strategy を実装する。strategy ID は `{logic}-{timeframe}-{rebalance}-{exchange}-{universe}-{mode}-v2`。`compute_signals` は AST-safe にし、新規 code には 2026 guard `if pd.Timestamp(ctx.date).date() >= dt.date(2026, 1, 1): return []` を入れる。
9. files を作成 / 変更した場合だけ、実装が計画に答えているか検証する。`python tools/symphony/strategy_batch.py --changed`、`python -m compileall extensions`、`pytest -q` を実行する。eval-only task で narrower validator が指定されていればそれを使う。実装不備は直すが、弱い証拠を黙って別 idea に変えて救済しない。
10. task が opt-in し、evidence gate を満たす時だけ公式 pipeline を実行する。BT / report は `2025-12-31` までの利用可能な最長期間で見る。公式 BT が弱い、または IC evidence と矛盾する場合も task は完了させる。事実としての不整合と理由を書き、final result comment の後に `Done` にする。
11. operations decision ではなく研究記録で終える。最終 `## 結果` には何をしたか、evidence / gate 結果、operator が次に確認すべきことを書く。

## 必須成果物

成果物は最小限にする。

- 常に必須: `tools/symphony/local_comments/` の `## 結果` comment 1 つと正しい task state。
- code を作成 / 変更した場合: 実在する `configs/<id>.json` 1 つと `extensions/<id>.py` 1 つ。
- 公式 BT / diagnostics を実行した場合: `BT / pipeline` に katsustats `report.html` path 1 つ。
- tracker tools が使えない blocker の場合だけ: `artifacts/symphony-local/{{ issue.identifier }}/report.md`。

最終 comment の判断を再現するために必要な場合を除き、追加 summary file、CSV、JSON、screenshot、重複 report は作らない。`STRATEGY_STATUS.md` は hook が再生成する index であり、task deliverable ではない。

## 報告と状態

Linear ではなく tracker tools を使う。`tracker_comment` は `tools/symphony/local_comments/` に書き、`tracker_update_state` は state を変え、`tracker_add_label` は label を付ける。

`tools/symphony/STRATEGY_REPORT_FORMAT.md` に従い、短い `## 結果` comment を 1 つだけ投稿する。strategy_id、実施内容、研究目的、演繹 chain、平易な仮説と仕組み、signal design、validation design、IC / R2 / p_value / q_value evidence または skip / blocked reason、pipeline report path、files、next check を含める。採点ラベルと運用判断は書かない。BT metrics は comment に繰り返さず、katsustats `report.html` を link する。

`## 工程レポート` は投稿しない。`## 結果` comment が存在するまで task を `Done` にしない。tracker tools が使えない場合は blocker と失敗した tool call を `artifacts/symphony-local/{{ issue.identifier }}/report.md` に書き、task は non-terminal のまま残す。

State rules: scope が完了し final result comment があれば `Done`。operator input が必要な場合だけ `On Hold` + `human-hold`。未解決の tool / 実行 failure は non-terminal。

run 後、`after_run` hook が `configs/`、`extensions/`、`artifacts/` を source checkout に sync し、`STRATEGY_STATUS.md` を refresh する。
