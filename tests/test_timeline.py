"""
test_timeline.py - timeline.pyのテスト
"""

import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from src.timeline import get_timeline_summary, WEEKDAY_JA

JST = timezone(timedelta(hours=9))


def make_db(path: Path, rows: list[tuple]) -> None:
    """テスト用のmemory.dbを作成する。rowsは(session_id, created_at)のリスト。"""
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL,
            content TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.executemany(
        "INSERT INTO memories (session_id, content, created_at) VALUES (?, ?, ?)",
        [(r[0], "test", r[1]) for r in rows],
    )
    conn.commit()
    conn.close()


# ── DBが存在しない場合 ────────────────────────────────────────────
def test_db_not_found_returns_empty():
    """DBファイルが存在しない場合は空文字を返すこと。"""
    result = get_timeline_summary(Path("/nonexistent/path/memory.db"))
    assert result == ""


# ── データが0件の場合 ─────────────────────────────────────────────
def test_empty_db_shows_no_memory():
    """DBにデータが1件もない場合は「メモリなし」と表示すること。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "memory.db"
        make_db(db_path, [])

        result = get_timeline_summary(db_path)

        assert "メモリなし" in result
        assert "総セッション数: 0件" in result
        assert "蓄積メモリ数: 0件" in result


# ── 通常データの場合 ──────────────────────────────────────────────
def test_summary_contains_required_sections():
    """通常データで必要なセクションがすべて含まれること。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "memory.db"
        # セッション2件、メモリ3件
        make_db(db_path, [
            ("session-A", "2026-03-22T10:00:00Z"),
            ("session-A", "2026-03-22T10:05:00Z"),
            ("session-B", "2026-03-23T09:00:00Z"),
        ])

        result = get_timeline_summary(db_path)

        assert "## 時間サマリー" in result
        assert "今日:" in result
        assert "前回のセッション:" in result
        assert "総セッション数: 2件" in result
        assert "蓄積メモリ数: 3件" in result


def test_last_session_date_is_max():
    """前回セッション日時がcreated_atの最大値（最新）であること。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "memory.db"
        make_db(db_path, [
            ("session-A", "2026-03-20T10:00:00Z"),
            ("session-B", "2026-03-23T10:00:00Z"),  # これが最新
        ])

        result = get_timeline_summary(db_path)

        # JST変換後の日付が表示されること
        assert "2026-03-23" in result or "2026-03-24" in result  # UTC→JSTで翌日になる場合あり


def test_today_label_contains_weekday():
    """今日の表示に曜日（日本語）が含まれること。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "memory.db"
        make_db(db_path, [("session-A", "2026-03-24T01:00:00Z")])

        result = get_timeline_summary(db_path)

        # いずれかの曜日が含まれること
        assert any(wd in result for wd in WEEKDAY_JA)


def test_days_ago_calculation():
    """経過日数が正しく計算されること（昨日のセッション → 1日前）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "memory.db"

        # 昨日のJST 10:00 = UTC 01:00
        yesterday_utc = datetime.now(JST).replace(hour=1, minute=0, second=0, microsecond=0)
        yesterday_utc = yesterday_utc - timedelta(days=1)
        created_at = yesterday_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        make_db(db_path, [("session-A", created_at)])

        result = get_timeline_summary(db_path)

        assert "1日前" in result


def test_today_session_shows_today():
    """今日のセッションは「今日」と表示されること。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "memory.db"

        # 今日のJST 10:00 → UTC 01:00
        today_utc = datetime.now(JST).replace(hour=1, minute=0, second=0, microsecond=0)
        created_at = today_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        make_db(db_path, [("session-A", created_at)])

        result = get_timeline_summary(db_path)

        assert "今日" in result


def test_output_format():
    """出力が仕様通りの4行フォーマットであること。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "memory.db"
        make_db(db_path, [("session-A", "2026-03-23T01:00:00Z")])

        result = get_timeline_summary(db_path)
        lines = result.strip().split("\n")

        assert lines[0] == "## 時間サマリー"
        assert lines[1].startswith("今日:")
        assert lines[2].startswith("前回のセッション:")
        assert lines[3].startswith("総セッション数:")
        assert lines[4].startswith("蓄積メモリ数:")
