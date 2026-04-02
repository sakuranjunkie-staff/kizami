"""
timeline.py - sui-memoryのDBから時間サマリーを生成する
"""

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

# JSTタイムゾーン定義
JST = timezone(timedelta(hours=9))

# 曜日の日本語マッピング
WEEKDAY_JA = ["月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日"]

# sui-memoryのDBパス
DB_PATH = Path.home() / ".sui-memory" / "memory.db"


def get_timeline_summary(db_path: Path = DB_PATH) -> str:
    """
    sui-memoryのDBから時間サマリーを生成して返す。

    DBが存在しない場合は空文字を返す。
    DBにデータが1件もない場合は「メモリなし」と表示する。
    """
    # DBが存在しない場合は空文字を返す
    if not db_path.exists():
        return ""

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 今日の日付と現在時刻をJSTで取得
        today = datetime.now(JST)
        today_str = today.strftime("%Y-%m-%d")
        today_time_str = today.strftime("%H:%M")
        weekday_str = WEEKDAY_JA[today.weekday()]

        # 総メモリ数と最終セッション日時、総セッション数を一括取得
        cursor.execute("""
            SELECT
                COUNT(*) as total_memories,
                MAX(created_at) as last_session_at,
                COUNT(DISTINCT session_id) as total_sessions
            FROM memories
        """)
        row = cursor.fetchone()
        conn.close()

        total_memories = row[0] if row else 0
        last_session_at_raw = row[1] if row else None
        total_sessions = row[2] if row else 0

        # データが1件もない場合
        if total_memories == 0:
            return (
                f"## 時間サマリー\n"
                f"今日: {today_str}（{weekday_str}）{today_time_str} JST\n"
                f"前回のセッション: メモリなし\n"
                f"総セッション数: 0件\n"
                f"蓄積メモリ数: 0件"
            )

        # 最終セッション日時をパースしてJSTに変換
        last_session_dt = _parse_datetime(last_session_at_raw)
        last_session_date = last_session_dt.astimezone(JST).date()
        last_session_str = last_session_date.strftime("%Y-%m-%d")

        # 経過日数を計算（今日との差）
        days_ago = (today.date() - last_session_date).days
        days_ago_str = f"{days_ago}日前" if days_ago > 0 else "今日"

        return (
            f"## 時間サマリー\n"
            f"今日: {today_str}（{weekday_str}）{today_time_str} JST\n"
            f"前回のセッション: {last_session_str}（{days_ago_str}）\n"
            f"総セッション数: {total_sessions}件\n"
            f"蓄積メモリ数: {total_memories}件"
        )

    except sqlite3.Error:
        # DBのスキーマが異なるなどのエラー時は空文字を返す
        return ""


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
