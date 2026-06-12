---
name: symphony
description: |
  HiveFi strategy factory を Symphony で unattended に回すための運用手順。
  default は local file tracker で、Markdown task 1 件から strategy idea 1 件を
  研究し、必要に応じて scaffold / verify / submit / BT したいときに使う。
trigger:
  - user が "Symphony を導入" "orchestrator で戦略を量産" と言う
  - local task または Linear issue ベースで hivefi strategy factory を自動運用したいとき
---

# Symphony Operations

この repo では Symphony 本体を vendor しない。外部 checkout
`~/symphony/elixir` を使い、この repo の `WORKFLOW.md` を渡して起動する。
default は local file tracker で、Linear / GitHub は不要。

## Local Paths

- Strategy factory checkout: `HIVEFI_STRATEGY_FACTORY_SOURCE` で指定した checkout
- Symphony checkout: `~/symphony/elixir`
- Strategy workspaces: `~/code/hivefi-strategy-workspaces`
- Workflow file: `WORKFLOW.md`
- Local helper docs: `tools/symphony/README.md`
- Local tasks: `tools/symphony/local_tasks/*.md`
- Local comments: `tools/symphony/local_comments/*.md`
- Local task template: `tools/symphony/LOCAL_TASK_TEMPLATE.md`

## Start

1. `set -a; . ./.env; set +a` で `HIVEFI_API_KEY` と `CLICKHOUSE_*` を
   exported environment として読み込む。`HIVEFI_API_BASE` は未設定なら production
   default を使う。
2. `HIVEFI_STRATEGY_FACTORY_SOURCE="$PWD"`、
   `HIVEFI_STRATEGY_FACTORY_TASKS_DIR="$PWD/tools/symphony/local_tasks"`、
   `HIVEFI_STRATEGY_FACTORY_COMMENTS_DIR="$PWD/tools/symphony/local_comments"` を
   export する。`LINEAR_API_KEY` は default local workflow では不要。
3. `~/symphony/elixir` で `WORKFLOW.md` を指定して Symphony を起動する。

起動コマンドは `tools/symphony/README.md` を参照。token は file に書かない。
`tools/symphony/check_data_access.sh` が通らない状態では strategy issue を実行しない。
この smoke は小さい `hivefi-factory data fetch` を実行し、成功結果を 5 分だけ
cache する。

## Strategy Issue Policy

- 1 local task は 1 strategy だけを作る。複数 strategy を 1 task に詰めない。
- local task の bulk 作成は禁止。大量に issue 化する場合でも、1 件ずつ
  出典、収益源、ターゲット参加者、観測 proxy、期待符号、反証条件を確認し、
  `tools/symphony/local_tasks/<id>.md` を 1 ファイル作成して YAML / 重複を
  検証してから次の 1 件に進む。一括生成 script、巨大 patch、テンプレ変数だけを
  差し替える流し込みは使わない。
- issue 作成前の既存確認では、失敗した shell command の結果から結論を出さない。
  `rg` の exit code 1 は no match として扱ってよいが、shell parse error、
  quote error、command substitution error、pipeline error は検索未完了として扱い、
  より単純な command で必ず再実行する。
- 検索 command は単純にする。Markdown backtick を含む pattern を double quote で
  shell に渡さない。strategy_id / identifier / paper title はまず `rg -F -n -e <literal>`
  か、1 pattern ずつの `rg -n '<plain-regex>' ...` で確認する。複雑な `sed` 置換、
  長い alternation、shell quote が混ざる 1 行 command は避ける。
- 「既存 task には無い」「重複なし」と書く前に、少なくとも
  `tools/symphony/local_tasks`, `tools/symphony/local_comments`, `configs`, `extensions`,
  `STRATEGY_STATUS.md` を対象に、strategy_id と主要 title / author の両方で検索する。
  検索 command が失敗した場合は、結論を出さずに失敗理由と再検索結果を確認してから進む。
- 戦略作成は `AGENTS.md` の推奨フローに従う。仮説立案、data 可用性確認、
  因子評価、`strategy-scaffold-from-paper` 型の scaffold、`compute_signals`
  実装、local verification の順で進め、push/submit / 公式 BT / 診断は ticket が明示した
  場合だけ行う。
- 新規 strategy は演繹的に作る。最初に「誰がなぜお金を落とすのか」を書く。
  具体的には、収益源になる市場参加者、相手が不利な価格で売買する理由、
  その行動が作る市場の歪み、歪みの観測 proxy、期待符号、売買ルール、
  反証条件を書いてから data / IC を見る。parameter sweep から始めない。
- 収益源は `momentum`, `mean-reversion`, `volatility` などの factor 名で終わらせない。
  例: `短期 momentum` ではなく、`遅れて急騰銘柄を買う参加者の追随買いが尽きた後の反落を short で取る` のように、
  誰のどの行動がこちらの PnL になるかまで書く。
- IC の前に、個別の仮説検証フェーズを置く。ターゲット参加者がいそうな局面と
  いなさそうな局面を事前に定義し、その違いが data に出ているかを見る。
  例: 追随買い仮説なら「急騰 + 出来高増 + funding 上昇」をターゲットあり、
  「急騰していない / 出来高が増えていない / funding が中立」をターゲットなしとし、
  proxy が前者でだけ強く出るか確認する。
- ターゲット存在検証で、ターゲットあり / なしの切り分けが data 上で作れない場合は、
  IC 方向や R2_mean が見えても収益源の説明は未確認として扱う。final comment には
  `target_evidence=present/weak/missing/skipped/blocked` のどれかを書く。
- target evidence が作れない、補正後有意性がない、sample が少ない、または方向が
  事前仮説と整合しない場合は、strategy files を作らず研究結果として `Done` にしてよい。
  弱い仮説を実装して形だけの strategy にしない。
- evidence gate を通って strategy code を作る場合は、必ず `configs/<id>.json` と
  `extensions/<id>.py` のペアで作る。ここでの evidence gate は、target behavior が
  data 上で観測でき、`R2_mean` と sample が記録され、事前に定義した検定ファミリー内で
  Benjamini-Hochberg FDR などの多重検定補正後 `q_value` が task の基準を満たし、
  IC / 分位 spread の方向が仮説と矛盾しないことを指す。
- `strategy_id` は `{logic}-{timeframe}-{rebalance}-{exchange}-{universe}-{mode}-v2`。
- 同じ task 内で tiny parameter sweep を増やさない。別案は次 task の研究案として
  local comment に残す。
- push は write 60/min/user、同時 pending backtest jobs 5 件の制限を意識する。
  多数 task 実行時は scaffold と local verify を先に行い、submit は優先順位を付ける。
- KPI として扱う公式 BT は `total_trades >= 2000` を目安にする。短い window は
  diagnostic と明記する。
- BT report は `2025-12-31` で終え、そこまでの利用可能な最長期間を使う。
  2026+ test period は BT evidence に入れない。
- Symphony run の完了条件は、scope に応じて変わる。実装した task は生成した
  `configs/` と `extensions/` が local checkout に sync され、local comment に
  最終報告が残ること。evidence gate で実装しない task は、最終報告に
  `成果物: なし (evidence gate で実装せず)` と skip 理由が残ること。
- local task の報告は `tools/symphony/STRATEGY_REPORT_FORMAT.md` に従い、
  最後に `## 結果` 1 コメントだけを投稿する。`## 工程レポート` は使わない。
- `factor-research` の IC mean、`R2_mean`、t_stat、p_value、q_value、hit_rate、
  分位 spread、sample、検定ファミリー数は `## 結果` の `分析結果` 行に記録する。
  省略時は理由を日本語で書く。探索や variant 比較を含む場合は、p_value だけで
  次工程に進めず、補正後の q_value を使う。
- 既存の古い local comment に `p_value` / `q_value` が無い場合、ログだけから
  補正後有意性を作ったように書かない。再評価 task で evidence analysis を
  rerun した時だけ `q_value` を追加し、未再分析のものは index 上 `q未記録` のままでよい。
- IC とは別に、ターゲット存在検証の結果を書く。最低限、ターゲットあり条件、
  ターゲットなし条件、観測した差分、`target_evidence` を `## 結果` の
  `仮説検証` または `分析結果` に残す。未実施なら skip 理由を書く。
- IC / data check で同じ price / kline CSV を繰り返し取得する場合は
  `python tools/symphony/fetch_data_cached.py <source_key> ... --save <csv>` を優先し、
  並列 task 間で `/tmp/hivefi-strategy-data-cache` を共有する。
- `## 結果` は短い研究記録に留め、strategy_id、実施内容、実装したか / 実装せず
  分析で止めたか / 評価のみか、研究目的、収益源、ターゲット参加者、
  なぜ相手が不利な価格で売買するか、ターゲット存在検証、signal design、
  IC / R2 / 補正後 q_value evidence、pipeline report path、files、次に確認すべきことを書く。
  運用判断や portfolio 判断は書かない。投稿前に `Done` へ移動しない。
  file を作っていない場合は、作ったように書かない。
  `Strategy IDs created` や `Changed files` などの重複英語 section は追加しない。
- workflow 修正の動作確認は、まず 1 件の task だけで行う。ユーザーが明示しない
  限り、全 task を一括で `Rework` に戻さない。

## Verification

strategy files を作成または変更した Symphony turn の完了前に最低限これを確認する:

- `python tools/symphony/strategy_batch.py --changed`
- `hivefi-factory validate --all`
- `python -m compileall extensions`
- `pytest -q`

strategy files を作っていない evidence-only task では、上記の実装検証は skip し、
skip 理由を `## 結果` に書く。公式 push / BT は ticket が明示しており、
かつ認証情報が揃っている時だけ実行する。

## Local Task Template

単一 strategy task は `tools/symphony/LOCAL_TASK_TEMPLATE.md` の形で作る。
特に strategy_id、許可 data、submit 方針、IS/OOS window に加えて、
収益源、ターゲット参加者、相手がなぜお金を落とすのか、
ターゲットあり / なしをどう data で切り分けるかを明記する。
