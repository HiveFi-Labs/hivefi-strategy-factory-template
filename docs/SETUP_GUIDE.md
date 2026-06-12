# 導入ガイド（はじめての人向け）

プログラミングやターミナルに慣れていない人でも、このガイドの通りに進めれば
**コピー & ペーストだけで** 戦略開発環境を作れる。所要時間はだいたい 30〜60 分。

すでにエンジニアで、git / Python / venv が分かる人は [README.md](../README.md) の
「Onboarding (〜5 分)」だけで十分。

---

## 0. 用意するもの

| 必要なもの | 説明 |
|---|---|
| パソコン | Mac (macOS 13 以降) または Windows (Windows 10 以降) |
| HiveFi 運営からの認証情報 3 つ | `HIVEFI_API_KEY` / `CLICKHOUSE_USER` / `CLICKHOUSE_PASSWORD`。**運営から個別に受け取る**。まだ持っていない場合は運営に発行を依頼する |
| Claude アカウント | AI agent (Claude Code) を使うために必要。**Pro または Max プラン**に加入する ([claude.com/pricing](https://claude.com/pricing))。無料プランでは Claude Code は使えない |

> **認証情報の注意**: 受け取った 3 つの値はパスワードと同じ扱いをする。
> チャットや SNS に貼らない、スクリーンショットに写さない、人に渡さない。

---

## 1. ターミナル（黒い画面）を開く

これ以降の作業はすべて「ターミナル」に文字を打ち込んで行う。
コマンドはこのガイドからコピーして、ターミナルに貼り付けて Enter を押せばよい。

- **Mac**: `⌘ + スペース` で Spotlight を開き、`ターミナル` と入力して Enter
- **Windows**: スタートボタンを右クリック →「ターミナル」または「Windows PowerShell」を選ぶ
  （以降の Windows 手順はすべて PowerShell 前提。「コマンドプロンプト」ではないので注意）

---

## 2. Git を入れる

Git はこのリポジトリ（プロジェクト一式）を自分の PC にコピーするための道具。

まず、すでに入っているか確認する:

```bash
git --version
```

`git version 2.x.x` のように表示されたら入っている → 手順 3 へ。

**入っていない場合:**

- **Mac**: 上のコマンドを打つと「コマンドライン・デベロッパツールをインストールしますか？」という
  ダイアログが出るので「インストール」を押して待つ（数分かかる）
- **Windows**: [git-scm.com](https://git-scm.com/download/win) からインストーラをダウンロードして実行。
  設定画面がたくさん出るが、**すべてそのまま「Next」でよい**。終わったらターミナルを開き直して
  もう一度 `git --version` で確認

---

## 3. Python を入れる

戦略のコードは Python という言語で動く。**バージョン 3.11 以上**が必要。

確認する:

```bash
python3 --version
```

（Windows では `python --version`）

`Python 3.11.x` 以上が表示されたら → 手順 4 へ。
表示されない、または `3.10` 以下の場合はインストールする:

1. [python.org/downloads](https://www.python.org/downloads/) を開き、黄色いボタンから最新版をダウンロード
2. インストーラを実行
   - **Windows は最初の画面で「Add python.exe to PATH」に必ずチェックを入れる**（これを忘れると後で詰まる）
3. 終わったらターミナルを開き直して、もう一度バージョン確認

> **コマンド名の使い分け**: Mac では常に `python3`、Windows では常に `python` と打つ。
> 手順 6 で venv を有効化した後は、どちらの OS でもどちらの名前でも動く。

---

## 4. このリポジトリを自分の PC にコピーする (git clone)

ターミナルに以下を 1 行ずつ貼り付けて Enter:

```bash
git clone https://github.com/HiveFi-Labs/hivefi-strategy-factory-template.git my-strategies
cd my-strategies
```

1 行目で `my-strategies` というフォルダにプロジェクト一式がコピーされ、
2 行目でそのフォルダの中に移動する。

> **導入は必ずこの git clone で行う。** `pip install hivefi-factory` のような
> インストールはしない（PyPI では配布していない）。

---

## 5. 認証情報を設定する (.env ファイル)

運営から受け取った 3 つの値を `.env` というファイルに書く。

まずテンプレートをコピーする:

**Mac:**
```bash
cp .env.example .env
open -e .env
```

**Windows:**
```powershell
copy .env.example .env
notepad .env
```

テキストエディタが開くので、`HIVEFI_API_KEY=` / `CLICKHOUSE_USER=` / `CLICKHOUSE_PASSWORD=`
の 3 行の `=` の右側を、受け取った値に書き換えて保存して閉じる。
（`xxxx...` のようなダミー文字が入っている場合はそれを消して置き換える。値の前後に
スペースや引用符は付けない。**値は手で打たず、受け取ったものをコピー & ペーストする** —
Mac のテキストエディットは手入力すると引用符などを勝手に変換することがある）

> `.env` は自分の PC の中だけで使われる。git の管理対象から除外済みなので、
> 誤って公開される心配は基本的にないが、ファイル自体を人に送らないこと。

---

## 6. 道具一式をインストールする

以下を 1 行ずつ実行する（2 行目は数分かかることがある）:

**Mac:**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

**Windows:**
```powershell
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

1 行目は「このプロジェクト専用の Python 環境 (venv)」を作って有効化している。
有効化されると、ターミナルの行頭に `(.venv)` と表示される。

> **Windows で「スクリプトの実行が無効」というエラーが出た場合**:
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```
> を実行して `Y` を押し、もう一度 1 行目からやり直す。

---

## 7. 動作確認

4 つのコマンドを順に実行する。それぞれ確認している内容が違う:

```bash
hivefi-factory --version
```
→ `hivefi-factory 0.x.x` と出れば OK（インストール成功）。

```bash
hivefi-factory validate --all
```
→ `[OK]` が並んで `passed` と出れば OK（同梱のサンプル戦略の検査が通った）。

```bash
hivefi-factory health
```
→ エラーなく応答が返れば OK（HiveFi のサーバと通信できた。この確認に API キーは使われない）。

```bash
hivefi-factory strategy list
```
→ エラーなく一覧（最初は空でよい）が返れば OK（**`.env` の API キーが正しい**）。

4 つとも通ったら環境構築は完了。

> ClickHouse の 2 値（`CLICKHOUSE_USER` / `CLICKHOUSE_PASSWORD`）はバックテスト結果の
> 取得時に初めて使われる。後でその種のコマンドがエラーになったら `.env` のこの 2 値を見直す。

---

## 8. AI agent (Claude Code) を入れる

戦略開発は AI agent に日本語で依頼して進めるのが基本。Claude Code をインストールする:

**Mac:**
```bash
curl -fsSL https://claude.ai/install.sh | bash
```

**Windows (PowerShell):**
```powershell
irm https://claude.ai/install.ps1 | iex
```

終わったら**ターミナルを開き直して**、プロジェクトフォルダに戻り、venv を有効化してから起動する:

**Mac:**
```bash
cd my-strategies
source .venv/bin/activate
claude
```

**Windows:**
```powershell
cd my-strategies
.venv\Scripts\Activate.ps1
claude
```

(venv の有効化を忘れると、agent が `hivefi-factory` コマンドを実行できずエラーになる)

初回はブラウザが自動で開いてログイン画面になるので、Claude アカウント
(Pro / Max) でログインする。ブラウザが開かない場合はターミナルに表示される
URL を手動でブラウザに貼る。

> Claude Code を終了したいときは `/exit` と打つか `Ctrl + D`。

---

## 9. 使ってみる

Claude Code の画面で、日本語でそのまま依頼すればよい。例:

```
このプロジェクトで何ができるか、初心者向けに説明して
```

```
20日モメンタム（直近20日で上がった銘柄を買い、下がった銘柄を売る）が
効きそうか調べて
```

agent がデータ取得 → 分析 → レポートまで自動で進めてくれる。
途中でコマンドの実行許可を求められたら、内容を見て `y`（許可）を押す。

戦略開発の流れ（仮説 → 検証 → 実装 → 提出 → バックテスト）の全体像は
[README.md](../README.md) と `CLAUDE.md` に書いてある。agent 自身がそれを
読んで動くので、まずは雑に話しかけて大丈夫。

---

## 10. 2 回目以降の起動手順

PC を再起動した後などは、これだけやればよい:

**Mac:**
```bash
cd my-strategies
source .venv/bin/activate
claude
```

**Windows:**
```powershell
cd my-strategies
.venv\Scripts\Activate.ps1
claude
```

（`my-strategies` を別の場所に置いた場合はそのパスに読み替える）

---

## トラブルシューティング

### `command not found: hivefi-factory`
venv が有効化されていない。行頭に `(.venv)` が無いはず。
手順 10 の 1〜2 行目（`cd` と `activate`）をやり直す。

### `command not found: claude`（Mac）
インストール先がまだ認識されていない。以下を実行してターミナルを開き直す:
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

### Windows で `claude` が見つからない
インストール直後はターミナルが古い状態のまま。**ターミナルを一度閉じて開き直す**。

### `hivefi-factory health` がエラーになる
`.env` の値が間違っている可能性が高い。手順 5 を見直す
（余計なスペース・引用符・改行が入っていないか、3 つの値を取り違えていないか）。
直らなければ運営に「health が通らない」と伝えてエラーメッセージを見せる。

### `zsh: no matches found: .[dev]`（Mac）
`pip install -e .[dev]` を引用符なしで打った場合に出る。
このガイドの通り `pip install -e ".[dev]"` と**引用符付き**で実行する。

### `git clone` で `destination path 'my-strategies' already exists` と出る
同名のフォルダが既にある（コマンドを 2 回打った場合など）。すでに手順 4 を
終えているなら clone は不要なので `cd my-strategies` だけ実行して次へ進む。
壊れた状態でやり直したい場合は、フォルダを削除するか別の名前
（`my-strategies-2` など）で clone し直す。

### Python のインストール後もバージョンが古いまま
ターミナルを開き直す。Windows の場合は「Add python.exe to PATH」の
チェックを忘れた可能性があるので、インストーラをもう一度実行して
「Modify」から修正するか、入れ直す。

### それでも詰まったら
Claude Code が動く状態であれば、**エラーメッセージをそのまま Claude Code に
貼り付けて「これを直して」と頼む**のが一番早い。動かない場合は、ターミナルの
表示をコピーして運営や周りの経験者に相談する。

---

## 用語ミニ辞典

| 用語 | 意味 |
|---|---|
| ターミナル | 文字でパソコンに命令する画面。コマンドを貼り付けて Enter で実行する |
| コマンド | ターミナルに打ち込む命令文 |
| リポジトリ | プロジェクトのファイル一式。GitHub 上に置いてあるものを git clone で PC にコピーする |
| venv | このプロジェクト専用の Python 環境。他のソフトと干渉しないための仕切り |
| `.env` | 認証情報を書いておく自分専用の設定ファイル。公開されない |
| API キー (`HIVEFI_API_KEY`) | 戦略の提出・一覧などに使う、パスワードのような文字列 |
| `CLICKHOUSE_USER` / `PASSWORD` | バックテスト結果やデータを読むための、もう 1 組の認証情報。API キーとは別物なので 3 つ全部必要 |
| agent / Claude Code | 日本語の依頼を理解してコマンド実行やコード作成を代行してくれる AI |
| バックテスト (BT) | 戦略を過去の相場データで動かして成績を測ること |
