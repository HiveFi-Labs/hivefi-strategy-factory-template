# AGENTS.md — hivefi strategy factory

`CLAUDE.md` の Codex CLI / 他 agent 向け版。**内容は同一**、formatting が Codex の convention (AGENTS.md 自動読込) に合わせてある。
詳細な CLI 仕様や戦略実装テンプレートは `CLAUDE.md` を参照。

## 概要

HiveFi Strategy Contest 参加者が手元で戦略開発するための repo。
agent (あなた) は自然言語の依頼を `hivefi-factory` CLI 呼出と Python 編集に翻訳する。
API は `https://strategy-api.hivefi.xyz` (X-API-Key 認証)、結果は ClickHouse Cloud から直接読む。

**複数戦略の unattended 運用・タスクキュー駆動は Symphony を使う。** 対話的に 1 本だけ作る場合は
この AGENTS.md の推奨フローと skill を直接使ってよい。Symphony は外部 checkout
`~/symphony/elixir` + 本 repo の `WORKFLOW.md` で起動し、詳細は `/symphony` skill、
`WORKFLOW.md`、`tools/symphony/README.md` を参照する。

## 使える primitive (`hivefi-factory` CLI)

```
# 戦略 push (config + code を 1 発、Stage 1 を auto trigger)
hivefi-factory strategy push <id>
hivefi-factory strategy list / show <id> / delete <id>

# code 単独再 upload
hivefi-factory code upload <id> [<file>]

# バックテストジョブ・結果 (ClickHouse 直読)
hivefi-factory bt status <id>
hivefi-factory bt poll <job_id>
hivefi-factory bt result <run_id> [--timeseries] [--trades] [--csv]

# market data
hivefi-factory data fetch <table> --symbols BTC ETH --start 2024-01-01
hivefi-factory data request --idea "..." --needed-data "..." --reason "..."

# 自分のシグナル
hivefi-factory signals <id> [--csv]

# upload 前のローカル AST 事前検査 (server denylist と同じルール、network 不要)
hivefi-factory validate --all
hivefi-factory validate <id>

# health check
hivefi-factory health
```

## 戦略 ID 規則

`{logic}-{timeframe}-{rebalance}-{exchange}-{universe}-{mode}[-f_{filter}]*-v2`

例: `tvl-pct20d-D-W-hl-all-ls-v2`、`oi-mom-2w-D-2W-hl-all-ls-v2`

- `timeframe`: `D | 1h | 4h | 8h`
- `rebalance`: `D | W | 2W | MS | H | 4H | 8H`
- `exchange`: `hl | bn`
- `universe`: 小英数 (`all`, `btc`, `majors`, ...)
- `mode`: `ls | lo | so`

## 戦略 code 雛形 (Method C)

```python
from __future__ import annotations
import datetime as dt
import pandas as pd
from core.base import StrategyV2
from core.context import Signal, StrategyContext


class Strategy(StrategyV2):
    warmup_periods = 30                 # Method B/C は宣言必須
    data_requirements = ["price"]

    def compute_signals(self, ctx: StrategyContext) -> list[Signal]:
        # 2026+ は test period 温存。.date() で tz を捨てて server BT の
        # tz-aware ctx.date でも安全に比較する
        if pd.Timestamp(ctx.date).date() >= dt.date(2026, 1, 1):
            return []
        price = ctx.data["price"]
        if price.empty:
            return []
        returns = price.pct_change(20).iloc[-1].dropna()
        longs = returns.nlargest(3).index.tolist()
        pct = 1.0 / len(longs)
        t = ctx.date.isoformat()
        return [Signal(symbol=s, side="buy", percentage=pct, time=t) for s in longs]
```

## AST denylist (server / client 両方で強制)

許可: `numpy`, `pandas`, `scipy`, `math`, `statistics`, `itertools`, `typing`, `collections`,
`datetime`, `dataclasses`, `abc`, `enum`, `decimal`, `fractions`, `json`, `re`, `warnings`,
`copy`, `core.base`, `core.context`, `__future__`

**禁止**: `os`, `sys`, `subprocess`, `socket`, `pathlib`, `tempfile`, `shutil`, `requests`,
`urllib`, `httpx`, `aiohttp`, `pickle`, `marshal`, `dill`, `boto3`, `inspect`, `operator`,
`functools`, `builtins`, `eval`, `exec`, `compile`, `__import__`, `open`, `input`,
`getattr`, `setattr`, `globals`, `locals`, `vars`, `dir`, dunder traversal
(`__class__`, `__subclasses__`, `__bases__`, `__globals__`, `__reduce__`, etc.),
文字列 trampoline (`"__sub" + "classes__"` 等の BinOp / JoinedStr 経由)、
pandas の file reader / writer (`read_csv`, `read_parquet`, `to_pickle`,
`to_parquet`, `to_csv` 等)

違反は upload 時 (`POST /v1/strategies/{id}/code`) に 422 reject。事前に
`hivefi-factory validate <id>` でローカル検査すれば無駄な往復を避けられる。

## skill

Claude Code と Codex の両方から auto-discovery されるよう **2 箇所に同内容で配置** (CI で同期を強制):

- `.claude/skills/<name>/SKILL.md` — Claude Code が読む (`/` slash で invoke)
- `.agents/skills/<name>/SKILL.md` — Codex CLI が読む (`/skills` selector or `$<name>` で invoke)

**片方を編集したら必ず両方を更新**。ズレると `tests/test_skills.py::test_skill_md_content_identical` が CI で失敗する。

配置済 skill (データ探索 → 因子評価 → 戦略実装 → push → BT → 診断):

- `/market-research`: universe データ品質チェック
- `/factor-research`: 因子の予測力測定
- `/strategy-scaffold-from-paper`: 論文 → crypto 戦略翻訳
- `/submit-flow`: local AST pre-flight + push + Stage 1 auto trigger 後の結果待ち
- `/backtest-diag`: 公式 BT 結果の多角診断 (WF 安定性 / 過学習 / regime)
- `/symphony`: local task file tracker で 1 task = 1 strategy の自動運用
- `/empirical-prompt-tuning`: skill / workflow / prompt をシナリオ評価で改善

skill の詳細手順は各 `SKILL.md` 参照。

## 禁止事項

- token / credentials を file / commit に書かない (`.env` 経由のみ、`.gitignore` 済)
- 他 participant の strategy_id で operation しない (ROW POLICY + API owner check で 404)
- rate limit 遵守 (write 60/min/user、read 600/min/user、同時 pending backtest jobs 5 件)
- Stage 1 sandbox で暴走 code を投げない (5 分 timeout / 1 vCPU / 2 GB hard cap)
- AST denylist を回避しない (動的 getattr / 文字列 trampoline 等の trick)
- `hivefi-factory strategy push` は task が opt-in し evidence gate を満たした時だけ実行する。
  evidence gate は、target behavior が data 上で観測でき、方向が事前仮説と整合し、
  `R2_mean` と sample が記録され、検定ファミリー内で多重検定補正後 `q_value`
  (既定は Benjamini-Hochberg FDR) が task の基準を満たすことを指す。

## BT 評価の規範 (サンプル数ベース、CLI/server では enforce されない)

- **KPI として提示する BT は total_trades ≥ 2000 を目安**。`hivefi-factory bt result` 出力の `total_trades=XXX` がこの閾値を超えていること。2000 未満は diagnostic 扱いで KPI 表に並べない
- **BT / report は 2025-12-31 まで**。可能な限り長い履歴を使うが、2026+ test period は BT evidence に含めない
- なぜサンプル数か: σ_SR ≈ √((1 + SR²/2) / N) は N に依存。window 長は間接指標で、universe 幅や rebalance 頻度でサンプル密度は変わる
- 2000 到達の目安: weekly × 6 signals × 4 年 ≈ 2000 / weekly × 20 signals × 1.2 年 ≈ 2000 / daily × 6 signals × 1 年 ≈ 2000
- **IS/OOS の 2 window 構成を推奨**。各 window でも 2000 以上を目標
- 単一 regime 期間の評価は regime-specific な結論に留める (crypto は 1 年で trend / sideways / drawdown が入れ替わる)
- 短サンプル BT は診断目的に限る。KPI として語るのは **trades ≥ 2000 時のみ**、併せて window と regime 文脈を明示

## KPI 単独で運用可否を決めない

- **SR / MaxDD に固定 threshold (SR>1.0 で◯等) を機械的に当てはめない**。最終 portfolio 構築は operator 側 (MV/RP/MD) で別途行う前提、参加者は stand-alone の質 + 既存 approved 戦略との signal source 独立性に集中
- SR は **σ_SR ≈ √((1+SR²/2)/N) と 95% CI を併記**、ゼロ跨ぎなら noise 扱い
- **SR 正 でも total_return 負**は vol drag (AM-GM inequality) で発生。SR は算術平均ベース、total_return は compound。整合性違和感があれば明示
- `total_commission` / `total_slippage` = 執行コスト (USD 累積)、`total_funding_pnl` = perp funding P/L。いずれも equity から引かれる実コスト。これらが total_return を支配していないか確認
- **仕様**: `sharpe_ratio` / `max_drawdown` / `total_return` / `annual_return` は equity-curve ベース = **funding/commission/slippage 全て net 反映後**。`win_rate` / `profit_factor` は **trade 単体ベース** (funding 含まず)
- **`turnover`** (年率 Σ\|Δweight\|/2/years) = capacity と cost sensitivity の尺度。**現状 bt result 出力に無し** (追加要請中)、暫定 `total_commission / commission_rate_bps × 10000 / avg_equity / years` で逆算。50x/年 超は AUM 拡大でコスト劣化

## Symphony 運用（推奨: 複数戦略・無人実行）

Symphony 本体は本 repo に含まれない。Markdown task を読み、1 task = 1 strategy idea として
研究〜scaffold〜（opt-in 時のみ）submit / BT まで Codex agent を回す orchestrator である。
**agent は Symphony 経由の作業を前提にする。** 手動で大量の `configs/` / `extensions/` を
増やすより、`tools/symphony/local_tasks/` に task を追加して Symphony に処理させる。

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
- フローは下記「推奨フロー」と同じ（`/market-research` → `/factor-research` → gate → scaffold →
  validate/smoke → opt-in 時のみ `/submit-flow`）。
- evidence gate を満たさない場合は `configs/` / `extensions/` を作らず、
  `local_comments/` に研究結果だけ残して `Done` にしてよい。
- 実行後は workspace から `configs/` / `extensions/` / `artifacts/` が source repo に
  rsync され、`STRATEGY_STATUS.md` が更新される（`WORKFLOW.md` の `after_run`）。

### いつ Symphony / いつ対話 agent か

| 状況 | 使うもの |
|---|---|
| 戦略アイデアをキューに積んで無人で回す | **Symphony** + `local_tasks/*.md` |
| 1 本を対話で素早く試す | この AGENTS.md + skill 直接 |
| 既存 task の再実行・rename retry | `local_tasks/` の該当 task + Symphony |

Symphony 上の agent も `AGENTS.md`・各 skill・`docs/false-positive-control.md` に従う。
詳細ポリシーは `/symphony` skill を invoke すること。

## 推奨フロー

（Symphony task でも対話でも同じ順序。Symphony では各 step の結果を `local_comments/` に残す。）

1. 演繹的な仮説立案: 市場メカニズム → 観測 proxy → 期待符号 → 売買ルール → 反証条件
2. `/market-research` でデータ可用性確認
3. 必要データが足りず target behavior を観測できない場合は
   `hivefi-factory data request` で不足 dataset / fields / coverage の要望を作る
4. `/factor-research` で予測力測定。IC だけでなく `R2_mean`、sample、p_value、
   多重検定補正後 `q_value`、分位形状、方向整合を記録する
5. evidence gate を満たさない場合は、実装や BT に進まず研究記録として完了してよい。
   探索や variant 比較を含む場合は p_value だけで進めず、補正後 q_value を使う
6. gate を満たし、task が code output を求める場合だけ `/strategy-scaffold-from-paper` で scaffold
7. compute_signals / compute_factor を実装
8. `hivefi-factory validate <id>` と `hivefi-factory smoke <id>` でローカル検査
9. task が opt-in し gate を満たす場合だけ `/submit-flow` で `hivefi-factory strategy push`
10. `hivefi-factory bt poll <job_id>` で完走を待ち、`hivefi-factory bt result <run_id>` で KPI 取得
11. `/backtest-diag` で WF 安定性・過学習・regime 診断

## リポジトリ構造

```
configs/<id>.json         # strategy config
extensions/<id>.py        # strategy code
src/hivefi_factory/       # 同梱 API client + ClickHouse helper + AST validator
data/                     # データ取得の出力 (.gitignore)
notebooks/                # exploratory
.claude/skills/           # Claude Code 用 skill 定義
.agents/skills/           # Codex 用 skill 定義 (.claude/skills/ と同内容、CI で強制)
WORKFLOW.md               # Symphony orchestrator 定義（起動時に必須）
tools/symphony/           # local_tasks / local_comments / README / bootstrap
STRATEGY_STATUS.md        # 全戦略の状態一覧（Symphony after_run で更新）
.env                      # secrets (.gitignore)
```
