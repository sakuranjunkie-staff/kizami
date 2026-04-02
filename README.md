# kizami（刻）

**Claude Code にセッション間の時間経過を意識させ、関連メモリを注入するツール**  
**A time-awareness and memory injection tool for Claude Code**

[sui-memory](https://github.com/sakuranjunkie-staff/sui-memory) と組み合わせて使う姉妹ツールです。  
Companion tool to [sui-memory](https://github.com/sakuranjunkie-staff/sui-memory). Use both together.

---

## 概要 / Overview

### 日本語

`kizami`（刻）は、Claude Code のプロンプト入力時（UserPromptSubmitHook）に以下の3つの情報を自動的に Claude の文脈に注入します。

1. **時間サマリー** — 今日の日付・前回セッションからの経過日数・総セッション数・蓄積メモリ数
2. **リマインド** — 前回から3日以上経過していた場合の注意喚起（積み残しタスクの確認を促す）
3. **関連メモリ** — ユーザーの入力プロンプトに意味的に近い過去の会話（直近7日以内、最大5件）

これにより Claude は「前回から何日経ったか」「前回どんな話をしていたか」を毎回のプロンプト入力時に把握できます。

**何の役に立つのか？**
- 何日ぶりかのセッションでも Claude がすぐ文脈を取り戻せる
- 積み残しタスクを Claude 自身が気づいて報告できるようになる
- 過去の設計判断と現在の方針が矛盾していないか自動チェックできる
- 会話の繰り返しを減らし、作業効率が上がる

### English

`kizami` (meaning "to notch / to mark time") automatically injects three types of information into Claude's context on every prompt submission (UserPromptSubmitHook):

1. **Time summary** — today's date, days elapsed since last session, total session count, total memory count
2. **Reminder** — a nudge to check pending tasks if 3+ days have passed since the last session
3. **Relevant memories** — past conversations semantically similar to the current prompt (last 7 days, up to 5 results)

This gives Claude awareness of "how many days since last time" and "what were we working on" at the start of every prompt.

**What is it good for?**
- Claude immediately recovers context even after days away
- Claude can proactively report unfinished tasks
- Automatically checks for contradictions between past design decisions and current direction
- Reduces repetitive re-explaining, improving work efficiency

---

## 仕組み / How It Works

```
ユーザーがプロンプトを入力
    ↓ UserPromptSubmitHook が起動
    ↓
1. sui-memory DB から時間サマリーを生成 (timeline.py)
   - 今日の日付・曜日・時刻
   - 前回セッション日時と経過日数
   - 総セッション数・蓄積メモリ数

2. 経過日数に応じたリマインドを生成 (reminder.py)
   - 0〜2日: リマインドなし
   - 3〜6日: 「3日以上経過」注意
   - 7〜13日: 「1週間以上経過」注意
   - 14日以上: 「2週間以上経過」強い注意

3. ユーザーのプロンプトで関連メモリを検索 (retriever.py via sui-memory)
   - FTS5 キーワード検索 + ベクトル検索（Ruri v3-310m）
   - RRF スコア統合 + 時間減衰
   - 直近7日以内のメモリを最大5件取得

    ↓
これらを結合してシステムプロンプトに注入
Claude はこの情報を文脈として受け取る
```

---

## ファイル構成 / File Structure

```
kizami/
├── src/
│   ├── injector.py     # UserPromptSubmitHook エントリーポイント
│   ├── timeline.py     # 時間サマリー生成
│   └── reminder.py     # 経過日数リマインド生成
└── tests/
```

---

## 必要環境 / Requirements

- Python 3.10+
- [uv](https://github.com/astral-sh/uv)
- Claude Code
- [sui-memory](https://github.com/sakuranjunkie-staff/sui-memory)（セットで使用 / use together）

---

## インストール / Installation

```bash
git clone https://github.com/sakuranjunkie-staff/kizami.git
cd kizami
uv sync
```

**sui-memory も別途インストールしてください。**  
**Also install sui-memory separately.**

```bash
git clone https://github.com/sakuranjunkie-staff/sui-memory.git
```

---

## 設定 / Setup

`~/.claude/settings.json` に UserPromptSubmitHook を追加します。  
Add the UserPromptSubmitHook to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "uv run --project /path/to/sui-memory python /path/to/kizami/src/injector.py"
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "uv run --project /path/to/sui-memory python /path/to/sui-memory/src/hook.py"
          }
        ]
      }
    ]
  }
}
```

`/path/to/sui-memory` と `/path/to/kizami` を実際のパスに置き換えてください。  
Replace `/path/to/sui-memory` and `/path/to/kizami` with actual paths.

> **注意**: `injector.py` は sui-memory の uv 環境で動かします（`sentence-transformers` 等が必要なため）。  
> **Note**: `injector.py` runs inside sui-memory's uv environment (requires `sentence-transformers` etc.).

---

## 出力サンプル / Output Sample

Claude の文脈に以下のような内容が毎回注入されます。  
The following is injected into Claude's context on every prompt:

```
## 時間サマリー
今日: 2026-04-02（木曜日）14:00 JST
前回のセッション: 2026-03-30（3日前）
総セッション数: 18件
蓄積メモリ数: 21件

## ⚠️ 3日以上経過しています
積み残しタスクがあれば確認してください。

## 関連メモリ（直近7日）
（注: これはkizami+sui-memoryが自動取得した過去の会話の断片です）

### 1. 2026-03-30
**あなた**: Supabase への移行どこから始める？
**Claude**: まず DATABASE_URL を変更して prisma migrate deploy を実行します。

### 2. 2026-03-29
**あなた**: NeonからSupabaseへの移行理由は？
**Claude**: 東京リージョンのレイテンシ改善と接続安定性のためです。
```

---

## sui-memory との関係 / Relationship with sui-memory

| ツール | 役割 |
|---|---|
| **[sui-memory](https://github.com/sakuranjunkie-staff/sui-memory)** | 会話の**保存**（StopHook）/ Saves conversations |
| **kizami** | 時間経過の把握 + 関連メモリの**注入**（UserPromptSubmitHook）/ Time awareness + injection |

`kizami` は `sui-memory` の `retriever.py` を直接 import して使用します。  
`kizami` directly imports `retriever.py` from `sui-memory`.

両ツールは `~/.sui-memory/memory.db` を共有します。  
Both tools share `~/.sui-memory/memory.db`.

---

## 動作環境 / Platform Support

| OS | 状態 |
|---|---|
| Windows | ✅ 動作確認済み |
| macOS | 🔧 対応予定 |
| Linux | 🔧 対応予定 |

---

## ライセンス / License

MIT
