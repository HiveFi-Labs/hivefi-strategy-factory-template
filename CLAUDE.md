# CLAUDE.md — hivefi strategy factory

このリポジトリは **HiveFi Strategy Contest 参加者が手元で戦略開発するための template**。
Claude Code として起動されたあなたは、このファイル + `.claude/skills/*` を参考に参加者を支援する。

## プロジェクト概要

参加者が戦略 (Python code) を書き、HiveFi マルチテナント Strategy API
(`https://strategy-api.hivefi.xyz`) に push すると:

1. API server が AST denylist + StrategyV2 継承チェックで code を validate
2. S3 (`users/{user_id}/strategies/{id}/v{N}/code.py`) に保存され、`backtest_jobs` 行を `queued` で INSERT
3. Stage 1 Worker (隔離 Fargate task) が signal-gen を走らせ `user_signals` に書込み
4. Stage 2 Worker が `user_signals` から `BacktestEngineV2` で P&L を計算し
   `backtest_runs / timeseries / trades` に書込み
5. クライアントは ClickHouse Cloud に直接接続して結果を SELECT する (ROW POLICY で自テナントのみ可視)

agent (あなた) の役目は **自然言語の依頼を `hivefi-factory` CLI 呼出と Python 編集に翻訳すること**。

**複数戦略の unattended 運用・タスクキュー駆動は Symphony を使う。** 対話的に 1 本だけ作る場合は
この CLAUDE.md の推奨フローと skill を直接使ってよい。Symphony は外部 checkout
`~/symphony/elixir` + 本 repo の `WORKFLOW.md` で起動し、詳細は `/symphony` skill、
`WORKFLOW.md`、`tools/symphony/README.md` を参照する。

## 参加者が使う primitive コマンド (`hivefi-factory` CLI)

`hivefi-factory` は本リポジトリ同梱の Python パッケージ (`src/hivefi_factory/`)。
`pip install -e .` 後に使える。venv を activate していなければ `python -m hivefi_factory ...` でも同等。

```bash
# 戦略 (config + code) を 1 発で push (Stage 1 を auto trigger)
hivefi-factory strategy push <id>            # configs/<id>.json + extensions/<id>.py
hivefi-factory strategy list                 # 自分の戦略一覧 (HTTP API)
hivefi-factory strategy show <id>
hivefi-factory strategy delete <id>

# code 単独再 upload (新バージョン v{N+1} を自動採番、再 BT が走る)
hivefi-factory code upload <id> [<file>]

# バックテストジョブ・結果 (ClickHouse 直読、ROW POLICY で自分のだけ見える)
hivefi-factory bt status <id>                # jobs 一覧
hivefi-factory bt poll <job_id>              # 完了まで polling (succeeded/failed/timeout)
hivefi-factory bt result <run_id> [--timeseries] [--trades] [--csv]

# market data (CH 経由のパネルフェッチ、研究用)
hivefi-factory data fetch <table> --symbols BTC ETH SOL --start 2024-01-01
hivefi-factory data request --idea "..." --needed-data "..." --reason "..."

# 自分のシグナル (debug 用)
hivefi-factory signals <id> [--csv]

# upload 前のローカル AST 事前検査 (server denylist と同じルール、network 不要)
hivefi-factory validate --all
hivefi-factory validate <id1> <id2>

# upload 前のローカル signal smoke (synthetic data で compute_signals を 1 回呼ぶ、network 不要)
hivefi-factory smoke <id1> <id2>
hivefi-factory smoke --all
# strategy push / code upload は内部で smoke を自動実行する。skip は --no-smoke

# health check (auth 不要)
hivefi-factory health
```

## 戦略 ID の命名規則

```
{logic}-{timeframe}-{rebalance}-{exchange}-{universe}-{mode}[-f_{filter}]*-v2
```

例:
- `tvl-pct20d-D-W-hl-all-ls-v2` (TVL の 20d pct_change、Daily eval、Weekly rebalance、Hyperliquid all symbols、long-short)
- `oi-mom-2w-D-2W-hl-all-ls-v2` (OI momentum 2 週、Daily eval、2 週 rebalance、long-short)

`-v2` サフィックス必須。各セグメントは server-side regex (`STRATEGY_ID_PATTERN`) で validate される。最大 128 文字。

- `timeframe`: `D | 1h | 4h | 8h`
- `rebalance`: `D | W | 2W | MS | H | 4H | 8H`
- `exchange`: `hl | bn`
- `universe`: 小英数 (例: `all`, `btc`, `majors`)
- `mode`: `ls | lo | so` (long-short / long-only / short-only)

## 戦略 code の書き方 (Method C: compute_signals)

```python
# extensions/<id>.py
from __future__ import annotations

import datetime as dt
import pandas as pd

from core.base import StrategyV2
from core.context import Signal, StrategyContext


class Strategy(StrategyV2):
    warmup_periods = 30           # Method B/C は宣言必須
    data_requirements = ["price"] # 必要な data source key (configs の panel と対応)

    def compute_signals(self, ctx: StrategyContext) -> list[Signal]:
        """1 rebalance 日分の signals を返す。

        ctx.data["price"]: DataFrame (index=date, columns=symbol, values=価格)
          → LookbackWindow が signal_date - 1 period で切出済。shift 不要
        ctx.date: 現在の rebalance 日 (pd.Timestamp)
        ctx.params: config の params dict

        return: list[Signal(symbol, side='buy'|'sell', percentage, time)]
        """
        # 2026+ は test period 温存。.date() で tz を捨てて server BT の
        # tz-aware ctx.date でも安全に比較する
        if pd.Timestamp(ctx.date).date() >= dt.date(2026, 1, 1):
            return []
        price = ctx.data["price"]
        if price.empty:
            return []

        # 例: 20 日 return の top 3 long / bottom 3 short、等金額配分
        returns = price.pct_change(20).iloc[-1].dropna()
        if len(returns) < 6:
            return []

        longs = returns.nlargest(3).index.tolist()
        shorts = returns.nsmallest(3).index.tolist()
        pct = 1.0 / (len(longs) + len(shorts))
        t = ctx.date.isoformat()

        return (
            [Signal(symbol=s, side="buy", percentage=pct, time=t) for s in longs]
            + [Signal(symbol=s, side="sell", percentage=pct, time=t) for s in shorts]
        )
```

### Method B (compute_factor) も可

ファクター値 (`pd.DataFrame`: index=date, columns=symbol) を返すと、フレームワーク側が
normalize (zscore / rank / winsorize) → filter (volume / funding / volatility) → signal 生成
(top_n / quantile / threshold) を自動で回す。

### AST denylist (server / client 共通、`hivefi-factory validate` で事前検査可能)

許可: `numpy`, `pandas`, `scipy` (subset), `math`, `statistics`, `itertools`, `typing`,
`collections`, `datetime`, `dataclasses`, `abc`, `enum`, `decimal`, `fractions`, `json`,
`re`, `warnings`, `copy`, `core.base`, `core.context`, `__future__`

**禁止 (主要なもの)**:
- 通信系: `requests`, `urllib`, `http`, `httpx`, `aiohttp`, `socket`, `ftplib`, `smtplib`, `telnetlib`, `urllib3`, `websocket(s)`
- プロセス・OS: `os`, `sys`, `subprocess`, `multiprocessing`, `signal`, `pathlib`, `tempfile`, `shutil`, `pty`, `ctypes`
- 動的 import / コード実行: `importlib`, `imp`, `runpy`, `pkgutil`, `pydoc`, `code`, `codeop`, `ast`
- シリアライザ: `pickle`, `marshal`, `shelve`, `dill`, `cloudpickle`, `dbm`
- AWS SDK: `boto3`, `botocore`, `aiobotocore`
- 高階呼出復元: `operator`, `functools`, `builtins`, `inspect`
- 動的 lookup: `eval`, `exec`, `compile`, `__import__`, `open`, `input`, `getattr`, `setattr`, `delattr`, `globals`, `locals`, `vars`, `dir`, `breakpoint`, `help`
- dunder traversal: `__class__`, `__bases__`, `__subclasses__`, `__mro__`, `__globals__`, `__builtins__`, `__import__`, `__reduce__`, `__code__`, `__dict__`, `__getattribute__`, etc.
- 文字列 trampoline: `"__sub" + "classes__"` 等 (BinOp/JoinedStr で静的に評価して reject)
- pandas file reader / writer: `read_csv`, `read_parquet`, `read_pickle`, `to_csv`,
  `to_parquet`, `to_pickle` など

違反時は `POST /v1/strategies/{id}/code` が 422 で reject される。`hivefi-factory validate <id>`
で同じルールがローカルで掛かる (network なしで先に確認できる)。

## 推奨される作業フロー

### 1. 仮説を立てる
「OI が増えている symbol は翌週上がりやすいか？」
→ この段階では skill `/market-research` でデータ可用性、`/factor-research` で予測力を確認。
IC だけでなく `R2_mean`、sample、p_value、多重検定補正後 `q_value`、分位形状、
方向整合を記録する。
必要データが足りず target behavior を観測できない場合は、実装へ進まず
`hivefi-factory data request` で不足 dataset / fields / coverage の要望を作る。

### 2. 戦略化
target behavior が data 上で観測でき、方向が事前仮説と整合し、`R2_mean` と sample が
記録され、検定ファミリー内で多重検定補正後 `q_value` (既定は Benjamini-Hochberg FDR) が
task の基準を満たす場合だけ `/strategy-scaffold-from-paper` skill を invoke する
(または agent が直接 `configs/<id>.json` + `extensions/<id>.py` を Write)。
compute_signals に仮説を code 化。AST denylist 違反しないように注意。

### 3. ローカル事前検査
```bash
hivefi-factory validate <id>     # AST denylist
hivefi-factory smoke <id>        # synthetic data で compute_signals を 1 回呼ぶ (Method C 戦略)
```
AST violations と signal smoke の両方を local で先に潰す (どちらも network 不要、< 1 秒)。
`strategy push` / `code upload` は内部で smoke を自動実行する (skip は `--no-smoke`)。
複数戦略を一括検査するなら `python tools/symphony/strategy_batch.py --strategy-id <id>` も可
(config / extension drift + AST + smoke を 1 コマンドで)。

### 4. 公式 push (Stage 1 自動起動)
task が opt-in し evidence gate を満たした場合だけ、`/submit-flow` skill
(or 手動 `hivefi-factory strategy push <id>`) で server に config + code を送信。
422 (denylist) / 409 (重複)/ 429 (pending 5 件超) / 413 (1MB 超) などが起きうる。

### 5. ジョブ完了待ち + KPI 取得
`hivefi-factory bt poll <job_id>` で 5〜10 分の Stage 1 + Stage 2 完走を待つ。
`hivefi-factory bt result <run_id>` で KPI を取得し、Sharpe / MaxDD / hit rate を評価。

### 6. BT 診断
`/backtest-diag` skill で WF 安定性 / 過学習パターン / regime sensitivity を CH の
`backtest_timeseries / backtest_trades` から多角診断。live 投入に耐えるか判定。

## agent としての振る舞い

### 参加者の依頼を primitive に翻訳する

ユーザ: "OI の momentum を BTC/ETH/SOL で 1 週間 forward で測って"
→ `/factor-research` skill を invoke、`hivefi-factory data fetch ...` を組む

ユーザ: "この factor で戦略を push して"
→ `/strategy-scaffold-from-paper` で scaffold → code 実装 → `/submit-flow`

### 出力は人間が読みやすく

- KPI 数値は `Sharpe=1.2, MaxDD=-18%` みたいに単位付きで
- factor IC の結果は `R2_mean`、sample、p_value、補正後 `q_value`、分位形状、
  方向整合、次工程を自然文で分かりやすく
- エラーは理由 + 修正提案をセットで

### 禁止事項

- **token / credentials を file や commit に書かない**: `.env` で env 経由のみ。`.gitignore` で `.env` 除外済
- **他 participant の strategy_id で operation しない**: ROW POLICY と API owner check で 404 になる
- **rate limit を尊重**: write 60 req/min/user, read 600 req/min/user, 同時 pending backtest jobs 5 件
- **Stage 1 sandbox で暴走 code を投げない**: 5 分 timeout / 1 vCPU / 2 GB の hard cap
- **AST denylist を回避しない**: `getattr(os, 'system')` のような動的アクセスで防御突破しようとしない (server で reject される)
- **push は evidence gate 後のみ**: `hivefi-factory strategy push` は task が opt-in し、
  事前に決めた evidence gate を満たした時だけ実行する。探索や variant 比較を含む場合は
  p_value だけで進めず、検定ファミリー内の補正後 `q_value` を使う

### BT 評価の規範 (サンプル数ベース、hard enforce ではない)

BT window や KPI の扱いは CLI / server 側で validate しない方針 (自由度を残す) なので、
agent 側で以下を守ること。window 長でなく **サンプル数 (trades)** で判定する:

- **KPI として提示する BT は total_trades ≥ 2000 を目安**。`hivefi-factory bt result` 出力の
  `total_trades=XXX` がこの閾値を超えていること。2000 未満は diagnostic 扱いで KPI 表に並べない
- **BT / report は 2025-12-31 まで**。可能な限り長い履歴を使うが、2026+ test period は BT evidence に含めない
- なぜサンプル数か: Sharpe の標準誤差 σ_SR ≈ √((1 + SR²/2) / N) は **N に依存**する。
  window 長は間接指標で、同じ window でも universe を広げたり rebalance を速めれば
  サンプル数は増やせる。「window 何ヶ月」より「サンプル何件」で考える方が本質
- 2000 到達の目安 (参考):
  - weekly × 6 signals × 4 年 ≈ 2000 (狭 universe は長い window が必要)
  - weekly × 20 signals × 1.2 年 ≈ 2000 (広 universe なら短い window でも到達)
  - daily × 6 signals × 1 年 ≈ 2000
- **IS/OOS の 2 window 構成を推奨**。ただし **各 window でも 2000 以上**を目標
- crypto は 1 年で **trend / sideways / drawdown** の regime が複数出る。
  サンプル数が足りても **単一 regime 期間の評価**は regime specific な結論に留めること
- agent が短サンプル BT を回すのは自由 (診断 / 挙動確認) だが、**結果を KPI として
  語るのは 2000 超過時のみ**。レポート時は `total_trades=XXX` と window / regime 文脈を併記

### KPI 単独で運用可否を決めない

- **stand-alone の SR / MaxDD に固定 threshold (SR>1.0 で◯等) を機械的に当てはめない**。
  最終的な portfolio 構築は **operator 側の MV / RP / MD 最適化** が別途行う前提
  なので、参加者は stand-alone の質 + 既存 approved 戦略と **signal source が
  異なる** (相関低くなる) 方向性に集中する
- SR を報告する時は **standard error (σ_SR ≈ √((1+SR²/2)/N)) と 95% CI を併記**。
  CI がゼロを跨ぐなら「noise と区別不能」と正直に書く
- **SR が正 でも `total_return` が負** のケースあり (vol drag, AM-GM inequality)。
  算術平均ベースの SR と compound return は別物。整合性に違和感があれば必ず明示
- `total_commission` / `total_slippage` は執行コスト (USD 累積)、
  `total_funding_pnl` (perp のみ) は funding 累積 P/L。いずれも equity から引かれる
  実コスト成分で、これらの総和が `total_return` の決定要因になっていないか確認
- **仕様**: `sharpe_ratio` / `max_drawdown` / `total_return` / `annual_return` は
  equity-curve ベース = **funding/commission/slippage 全て反映後の net 値**。
  一方 `win_rate` / `profit_factor` / `total_trades` は **trade 単体ベース** で
  funding は含まない。profit_factor=1.0 なのに total_return が負なら差分は funding
  が主犯
- **`turnover`** (年率化、Σ\|Δweight\|/2/years) は **capacity と cost sensitivity の
  尺度**。現状 `bt result` 出力に含まれないため docs 上で要望 metric として記載、
  暫定は `total_commission / commission_rate_bps × 10000 / avg_equity / years`
  で逆算。50x/年 超は AUM 拡大でコスト劣化リスク

## Symphony 運用（推奨: 複数戦略・無人実行）

Symphony 本体は本 repo に含まれない。Markdown task を読み、1 task = 1 strategy idea として
研究〜scaffold〜（opt-in 時のみ）submit / BT まで agent を回す orchestrator である。
手動で大量の `configs/` / `extensions/` を増やすより、`tools/symphony/local_tasks/` に
task を追加して Symphony に処理させる。

### パスとファイル

| 用途 | パス |
|---|---|
| Orchestrator 用 workflow | `WORKFLOW.md` |
| 起動手順 | `tools/symphony/README.md` |
| 待ち行列（Todo 等） | `tools/symphony/local_tasks/*.md` |
| 実行結果コメント | `tools/symphony/local_comments/*.md` |
| task テンプレ | `tools/symphony/LOCAL_TASK_TEMPLATE.md` |
| 戦略一覧（再生成） | `STRATEGY_STATUS.md` ← `python tools/refresh_strategy_status.py` |
| 作業用 workspace | `~/code/hivefi-strategy-workspaces/<task-id>/` |

### 起動前の環境変数

```bash
cd /path/to/hivefi-strategy-factory
set -a && . ./.env && set +a   # HIVEFI_API_KEY, CLICKHOUSE_* のみ。token は commit しない

export HIVEFI_STRATEGY_FACTORY_SOURCE="$PWD"
export HIVEFI_STRATEGY_FACTORY_TASKS_DIR="$PWD/tools/symphony/local_tasks"
export HIVEFI_STRATEGY_FACTORY_COMMENTS_DIR="$PWD/tools/symphony/local_comments"
```

`WORKFLOW.md` の `before_run` は `hivefi-factory health`、`validate --all`、
`tools/symphony/check_data_access.sh` が通るまで agent 実行を止める。

### 起動

```bash
cd ~/symphony/elixir
mise exec -- ./bin/symphony "$HIVEFI_STRATEGY_FACTORY_SOURCE/WORKFLOW.md" --port 4000 \
  --i-understand-that-this-will-be-running-without-the-usual-guardrails
```

（`HIVEFI_STRATEGY_FACTORY_SOURCE` 等は上記 export 済みであること）

### task の書き方（agent が守ること）

- **1 task = 1 strategy idea**。同一 task 内で parameter sweep や variant 量産をしない。
- 新規 task は `tools/symphony/LOCAL_TASK_TEMPLATE.md` に沿い、収益源・観測 proxy・期待符号・
  反証条件・`Submit: あり/なし` を事前に書く。
- 重複確認: `local_tasks`、`local_comments`、`configs/`、`extensions/`、`STRATEGY_STATUS.md` を
  strategy_id と title で検索してから作成する。
- フローは推奨作業フローと同じ（`/market-research` → `/factor-research` → gate → scaffold →
  validate/smoke → opt-in 時のみ `/submit-flow`）。
- evidence gate を満たさない場合は `configs/` / `extensions/` を作らず、
  `local_comments/` に研究結果だけ残して `Done` にしてよい。
- 実行後は workspace から `configs/` / `extensions/` / `artifacts/` が source repo に
  rsync され、`STRATEGY_STATUS.md` が更新される（`WORKFLOW.md` の `after_run`）。

### 出力の見本

```
user: "TVL momentum 戦略を作って push まで"

agent:
1. /factor-research で TVL pct_change の IC を測定
   → IC=0.035, R2_mean=0.0014, q_value=0.04, sample=320。方向と分位が事前仮説と整合
2. task が code output と push を明示しており、evidence gate を満たすため /strategy-scaffold-from-paper で scaffold (configs + extensions を直接 Write)
3. extensions/tvl-pct20d-D-W-hl-all-ls-v2.py を実装
   (top 3 long / bottom 3 short、等金額)
4. hivefi-factory validate tvl-pct20d-D-W-hl-all-ls-v2 → OK
5. hivefi-factory strategy push tvl-pct20d-D-W-hl-all-ls-v2
   → 201 Created (job_id=..., run_id=...)
6. 次のステップ提案: "hivefi-factory bt poll <job_id> で完了を待ちますか？"
```

## 詳細: skill 一覧

戦略開発フローは **データ探索 → ファクター評価 → 戦略実装 → push → BT → 診断 → ポートフォリオ構築** の一本道。

| Skill | 何ができる | 使う primitive |
|---|---|---|
| `/market-research` | universe のデータ可用性 / 欠損 / 分布 / 論理整合性を一括確認 | `hivefi-factory data fetch` + ClickHouse 直接 SELECT |
| `/factor-research` | 因子を多方向に transform × forward_days でスイープ、IC / spread を summarize | `hivefi-factory data fetch` + local pandas |
| `/strategy-scaffold-from-paper` | trading_ideas 等から戦略 idea を取得、crypto 適用版 scaffold を直接 Write | agent 内蔵 (configs/ + extensions/ を Write tool で生成) |
| `/submit-flow` | local AST pre-flight → push (config + code) → Stage 1 auto trigger → CH 経由で結果待ち | `hivefi-factory validate` + `hivefi-factory strategy push` + `hivefi-factory bt poll` + `hivefi-factory bt result` |
| `/backtest-diag` | 公式 BT 結果を多角診断 (サマリー / WF 安定性 / 過学習 / regime 感度) | `hivefi-factory bt result --timeseries --trades` + local pandas |
| `/symphony` | local file tracker で 1 task = 1 strategy の unattended 運用 | 外部 `~/symphony/elixir` + `WORKFLOW.md` |
| `/empirical-prompt-tuning` | skill / workflow / prompt をシナリオ評価で改善し、曖昧さを小さくする | agent evaluation + local edits |

詳細は各 `.claude/skills/<name>/SKILL.md` を参照。

## リポジトリ規約

- `configs/*.json` は strategy_id と file 名を一致させる
- `extensions/*.py` も同様、class 名は `Strategy` 固定推奨
- `data/` は `.gitignore`、でかくなり得るので commit しない
- `.env` は `.gitignore`、secret を絶対に commit しない
- Symphony 運用は `WORKFLOW.md` と `tools/symphony/README.md` を参照し、token は env のみに置く
- 分析の notebook は `notebooks/` に、成果物 (plot 等) は `notebooks/outputs/` に
