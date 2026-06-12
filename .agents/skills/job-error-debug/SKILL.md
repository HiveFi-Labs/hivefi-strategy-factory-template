---
name: job-error-debug
description: |
  ファクトリー内で失敗した backtest job の error_message を ClickHouse `backtest_jobs`
  から横断取得・分類し、戦略 ID 別に修正パターンを提案する。pipeline / Method C
  双方の典型エラーをカタログ化し、quick fix を返す。

trigger:
  - user が "失敗 job" / "エラー戦略" / "なぜ failed" / "BT が通らない" 等
  - 大量 push 後の失敗 cluster 確認
  - `bt status` で failed が並んだ後の triage
---

# /job-error-debug skill

## 目的
全戦略の **最新 job** で `status='failed'` のもののエラーを集約し、root cause を pattern-match で分類、修正方針を出す。

## 手順

### 1. 失敗 job 一覧を取得
ClickHouse `backtest_jobs` で各 strategy_id の最新 job を取り、failed のみ抽出する:

```python
from hivefi_factory.clickhouse import ClickHouseClient
with ClickHouseClient() as ch:
    rows = ch.query_rows(
        "SELECT strategy_id, "
        "       argMax(error_message, submitted_at) AS err, "
        "       argMax(stage, submitted_at) AS stage, "
        "       argMax(status, submitted_at) AS status, "
        "       argMax(submitted_at, submitted_at) AS ts "
        "FROM backtest_jobs GROUP BY strategy_id "
        "HAVING status = 'failed' ORDER BY ts DESC"
    )
```

### 2. エラーパターン分類

下記 8 カテゴリに pattern-match で振り分ける:

| カテゴリ | 検出 substring | 修正方針 |
|---|---|---|
| **nlargest-dtype** | `Cannot use method 'nlargest' with dtype object` | Series の dtype を `.astype(float)` に強制 |
| **nsmallest-dtype** | `Cannot use method 'nsmallest' with dtype object` | 同上 |
| **data-late** | `Factor is empty on first rebalance` | data 開始日が遅い source。配置不可なら 戦略削除 / 別 source に変更 |
| **warmup-excess** | `warmup_periods=N exceeds estimate*3=M` | config の `warmup_periods` を [estimate, estimate*3] 範囲内に修正 |
| **warmup-insuff** | `warmup_periods=N is less than estimated minimum=M` | warmup を引き上げる |
| **hyphen-table** | `Invalid table name:` (table 名にハイフン) | data_source key を変更 (例: `bridge_volume` の代替) |
| **FINAL-on-shared** | `SharedMergeTree doesn't support FINAL` | server-side bug、 retry or worker-side fix が必要 |
| **ch-resource** | `signal_reader_worker: Not enough pr` | worker resource、retry |
| **index-bounds** | `single positional indexer is out-of-bounds` | data 開始日に対して lookback 不足、`warmup_periods` を増やす (resolver が start を後ろに移す) |

### 3. 戦略別修正レシピ

#### nlargest-dtype 修正
症状: `pd.Series({sym: f(s) for sym in ...}).dropna()` が object dtype のまま。

修正パターン:
```python
# Before
score = pd.Series({s: _f(returns[s]) for s in returns.columns}).dropna()
longs = score.nlargest(_N).index.tolist()  # ← TypeError

# After
score = pd.Series(
    {s: _f(returns[s]) for s in returns.columns}, dtype=float
).dropna()
# または
score = score.astype(float).dropna()
```

特に `(ret > 0).sum() / valid.replace(0, pd.NA)` のような計算で `pd.NA` が混入すると object 型になる。`replace(0, np.nan)` を使うか、計算後 `.astype(float)` する。

#### data-late 修正
症状: `Factor is empty on first rebalance date YYYY-MM-DD. warmup_periods=N may be insufficient.`

検証手順:
1. data source の最初の有効日を CH で確認 (`SELECT min(time) FROM <table>`)
2. data 開始日 + warmup_periods が strategy の first rebalance より早ければ OK
3. config の `warmup_periods` を increase で対処 (Orchestrator は min(time) + warmup_periods で開始日を決めるため warmup を増やせば first rebalance も後ろに移る)。`backtest_start` フィールドは PR2094 で廃止済 (送っても Pydantic extra=ignore で黙って捨てられる)
4. Method C の場合 `compute_signals` で `if len(price) < N: return []` の guard を追加
5. それでも駄目なら戦略削除し、より早い data source の variant に置換

#### hyphen-table 修正
ハイフンを含む clickhouse table 名 (例: `defillama_bridge_net-flow-usd`) は CH client validator が拒否する。
- `bridge_volume` data key 自体が当該 table を指すので、別 data source への切替が必要 (例: `bridge_deposit` + `bridge_withdraw` を weighted_composite で代替)

#### warmup-excess 修正
config の pipeline 内 `transform` step を見て estimate を計算:
- `pct_change` periods → estimate=periods
- `rolling_mean/std` window → estimate=window
- `diff` periods → estimate=periods
- `diff2` → estimate=2
- `latest_value` / `ratio` / `weighted_composite` → estimate=1

`warmup_periods` を [estimate, estimate*3] 範囲に。最も安全は estimate*2。

#### FINAL-on-shared 修正
ClickHouse `SharedMergeTree` engine では `FINAL` 修飾子が使えない。worker 側の signal_reader が FINAL を打ってる可能性。
- 対処: 戦略 config の問題ではないので戦略側で fix できない
- 一時対処: 同じ戦略を再 push して reschedule (運次第で別 worker や reasoner が処理)
- 恒久対処: worker code 側で FINAL を除去、もしくは別 query 形式に置き換え

### 4. 一括修正コマンド

複数戦略を同時に修正する場合は、影響戦略を grep で洗い出してから `Edit` で
一括置換する:

```bash
# nlargest-dtype 影響戦略の self-detection
grep -l "pd.Series({" extensions/*.py | xargs grep -L "dtype=float" | head
```

修正対象が固まったら `Edit` tool で `pd.Series({...}).dropna()` → `pd.Series({...}, dtype=float).dropna()` に置換。

### 5. 修正後の再 push

```bash
hivefi-factory validate <修正した戦略 id>
hivefi-factory smoke    <修正した戦略 id>   # compute_signals が runtime exception を投げないかローカル確認
hivefi-factory strategy push <id>           # code v(N+1) を生成、Stage 1 自動 trigger
hivefi-factory bt poll <job_id>
```

`strategy push` は内部で smoke を自動実行する (skip は `--no-smoke`)。
複数 retry が必要な場合は **pending 5 件上限**を尊重し、`bt poll` で 1 件完走を
待ってから次の push に進む。

## 出力例

```
=== Failed jobs (latest per strategy) ===
Total: 50

By category:
  17 nlargest-dtype       hitcalm-D-W, hitsort-D-W, ...
  11 data-late            fundvol-30d, fundz-21d, ...
   9 FINAL-on-shared      csize, dexprmcap, ...
   5 index-bounds         apyz, fundoi, ...
   4 hyphen-table         brfees, brmcap, ...

Recommended actions:
  P0: nlargest-dtype 17 件 → astype(float) で 30 分以内に修正可能
  P1: hyphen-table 4 件 → bridge_volume → bridge_deposit + bridge_withdraw に切替 (戦略再設計)
  P2: data-late 11 件 → 戦略削除 / window 短縮 / 別 source 切替
  P3: FINAL-on-shared 9 件 → server fix が来るまで retry のみ
```

## 注意
- 同じ戦略に対する複数 job (v1 failed, v2 succeeded) は最新のみ見る (`argMax(submitted_at)`)
- `failed` でも実体は worker resource の transient issue の場合があり、再 push で通ることもある
- `index-bounds` と `data-late` は近い症状: warmup_periods より早期に return [] するか、戦略を削除する判断
