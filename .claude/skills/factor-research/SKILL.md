---
name: factor-research
description: |
  ある数値 (factor) が将来 return を予測するか、ローカルで IC + 分位分析する。
  `hivefi-factory data fetch` (CH 直読) で factor + price を panel 形式で取得し、
  手元 pandas で計算。結果は IC mean/std/t_stat/p_value/q_value + R2_mean +
  hit rate + 分位 spread + sample size + PNG 可視化 + 自然な日本語 report.md +
  根拠付きの総合判断の形で返す。
  参加者が戦略を実装する前に「この因子、効くの？」を数分で測る用途。

  IC は Pearson correlation で計算する (単に "IC" と呼ぶ)。R2_mean は
  評価時点ごとの `IC^2` の平均で、方向は IC mean、説明力は R2_mean で見る。

trigger:
  - user が "XYZ factor の IC を測って" "TVL momentum 効くか見て" 等の依頼
  - 戦略の仮説検証を始めるとき
---

# /factor-research skill

## 目的

ある numerical factor (CH にある time-series) が cross-section で将来 return を予測するかを測る。
結果は `artifacts/<experiment_id>/factor-research/report.md` に、人間が読める自然な日本語レポートとして保存する。
artifact のディレクトリ名は `strategy_id` 固定ではなく `experiment_id` として扱う。
単一戦略や単一 factor の検証なら `experiment_id = strategy_id` や factor 名でよい。比較実験や再検証では
`trendscan-30d-baseline`, `funding-rate-7d-v2` のように experiment 単位で分けてよい。
開始時点で `experiment_id` を決められない場合は、まず `factor-research-2026-04-23` や
`factor-research-2026-04-23-draft` のような暫定 slug を使ってよい。strategy 名や
比較条件が後で確定したら、必要なら最後にディレクトリ名を rename して report 内の path も合わせる。
artifact 保存を、正式 ID の確定待ちで止めない。
空白・記号は `-` に寄せ、内部列名ではなく人間が見て意味の分かる名前にする。

探索や transform / horizon / universe の比較を含む場合は、多重検定として扱う。
単発の事前仮説でも `p_value` と `q_value` を report に残す。探索 batch では、同じ
収益源・同じ signal family から出た idea / variant を 1 つの検定ファミリーとして
Benjamini-Hochberg FDR で補正し、`q_value` と family size を書く。ファミリーを
後から細かく分けて有意に見せない。

## データ期間 / holdout

2026-01-01 以降は test period / holdout として扱い、factor-research の IC / 分位分析には使わない。
fetch では原則 `--end 2025-12-31` を明示する。API 側でも `data rows` は 2026+ を返さない前提なので、2026 指定で拒否された場合は回避せず、期間を 2025-12-31 までに直して報告する。
report には使用 period を必ず書き、2026 以降を含めていないことを明示する。

## 手順

### 1. 必要な data source を特定

参加者の依頼から factor / price table、universe、rebalance 間隔を決める。
データは ClickHouse Cloud に直接接続して読む (HTTP API には fetch endpoint は無い)。
まず該当 table の存在と column を SQL で確認し、以下を記録する:

- factor table 名 と factor value column (例: `value`, `tvl`, `funding_rate`)
- price table 名 と close column (例: `hyperliquid_kline_1d.close`)
- time column / symbol column
- universe、start date、forward horizon

universe はユーザー指定を優先する。`主要 N 銘柄` のように曖昧な場合は、factor と price の両方に存在する大型・高流動性銘柄から N 個を選び、選定規則を報告する。market cap source まで検証できない場合は「主要」の厳密判定ではなく、data availability と大型銘柄の実務的 proxy と明示する。

table と column を確認する基本パターン (Python で `clickhouse_connect` を使うか、`hivefi_factory.clickhouse.ClickHouseClient` を使う):

```python
from hivefi_factory.clickhouse import ClickHouseClient

with ClickHouseClient() as ch:
    # 候補 table を探す
    tables = ch.query_rows(
        "SELECT name FROM system.tables WHERE database = currentDatabase() "
        "AND name ILIKE {pattern:String} ORDER BY name LIMIT 50",
        {"pattern": "%funding%"},
    )
    print(tables)

    # column を確認
    cols = ch.query_rows(
        "SELECT name, type FROM system.columns "
        "WHERE database = currentDatabase() AND table = {t:String}",
        {"t": "hyperliquid_funding_rate"},
    )
    print(cols)
```

代表的な factor table 候補:
- `coinglass_oi_d` / `coinglass_oi_*` (open interest)
- `defillama_tvl_*` (TVL 合計、protocol or chain)
- `hyperliquid_funding_rate` / `binance_funding_rate`

price は `hyperliquid_kline_1d.close` が定番だが、実際の table 名と column は system.columns で確認する。
funding rate のように source 候補が複数ある場合は、まず依頼で指定された venue を優先する。指定がなければ price source と同じ取引所・同じ市場種別に揃う source を第一候補にする。揃う候補が複数残る、または schema 上 venue が判別できない場合は arbitrary に選ばず blocker にし、選択理由と未検証の候補を report に書く。

少量 sample で column が読めるか確認:

```bash
hivefi-factory data fetch {factor_table} --symbols BTC --start 2024-01-01 --end 2024-01-31 --value-col value
hivefi-factory data fetch {price_table}  --symbols BTC --start 2024-01-01 --end 2024-01-31 --value-col close
```

### 2. data fetch

`hivefi-factory data fetch` は wide panel CSV (rows=time, cols=symbol) を吐く。
`--symbols` を指定しないと universe 全銘柄 (重い)。デフォルトの `--time-col` / `--symbol-col` /
`--value-col` は `time` / `symbol` / `close` なので、factor table では `--value-col` を上書きする。

```bash
mkdir -p data
hivefi-factory data fetch {factor_table} \
  --symbols BTC ETH SOL ... \
  --start 2022-01-01 --end 2025-12-31 \
  --time-col time --symbol-col symbol --value-col {factor_value_col} \
  --output data/factor.csv

hivefi-factory data fetch {price_table} \
  --symbols BTC ETH SOL ... \
  --start 2022-01-01 --end 2025-12-31 \
  --time-col time --symbol-col symbol --value-col close \
  --output data/price.csv
```

API key / ClickHouse credentials / table / 十分な universe が使えない場合は、IC を推測で埋めず、blocker として日本語で報告して停止する。CH の query が timeout / memory cap にひっかかる場合は universe / date range を分割する。horizon や transform を都合よく変えて回避しない。
ユーザー指定 universe の symbol 数が明らかに足りない場合は、source/schema 確認より先に sample size blocker を出してよい。

### 3. ローカルで IC + 分位分析

Python snippet (agent が実行):

```python
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats
from pathlib import Path

# schema 確認結果に合わせて必ず設定する
TIME_COL = "time"
SYMBOL_COL = "symbol"
FACTOR_VALUE_COL = "value"
PRICE_CLOSE_COL = "close"
EXPERIMENT_ID = "factor-research-run"  # strategy_id / factor_name / 比較実験名。未確定なら factor-research-YYYY-MM-DD-draft などの暫定 slug で開始してよい
OUT_DIR = Path("artifacts") / EXPERIMENT_ID / "factor-research"

# rebalance 間隔に合わせる。crypto weekly は 7 を既定にし、business-day 指定時のみ 5。
FORWARD_DAYS = 7
TRANSFORM_WINDOW = 20

# transform window は既定 20。別 window を使う場合は、依頼または感度分析として理由を報告する。

# 主結果の transform。
# ユーザーが raw factor を聞いている場合は "level" を主結果にし、sweep は補助。
# "momentum" / "change" 依頼なら、指定がない限り window=20 の "pct_change" を主結果、
# 同じ window の "diff" を比較対象にする。価格トレンド t-stat など独自候補比較では、
# DISPLAY_NAMES と候補生成を依頼に合わせて明示的に差し替える。
PRIMARY_TRANSFORM = "level"

# report / PNG では内部名をそのまま使わず、意味ベースの表示名にする。
# 依頼に合わせて必ず具体化する。
DISPLAY_NAMES = {
    "level": "水準",
    "pct_change": "変化率",
    "diff": "差分",
    "ema": "指数平滑",
    "rolling_std": "変動性",
    "deviation_from_mean": "平均からの乖離",
}

# 同一 symbol/day に複数行ある factor は日次集約してから IC を計算する。
# funding_rate など intraday rate は "mean"、日次 close や EOD level は "last" が基本。
FACTOR_AGG = "last"
PRICE_AGG = "last"

def apply_transform(panel, transform, window):
    """factor panel に transform を適用"""
    if transform == "level": return panel
    if transform == "diff": return panel.diff(window)
    if transform == "pct_change": return panel.pct_change(window)
    if transform == "ema": return panel.ewm(span=window, adjust=False).mean()
    if transform == "rolling_std": return panel.rolling(window).std()
    if transform == "deviation_from_mean": return panel - panel.rolling(window).mean()
    raise ValueError(f"unknown: {transform}")

def daily_ic(factor, fwd):
    """日次 IC 系列 (Pearson correlation)。"""
    common_idx = factor.index.intersection(fwd.index)
    common_cols = factor.columns.intersection(fwd.columns)
    f = factor.loc[common_idx, common_cols]
    r = fwd.loc[common_idx, common_cols]
    ics = []
    for t in f.index:
        rf, rr = f.loc[t], r.loc[t]
        mask = rf.notna() & rr.notna()
        if mask.sum() < 5:
            ics.append(float("nan"))
            continue
        ics.append(rf[mask].corr(rr[mask]))  # Pearson = default .corr()
    return pd.Series(ics, index=f.index, name="ic")

def quintile_returns(factor, fwd, q=5):
    """分位ごとの平均 forward return"""
    common_idx = factor.index.intersection(fwd.index)
    common_cols = factor.columns.intersection(fwd.columns)
    rows = []
    for t in common_idx:
        rf = factor.loc[t, common_cols]
        rr = fwd.loc[t, common_cols]
        mask = rf.notna() & rr.notna()
        if mask.sum() < q * 2:
            continue
        try:
            buckets = pd.qcut(rf[mask], q=q, labels=False, duplicates="drop")
        except ValueError:
            continue
        g = rr[mask].groupby(buckets).mean()
        rows.append({"time": t, **{f"Q{int(k)+1}": v for k, v in g.items()}})
    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()

def benjamini_hochberg(p_values):
    """Benjamini-Hochberg FDR correction. Returns q-values in original order."""
    p = pd.Series(p_values, dtype="float64")
    out = pd.Series(np.nan, index=p.index, dtype="float64")
    valid = p.dropna()
    m = len(valid)
    if m == 0:
        return out
    ordered = valid.sort_values()
    ranks = np.arange(1, m + 1, dtype="float64")
    q_sorted = (ordered.to_numpy() * m / ranks)
    q_sorted = np.minimum.accumulate(q_sorted[::-1])[::-1]
    q_sorted = np.clip(q_sorted, 0.0, 1.0)
    out.loc[ordered.index] = q_sorted
    return out

def load_wide_panel(path):
    """`hivefi-factory data fetch` が吐いた wide CSV (rows=time, cols=symbol) を読む。"""
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(None).floor("D")
    # 同一日の複数行は agg で潰す (`hivefi-factory` は通常 1 日 1 行だが defensively)
    df = df.groupby(df.index).last() if PRICE_AGG == "last" else df.groupby(df.index).mean()
    return df.sort_index()

# --- run ---
# CSV → panel (wide format, columns = symbol)
factor_panel = load_wide_panel("data/factor.csv")
price_panel  = load_wide_panel("data/price.csv")

# forward return
fwd = price_panel.shift(-FORWARD_DAYS) / price_panel - 1.0
fwd = fwd.replace([np.inf, -np.inf], np.nan)

# factor transform (主結果 + sweep 候補)
results = []
quintile_summaries = {}
for transform in ["level", "pct_change", "diff", "ema", "rolling_std", "deviation_from_mean"]:
    factor = apply_transform(factor_panel, transform, window=TRANSFORM_WINDOW)
    factor = factor.replace([np.inf, -np.inf], np.nan)
    ic = daily_ic(factor, fwd).dropna()
    q = quintile_returns(factor, fwd)
    spread = (q["Q5"] - q["Q1"]).mean() if {"Q1", "Q5"}.issubset(q.columns) else float("nan")
    if not q.empty:
        quintile_summaries[transform] = q.mean()
    ic_std = ic.std(ddof=1)
    t_stat = ic.mean() / (ic_std / np.sqrt(len(ic))) if len(ic) > 1 and pd.notna(ic_std) and ic_std > 0 else np.nan
    p_value = 2.0 * stats.t.sf(abs(t_stat), df=len(ic) - 1) if len(ic) > 1 and pd.notna(t_stat) else np.nan
    results.append({
        "transform": transform,
        "display_name": DISPLAY_NAMES.get(transform, transform),
        "horizon_days": FORWARD_DAYS,
        "n_dates": len(ic),
        "ic_mean": ic.mean(),
        "r2_mean": (ic ** 2).mean(),
        "ic_std": ic_std,
        "t_stat": t_stat,
        "p_value": p_value,
        "hit_rate": (ic > 0).mean(),
        "q5_minus_q1": spread,
    })

df = pd.DataFrame(results)
df["q_value"] = benjamini_hochberg(df["p_value"])
df["test_family_n"] = df["p_value"].notna().sum()
df["abs_ic"] = df["ic_mean"].abs()
df = df.sort_values("abs_ic", ascending=False).drop(columns=["abs_ic"])
print(df.to_string(index=False))

primary = df[df["transform"] == PRIMARY_TRANSFORM]
if not primary.empty:
    print("\nPRIMARY")
    print(primary.to_string(index=False))

# CSV + PNG report
OUT_DIR.mkdir(parents=True, exist_ok=True)
df.to_csv(OUT_DIR / "ic_results.csv", index=False)
if quintile_summaries:
    qdf = pd.DataFrame(quintile_summaries).T
    qdf.index.name = "transform"
    qdf.to_csv(OUT_DIR / "quintile_means.csv")
    qdf_plot = qdf.rename(index=DISPLAY_NAMES)

    fig, ax = plt.subplots(figsize=(9, 5))
    for display_name, row in qdf_plot.iterrows():
        ax.plot(row.index, row.values, marker="o", label=display_name)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title("分位別の平均リターン")
    ax.set_xlabel("分位")
    ax.set_ylabel("平均リターン")
    ax.legend(title="評価対象", fontsize=8)
    plt.tight_layout()
    plt.savefig(OUT_DIR / "quintile_means.png", dpi=160)
    plt.close()

fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
plot_df = df.set_index("display_name")
for ax, col, title in [
    (axes[0], "ic_mean", "IC mean"),
    (axes[1], "t_stat", "IC t-stat"),
    (axes[2], "q5_minus_q1", "Q5 - Q1"),
]:
    plot_df[col].plot(kind="bar", ax=ax)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title(title)
    ax.set_xlabel("")
    ax.tick_params(axis="x", labelrotation=45)
    for label in ax.get_xticklabels():
        label.set_horizontalalignment("right")
fig.suptitle(f"指標検証サマリ, forward={FORWARD_DAYS}d")
plt.tight_layout()
plt.savefig(OUT_DIR / "ic_summary.png", dpi=160)
plt.close()
```

### 4. 可視化を確認

標準出力に加えて、`artifacts/<experiment_id>/factor-research/` に以下を保存する:

- `ic_results.csv` — transform 別 IC / R2_mean / t_stat / p_value / q_value / hit_rate / q5_minus_q1 / test_family_n
- `quintile_means.csv` — transform 別の Q1〜Q5 平均 forward return
- `ic_summary.png` — IC mean / t_stat / q5_minus_q1 の縦棒グラフ
- `quintile_means.png` — 横軸を factor quintile、縦軸を平均 forward return にした分位別グラフ
- `regime_ic.csv` / `regime_stability.png` — 相場期間ごとの安定性。実行できない場合は理由を report に書く
- `symbol_counts.csv` / `symbol_counts.png` — 日次の有効銘柄数推移。価格データあり、forward return あり、主評価対象で評価可能、など report に必要な系列を含める
- `sample_skips.csv` — warmup / 期間末尾 / 欠損などで除外した sample の再現用詳細。report 本文では除外日数より、評価日数と有効銘柄数を重視する
- `commands.txt` — 実行コマンドと前提
- `report.md` — 人間が読む最終レポート

`regime_ic.csv` / `regime_stability.png` は原則生成する。標準の区切りは `全期間`, `2022年`, `2023-2024年`, `2025年` とし、依頼期間と重なる部分だけ集計する。十分な評価日がない区切りは無理に数値を埋めず、report に「未評価」と理由を書く。

`sample_skips.csv` は、少なくとも以下の理由を分けて集計する:

- `factor_warmup`: rolling / momentum 計算に必要な初期期間
- `forward_return_unavailable_at_period_end`: 期間末尾で forward return が取れない日
- `missing_factor_or_price`: factor または price の欠損
- `insufficient_symbols_for_ic`: IC の最低銘柄数を満たさない日
- `insufficient_symbols_for_quintile`: 分位分析の最低銘柄数を満たさない日

ただし、除外日数の棒グラフは標準 report には載せない。重要なのは「評価できた日数」と「その日に横断比較できた銘柄数」である。`symbol_counts.png` は横軸を日付、縦軸を有効銘柄数にし、必要に応じて IC 最低目安 5 銘柄、分位分析最低目安 10 銘柄の補助線を入れる。

可視化は原則として、測定値・return・IC・spread などの数値を縦軸に置く。category / transform / quintile / period などの比較対象は横軸に置く。例外的に横棒や横向き line plot を使う場合は、長いラベルの可読性など理由を report に明記する。

report では PNG path を明記し、画像を見た上で「どの指標が整合 / 不整合か」を説明する。CSV だけ出して終わらない。
report 本文には `DataFrame.to_string()` の横長出力をそのまま貼らない。横に長い数値は以下のどれかに変換する:

- 5〜10 行程度の要約は Markdown table にし、数値は必要に応じて percentage / 小数桁丸めで読む
- transform × quintile、regime × metric、skip reason × transform などは PNG 可視化を追加する
- 詳細 CSV は artifact として保存し、本文では主要な列と解釈だけを載せる

コードブロックはコマンド、短いログ、または本文で表にしにくい raw text に限定する。
report 本文では変数名・内部名を説明の主語にしない。内部名は、必要なら artifact CSV や commands に残し、本文では「20日固定トレンド」「現行ロジック」「弱いシグナルを除外しない現行ロジック」のような意味ベースの表示名を使う。内部名を出す場合も、再現用の補助情報に限定し、判断や説明は自然言語の表示名で書く。

価格トレンド t-stat の 10 / 20 / 30 日候補を比較する場合は、report 本文の表示名を次に揃える:

- `10日固定トレンド`
- `20日固定トレンド`
- `30日固定トレンド`
- `現行ロジック`
- `弱いシグナルを除外しない現行ロジック`

結果 table は、単なる内部名順ではなく、読み手が判断しやすい順にする。原則は `最も説明が強い評価対象`, `次点`, `主評価対象`, `補助比較`, `弱い評価対象` の順。CSV は再現用なので内部名や機械ソートを残してよい。

### 5. report.md を書く

report は短い実行ログではなく、戦略開発者がそのまま判断に使える日本語レポートにする。
通常完了時は、`artifacts/trendscan-30d-D-W-hl-all-ls-v2/factor-research/report.md` と同じ構成を canonical format とし、以下の順序・表・画像配置を守る。`要約`、`診断モード`、`実行ログ` のような top-level section は追加しない。

1. `# {指標または戦略候補名}の検証レポート`
2. `作成日時: YYYY-MM-DD`
3. `## 戦略の意図`
4. `## 検証条件`
5. `## 結果`
6. `## 分位の形`
7. `## 相場期間ごとの効き方`
8. `## サンプルとカバレッジ`
9. `## 総合評価`
10. `## 生成物`

各章で必ず使う構成:

- **戦略の意図**: 3 段落程度で書く。1 段落目は検証したい仮説、2 段落目は元データと指標の作り方、3 段落目はロング / ショートなど戦略化したときの意味を書く。数式や内部名だけで説明しない。
- **検証条件**: 先に 1 段落で、期間、2026 以降を使っていないこと、対象銘柄数、入力行数、予測対象を説明する。続けて次の table を置く: `項目 | 内容`。行は `元データ`, `主評価対象`, `比較対象`, `対象銘柄`, `期間`, `行数 / 銘柄数`, `horizon`, `評価指標`, `検定ファミリー`, `多重検定補正` を基本にする。最後に、これは指標単体の検証であり、コスト込みのポートフォリオ検証ではないと明記する。
- **結果**: 主要評価対象の数値を 2 段落程度で説明してから、次の table を置く: `評価対象 | n dates | IC mean | R2_mean | IC t-stat | p_value | q_value | Hit rate | Q5 - Q1 | コメント`。その直後に `![IC summary](ic_summary.png)` を置き、図から読める強弱を 1 段落で説明する。
- **分位の形**: 分位形状の読み方を 1 段落で説明してから、次の table を置く: `評価対象 | Q1 | Q2 | Q3 | Q4 | Q5 | コメント`。その直後に `![Quintile means](quintile_means.png)` を置き、単調性、Q4/Q5 の逆転、過熱反転の可能性を 1 段落で説明する。
- **相場期間ごとの効き方**: 期間別の偏りを 1 段落で説明してから、次の table を置く: `期間 | 日付 | n dates | IC mean | IC t-stat | Hit rate | コメント`。期間は原則 `全期間`, `2022年`, `2023-2024年`, `2025年`。その直後に `![相場期間ごとの効き方](regime_stability.png)` を置き、特定期間に寄っていないかを 1 段落で説明する。
- **サンプルとカバレッジ**: `n_dates`、`n_quintile_dates`、日次平均の有効銘柄数、最小有効銘柄数、IC / 分位分析の最低銘柄数、sample 不足かどうかを説明する。除外日数そのものを主役にしない。次の table を置く: `評価対象 | 評価日数 | 分位評価日数 | 日次平均有効銘柄数 | 最小有効銘柄数 | コメント`。その直後に `![日次有効銘柄数の推移](symbol_counts.png)` を置き、銘柄数が期間を通じて十分かを説明する。
- **総合評価**: 箇条書きだけで終わらせない。1 段落目で次工程へ進める / 分析で止める / 条件を変えて再検証する、のどれかを述べる。2 段落目で弱点を「第一に、第二に、第三に」のように文章で整理し、3 段落目で補正後有意性、R2_mean、sample、分位形状、事前仮説との整合をまとめる。submit / 公式 BT / BT 診断の command や状態は書かない。
- **生成物**: 最後に `種類 | Path` table を置く。行は `IC 集計`, `分位別平均`, `相場期間別 IC`, `有効銘柄数推移`, `IC 図`, `分位図`, `相場期間別図`, `有効銘柄数推移図`, `実行コマンド` を基本にする。

通常 report の骨組み:

```markdown
# {指標または戦略候補名}の検証レポート

作成日時: {YYYY-MM-DD}

## 戦略の意図

{仮説}

{元データと指標の作り方}

{ロング / ショートなど戦略化したときの意味}

## 検証条件

{期間、2026 以降を使っていないこと、対象銘柄数、行数、予測対象}

| 項目 | 内容 |
|---|---|
| 元データ | {source display name} |
| 主評価対象 | {primary display name and meaning} |
| 比較対象 | {comparison display names} |
| 対象銘柄 | {symbols} |
| 期間 | {start} 〜 {end} |
| 行数 / 銘柄数 | {rows} 行 / {symbols_count} 銘柄 |
| horizon | {forward horizon in Japanese} |
| 評価指標 | Pearson IC, R2_mean, IC t-stat, p_value, q_value, hit rate, Q5 - Q1, 分位単調性, regime split |
| 検定ファミリー | {single predeclared hypothesis / N transforms / N ideas in same signal family} |
| 多重検定補正 | {Benjamini-Hochberg FDR / 単独仮説なので q_value = p_value} |

このレポートは指標単体の検証であり、コスト込みのポートフォリオ検証ではない。したがって、ここで見るのは「銘柄を並べる指標として筋があるか」である。

## 結果

{primary result paragraph}

{comparison result paragraph}

| 評価対象 | n dates | IC mean | R2_mean | IC t-stat | p_value | q_value | Hit rate | Q5 - Q1 | コメント |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| {display name} | {n} | {ic} | {r2} | {t} | {p} | {q} | {hit} | {spread} | {comment} |

![IC summary](ic_summary.png)

{IC summary image interpretation}

## 分位の形

{quintile shape paragraph}

| 評価対象 | Q1 | Q2 | Q3 | Q4 | Q5 | コメント |
|---|---:|---:|---:|---:|---:|---|
| {display name} | {q1} | {q2} | {q3} | {q4} | {q5} | {comment} |

![Quintile means](quintile_means.png)

{quintile interpretation paragraph}

## 相場期間ごとの効き方

{regime paragraph}

| 期間 | 日付 | n dates | IC mean | IC t-stat | Hit rate | コメント |
|---|---|---:|---:|---:|---:|---|
| 全期間 | {start} 〜 {end} | {n} | {ic} | {t} | {hit} | {comment} |

![相場期間ごとの効き方](regime_stability.png)

{regime interpretation paragraph}

## サンプルとカバレッジ

{sample size paragraph}

{symbol count paragraph}

| 評価対象 | 評価日数 | 分位評価日数 | 日次平均有効銘柄数 | 最小有効銘柄数 | コメント |
|---|---:|---:|---:|---:|---|
| {display name} | {n_dates} | {n_quintile_dates} | {avg_symbols} | {min_symbols} | {comment} |

![日次有効銘柄数の推移](symbol_counts.png)

## 総合評価

{first verdict paragraph}

{weakness / strength paragraph}

したがって次工程は **{next_step}** とする。{factor-research next action sentence}

| 追加検証案 | 目的 |
|---|---|
| {candidate} | {purpose} |

{factor-research closing sentence}

## 生成物

| 種類 | Path |
|---|---|
| IC 集計 | `artifacts/{experiment_id}/factor-research/ic_results.csv` |
| 分位別平均 | `artifacts/{experiment_id}/factor-research/quintile_means.csv` |
| 相場期間別 IC | `artifacts/{experiment_id}/factor-research/regime_ic.csv` |
| 有効銘柄数推移 | `artifacts/{experiment_id}/factor-research/symbol_counts.csv` |
| IC 図 | `artifacts/{experiment_id}/factor-research/ic_summary.png` |
| 分位図 | `artifacts/{experiment_id}/factor-research/quintile_means.png` |
| 相場期間別図 | `artifacts/{experiment_id}/factor-research/regime_stability.png` |
| 有効銘柄数推移図 | `artifacts/{experiment_id}/factor-research/symbol_counts.png` |
| 実行コマンド | `artifacts/{experiment_id}/factor-research/commands.txt` |
```

本文の書き方:

- 「この値がこの閾値以上だから OK」のような機械判定を書かない。
- 数値は判断材料として使い、必ず分位形状、期間安定性、sample、戦略意図との整合を一緒に説明する。
- `要約` だけで終わらせない。薄い最終ノートではなく、各 stage の分析結果を残す。
- 「診断モード」のような実行都合の見出しは使わない。
- 英語の内部用語を本文の主語にしない。必要な指標名を除き、「指標」「リターン」「シグナル」「対象銘柄」「期間」のような読み手向けの語に寄せる。
- PNG のラベルも表示名を使う。日本語ラベルを使う場合は日本語対応フォントを指定し、文字化けしていないことを確認する。

ユーザーが mo 表示を求めている、または IAB で `factor-research` を見ている場合は、report 作成後に次を実行し、表示 URL を最終回答に含める:

```bash
mo artifacts/<experiment_id>/factor-research/report.md --target factor-research --open
```

`mo` command が使えない場合は、失敗を隠さず、最終回答に `report.md` の path と「mo 表示は command 不在で未実施」と書く。

### 6. 統計的証拠を付けて報告

blocker で通常分析を完了できない場合も `report.md` は残す。この場合は通常完了 report の数値表を無理に埋めず、次の最小構成にする:

1. `# {指標または戦略候補名}の検証レポート`
2. `作成日時: YYYY-MM-DD`
3. `## 戦略の意図`
4. `## 検証条件`
5. `## 未完了の理由`
6. `## 次に必要な作業`
7. `## 生成物`

`未完了の理由` には、CLI / API key / source / schema / universe / sample size / 2026 holdout など、どこで止まったかを書く。IC / 分位 / regime の値を推測で埋めない。`生成物` には作れた commands / partial CSV / report の path だけを載せ、未生成の artifact は表に入れないか `未生成` と理由を書く。

まず、source / columns / universe / date range / horizon / n_dates を明記する。
基本は **IC の符号と大きさ、R2_mean、補正後 q_value、sample、分位形状** を見る。
機械的な合否判定にはしないが、探索や複数 variant を含む場合は p_value だけを根拠に
次工程へ進めない。数値は総合判断の根拠の一部であり、「IC が X 以上だから OK」のように扱わない:

- **IC が正** → high factor が high future return を示唆している可能性。次工程へ進めるには、sample size、R2_mean、q_value、hit_rate、q5_minus_q1、分位の単調性、horizon、universe、regime を合わせて説明する
- **IC が 0 付近** → 予測力が弱い可能性。transform / forward_days / universe / regime split を再確認し、弱い理由を具体化する
- **IC が負** → high factor が low future return を示唆している可能性。反転方向で使うには、負方向の IC と q5_minus_q1、hit_rate、sample size、q_value が整合するか説明する
- **q5_minus_q1** は Q5(high factor) - Q1(low factor)。正 IC なら正、負 IC なら負が整合的
- IC の符号と q5_minus_q1 の符号が不整合、または分位が単調でない場合は弱い証拠として扱う
- raw factor の依頼では `level` を主結果、transform sweep は「改善案」として分けて報告する
- `momentum` 依頼では `pct_change` / `diff` を主評価対象にし、`level` は補助として扱う

#### 参考値 (運営側 offline 分析、**判定基準ではなく参考**)

実績ベースでは概ね以下の傾向:

| IC 水準 | 観測された SR 正率 | 年率 median |
|---|---|---|
| ≥ 0.05 | ~88% | +12.8% |
| ≥ 0.03 | ~82% | +12.4% |
| ≥ 0.02 | ~79% | +11.6% |
| 0〜0.02 | ~68% | +8.9% |
| \|IC\| < 0.01 | ~49% | +3% (≒ noise) |

これは先行戦略群の統計であり、**新 factor に直接的な合否ラインを与えるものではない**。agent は IC 値そのものに加えて R2_mean / q_value / sample / hit_rate / quintile spread / BT 環境を総合判断する。参考値 table を使う場合も、「この値以上だから次工程へ進める」とは書かず、先行分布との相対位置と不確実性を説明する。

#### 併読する補助指標

- **hit_rate** → IC の方向がどの程度安定しているかを見る補助指標。固定閾値で OK/NG にしない
- **quintile spread が IC と同符号 & monotonic** → rank 戦略としての整合性を見る補助指標。不整合なら理由を説明する

#### 総合判断の出し方

最後に、以下を必ず日本語でまとめる:

1. **方向**: raw factor を使うのか、反転方向で使うのか、方向が不明なのか
2. **強さ**: IC / R2_mean / q_value / hit_rate / q5_minus_q1 / 分位単調性 / sample size がどこまで整合しているか
3. **補正**: 検定ファミリー、p_value、q_value、family size
4. **次工程**: 実装へ進める / 分析で止める / 条件を変えて再検証する、のどれか
5. **理由**: 固定閾値ではなく、観測した指標と可視化から読める根拠
6. **次の作業**: regime split / venue 比較 / transform 再試行 / strategy scaffold / 分析終了 の具体アクション

総合判断は「IC が X 以上だから次工程へ進める」のような機械的判定にしない。特に以下の不整合があれば弱い証拠として扱う:

- IC の符号と q5_minus_q1 の符号が逆
- p_value は低いが、多重検定補正後の q_value が高い
- t_stat は強めでも hit_rate が弱い
- q5_minus_q1 は整合するが分位平均が単調でない
- n_dates や universe が少ない
- 1 regime / 1 venue に偏っている

例:

```
総合判断:
- 方向: high funding をそのまま買うより反転方向
- 強さ: IC と q5_minus_q1 は負方向で整合。ただし hit_rate は弱く、分位は単調でない
- 補正: 12 variant の BH-FDR 後 q_value は有意水準を満たさない
- 次工程: 分析で止める。単独で strategy scaffold へ進めるには早い
- 次の作業: regime split、venue 比較、funding z-score / change の再検証
```

#### blocker 報告テンプレート

```
factor-research は未完了です。
理由: {hivefi-factory CLI / API key / ClickHouse credentials / table / 列名 / universe / sample size の blocker}
現時点では IC・分位 spread を検証済みとして扱えません。
次に必要な作業: {具体的な解消手順}
```

### 7. 実装や BT へ進めるか判断

- `実装へ進める`: target behavior が data 上で観測でき、事前仮説の方向と IC / q5_minus_q1 が整合し、sample が十分にあり、R2_mean が記録され、検定ファミリー内の BH-FDR 補正後 q_value が task の基準を満たす → `/strategy-scaffold-from-paper` を invoke してよい
- `分析で止める`: target evidence が作れない、補正後 q_value が基準を満たさない、IC と分位 spread が不整合、sample 不足、または blocker → 実装へ進まない
- `条件を変えて再検証`: source / universe / horizon / aggregation の裁量が大きい、または指標が不整合 → 条件を predeclare して再測する

## 注意

- universe に BTC 以外を含めないと cross-section IC 計算できない
- IC は 1 日あたり最低 5 symbols を hard minimum とする
- `q=5` の q5_minus_q1 は 1 日あたり最低 10 symbols を hard minimum とする。5〜9 symbols しかない場合は IC のみ diagnostic とし、分位 spread は未検証と報告する
- `forward_days` は rebalance 間隔に合わせる (daily なら 1、crypto weekly なら 7、business-day weekly なら 5、monthly なら 20+)
- funding rate など intraday factor は daily 依頼なら日次平均、EOD level は日次 last に集約する。別集約を使う場合は理由を報告する
- API row limit がある source は、date range か universe を分割して fetch する。分割した場合は結合範囲と欠損を報告する
- 2026-01-01 以降は test period / holdout なので、IC / 分位分析の input に含めない。API が 2026+ を拒否した場合は正常な保護として扱い、2025-12-31 までで再実行する
- PNG 可視化を必ず生成し、最終 report に path を含める。可視化できない場合は理由を blocker / warning として書く
- `n_dates` が少ない場合は diagnostic 扱いにし、戦略化の根拠として強く扱わない
- 生存性バイアスに注意: 上場廃止 symbol は data にそもそも残らないので過大評価しがち
- regime dependence: 2022 (bear) と 2023-24 (bull) で分けて IC を見ると robustness がわかる
- この skill を編集する場合は `.claude/skills/factor-research/SKILL.md` と `.agents/skills/factor-research/SKILL.md` を同一内容にし、`pytest tests/test_skills.py -q` と `git diff --check` を実行する

## 出力規約

最終回答では、`report.md` の path と mo 表示先を示し、補正後の統計的証拠と次工程を 2〜4 文で伝える。詳細な数値表は `report.md` に置き、チャットでは貼りすぎない。

`report.md` では以下を守る:

1. 評価対象は表示名で書く。内部列名・変数名は artifact の再現情報に閉じ込める
2. IC / t-stat / hit rate / Q5-Q1 は、表と本文の両方で「何を意味するか」まで説明する
3. 分位の単調性は、表・PNG・本文で必ず確認する
4. sample が少ない、期間偏りが強い、分位形状が不自然な場合は、総合評価で明示的に弱点として扱う
5. 最後に、実装へ進める / 分析で止める / 条件を変えて再検証する、のいずれかと、次に実施すべき作業を書く
