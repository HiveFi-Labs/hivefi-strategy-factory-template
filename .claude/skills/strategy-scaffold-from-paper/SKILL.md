---
name: strategy-scaffold-from-paper
description: |
  trading_ideas や web (arXiv, SSRN, Quantpedia) から戦略 idea を拾い、
  crypto 適用版の scaffold (configs + extensions) を自動生成する。
  "この論文の戦略を crypto で試したい" に対応。

trigger:
  - user が "この論文を crypto で実装" "Residual Momentum を crypto で" 等
  - 新しい戦略 idea を形にしたいとき
---

# /strategy-scaffold-from-paper skill

## 目的

文献の alpha idea を最小の crypto 適用版として scaffold 化、submit まで繋げる。

## 手順

### 1. 文献取得 (trading_ideas 想定)

```bash
# trading_ideas CLI が手元にあれば (別 project)
trading-ideas list --tag momentum --limit 5
trading-ideas show awesomelist-f56de4dd81f2
```

または user が URL / 論文 abstract を直接提示するケース。

### 2. 戦略の核を抽出

agent は abstract から以下を特定:

| 要素 | 例 |
|---|---|
| **signal** | "直近 12 ヶ月 return の top 決ile を long" |
| **rebalance** | monthly |
| **universe** | US equity → crypto だと hl_all や hl_majors に置換 |
| **side** | long-only or long-short |
| **filter** | 生存バイアス対策 / momentum crash 対策の trend filter 等 |

### 3. Crypto 移植の翻訳ルール

| 株の概念 | crypto 版 |
|---|---|
| 月次 rebalance | 週次 rebalance (crypto は 24/7、更新頻度高) |
| 12 ヶ月 return | 60-90 日 return (crypto の時間感覚スケール) |
| S&P500 universe | hl_all (Hyperliquid の perp 全 symbol) or hl_majors (top 20) |
| sector neutral | BTC 残差 (residual momentum 的アプローチ) |
| earnings 前後除外 | 該当概念なし、撤去 |
| long-only | long-short に拡張 (crypto は short 容易) |
| 月次 BT | 週次 or 日次 BT |

### 4. strategy_id を生成

命名規則: `{logic}-{timeframe}-{rebalance}-{exchange}-{universe}-{mode}-v2`

例:
- Faber "Relative Strength" monthly → `rsfaber-20d-D-W-hl-all-ls-v2`
- Blitz "Residual Momentum" → `resmom-20d-D-W-hl-all-ls-v2`
- Padysak "BTC Seasonality" → `trendmax-20d-D-W-hl-all-ls-v2`

### 5. Scaffold 生成 (agent が直接 Write)

agent が `./configs/{strategy_id}.json` と `./extensions/{strategy_id}.py` の 2
ファイルを直接書く。`hivefi-factory` CLI には scaffold コマンドは無い (skill 側に内在化)。

`./configs/{strategy_id}.json` テンプレート:

```json
{
  "strategy_id": "{strategy_id}",
  "title": "{人間が読める title}",
  "description": "{論文の抽象 + crypto 翻訳の意図}",
  "exchange": "hyperliquid",
  "universe": "hl_all",
  "rebalance_freq": "W-FRI",
  "rebalance_enabled": true,
  "auto_close_missing": true,
  "warmup_periods": 60
}
```

`./extensions/{strategy_id}.py` テンプレート (compute_signals の中身は次 step で
置換、`warmup_periods` は config と一致させる):

```python
from __future__ import annotations
import pandas as pd
from core.base import StrategyV2
from core.context import Signal, StrategyContext


class Strategy(StrategyV2):
    warmup_periods = 60          # config の warmup_periods と一致させる (Method B/C 必須)
    data_requirements = ["price"]

    def compute_signals(self, ctx: StrategyContext) -> list[Signal]:
        # TODO: 次 step で論文の signal 定義をここに実装
        return []
```

### 6. compute_signals を翻訳実装

agent は論文の signal 定義を Python に書き下す。AST denylist
(`os` / `sys` / `subprocess` / `socket` / `pickle` / `boto3` / `inspect` / `operator` /
`functools` / `getattr` / `setattr` / `eval` / `exec` / `__class__` / `__subclasses__` 等は禁止) を遵守。
書き終わったら `hivefi-factory validate {strategy_id}` で server-side denylist と同じルールで事前検査する:

```python
from __future__ import annotations
import pandas as pd
from core.base import StrategyV2
from core.context import Signal, StrategyContext


class Strategy(StrategyV2):
    """論文 XYZ の crypto 適用版。

    出典: Blitz et al. 2013 "Residual Momentum"
    Crypto 翻訳: BTC を market factor と見做し、他 symbol の return から
                 BTC return を引いた残差 (20 日累積) で cross-sectional rank。

    元論文との差異:
      - monthly → weekly rebalance (crypto の時間感覚)
      - OLS residual → 単純差分 (beta=1 仮定、MVP)
      - long-only → long-short (crypto は short 容易)
    """

    warmup_periods = 60
    data_requirements = ["price"]

    def compute_signals(self, ctx: StrategyContext) -> list[Signal]:
        price = ctx.data["price"]
        if price.empty or len(price) < 22 or "BTC" not in price.columns:
            return []

        rets = price.pct_change().iloc[-20:]
        btc_rets = rets["BTC"]
        residual = rets.sub(btc_rets, axis=0)
        cum = residual.sum().drop(labels=["BTC"], errors="ignore").dropna()
        if len(cum) < 6:
            return []

        longs = cum.nlargest(3).index.tolist()
        shorts = cum.nsmallest(3).index.tolist()
        pct = 1.0 / (len(longs) + len(shorts))
        t = ctx.date.isoformat()
        return (
            [Signal(symbol=s, side="buy", percentage=pct, time=t) for s in longs]
            + [Signal(symbol=s, side="sell", percentage=pct, time=t) for s in shorts]
        )
```

### 7. docstring で出典を必ず明記

論文の title, authors, year, URL、そして **crypto 翻訳での差異** を必ず書く。IP / citation 的な誠実さと、後から参加者自身が振り返る時に必須。

### 8. (optional) 事前に `/factor-research` で IC check

戦略化する前に同じ factor で IC を測ると、実装 vs 理論の gap を確認できる。

### 9. `/submit-flow` で公式 push

```
→ /submit-flow を invoke して `hivefi-factory validate` → `hivefi-factory strategy push`
   → Stage 1 auto trigger → `hivefi-factory bt poll` で完走待ち → KPI 取得まで
```

## 注意

- 論文の backtest 期間は eq market、crypto では時代が浅いので過剰適合リスク大
- regime (bull / bear) での robustness を必ず確認 (crypto は 2022 bear 期と 2023-24 bull 期で大きく違う)
- 論文そのままでは効かないことが多い、翻訳時に「どこで寄与を生むか」を自分で考える

## 出力例

```
user: "Residual Momentum 論文の crypto 版を作って"

agent:
1. trading-ideas show awesomelist-f56de4dd81f2 で abstract 確認
   → Blitz et al. 2013, 株 residual momentum が total return momentum の 2 倍 sharper

2. crypto 翻訳:
   - universe: hl_all (Hyperliquid の全 perp)
   - rebalance: W-FRI (crypto 時間感覚)
   - market factor: BTC return (OLS residual は未使用、beta=1 仮定)
   - side: long-short (crypto は short 容易)

3. strategy_id: resmom-20d-D-W-hl-all-ls-v2

4. agent が直接 Write:
   - configs/resmom-20d-D-W-hl-all-ls-v2.json
   - extensions/resmom-20d-D-W-hl-all-ls-v2.py

5. extensions/resmom-20d-D-W-hl-all-ls-v2.py の compute_signals を実装
   (BTC 残差の 20 日累積で rank、top 3 long / bottom 3 short)

6. hivefi-factory validate resmom-20d-D-W-hl-all-ls-v2 → OK

7. 次のステップ提案: "/submit-flow で公式 push しますか？"
```
