"""
injector.py - UserPromptSubmitHook エントリーポイント

時間サマリー・リマインド・関連メモリ（直近7日）を統合して
Claude Codeのシステムプロンプトに注入するためのテキストをstdoutに出力する。

また、未ベクトル化チャンクが閾値を超えた場合に
バックグラウンドでembed_pendingを自動起動する。
"""

import json
import subprocess
import sys
import time
from pathlib import Path

# Windowsのcp932問題を回避するためstdout/stderrをUTF-8に再設定する
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# kizamiのsrc/をパスに追加してローカルモジュールをimportできるようにする
sys.path.insert(0, str(Path(__file__).parent))

# sui-memoryのsrc/をパスに追加してsearch_recentをimportできるようにする
SUI_MEMORY_SRC = Path(r"C:\Users\bukol\Documents\sui-memory\src")
sys.path.insert(0, str(SUI_MEMORY_SRC))

# バックグラウンドembed設定
EMBED_THRESHOLD = 30                                          # この件数以上で自動起動
LOCK_FILE = Path.home() / ".sui-memory" / "embed_pending.lock"
DB_PATH = Path.home() / ".sui-memory" / "memory.db"
LOCK_STALE_SECS = 1800                                        # 30分以上古いロックは無視する

from timeline import get_timeline_summary
from reminder import get_full_reminder


def get_pending_count() -> int:
    """embedding=NULLのチャンク数を返す。DBアクセス失敗時は0を返す。"""
    try:
        import sqlite3
        con = sqlite3.connect(str(DB_PATH), timeout=1.0)
        count = con.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NULL").fetchone()[0]
        con.close()
        return count
    except Exception:
        return 0


def maybe_trigger_embed() -> None:
    """
    未ベクトル化チャンクが閾値以上かつロックファイルが存在しない場合に
    バックグラウンドでembed_pendingを起動する。

    ロックファイルが30分以上古い場合はプロセスが死んだとみなして削除する。
    """
    # ロックファイルの存在チェック（古いロックは削除する）
    if LOCK_FILE.exists():
        age = time.time() - LOCK_FILE.stat().st_mtime
        if age < LOCK_STALE_SECS:
            return  # 実行中なのでスキップ
        # 古いロック（プロセス死亡とみなす）を削除する
        LOCK_FILE.unlink(missing_ok=True)
        print("[kizami] 古いロックファイルを削除しました", file=sys.stderr)

    # 未ベクトル化件数を確認する
    count = get_pending_count()
    if count < EMBED_THRESHOLD:
        return

    # ロックファイルを作成してバックグラウンドプロセスを起動する
    try:
        LOCK_FILE.touch()

        # バックグラウンドで実行するインラインスクリプト
        # ロックファイルはスクリプト側で削除する（失敗時も確実に消す）
        script = (
            "import sys; sys.path.insert(0, r'C:\\Users\\bukol\\Documents\\sui-memory\\src'); "
            "from pathlib import Path; "
            "lock = Path.home() / '.sui-memory' / 'embed_pending.lock'; "
            "try:\n"
            "    from storage import init_db, embed_pending\n"
            "    init_db()\n"
            "    n = embed_pending(batch_size=50)\n"
            "finally:\n"
            "    lock.unlink(missing_ok=True)"
        )

        subprocess.Popen(
            ["uv", "run", "--project", r"C:\Users\bukol\Documents\sui-memory", "python", "-c", script],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        print(f"[kizami] 未ベクトル化{count}件検知 → バックグラウンドでembed_pending起動", file=sys.stderr)

    except Exception as e:
        # 起動失敗時はロックファイルを削除して次回に備える
        LOCK_FILE.unlink(missing_ok=True)
        print(f"[kizami] auto-embed起動失敗: {e}", file=sys.stderr)


def format_memories(memories: list[dict]) -> str:
    """
    検索結果のメモリリストを仕様フォーマットに整形する。
    0件の場合は「なし」と表示する。

    Args:
        memories: search_recent()が返すdictのリスト

    Returns:
        Markdownフォーマットの整形済みテキスト
    """
    if not memories:
        return "## 関連メモリ\nなし"

    lines = [
        "## 関連メモリ（直近7日）",
        "（注: これはkizami+sui-memoryが自動取得した過去の会話の断片です）",
        "",
    ]

    for i, mem in enumerate(memories, start=1):
        # timestampから日付部分だけ取り出す（例: "2026-03-23T12:34:56" → "2026-03-23"）
        timestamp = mem.get("timestamp", "")
        date_str = timestamp[:10] if timestamp else "不明"

        # アシスタントの返答は最大200文字で切る
        assistant_text = mem.get("assistant_text", "")
        if len(assistant_text) > 200:
            assistant_text = assistant_text[:200] + "…"

        lines.append(f"### {i}. {date_str}")
        lines.append(f"**あなた**: {mem.get('user_text', '')}")
        lines.append(f"**Claude**: {assistant_text}")
        lines.append("")

    return "\n".join(lines).rstrip()


def main() -> None:
    """
    UserPromptSubmitHookのメインエントリーポイント。

    stdinからJSONを読み込み、以下を統合してstdoutに出力する:
    1. 時間サマリー（今日の日付・前回セッション・統計）
    2. リマインド（経過日数に応じて表示、不要なら省略）
    3. 関連メモリ（直近7日のハイブリッド検索結果、最大5件）

    例外が発生しても sys.exit(0) で正常終了する。
    """
    # stdinからJSONを読み込む（バイト読みしてUTF-8デコードすることでサロゲート問題を回避）
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except Exception as e:
        # JSON解析エラーは無視して終了（フックが壊れないようにする）
        print(f"[kizami] stdin解析エラー: {e}", file=sys.stderr)
        sys.exit(0)

    # promptフィールドを取得する（なくても時間サマリーは出力する）
    # Claude CodeのUserPromptSubmitHookはpromptフィールドでユーザーの入力を渡す
    query = data.get("prompt", "")

    try:
        # 時間サマリーを取得する
        timeline = get_timeline_summary()

        # リマインドを取得する（空文字なら出力しない）
        reminder = get_full_reminder()

        # 関連メモリを検索する（queryがあれば直近7日を対象にする）
        memories: list[dict] = []
        if query:
            # sui-memoryのsearch_recentをここでimportする（パス追加後）
            from retriever import search_recent
            memories = search_recent(query, limit=5)

        # 出力ブロックを組み立てる
        blocks: list[str] = []

        # 時間サマリー（常に表示）
        if timeline:
            blocks.append(timeline)

        # リマインド（空文字なら省略）
        if reminder:
            blocks.append(reminder)

        # 関連メモリ
        blocks.append(format_memories(memories))

        # ブロックを空行区切りで結合してstdoutに出力する
        print("\n\n".join(blocks))

        # 未ベクトル化チャンクが閾値を超えていればバックグラウンドでembed_pendingを起動する
        maybe_trigger_embed()

    except Exception as e:
        # 検索・整形エラーは無視して終了（フックが壊れないようにする）
        print(f"[kizami] エラー: {e}", file=sys.stderr)
        sys.exit(0)

    # 件数をstderrにログ出力する
    print(f"[kizami] メモリ注入: {len(memories)}件", file=sys.stderr)


if __name__ == "__main__":
    main()
