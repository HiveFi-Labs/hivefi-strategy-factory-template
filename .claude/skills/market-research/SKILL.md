---
name: market-research
description: |
  universe として想定する symbol 群について、data 可用性 / 欠損 / 分布 / 論理整合性を
  一括で確認する。戦略設計の前に「このデータ信頼できる？」を検証。

trigger:
  - user が "universe のデータ品質を確認" "BTC/ETH/SOL/.. のデータ揃ってるか" 等
  - 新しい data source を使おうとするとき
  - 戦略の results が不自然な時の diagnostic
---

# /market-research skill

## 目的

データ品質の 4 観点 (可用性 / カバレッジ / 分布 / 論理整合性) を universe 全体で調べる。
データは ClickHouse Cloud に直接接続して読む (HTTP API には fetch endpoint は無い)。

## データ期間 / holdout

2026-01-01 以降は test period / holdout として扱い、market-research の可用性・欠損・分布確認には使わない。
fetch では原則 `--end 2025-12-31` を明示する。CH 側でも 2026 行が残っている場合があるが、test 期間に含めて評価しない。

## 手順

### 1. 利用可能な data source を把握

ClickHouse の `system.tables` を直接見るか、`hivefi_factory.clickhouse.ClickHouseClient` でクエリする:

```python
from hivefi_factory.clickhouse import ClickHouseClient

with ClickHouseClient() as ch:
    rows = ch.query_rows(
        "SELECT name FROM system.tables "
        "WHERE database = currentDatabase() AND engine != 'View' "
        "ORDER BY name LIMIT 200",
    )
    for r in rows:
        print(r["name"])
```

または特定キーワードで絞る:

```python
rows = ch.query_rows(
    "SELECT name, total_rows, total_bytes "
    "FROM system.tables WHERE database = currentDatabase() "
    "AND name ILIKE {p:String} ORDER BY name",
    {"p": "%kline%"},
)
```

参加者の想定 universe に使えそうな table を特定する。代表例:

- `hyperliquid_kline_1d`, `binance_kline_1d` (price OHLCV)
- `coinglass_oi_*` (open interest)
- `defillama_tvl_*` (TVL)
- `hyperliquid_funding_rate`, `binance_funding_rate`

### 2. Symbol カバレッジ (時間範囲 + 欠損)

wide panel CSV で吐き出してから pandas で集計するパターン:

```bash
hivefi-factory data fetch {source_table} \
  --symbols BTC ETH SOL AVAX BNB \
  --start 2022-01-01 --end 2025-12-31 \
  --time-col time --symbol-col symbol --value-col {value_col} \
  --output data/{source}.csv
```

```python
import pandas as pd

df = pd.read_csv("data/{source}.csv", index_col=0, parse_dates=True)
coverage = pd.DataFrame(
    {
        "first_date": df.apply(lambda c: c.first_valid_index()),
        "last_date":  df.apply(lambda c: c.last_valid_index()),
        "rows":       df.notna().sum(),
    }
).reset_index().rename(columns={"index": "symbol"})
print(coverage.to_string(index=False))
```

欠損日数を計算:

```python
for sym in coverage["symbol"]:
    col = df[sym].dropna()
    if col.empty:
        continue
    expected = pd.date_range(col.index.min(), col.index.max(), freq="D")
    missing = expected.difference(col.index)
    print(f"{sym}: {len(missing)} missing days")
```

### 3. 分布チェック

値の範囲・外れ値・歪みを symbol ごとに:

```python
desc = df.describe(percentiles=[0.5, 0.99]).T
print(desc[["min", "50%", "99%", "max"]])
```

極端な外れ値は data 品質問題 (price feed 事故など) か本物の shock かを判断。

### 4. 論理整合性

- `price > 0` が常に成立か
- `OI >= 0`、`funding_rate` が `[-1, 1]` 等の範囲制約が守られているか
- `high >= close >= low` 等のOHLC ロジック (個別に列ごとに fetch して比較)

データ source によってチェック項目は変わる。OHLC を 1 度に見たい場合は ClickHouse 直接 SELECT が早い:

```python
rows = ch.query_rows(
    "SELECT time, symbol, open, high, low, close FROM {table:Identifier} "
    "WHERE time BETWEEN {start:DateTime} AND {end:DateTime} "
    "AND symbol IN {syms:Array(String)} ORDER BY time, symbol",
    {
        "table": "hyperliquid_kline_1d",
        "start": "2024-01-01",
        "end":   "2024-12-31",
        "syms":  ["BTC", "ETH", "SOL"],
    },
)
```

### 5. 報告

```
=== universe data quality: BTC, ETH, SOL, AVAX, BNB (coinglass_oi_d) ===

coverage:
  BTC: 2020-02-27 ~ 2025-12-31, ... rows, 0 missing
  ETH: 2020-02-27 ~ 2025-12-31, ... rows, 0 missing
  SOL: 2021-06-12 ~ 2025-12-31, ... rows, 0 missing
  AVAX: 2021-07-15 ~ 2025-12-31, ... rows, 3 missing (2022-06-18,19,20)
  BNB: 2020-08-30 ~ 2025-12-31, ... rows, 0 missing

分布 (OI, USD):
  BTC: min=1.2B, p50=12B, p99=45B — 健全
  ETH: min=500M, p50=5B, p99=18B — 健全
  SOL: min=100M, p50=1.2B, p99=6B — 健全
  AVAX: min=50M, p50=300M, p99=1.5B — p99 が通常より高い、2022 crash 時の spike か？
  BNB: min=300M, p50=1B, p99=3B — 健全

論理整合性: 全 symbol で OI >= 0 ✓

→ AVAX の 2022 時期が異常値あり、戦略 BT 前に regime split を検討
```

## 注意

- symbol list は universe によって変わる (hl_all は動的、hl_btc は固定)
- 2026-01-01 以降は test period / holdout なので、market-research の input に含めない
- 欠損が多い symbol は universe から外す / imputation するか判断必要
- 異常値は data source の bug か market regime かを峻別
- ClickHouse 直接接続には `CLICKHOUSE_USER` + `CLICKHOUSE_PASSWORD` 環境変数が必要 (`.env` を参照)。host / port などは `src/hivefi_factory/config.py` の固定値
