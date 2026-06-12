---
name: empirical-prompt-tuning
description: |
  skill / workflow / task prompt / CLAUDE.md / AGENTS.md / Symphony workflow などの
  agent 向け指示を、実行者視点のシナリオ評価で改善する。静的整合チェック、
  評価シナリオ、要件チェックリスト、実行結果、自己申告の不明瞭点を使い、
  指示の曖昧さが収束するまで小さく反復する。
trigger:
  - user が "プロンプトを実測評価" "skill を改善" "workflow をチューニング" と言う
  - 新規または大幅改訂した skill / workflow / prompt を実運用前に検証したいとき
  - agent の挙動不良を、指示側の曖昧さとして切り分けたいとき
---

# Empirical Prompt Tuning

agent 向け指示は、書いた本人には明瞭に見えやすい。実行者がどこで迷うかを
シナリオで測り、曖昧さを小さく潰す。

この skill は、mizchi 氏の
`empirical-prompt-tuning` skill の考え方を、この repo 用に短く再実装したもの。
出典:
https://github.com/mizchi/chezmoi-dotfiles/blob/main/dot_claude/skills/empirical-prompt-tuning/SKILL.md

元 repository に明示 LICENSE が見つからなかったため、本文は verbatim copy しない。

## 使う場面

- `WORKFLOW.md`, `AGENTS.md`, `CLAUDE.md`, skill, automation prompt を新規作成または大幅改訂した直後
- Symphony / Codex / Claude の挙動が期待とズレており、指示の曖昧さを疑うとき
- 重要な運用プロンプトを、実運用前に壊れにくくしたいとき

使わない場面:

- 1 回限りの使い捨てプロンプト
- 成功率ではなく、文体や主観的好みだけを調整したいとき
- subagent 実行が必要なのに、ユーザーが subagent / delegation / parallel work を明示していないとき

## Codex での制約

Codex では subagent / parallel work は、ユーザーが明示した場合だけ使う。

- 明示あり: 独立した実行者として subagent を使ってよい
- 明示なし: まず構造審査モードで静的レビューする。実測評価が必要なら、subagent 実行の許可をユーザーに求める
- 自己再読を empirical evaluation として扱わない

## Workflow

### 0. 静的整合チェック

対象指示を読んで、frontmatter / description / trigger / body / 出力形式が一致しているか見る。

確認項目:

- description が謳う用途を body が実際にカバーしているか
- trigger と使わない場面が矛盾していないか
- 必須成果物、禁止事項、完了条件が同じ粒度で書かれているか
- 参照先ファイルに逃がした情報が、実行時に読まれるよう明示されているか

ここでズレがあれば、実測評価前に直す。

### 1. 評価シナリオを固定する

2-3 個のシナリオを作る。

- 中央値シナリオ: 普通に期待される使い方
- edge シナリオ: 省略、失敗、例外、既存変更あり、権限不足など
- 回帰シナリオ: 直近で実際に失敗したケース

各シナリオに、3-7 個の要件チェックリストを付ける。
最低 1 個は `[critical]` にする。

例:

```markdown
## シナリオ
Symphony workflow を 1 issue だけで検証する。

## 要件チェックリスト
1. [critical] 全 issue を Rework に戻さない
2. [critical] Linear issue に工程別コメントを残す
3. HiveFi data access がない場合は実装へ進まない
4. 最終ハンドオフ前に Done にしない
```

### 2. 実行者を分離する

ユーザーが subagent 実行を明示している場合だけ、白紙の実行者に渡す。

subagent に渡す入力:

```markdown
あなたは <対象指示名> を初めて読む実行者です。

## 対象指示
<全文、または読むべきファイルパス>

## シナリオ
<実行状況>

## 要件チェックリスト
1. [critical] ...
2. ...

## タスク
1. 対象指示に従ってシナリオを実行する。
2. 最後に次の形式で報告する。

## レポート
- 成果物:
- 要件達成: ○ / × / 部分的。各項目に理由
- 不明瞭点:
- 裁量補完:
- 再試行:
- 追加で読んだ参照:
```

### 3. 両面評価

実行結果から、指示側と実行者側の両方を見る。

| 軸 | 取り方 | 意味 |
| --- | --- | --- |
| 成功 / 失敗 | `[critical]` が全て ○ なら成功 | 最低ライン |
| 精度 | ○=1, 部分的=0.5, ×=0 で平均 | 部分成功の度合い |
| 不明瞭点 | 実行者の自己申告 | 直すべき文言 |
| 裁量補完 | 実行者の自己申告 | 暗黙仕様 |
| 追加参照 | 実行者の自己申告 | 指示の自己完結性 |
| 再試行 | 実行者の自己申告 | 判断の迷い |
| tool uses / 時間 | 取得できる場合だけ | 補助指標 |

成功判定:

- `[critical]` が 1 つでも × または部分的なら失敗
- `[critical]` なしの checklist は無効
- 後から checklist を変えない

### 4. 最小修正を入れる

1 iteration で直すテーマは 1 つに絞る。

修正前に必ず書く:

- どの不明瞭点を直すか
- checklist のどの項目に効くか
- 何を変更しないか

よく効く修正:

- 完了条件を明示する
- skip / blocked の扱いを明示する
- 出力形式を 1 箇所に集約する
- 参照ファイルと source of truth を 1 つにする
- 「いつ止まるか」を書く

### 5. 再評価する

同じシナリオを新しい実行者で再評価する。
前回の実行者は、改善意図を学習しているため再利用しない。

停止条件:

- 連続 2 iteration で新規の不明瞭点がない
- `[critical]` が全て ○
- 精度改善が小さい
- 追加参照や裁量補完が増えていない

重要な workflow では連続 3 iteration を目安にする。

## 構造審査モード

subagent 実行ができない、またはユーザーが明示していない場合は、構造審査だけ行う。

出力形式:

```markdown
## 構造審査
| 項目 | 判定 | 根拠 | 修正案 |
| --- | --- | --- | --- |
| description / body 整合 | OK / NG | file:line | ... |
| trigger / 対象外 | OK / NG | file:line | ... |
| 完了条件 | OK / NG | file:line | ... |
| 出力形式 | OK / NG | file:line | ... |
| source of truth | OK / NG | file:line | ... |

## 優先修正
1. ...
2. ...
```

## 最終報告

```markdown
## 対象
- prompt / skill:
- file:

## 評価結果
| iteration | scenario | success | accuracy | unclear points | discretion | action |
| --- | --- | --- | --- | --- | --- | --- |

## 変更
- ...

## 残リスク
- ...

## 次回
- 再評価不要 / 次の scenario で再評価 / subagent 実測が必要
```
