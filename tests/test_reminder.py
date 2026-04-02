"""
test_reminder.py - reminder.pyのテスト
"""

import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.reminder import get_reminder, get_days_since_last, get_full_reminder

JST = timezone(timedelta(hours=9))


def make_db(path: Path, created_at: str) -> None:
    """テスト用のmemory.dbを1件だけ作成する。"""
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL,
            content TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO memories (session_id, content, created_at) VALUES (?, ?, ?)",
        ("session-A", "test", created_at),
    )
    conn.commit()
    conn.close()


def make_empty_db(path: Path) -> None:
    """テスト用の空のmemory.dbを作成する。"""
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL,
            content TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def utc_str_days_ago(days: int) -> str:
    """今日のJST 10:00を基準に、N日前のUTC ISO文字列を返す。"""
    # JSTの今日10:00をUTCに変換してN日引く
    base = datetime.now(JST).replace(hour=10, minute=0, second=0, microsecond=0)
    target = base - timedelta(days=days)
    return target.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── get_reminder() ────────────────────────────────────────────────

def test_reminder_0_days_empty():
    """0日（当日）は空文字を返すこと。"""
    assert get_reminder(0) == ""


def test_reminder_1_day_empty():
    """1日経過は空文字を返すこと。"""
    assert get_reminder(1) == ""


def test_reminder_2_days_empty():
    """2日経過は空文字を返すこと。"""
    assert get_reminder(2) == ""


def test_reminder_3_days():
    """3日経過は「3日以上」メッセージを返すこと。"""
    result = get_reminder(3)
    assert "3日以上経過しています" in result
    assert "積み残しタスク" in result


def test_reminder_6_days():
    """6日経過は「3日以上」メッセージを返すこと（境界値）。"""
    result = get_reminder(6)
    assert "3日以上経過しています" in result


def test_reminder_7_days():
    """7日経過は「1週間以上」メッセージを返すこと。"""
    result = get_reminder(7)
    assert "1週間以上経過しています" in result
    assert "前回の作業内容を確認" in result


def test_reminder_13_days():
    """13日経過は「1週間以上」メッセージを返すこと（境界値）。"""
    result = get_reminder(13)
    assert "1週間以上経過しています" in result


def test_reminder_14_days():
    """14日経過は「2週間以上」メッセージを返すこと。"""
    result = get_reminder(14)
    assert "2週間以上経過しています" in result
    assert "CLAUDE.mdを読み直して" in result


def test_reminder_100_days():
    """100日経過も「2週間以上」メッセージを返すこと。"""
    result = get_reminder(100)
    assert "2週間以上経過しています" in result


# ── get_days_since_last() ─────────────────────────────────────────

def test_days_since_last_db_not_found():
    """DBが存在しない場合は -1 を返すこと。"""
    result = get_days_since_last(Path("/nonexistent/memory.db"))
    assert result == -1


def test_days_since_last_empty_db():
    """DBにデータが0件の場合は -1 を返すこと。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "memory.db"
        make_empty_db(db_path)
        assert get_days_since_last(db_path) == -1


def test_days_since_last_today():
    """今日のセッションは 0 を返すこと。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "memory.db"
        make_db(db_path, utc_str_days_ago(0))
        assert get_days_since_last(db_path) == 0


def test_days_since_last_1_day():
    """昨日のセッションは 1 を返すこと。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "memory.db"
        make_db(db_path, utc_str_days_ago(1))
        assert get_days_since_last(db_path) == 1


def test_days_since_last_7_days():
    """7日前のセッションは 7 を返すこと。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "memory.db"
        make_db(db_path, utc_str_days_ago(7))
        assert get_days_since_last(db_path) == 7


# ── get_full_reminder() ───────────────────────────────────────────

def test_full_reminder_db_not_found():
    """DBが存在しない場合は空文字を返すこと。"""
    result = get_full_reminder(Path("/nonexistent/memory.db"))
    assert result == ""


def test_full_reminder_today():
    """当日セッションはリマインドなし（空文字）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "memory.db"
        make_db(db_path, utc_str_days_ago(0))
        assert get_full_reminder(db_path) == ""


def test_full_reminder_3_days():
    """3日前セッションは「3日以上」メッセージを返すこと。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "memory.db"
        make_db(db_path, utc_str_days_ago(3))
        result = get_full_reminder(db_path)
        assert "3日以上経過しています" in result


def test_full_reminder_14_days():
    """14日前セッションは「2週間以上」メッセージを返すこと。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "memory.db"
        make_db(db_path, utc_str_days_ago(14))
        result = get_full_reminder(db_path)
        assert "2週間以上経過しています" in result
