# CLAUDE.md - kizami プロジェクト規約

## プロジェクト概要
- **名前:** kizami（刻）
- **目的:** Claude Codeにセッション間の時間経過を意識させるツール
- **連携:** sui-memoryと組み合わせて使う
- **言語:** Python
- **公開予定:** GitHub（MITライセンス）

## 絶対ルール
1. git pushを勝手に実行するな
2. 指示にない機能を勝手に追加するな
3. コードには必ず日本語コメントを書け
4. PayBalanceのコードには触れるな

## sui-memoryとの関係
- sui-memory: セッションの内容を記憶する
- kizami: セッション間の時間経過を意識させる
- 両者はinjector.pyで統合される

## 技術スタック
- Python（uvで管理）
- SQLite（sui-memoryのmemory.dbを参照）
- Claude Code Hooks（UserPromptSubmitHook）

## ディレクトリ構成
src/
├── timeline.py      # 時間サマリー生成
├── reminder.py      # 経過時間トリガー（案B）
└── injector.py      # UserPromptSubmitHookエントリーポイント
tests/

## 作業ログ
- 2026/3/24: プロジェクト開始
