# hivefi strategy factory

HiveFi Strategy Contest 参加者向けの **template repo**。
Claude Code または OpenAI Codex CLI で開くと、同梱の `CLAUDE.md` / `AGENTS.md` / `.claude/skills/` を agent が読込み、自然言語で戦略開発 → 公式 submit → ClickHouse 直接読取での結果分析まで回せる。

API は HiveFi のマルチテナント Strategy API (`https://strategy-api.hivefi.xyz`、X-API-Key 認証)。バックテストは `POST /v1/strategies/{id}/code` の upload で 2 段ジョブパイプライン (signal-gen → backtest) が自動 trigger され、結果は ClickHouse Cloud (`backtest_runs / backtest_timeseries / backtest_trades / user_signals / backtest_jobs`) に書き込まれる。ROW POLICY で自分のテナント分しか見えないので、SELECT に owner 句を付ける必要はない。

## 前提条件

- Python 3.11+
- HiveFi 運営から発行される credentials 一式 (`.env` に設定する 3 値):
  - `HIVEFI_API_KEY` (`hvf_<env>_<32hex>`)
  - `CLICKHOUSE_USER` (`u_<6hex>`)
  - `CLICKHOUSE_PASSWORD`
- それ以外 (API base URL / ClickHouse host・port・database) は
  `src/hivefi_factory/config.py` に固定値として埋め込まれている。`.env` で上書きも可
- 好きな agent CLI のどれか:
  - [Claude Code](https://claude.ai/code)
  - [OpenAI Codex CLI](https://github.com/openai/codex)

## 認証情報の取得方法

この repo / CLI から認証情報を自分で発行することはできない。参加者ごとの
credential は **HiveFi 運営 / admin から個別に受け取る** 前提。

まだ受け取っていない場合は、HiveFi 運営に以下 3 つの発行を依頼する:

- `HIVEFI_API_KEY`
- `CLICKHOUSE_USER`
- `CLICKHOUSE_PASSWORD`

使い分けは次のとおり:

- `HIVEFI_API_KEY`: `health`, `strategy list`, `strategy push` など Strategy API 用
- `CLICKHOUSE_USER` / `CLICKHOUSE_PASSWORD`: `bt status`, `bt result`, `data fetch`, `signals` など ClickHouse 直読用

受領後は `.env.example` を `.env` にコピーし、3 値だけ埋めればよい。host / port /
database などは repo 側の固定値を使うため、通常は追加設定不要。

## Onboarding (〜5 分)

```bash
# 1) template から自分用 repo を作る
git clone https://github.com/HiveFi-Labs/hivefi-strategy-factory-template.git my-strategies
cd my-strategies

# 2) .env を作る (3 行だけ埋める)
cp .env.example .env
# → HIVEFI_API_KEY / CLICKHOUSE_USER / CLICKHOUSE_PASSWORD を運営から受領した値で埋める

# 3) 依存導入 + パッケージ install
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]

# 4) CLI 動作確認 (network 不要)
hivefi-factory --version
hivefi-factory validate --all          # 同梱 demo 戦略の AST 事前検査

# 5) API 疎通確認 (HIVEFI_API_KEY が必要)
hivefi-factory health
hivefi-factory strategy list

# 6) agent 起動 (Claude Code の場合)
claude
# or: codex

# 7) agent に自然言語で依頼
# 例: "TVL momentum が BTC/ETH/SOL で効くか調べて、戦略化して push"
```

## ディレクトリ構成

```
.
├── CLAUDE.md              # Claude Code 向けプロジェクト規約・primitive の使い方
├── AGENTS.md              # Codex CLI / 他 AI agent 向け (CLAUDE.md と整合)
├── WORKFLOW.md            # Symphony orchestrator 用 workflow
├── README.md              # この file
├── src/hivefi_factory/    # 同梱の API クライアント + ClickHouse helper + AST validator
├── .claude/skills/        # agent が invoke できる skill 群
│   ├── market-research/   # universe のデータ品質確認 (CH SQL ベース)
│   ├── factor-research/   # 因子の予測力を測る (CH 経由のパネルフェッチ)
│   ├── strategy-scaffold-from-paper/  # 論文を crypto 戦略に翻訳
│   ├── submit-flow/       # config + code push (Stage 1 自動起動) と CH での結果待ち
│   ├── backtest-diag/     # 公式 BT 結果を CH から読んで多角診断
│   └── symphony/          # Strategy Factory Symphony orchestrator 運用
├── .agents/skills/        # AGENTS.md 系 (.claude/skills/ と bitwise 同期)
├── tools/symphony/        # Symphony workspace bootstrap / batch validator
├── configs/               # strategy config JSON (<id>.json)
├── extensions/            # strategy code (<id>.py)
├── data/                  # データ取得の出力先 (.gitignore)
├── notebooks/             # exploratory notebook (optional)
├── .env.example           # 環境変数テンプレート
├── .gitignore
└── pyproject.toml
```

## 使える CLI コマンド (primitives)

`hivefi-factory --help` で全体。代表的なもの:

```bash
# 戦略 (config + code) push、Stage 1 を auto trigger
hivefi-factory strategy push <id>            # configs/<id>.json + extensions/<id>.py を 1 発で
hivefi-factory strategy list                 # 自分の戦略一覧
hivefi-factory strategy show <id>
hivefi-factory strategy delete <id>

# code 単独再 upload (新バージョン v{N+1} を自動採番)
hivefi-factory code upload <id> [<file>]

# バックテストジョブ (CH 直読、ROW POLICY で自分のだけ見える)
hivefi-factory bt status <id>                # jobs 一覧
hivefi-factory bt poll <job_id>              # 完了まで polling (succeeded/failed/timeout)
hivefi-factory bt result <run_id> [--timeseries] [--trades] [--csv]

# market data (CH 経由のパネルフェッチ)
hivefi-factory data fetch <table> --symbols BTC ETH SOL --start 2024-01-01
hivefi-factory data request --idea "..." --needed-data "..." --reason "..."

# 自分のシグナル (debug 用)
hivefi-factory signals <id> [--csv]

# upload 前のローカル AST 事前検査 (server-side denylist と同じルール)
hivefi-factory validate --all
hivefi-factory validate <id1> <id2>
```

詳細は `CLAUDE.md` / `AGENTS.md` を参照。

## Symphony orchestration

Symphony orchestrator からこの repo を回す場合は、外部 checkout
`~/symphony/elixir` にこの repo の `WORKFLOW.md` を渡す。
default workflow は local file tracker で、`tools/symphony/local_tasks/`
の Markdown task を読み、`tools/symphony/local_comments/` に結果コメントを
書く。Linear / GitHub は不要。

```bash
cd /path/to/hivefi-strategy-factory
set -a
. ./.env
set +a
export HIVEFI_STRATEGY_FACTORY_SOURCE="$PWD"
export HIVEFI_STRATEGY_FACTORY_TASKS_DIR="$PWD/tools/symphony/local_tasks"
export HIVEFI_STRATEGY_FACTORY_COMMENTS_DIR="$PWD/tools/symphony/local_comments"
cd ~/symphony/elixir
mise exec -- ./bin/symphony "$HIVEFI_STRATEGY_FACTORY_SOURCE/WORKFLOW.md" --port 4000 \
  --i-understand-that-this-will-be-running-without-the-usual-guardrails
```

詳細は `tools/symphony/README.md` と `/symphony` skill を参照。

## 典型的な agent 対話例

```
(you) > @factor-research で OI pct_change が BTC/ETH/SOL で効くか調べて

(agent) [uses /factor-research skill]
  → hivefi-factory data fetch coinglass_oi_d --symbols BTC ETH SOL ...
  → hivefi-factory data fetch hyperliquid_kline_1d --symbols BTC ETH SOL ...
  → local で Pearson IC 計算
  → "IC mean=+0.04, t=+2.3, hit=58% → ✅ adopt (IC ≥ 0.03)"

(you) > これで long-short 戦略を組んで push

(agent) [uses /strategy-scaffold-from-paper + /submit-flow]
  → configs/oi-pct-20d-D-W-hl-all-ls-v2.json を Write
  → extensions/oi-pct-20d-D-W-hl-all-ls-v2.py を Write + 実装
  → hivefi-factory validate oi-pct-20d-D-W-hl-all-ls-v2  (ローカル AST 事前検査)
  → hivefi-factory strategy push oi-pct-20d-D-W-hl-all-ls-v2
  → 201 Created (job_id=..., run_id=...)

(you) > BT 結果が出たら見せて

(agent)
  → hivefi-factory bt poll <job_id>            # ~3-5 分で completes
  → hivefi-factory bt result <run_id> --timeseries
  → KPI: Sharpe=0.8, MaxDD=-22%, total_trades=2143
```

## トラブルシューティング

### `hivefi-factory: command not found`
→ `pip install -e .` を実行したか確認 (venv を activate していない場合も同じ症状)。venv を使わずに `python -m hivefi_factory ...` でも起動できる。

### `HIVEFI_API_KEY is not set`
→ `.env` を作って `HIVEFI_API_KEY` を埋める。または process env で渡す。

### `ClickHouse credentials missing`
→ `bt status` / `bt result` / `data fetch` / `signals` は ClickHouse 直接接続が必要。`.env` の `CLICKHOUSE_USER` / `CLICKHOUSE_PASSWORD` を埋める (host / port は `config.py` 内の固定値)。`strategy push` / `strategy list` などは API key だけで動く。

### `strategy push` が 422 Forbidden constructs で reject される
→ サーバ側 AST denylist に当たっている。事前に `hivefi-factory validate <id>` を回すと同じルールでローカル検査できる。`os` / `subprocess` / `socket` / `pickle` / `boto3` / `inspect` / `operator` / `functools` / `getattr` / `setattr` / `eval` / `exec` などは禁止 (詳細は `src/hivefi_factory/validator.py` の denylist を参照)。

### `strategy push` で 409 Conflict
→ 同じ `strategy_id` が既に登録済み。`hivefi-factory strategy push <id>` はデフォルトで PUT 更新にフォールバックする。`--no-update` を付けるとフォールバックせず失敗で抜ける。

### `strategy push` で 429 Too Many Requests
→ pending ジョブが 5 件 (per-user 上限) を超えた。完了を待ってから再試行。stuck している場合は HiveFi 管理者に連絡。

### 他の participant の戦略を見ようとしてもエラー
→ **仕様**。ROW POLICY と API 側の owner check で、他者の `strategy_id` での operation は 404 になる。

### agent が token を commit しようとする
→ `.gitignore` で `.env` を除外済。`CLAUDE.md` で agent に "token を直接ファイルに書かない" 規約を課している。`git add` 前に必ず確認。

## ライセンス / 利用条件

[TODO]
