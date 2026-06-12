# Symphony Strategy Result Format

local task への報告は `## 結果` 1 コメントだけにする。ただし「短い」とは
情報を削ることではない。operator が次の確認をできるように、研究目的、
シグナル設計、分析結果、補正後の統計的証拠を分けて書く。運用判断や
portfolio 判断、運用可否ラベルは書かない。

途中で止まった場合も同じ形式で、どの前提が足りず何を確認すべきかを書く。

## コメント作成

Symphony の `tracker_comment` tool を使う。

```json
{"issueId":"{{ issue.id }}","body":"<markdown result>"}
```

`issueId` は workflow prompt の `{{ issue.id }}` を使う。完了時は
`tracker_update_state` で `Done` へ移動する。`## 結果` を投稿する前に
task を `Done` にしない。

## 形式

```markdown
## 結果

結果: `<strategy_id>`

結論:
何を実施し、実装したのか、実装せず分析で止めたのか、評価のみ行ったのかを
2-3 文で書く。運用判断は書かず、operator が次に何を確認すべきかを書く。

研究目的:
どの市場効果を試したか。例: 「短期の過熱を追う」のか、
「大きく崩れた銘柄を避ける」のか、「BTC から独立した相対強度を見る」のか。

演繹設計:
市場メカニズム、観測 proxy、期待符号、売買ルール、反証条件を書く。
例: `過熱後に反転する -> 5日急騰率を proxy にする -> 高いほど将来 return は低い -> 上位を short / 下位を long -> IC と Q spread が正なら仮説は弱い`。

仮説と仕組み:
なぜ効く想定かを専門語だけで終わらせず説明する。
専門語を使う場合は直後に短く言い換える。

シグナル設計:
入力データ、計算式、ランキング方法、long / short の選び方、rebalance を書く。
例: `40d return / 40d volatility` を銘柄横断で rank し、上位 5 を long、下位 5 を short。

検証設計:
利用データ、期間、評価頻度、forward return horizon、除外条件、skip した検証を書く。
BT / report は `2025-12-31` までの利用可能な最長期間にしたか明記する。

偽陽性コントロール:
この task が単独仮説か、探索 / variant 選択を含むかを書く。探索を含む場合は、
確認した idea 数、実装数、BT 数、同一 signal family 内の variant 数、best-of-N
selection の有無、検定ファミリー、補正方法を分かる範囲で書く。

分析結果:
target_evidence=...
IC: mean=..., R2_mean=..., t=..., p_value=..., q_value=..., hit_rate=..., Q5-Q1=..., sample=...
この数字が何を示すか、補正後有意性があるか、方向が事前仮説と整合するかを
1-2 文で説明する。`q_value` は Benjamini-Hochberg FDR 補正後の値を基本にする。
古い結果の読み直しで evidence analysis を rerun していない場合は、`q未記録`
として扱い、補正後有意性を後付けで作らない。
必要データが足りず target evidence を作れない場合は、
`data_request=tools/symphony/data_requests/<id>.md` と、その要望が満たすべき
dataset / fields / coverage を書く。

BT / pipeline:
run/skip/blocked と katsustats `report.html` path だけを書く。
BT の詳細数値はここに再掲しない。report 期間が `2025-12-31` で終わっているか、
trades < 2000 の場合だけ diagnostic と明記する。

成果物:
この task が残した最小成果物だけを書く。
新規 / rework なら `configs/<id>.json`, `extensions/<id>.py`。
evidence gate を通さず実装しなかった場合は `なし (evidence gate で実装せず)` と書く。
eval-only なら `なし (既存ファイルを評価)` でもよい。
不足データが原因で止めた場合は、作成済みの `tools/symphony/data_requests/<id>.md`
だけを書く。

次:
次に確認すべき相関、重複、リスク、または再検証案を書く。
```

## 書き方ルール

- 1行目は `結果: <strategy_id>` で始める。運用判断を表す status 語は使わない。
- `結論` は結果であり、`研究目的` / `仮説と仕組み` は事前計画の説明。
  計画と結果を同じ行に混ぜない。
- 新規 strategy の `演繹設計` は必須。データを見てから選んだ parameter の説明ではなく、
  事前に成立している market mechanism から signal を導く。
- `仮説と仕組み` は、専門語だけで終わらせない。
  不十分な例: `40日 fractional-differentiated momentum を volatility-adjust する。`
  改善例: `40日分の価格変化から、古い値動きも少し残した momentum を作る。
  そのままだと荒い銘柄が上位に来やすいので、40日 volatility で割って
  「値動きの大きさに対してどれだけ一貫して上がったか」に直す。`
- `シグナル設計` は、誰を long / short するかが再実装できる粒度で書く。
- `偽陽性コントロール:` は必須。単独仮説なら `単独仮説。variant sweep なし。`
  のように明記し、大量生成や parameter sweep を含む場合は母数を隠さない。
- 探索や複数 variant を含む場合は、`p_value` だけで次工程に進めない。
  検定ファミリー内で Benjamini-Hochberg FDR などの補正を行い、`q_value` と
  family size を書く。
- 既存 comment に `q_value` が無いだけの場合は、再分析していない限り
  `q未記録` のままでよい。`t` や `IC mean` だけから補正後有意性を捏造しない。
- IC / 分位分析を省略した場合は、`分析結果:` に理由を書く。
- 必要データが repo / ClickHouse にない、または history が足りず仮説を観測できない
  場合は、`hivefi-factory data request` で要望を作り、`分析結果:` と `成果物:` に
  request path を書く。
- `分析結果:` は evidence の見え方を書く場所であり、運用判断を書く場所ではない。
- submit / BT を実行しない場合は、`BT / pipeline:` に skip 理由を書く。
- BT を実行した場合、`BT / pipeline:` は `report.html` への path だけでよい。
  SR / MaxDD / total_return / run_id の羅列は report 側に任せる。
- BT report は `2025-12-31` で終え、そこまでの利用可能な最長期間を使う。
- `成果物:` には task の必須出力だけを書く。BT report は `BT / pipeline:` に書き、
  `成果物:` に重複させない。
- `成果物:` に `configs/<id>.json` / `extensions/<id>.py` を書く前に、実ファイルが
  workspace に存在することを確認する。存在しない file path を成果物として書かない。
- target evidence が作れない、補正後有意性がない、sample が少ない、または方向が
  事前仮説と整合しないため implementation gate を通さなかった task は、code file を
  作らずに `Done` でよい。その場合、`成果物:` は `なし (evidence gate で実装せず)`
  と書く。
- 追加 CSV / JSON / summary は、最終判断の再現に必要な場合だけ作り、その path を
  該当する分析欄に 1 つだけ書く。
- 長い stderr、全銘柄表、rolling window の詳細は artifact に寄せる。
- Markdown table は使わない。短い見出しと段落で書く。
- `Strategy IDs created`, `Changed files`, `Local verification results` などの
  重複英語セクションは追加しない。
- `## 工程レポート` 見出しは使わない。
