# 大量戦略生成における偽陽性コントロール

agent で戦略候補を大量に作れるようになると、探索速度は上がる一方で、偶然よく見える戦略を採用するリスクも上がる。この文書は、HiveFi strategy factory を「大量生成で当たりを探す仕組み」ではなく、「仮説を反証しながら偽陽性を減らす research framework」として運用するための基準をまとめる。

## 問題

真の予測力がない戦略でも、バックテスト期間のノイズに合っただけで高 Sharpe、低 MaxDD、高リターンに見えることがある。1 本だけなら偶然の確率は小さく見えても、100 本、1000 本と試すほど、少なくとも 1 本が偶然よく見える確率は急激に上がる。

例として、各戦略が 5% の確率で偶然よく見えるだけだとしても、100 本試すと少なくとも 1 本がよく見える確率は次の通り。

```text
1 - 0.95^100 = 99.4%
```

したがって、agent が大量生成した候補の中から最良の KPI だけを採用すると、見かけの性能は過大評価されやすい。これは multiple testing、data snooping、selection bias、p-hacking と同じ種類の問題である。

## 原則

1. 戦略は KPI から逆算しない。市場メカニズム、観測 proxy、期待符号、売買ルール、反証条件を先に書く。
2. 1 task で扱う戦略 idea は 1 つに限定する。parameter sweep や近傍 variant の量産は別の試行として記録する。
3. 2026 年以降の test period は温存する。BT evidence と report は原則 2025-12-31 までに止める。
4. 公式 BT 前に IC、分位分析、coverage、欠損、論理整合性を確認する。
5. KPI として扱う BT は `total_trades >= 2000` を目安にする。未満は diagnostic と明示する。
6. 成功した候補だけでなく、悪い・不明・blocked の結果も記録する。
7. 既存戦略との signal source の独立性を確認する。似た signal の最良 variant を増やしても独立した発見とはみなさない。

## 偽陽性を減らすフレームワーク改善

### 1. 事前登録

各 task は実行前に次を明示する。

- 仮説: どの市場参加者の行動や制約から収益機会が生じるか。
- proxy: どのデータ列がその効果を観測しているか。
- 期待符号: proxy が大きいと将来 return は正か負か。
- 売買ルール: universe、rebalance、long / short、weighting、holding period。
- 反証条件: どの IC、分位 spread、coverage、コスト条件なら実装しないか。
- 評価 window: IS、OOS、使用しない holdout。

これにより、BT を見た後に都合よくロジックを説明する後付けを減らす。

### 2. Evidence gate

戦略ファイルを作る前に、最低限の evidence gate を通す。

- data gate: 必要データが対象 universe と期間で十分に存在する。
- sanity gate: proxy の分布、欠損、外れ値、単位、timestamp が説明できる。
- IC gate: 期待符号と一致する IC / hit rate / 分位 spread がある。
- cost gate: turnover、commission、slippage、funding が edge を食い潰していない。
- novelty gate: 既存 strategy と同じ signal source の軽微な variant ではない。

gate を通らない場合は、strategy code を作らず、bad / inconclusive result として記録する。

### 3. 試行回数の記録

偽陽性は試行回数に依存するため、成功候補だけでなく母数を記録する。

- 生成した idea 数
- 実装まで進めた idea 数
- official BT まで進めた idea 数
- 採用候補に残った idea 数
- 同一 signal family 内の variant 数

最良候補を報告するときは、その候補が何本の中から選ばれたかを併記する。

### 4. IS / OOS / holdout

推奨構成は次の通り。

- IS: 仮説検討、feature definition、粗いパラメータ選定に使う。
- OOS: 事前に決めたルールの確認に使う。失敗したら原則として再調整しない。
- Private holdout: 最終確認まで触らない。2026 年以降はこの用途として温存する。

OOS を見て調整した場合、その OOS は次回から IS 扱いに降格する。

### 5. KPI の扱い

Sharpe、MaxDD、total return だけで採否を決めない。最低限、次を併記する。

- `total_trades` と sample span
- Sharpe の不確実性または 95% CI
- window 別の安定性
- total commission / slippage / funding pnl
- turnover の概算
- 既存戦略との相関または signal source の重複

特に短サンプルで高 Sharpe の候補は、採用候補ではなく diagnostic finding として扱う。

### 6. Negative result の保存

失敗した task を消さない。次の情報を残す。

- なぜ弱いと判断したか。
- どの gate で落ちたか。
- 将来再検証するなら何が変わる必要があるか。
- 似た idea を再生成しないためのキーワードや strategy family。

negative result は探索空間の重複を減らし、同じ偽陽性を何度も引く確率を下げる。

## Agent 運用ルール

agent に依頼するときは、次を守る。

- 「良い戦略を大量に作って」ではなく、1 task = 1 hypothesis に分ける。
- parameter sweep を依頼する場合は、探索範囲、試行数、選定基準、holdout 使用禁止を先に指定する。
- BT 結果が良くても、事前 hypothesis と IC evidence が弱いものは採用候補にしない。
- official push / BT は evidence gate を通った候補だけに限定する。
- final report には `良い` / `悪い` / `不明` / `未評価` を明示し、悪い結果も完了扱いで保存する。

## 実装すべき追加改善

現行 framework は、deductive chain、IC gate、2026+ guard、`total_trades >= 2000`、one-strategy-per-task を要求している。ただし、大量 agent 運用で偽陽性をさらに減らすには、次の改善を追加する余地がある。

- task metadata に `hypothesis_id`、`signal_family`、`trial_count`、`variant_count` を持たせる。
- pipeline が official BT 前に evidence gate の記録を必須チェックする。
- `STRATEGY_STATUS.md` に失敗・不明の research result も strategy family 単位で集約する。
- 同一 signal family 内で best-of-N selection が起きた場合、report に母数を強制表示する。
- 既存 strategy との signal correlation / return correlation を自動診断に入れる。
- OOS を見た後の再調整を検出できるよう、task の evaluation window と変更履歴を固定する。

この framework の目的は、探索量を止めることではない。探索量が増えても、偶然の良結果を本物の edge と誤認する確率を下げることである。
