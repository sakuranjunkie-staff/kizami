"""
reminder.py - 前回セッションからの経過日数に応じてリマインドメッセージを生成する
"""

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

# JSTタイムゾーン定義
JST = timezone(timedelta(hours=9))

# sui-memoryのDBパス
DB_PATH = Path.home() / ".sui-memory" / "memory.db"


def get_reminder(days_since_last: int) -> str:
    """
    経過日数に応じてリマインドメッセージを返す。

    0〜2日: 空文字（リマインド不要）
    3〜6日: 3日以上経過メッセージ
    7〜13日: 1週間以上経過メッセージ
    14日以上: 2週間以上経過メッセージ
    """
    if days_since_last < 3:
        # 当日または1〜2日以内はリマインド不要
        return ""
    elif days_since_last < 7:
        # 3〜6日経過
        return "## ⚠️ 3日以上経過しています\n積み残しタスクがあれば確認してください。"
    elif days_since_last < 14:
        # 7〜13日経過
        return "## ⚠️ 1週間以上経過しています\n前回の作業内容を確認してから始めることを推奨します。"
    else:
        # 14日以上経過
        return "## ⚠️ 2週間以上経過しています\n大きく状況が変わっている可能性があります。CLAUDE.mdを読み直してください。"


def get_days_since_last(db_path: Path = DB_PATH) -> int:
    """
    sui-memoryのDBから前回セッションの経過日数を取得して返す。

    DBが存在しない・データがない場合は -1 を返す。
    """
    # DBファイルが存在しない場合は -1 を返す
    if not db_path.exists():
        return -1

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # created_atの最大値（最終セッション日時）を取得
        cursor.execute("SELECT MAX(created_at) FROM memories")
        row = cursor.fetchone()
        conn.close()

        # データが1件もない場合は -1 を返す
        if row is None or row[0] is None:
            return -1

        last_session_dt = _parse_datetime(row[0])
        today = datetime.now(JST).date()
        last_date = last_session_dt.astimezone(JST).date()

        return (today - last_date).days

    except sqlite3.Error:
        # DBのスキーマ不一致などのエラー時は -1 を返す
        return -1


def get_full_reminder(db_path: Path = DB_PATH) -> str:
    """
    get_days_since_last() を呼び出して get_reminder() の結果を返す。

    空文字の場合はそのまま空文字を返す。
    """
    days = get_days_since_last(db_path)

    # DB不在・データなしの場合はリマインド不要
    if days == -1:
        return ""

    return get_reminder(days)


def _parse_datetime(value) -> datetime:
    """
    SQLiteのcreated_atをdatetimeに変換する。

    sui-memoryの実DBはREAL（Unix timestamp float）で保存する。
    テスト用DBはTEXT（ISO形式文字列）で保存するケースもある。
    両方に対応する。
    """
    # float/int → Unix timestampとして変換する
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)

    # 文字列 → ISO形式をパースする
    if value.endswith("Z"):
        # UTC明示のZ形式
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif "+" in value or value.count("-") > 2:
        # タイムゾーン情報あり
        return datetime.fromisoformat(value)
    else:
        # タイムゾーン情報なし → UTCとして扱う
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
