"""Example momentum strategy — 20 日 return の top 3 long / bottom 3 short。

参考用の最小例。`hivefi strategy push example-mom-D-W-hl-btc-ls-v2` で
smoke test が通るかを確認するのに使える。
"""

from __future__ import annotations

import datetime as dt
import pandas as pd

from core.base import StrategyV2
from core.context import Signal, StrategyContext


_LOOKBACK = 20
_TOP_N = 3


class Strategy(StrategyV2):
    data_requirements = ["price"]

    def compute_signals(self, ctx: StrategyContext) -> list[Signal]:
        if pd.Timestamp(ctx.date).date() >= dt.date(2026, 1, 1):
            return []
        price: pd.DataFrame = ctx.data["price"]
        if price.empty or len(price) < _LOOKBACK + 1:
            return []

        returns = price.pct_change(_LOOKBACK).iloc[-1].dropna()
        if len(returns) < _TOP_N * 2:
            return []

        longs = returns.nlargest(_TOP_N).index.tolist()
        shorts = returns.nsmallest(_TOP_N).index.tolist()
        pct = 1.0 / (len(longs) + len(shorts))
        t = ctx.date.isoformat()

        signals = [Signal(symbol=s, side="buy", percentage=pct, time=t) for s in longs]
        signals += [Signal(symbol=s, side="sell", percentage=pct, time=t) for s in shorts]
        return signals
