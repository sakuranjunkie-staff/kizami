"""
injector.py - UserPromptSubmitHook エントリーポイント

時間サマリー・リマインド・関連メモリ（直近7日）を統合して
Claude Codeのシステムプロンプトに注入するためのテキストをstdoutに出力する。

関連メモリの検索は、モデルを読み込み済みの sui-memory daemon に頼む（2026-11-23）。
このフックは発言のたびに新しいプロセスで立ち上がるため、自分で検索すると
毎回モデル（約1.2GB）を読み込み直して約9秒かかっていた。
daemon が動いていなければ裏で立ち上げ、その回だけキーワード検索で済ませる。

また、未ベクトル化チャンクが閾値を超えた場合に daemon へ embed_pending を頼む。
"""

import json
import os
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
DB_PATH = Path.home() / ".sui-memory" / "memory.db"

# sui-memory daemon の設定
SUI_MEMORY_DIR = r"C:\Users\bukol\Documents\sui-memory"
# uv は場所を固定して呼ぶ。名前だけで呼ぶと、Windows はまず今いるフォルダから探す。
# このフックは会話を始めたフォルダで動くので、そこに置かれた偽の uv.exe が動きうる（2026-11-23）
UV_EXE = Path.home() / ".local" / "bin" / "uv.exe"
DAEMON_URL = "http://127.0.0.1:7766"
SEARCH_TIMEOUT_SECS = 5.0       # 検索の待ち上限。超えたらキーワード検索に切り替える
DAEMON_IDLE_TIMEOUT_SECS = 3600  # このフックが立ち上げた daemon は、1時間使われなければ自分で止まる
# 立ち上げの重複防止：daemon はモデルを読み込み終えるまで（約10秒）応答しない。
# その間に次の発言が来ても、もう一つ立ち上げないための印と、その有効時間
SPAWN_MARK = Path.home() / ".sui-memory" / "daemon_spawn.mark"
SPAWN_COOLDOWN_SECS = 120

from timeline import get_timeline_summary
from reminder import get_full_reminder

# idle_sl_watcherが失敗印（*.sl_fail）を置く場所（2026-08-10 流の指示で注入対象に追加）
MARKERS_DIR = Path.home() / ".claude" / "kizami_markers"


def get_sl_failures() -> str:
    """未処理のオート/sl失敗印を注入用テキストにする。無ければ空文字。

    印は idle_sl_watcher.ps1 が蒸留失敗時に焼き、後続の成功時に自動で消す。
    デスクトップ通知を見ていなくても、セッション内のClaudeが失敗に
    気づいてユーザーに報告できるようにするのが目的。
    """
    try:
        fails = sorted(MARKERS_DIR.glob("*.sl_fail"))
        if not fails:
            return ""
        lines = [
            "## ⚠ オート/sl失敗（未処理）",
            "（注: idle_sl_watcherの蒸留失敗印。ユーザーに報告せよ。対処されるか後続の自動/slが成功すると消える）",
        ]
        for f in fails:
            try:
                detail = f.read_text(encoding="utf-8").strip().splitlines()[0]
            except Exception:
                detail = "(詳細読み取り不能)"
            lines.append(f"- セッション {f.stem[:8]}…: {detail}／詳細=~\\.claude\\kizami_markers\\watcher.log")
        return "\n".join(lines)
    except Exception:
        return ""


def get_pending_count() -> int:
    """embedding=NULLのチャンク数を返す。DBアクセス失敗時は0を返す。"""
    try:
        import sqlite3
        con = sqlite3.connect(str(DB_PATH), timeout=1.0)
        count = con.execute("SELECT COUNT(*) FROM memories WHERE embedding IS NULL").fetchone()[0]
        con.close()
        return count
    except Exception:
        return 0


def _post_daemon(path: str, payload: dict, timeout: float):
    """daemon に JSON を POST して、返ってきた JSON を dict で返す。
    つながらない・時間切れ・エラーのときは例外をそのまま投げる（呼び出し側で判断する）。"""
    import urllib.request
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        DAEMON_URL + path,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _try_daemon_search(query: str) -> list[dict] | None:
    """daemon に直近7日のハイブリッド検索を頼む。daemon が使えなければ None を返す。"""
    try:
        res = _post_daemon(
            "/search_recent",
            {"query": query, "limit": 5, "exclude_project": "G:/KagamiAlice"},
            timeout=SEARCH_TIMEOUT_SECS,
        )
    except Exception:
        return None
    # 返事の形を確かめてから使う。同じ口で別のプログラムが待ち受けていた場合に、
    # 想定外の形や件数のものを注入に流さない（2026-11-23）
    mems = res.get("memories") if isinstance(res, dict) else None
    if not isinstance(mems, list):
        return None
    cleaned: list[dict] = []
    for m in mems[:5]:
        if not isinstance(m, dict):
            continue
        # 注入に使う3項目だけを文字列として持つ（長さは format_memories が切る）
        cleaned.append({
            "timestamp": str(m.get("timestamp", "")),
            "user_text": str(m.get("user_text", "")),
            "assistant_text": str(m.get("assistant_text", "")),
        })
    return cleaned


def _spawn_daemon() -> None:
    """daemon を裏で立ち上げる（画面は出さない・待たない）。

    直近 SPAWN_COOLDOWN_SECS 以内に立ち上げていれば何もしない。daemon はモデルを
    読み込み終えるまで応答しないので、その間の発言でもう一つ立ち上げると、
    モデルの読み込みが二重に走る（二つ目はポートが取れずに終わるが、読み込みは無駄になる）。
    """
    try:
        if SPAWN_MARK.exists() and time.time() - SPAWN_MARK.stat().st_mtime < SPAWN_COOLDOWN_SECS:
            return
        if not UV_EXE.is_file():
            print(f"[kizami] uv が見つからないので daemon を立ち上げない: {UV_EXE}", file=sys.stderr)
            return
        SPAWN_MARK.parent.mkdir(parents=True, exist_ok=True)
        SPAWN_MARK.touch()
        env = dict(os.environ)
        env["SUI_MEMORY_IDLE_TIMEOUT"] = str(DAEMON_IDLE_TIMEOUT_SECS)
        subprocess.Popen(
            [str(UV_EXE), "run", "--directory", SUI_MEMORY_DIR, "python", "-m", "src.daemon"],
            cwd=SUI_MEMORY_DIR,  # 作業フォルダも固定する（会話を始めたフォルダで動かさない）
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        print("[kizami] daemon が動いていないので裏で立ち上げた", file=sys.stderr)
    except Exception as e:
        print(f"[kizami] daemon の立ち上げに失敗: {e}", file=sys.stderr)


def maybe_trigger_embed() -> None:
    """
    未ベクトル化チャンクが閾値以上なら、daemon に embed_pending を頼む。

    返事は待たない（時間切れを短くして、頼んだらすぐ戻る）。daemon は接続が切れても
    受けた処理を最後まで続ける。以前は最大30秒待っていて、そのぶん発言が待たされていた。
    daemon が動いていなければ何もしない（検索側が立ち上げるので、次の発言で頼める）。
    以前の「daemon が無ければ別プロセスでモデルを読み込んでベクトル化」は、daemon の
    立ち上げとモデルの読み込みが重なるので廃止した（2026-11-23）。
    """
    count = get_pending_count()
    if count < EMBED_THRESHOLD:
        return
    try:
        _post_daemon("/embed_pending", {"batch_size": 50}, timeout=1.0)
    except TimeoutError:
        # 頼んだ後の時間切れ＝daemon は処理を続けている
        print(f"[kizami] 未ベクトル化{count}件 → daemon に依頼した", file=sys.stderr)
    except Exception:
        # つながらない（daemon が止まっている）等。次の発言で頼む
        pass


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

        # ユーザー発言も上限を設ける（assistant側と対称化）。
        # user_textを無制限で注入すると、巨大な貼り付け（スキルダンプ・
        # デバッグ依頼プロンプト等）が丸ごとシステムプロンプトに流れ込み、
        # 注入ブロックだけで数千トークンを食ってコンテキストを劣化させる。
        # 閾値500字はDB実測（全7324件）から決めた: user_text長はp90=379字で
        # 「まともな質問」が頭打ちになり、500字に余白を取ると本物の質問はほぼ
        # 無傷（切られるのは6.6%＝巨大貼り付け/デバッグ依頼クラスだけ）。
        # 5件×500字で注入は有界。主題は冒頭に出るのでassistant(200)より長めでよい。
        user_text = mem.get("user_text", "")
        if len(user_text) > 500:
            user_text = user_text[:500] + "…"

        lines.append(f"### {i}. {date_str}")
        lines.append(f"**あなた**: {user_text}")
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
        # KagamiAliceのユーザー会話はClaude Codeとの会話ではないため除外する
        memories: list[dict] = []
        if query:
            # まず daemon に頼む（モデル読み込み済みなので速い）
            found = _try_daemon_search(query)
            if found is None:
                # daemon が使えない：裏で立ち上げ、この回はキーワード検索だけで済ませる
                # （モデルを読み込まないので一瞬で終わる。次の発言からは daemon が答える）
                _spawn_daemon()
                from retriever import search_recent
                found = search_recent(query, limit=5, exclude_project="G:/KagamiAlice",
                                      use_vector=False)
            memories = found

        # 出力ブロックを組み立てる
        blocks: list[str] = []

        # 時間サマリー（常に表示）
        if timeline:
            blocks.append(timeline)

        # リマインド（空文字なら省略）
        if reminder:
            blocks.append(reminder)

        # オート/sl失敗の未処理印（あれば警告として注入する）
        sl_fail = get_sl_failures()
        if sl_fail:
            blocks.append(sl_fail)

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
