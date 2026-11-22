# kizami idle watcher v3: 直近48時間の未処理セッションを走査し、30分無操作なら自動/slを撃つ
# v2の恒久印（<id>.sl_done＝一度きり）は、蒸留後・too-small判定後・手動/sl後に育った会話が
# 永久に網から外れる穴だった（2026-07-31 流決裁で全面改修）。v3の骨子：
#  - 印＝<id>.sl_time（最後に蒸留が完了した実会話時刻）。それ以降に3往復たまれば何度でも撃つ
#  - 印は蒸留の「成功後」に焼く（resume失敗で済扱いになる穴を塞ぐ）。失敗は通知する
#  - <id>.lock で二重発火を原子的に排除（v2はチェック→印付けの間に窓があり実際に二重発火した）
#  - cold_skip は恒久でなく「この放置区間は通知済み」の印。再開して温まれば再び対象
#  - 48時間窓を未蒸留のまま越えたセッションは一度だけ通知（.win_warn）
# タスクスケジューラから15分ごとに起動。テスト用: -TargetId で対象を強制指定。
param([int]$ThresholdMin = 30, [int]$WindowHours = 48, [string]$TargetId = '', [switch]$Force)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Send-Toast([string]$Title, [string]$Body) {
    # wscriptはGUIアプリ＝待たずに戻る。0秒＝クリックまで表示（不在時の見逃し防止）
    # 先頭に発報時刻を刻む＝残留した通知と新着の区別用（2026-08-06 流の指示）
    $stamp = Get-Date -Format 'yyyy/MM/dd HH:mm'
    & wscript.exe 'C:\kz\scripts\toast.vbs' $Title "[$stamp] $Body" 0
    Add-Content $log "$(Get-Date -Format s) toast fired: $Title"
}

# 蒸留されたsession_logのフロントマターから「日付・時間帯・話題一覧」を通知用に組む
# （2026-08-05 流の要望。読めなければ空文字＝呼び元が従来文言に落ちる）
function Get-SlSummary([string]$Path) {
    try {
        $date = ''; $time = ''; $topics = @(); $name = ''; $dashes = 0
        foreach ($ln in (Get-Content $Path -TotalCount 40 -Encoding UTF8)) {
            if ($ln -match '^---\s*$') { $dashes++; if ($dashes -ge 2) { break }; continue }
            if ($ln -match '^date:\s*(\S+)') { $date = $Matches[1] }
            elseif ($ln -match '^session_time:\s*(\S+)') { $time = $Matches[1] }
            elseif ($ln -match '^\s*-\s*name:\s*(.+)$') { $name = $Matches[1].Trim() }
            elseif ($name -and $ln -match '^\s*weight:\s*(\d+)') { $topics += "$name $($Matches[1])%"; $name = '' }
        }
        if (-not $date) { return '' }
        $s = "$date $time の分"
        if ($topics.Count) { $s += "／話題: " + ($topics -join '・') }
        return $s
    } catch { return '' }
}

$jl      = 'C:\Users\bukol\.claude\projects\C--Users-bukol'
$markers = 'C:\Users\bukol\.claude\kizami_markers'
New-Item -ItemType Directory -Force $markers | Out-Null
$log = Join-Path $markers 'watcher.log'

# セッションの「起動時」の作業フォルダをjsonl先頭から読む。resumeは起動時cwdのプロジェクトを探すため、
# 末尾から読むと作業中のcd（convert_for_claude等）を拾って"No conversation found"になる（7/31実害3件で判明）
function Get-SessionCwd([System.IO.FileInfo]$f, [string]$fallback) {
    foreach ($ln in (Get-Content $f.FullName -TotalCount 20 -Encoding UTF8)) {
        $head = $ln.Substring(0, [Math]::Min(800, $ln.Length))
        if ($head -match '"cwd":"([^"]+)"') { return ($Matches[1] -replace '\\\\', '\') }
    }
    return $fallback
}

# このセッションが自動/slの蒸留用に作った写しかを返す。
# 蒸留は写し（--fork-session）で撃つので、写しのセッションが新規の記録として残る。
# それを巡回が拾って再び撃つと、写しが写しを生んで止まらない（2026-11-18 流の指示で写し方式へ移行）。
# 写しは先頭近く（実測8行目）に、撃たれた指示文の受付行（queue-operation）を持つ。
# 末尾から探すと蒸留中の道具の行に埋もれて届かないので、頭だけ読む
function Test-IsAutoSlFork([System.IO.FileInfo]$f) {
    foreach ($ln in (Get-Content $f.FullName -TotalCount 40 -Encoding UTF8)) {
        $head = $ln.Substring(0, [Math]::Min(800, $ln.Length))
        if ($head.Contains('"type":"queue-operation"') -and $head.Contains('"content":"/sl。対象=')) { return $true }
    }
    return $false
}

# 最後の実会話（user/assistant）の時刻を返す。away_summary等のシステム書き込みは無操作扱い。
function Get-LastActivityTime([System.IO.FileInfo]$f) {
    $lines = Get-Content $f.FullName -Tail 40 -Encoding UTF8
    for ($i = $lines.Count - 1; $i -ge 0; $i--) {
        $ln = $lines[$i]
        $head = $ln.Substring(0, [Math]::Min(400, $ln.Length))
        if (-not ($head.Contains('"type":"user"') -or $head.Contains('"type":"assistant"'))) { continue }
        # timestampは行頭側にも行末側にも来る。両端だけ見る（巨大行の全文regex禁止）
        $tailStr = $ln.Substring([Math]::Max(0, $ln.Length - 600))
        if ($head -match '"timestamp":"([^"]+)"' -or $tailStr -match '"timestamp":"([^"]+)"') {
            return ([datetime]::Parse($Matches[1])).ToLocalTime()
        }
    }
    return $f.LastWriteTime
}

function Invoke-AutoSl([System.IO.FileInfo]$j) {
    $id = $j.BaseName
    $timeRec = Join-Path $markers "$id.sl_time"
    $cold = Join-Path $markers "$id.cold_skip"
    $lock = Join-Path $markers "$id.lock"

    # 最後に蒸留が完了した実会話時刻。それ以降に会話が無ければ何もしない
    $since = [datetime]::MinValue
    if (Test-Path $timeRec) {
        try { $since = [datetime]::Parse((Get-Content $timeRec -First 1)) } catch {}
    }
    $lastAct = Get-LastActivityTime $j
    if ($lastAct -le $since) {
        if ($TargetId) { Add-Content $log "$(Get-Date -Format s) $id skip: no new activity since $($since.ToString('s'))" }
        return
    }

    # 蒸留用の写しは撃たない。記録済み扱いにして、48時間窓の未蒸留通知にも載せない
    if (Test-IsAutoSlFork $j) {
        Set-Content $timeRec ($lastAct.ToString('o'))
        Add-Content $log "$(Get-Date -Format s) $id skip: auto-/sl fork - time recorded"
        return
    }

    # ロック（原子的）：時限タスクと巡回の同時侵入による二重発火を排除。30分超の残骸は掃除
    if (Test-Path $lock) {
        if (((Get-Date) - (Get-Item $lock).LastWriteTime).TotalMinutes -lt 30) {
            Add-Content $log "$(Get-Date -Format s) $id skip: locked (another run in progress)"
            return
        }
        Remove-Item $lock -Force
    }
    try { New-Item $lock -ItemType File -ErrorAction Stop | Out-Null }
    catch { Add-Content $log "$(Get-Date -Format s) $id skip: lock race"; return }
    try {

    # 最後の「本物のユーザー入力」が/slコマンドなら撃たない（畳み済みセッション）。
    # ツール結果(tool_result)やassistant行は見ない＝スクリプト自身の検出文字列を拾う偽陽性を防ぐ
    $tailLines = Get-Content $j.FullName -Tail 60 -Encoding UTF8
    for ($i = $tailLines.Count - 1; $i -ge 0; $i--) {
        $h = $tailLines[$i].Substring(0, [Math]::Min(800, $tailLines[$i].Length))
        if (-not $h.Contains('"type":"user"')) { continue }
        if ($h.Contains('"tool_result"')) { continue }
        if ($h.Contains('command-name>/sl<')) {
            # 手動で畳み済み＝ここまでは記録済み扱い。以後会話が続けば自然に再武装する
            Set-Content $timeRec ($lastAct.ToString('o'))
            Add-Content $log "$(Get-Date -Format s) $id skip: manual /sl detected (time recorded)"
            return
        }
        break
    }

    # コールドガード：冷え切った（キャッシュ失効後・65分超）太いセッションは自動で燃やさない。
    # 判定は往復数チェック（大物の全パース）より先＝冷えた55MB級を巡回のたびに読む無駄を避ける
    $idleMin = [int]((Get-Date) - $lastAct).TotalMinutes
    if (-not $Force -and $idleMin -gt 65 -and $j.Length -gt 500KB) {
        # 恒久印ではない：この放置区間について通知済みかだけ見る。再開して温まれば再び対象
        if (-not (Test-Path $cold) -or (Get-Item $cold).LastWriteTime -lt $lastAct) {
            # 通知の前に増分を数える：蒸留が自分の/sl往復を1つ追記するため「新規1往復」は常態。
            # 3往復未満は通知に値しない。cold印は数に関わらず焼く＝この放置区間の再パースを止める
            Push-Location C:\Users\bukol\Documents\session_logs
            $ca = @(); if ($since -gt [datetime]::MinValue) { $ca = @('--since', $since.ToUniversalTime().ToString('o')) }
            $ct = & uv run python _sl_dump.py $j.FullName --times @ca 2>$null | Out-String
            Pop-Location
            Set-Content $cold (Get-Date).ToString('o')
            $cn = -1
            if ($ct -match 'pairs:\s*(\d+)') { $cn = [int]$Matches[1] }
            if ($cn -lt 0 -or $cn -ge 3) {
                Add-Content $log "$(Get-Date -Format s) $id cold-skip: idle=${idleMin}min size=$([int]($j.Length/1KB))KB newpairs=$cn"
                Send-Toast 'kizami: cold skip' "冷えた大物（$($id.Substring(0,8))…・$([int]($j.Length/1KB))KB・放置${idleMin}分・未蒸留${cn}往復）。畳む: pwsh -NoProfile -File C:\kz\scripts\idle_sl_watcher.ps1 -TargetId $id -Force"
            }
        }
        return
    }

    # 新規の会話が3往復未満なら今回は見送り（印は焼かない＝育てば次の機会に撃つ）
    Push-Location C:\Users\bukol\Documents\session_logs
    $sinceArgs = @()
    if ($since -gt [datetime]::MinValue) { $sinceArgs = @('--since', $since.ToUniversalTime().ToString('o')) }
    $times = & uv run python _sl_dump.py $j.FullName --times @sinceArgs 2>$null | Out-String
    Pop-Location
    # fail-closed：pairsが読めなければ撃たない（2026-08-07、0往復セッションがpairs行なしで素通り→誤射）
    $np = -1
    if ($times -match 'pairs:\s*(\d+)') { $np = [int]$Matches[1] }
    if ($np -lt 3) {
        if ($np -lt 0) { Add-Content $log "$(Get-Date -Format s) $id skip: pairs unreadable (dump failed) - fail-closed" }
        elseif ($TargetId) { Add-Content $log "$(Get-Date -Format s) $id skip: too few new pairs ($np) - retry when it grows" }
        return
    }

    Add-Content $log "$(Get-Date -Format s) $id firing auto-/sl"

    # そのセッションが立てられたフォルダから呼ぶ（別フォルダだとresumeがセッションを見つけられない）
    $cwd = Get-SessionCwd $j 'C:\Users\bukol'
    if (-not (Test-Path $cwd)) { $cwd = 'C:\Users\bukol' }
    Set-Location $cwd
    $tp = $j.FullName -replace '\\', '/'
    # 写しの中ではシェルを一切使わせない（2026-11-23 流決裁）。写しは元の会話を丸ごと持っており、
    # そこにはウェブやダウンロード物から取り込んだ文章も混ざる。仕込まれた指示に従って、
    # 無人のまま確認なしでコマンドが走る恐れがあった。/slでシェルが要るのは _sl_dump.py の2回だけなので、
    # 1回目（Step2＝時刻と往復数）は上で出した $times を指示文で渡し、2回目（Step5＝生ログの追記）は
    # 写しが要点を書き終えた後にこの巡回が自分で撃つ。
    $timesLine = (($times -split "`r?`n") | Where-Object { $_ -match '\S' }) -join ' / '
    # SL_SKIP_OK＝「焼く価値のある差分なし」の正規の見送り合図（2026-08-10・焼き直し禁止設計との整合）
    $prompt = "/sl。対象=$tp。自動/slなので次を守れ：Bash・PowerShellは禁止されていて使えない。" +
        "Step1とStep2は撃つな（Step2の結果＝$timesLine）。Step5（生ログの追記）とStep6b（印焼き）は巡回が撃つので撃つな。" +
        "保存は C:\Users\bukol\Documents\session_logs\ の下にWriteで新規作成する（C:\slog 経由のパスは使うな）。" +
        "同日・同ラベルのファイルがあるかはGlobで確かめてよい。" +
        "焼く価値のある差分が無ければ新規ファイルを作らず、応答の最後の行を SL_SKIP_OK の一語だけにして終われ（失敗した時はこの語を書くな）。"
    if ($since -gt [datetime]::MinValue) {
        # 増分蒸留：前回焼き終えた実会話時刻（sl_time）を基点に、それ以降だけ焼かせる（2026-08-05 流決裁）
        $prompt += "増分蒸留=--since $($since.ToUniversalTime().ToString('o'))（sl.mdの増分蒸留節に従え。範囲はStep2の結果のとおり）。"
    }
    # 写しに許す道具：session_log_*.md の書き込みと、session_logs の読み取りとファイル名の確認だけ。
    # 書き込みを session_logs 全体にすると、巡回が後で動かす _sl_dump.py（や uv の設定）を写しが
    # 書き換えられ、シェル禁止を回り込めてしまう。だから書けるのは記録のファイル名の形だけにする（2026-11-23）。
    # シェル・ウェブ・サブエージェントは「禁止」で渡す（禁止はどこの許可よりも優先される）
    # ファイルを書く道具のパス指定は Edit(...) の形で書く（Write(...) はパス判定に使われない。
    # Edit の規則が Write を含む全ての書き込み道具に効く・2026-11-23 実地で確認）
    $allowTools = @(
        'Edit(//c/Users/bukol/Documents/session_logs/session_log_*.md)',
        'Read(//c/Users/bukol/Documents/session_logs/**)',
        'Glob'
    )
    # 写しに存在させる道具そのものを --tools で決め打ちする（ここに無い道具は最初から無い）。
    # 禁止を並べる形だと、別セッションへ文章を送る・作業員を動かす・予約を仕掛ける等の道具が
    # 増えるたびに回り込みの穴が開く。MCP の道具（ブラウザ操作・Blender でのコード実行など）は
    # --strict-mcp-config（--mcp-config を渡さない）で一つも読み込ませない（2026-11-23）
    $toolSet = 'Read,Write,Edit,Glob,Skill'
    # 念のための二重化：決め打ちが効かなかった場合でも、シェル等は禁止で止まる
    $denyTools = @('Bash', 'PowerShell', 'WebFetch', 'WebSearch', 'Agent', 'Task', 'NotebookEdit',
        'SendMessage', 'Workflow', 'RemoteTrigger', 'CronCreate')
    $fireStart = Get-Date
    # 写し（--fork-session）で撃つ：元のセッションが裏で動いている最中でも開ける。
    # 元のセッションに/slの往復を書き足さない（2026-11-18 流の指示。写しは上の見張りで巡回から外れる）
    # 写しの中のStopフック（sui-memoryの保存・時限の仕掛け）を止める目印。写しの子プロセスにだけ効かせ、撃ったら外す
    # （元の会話の複製が別の会話IDでDBに入り、検索に二重に出ていた・2026-11-23）
    $env:KIZAMI_AUTO_SL_FORK = '1'
    try {
        $out = & claude -p $prompt --resume $id --fork-session --tools $toolSet --strict-mcp-config --allowedTools @allowTools --disallowedTools @denyTools 2>&1 | Out-String
        $claudeExit = $LASTEXITCODE
    } finally {
        Remove-Item Env:KIZAMI_AUTO_SL_FORK -ErrorAction SilentlyContinue
    }
    Add-Content $log $out
    # 成功判定は完了報告を盲信せず現物で：exit 0＋resume成功＋（session_logが書かれた or 意図的見送りの合図）
    # 見送りを失敗扱いにすると印が焼かれず巡回のたび再発火する（2026-08-10実害＝無限ループ）
    # 撃った後に更新された記録を探す。ちょうど1つの時だけ、この写しが書いた物とみなす。
    # 同じ時間に別の会話で /sl が書かれていると、2つ以上が当たる。その場合は会話全文を
    # 違うファイルに足しかねないので、失敗として扱う（2026-11-23）
    $touchedAll = @(Get-ChildItem 'C:\Users\bukol\Documents\session_logs\session_log_*.md' |
        Where-Object { $_.LastWriteTime -gt $fireStart })
    $slTouched = $null
    if ($touchedAll.Count -eq 1) { $slTouched = $touchedAll[0] }
    elseif ($touchedAll.Count -gt 1) {
        Add-Content $log "$(Get-Date -Format s) $id ambiguous: $($touchedAll.Count) session_logs written since fire - not appending"
    }
    # 見送りの合図は「最後の行が SL_SKIP_OK の一語だけ」の時に限る。文字列が含まれるだけで認めると、
    # 「SL_SKIP_OK ではない」と書いた失敗報告まで見送り扱いになり、未蒸留の分に済みの印を焼いていた（2026-11-23 実害）
    $lastLine = (($out -split "`r?`n") | Where-Object { $_ -match '\S' } | Select-Object -Last 1)
    $skipOk = ($null -ne $lastLine) -and ($lastLine.Trim() -eq 'SL_SKIP_OK')
    # Step5の代わり：写しが書いた要点の後ろに、会話の全文を巡回が追記する（写しにはシェルを渡していないため）
    if ($claudeExit -eq 0 -and $slTouched) {
        Push-Location C:\Users\bukol\Documents\session_logs
        $dumpOut = & uv run python _sl_dump.py $j.FullName $slTouched.FullName @sinceArgs 2>&1 | Out-String
        $dumpExit = $LASTEXITCODE
        Pop-Location
        Add-Content $log "$(Get-Date -Format s) $id raw log append (exit=$dumpExit): $($dumpOut.Trim())"
        if ($dumpExit -ne 0) { $slTouched = $null }  # 追記に失敗したら失敗扱い（印を焼かず、失敗を知らせる）
    }
    $distilled = ($claudeExit -eq 0) -and ($out -notmatch 'No conversation found') -and ($slTouched -or $skipOk)
    if ($distilled) {
        # 成功してから印を焼く。基本は発火判定時点の実会話時刻＝蒸留中に増えた分は次回拾う。
        # ただし最後の本物のユーザー入力がこの自動/slのプロンプト自身なら、発火後の時刻で焼く
        # ＝自分の/sl往復を「新規活動」と誤認して巡回のたび再走する自食いを塞ぐ（2026-08-10実害）
        $burnAt = $lastAct
        $tl2 = Get-Content $j.FullName -Tail 60 -Encoding UTF8
        for ($k = $tl2.Count - 1; $k -ge 0; $k--) {
            $h2 = $tl2[$k].Substring(0, [Math]::Min(800, $tl2[$k].Length))
            if (-not $h2.Contains('"type":"user"')) { continue }
            if ($h2.Contains('"tool_result"')) { continue }
            if ($h2.Contains('/sl。対象=')) { $burnAt = Get-LastActivityTime $j }
            break
        }
        Set-Content $timeRec ($burnAt.ToString('o'))
        Remove-Item (Join-Path $markers "$id.sl_fail") -Force -ErrorAction SilentlyContinue
        if ($skipOk -and -not $slTouched) { Add-Content $log "$(Get-Date -Format s) $id intentional skip (SL_SKIP_OK) - time recorded" }
    } else {
        Add-Content $log "$(Get-Date -Format s) $id distill FAILED (exit=$claudeExit)"
        # 失敗印：kizami injectorが次のプロンプトでセッション内のClaudeに注入して知らせる。後続の成功時に自動で消す
        Set-Content (Join-Path $markers "$id.sl_fail") "$(Get-Date -Format s) exit=$claudeExit"
        Send-Toast 'kizami: /sl失敗' "セッション $($id.Substring(0,8))…（exit=$claudeExit）。詳細=watcher.log"
    }

    # /sr相当：daemonを起こし、対象transcriptを明示してPOST（sr_post.shは最新自動選択なのでここでは使わない）
    & pwsh -NoProfile -File C:\sui\scripts\spawn_daemon.ps1
    foreach ($i in 1..60) {
        try { Invoke-RestMethod 'http://127.0.0.1:7766/health' -TimeoutSec 1 | Out-Null; break }
        catch { Start-Sleep 1 }
    }
    $body = @{ transcript_path = $j.FullName } | ConvertTo-Json
    try {
        $r = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:7766/save_and_embed' -ContentType 'application/json' -Body $body
        Add-Content $log ($r | ConvertTo-Json -Compress)
    } catch { Add-Content $log "$(Get-Date -Format s) $id sr POST failed: $_" }
    Add-Content $log "$(Get-Date -Format s) $id auto-/sl done (distilled=$distilled)"
    # 完了をデスクトップ通知（TUI画面には外から出せないため）。失敗時は上で失敗通知済み。
    # 意図的見送り（SL_SKIP_OK・ファイル無し）は通知に値しないのでログのみ
    if ($distilled -and $slTouched) {
        $sum = Get-SlSummary $slTouched.FullName
        if ($sum) {
            Send-Toast 'kizami: /sl完了' "$sum を蒸留完了。放置窓は畳んでよし。"
        } else {
            Send-Toast 'kizami: /sl完了' "セッション $($id.Substring(0,8))…の蒸留完了。放置窓は畳んでよし。"
        }
    }
    } finally { Remove-Item $lock -Force -ErrorAction SilentlyContinue }
}

if ($TargetId) {
    # 時限タスク（イベント駆動）の着弾点。役目を終えた時限タスクは掃除する
    schtasks /delete /tn "kizami_sl_once_$($TargetId.Substring(0,8))" /f 2>$null | Out-Null
    $j = Get-ChildItem "C:\Users\bukol\.claude\projects\*\$TargetId.jsonl" | Select-Object -First 1
    if (-not $j) { Add-Content $log "$(Get-Date -Format s) $TargetId not found"; return }
    if (-not $Force) {
        # 安全弁：時限の上書きが失敗していた場合の誤射防止。本当に無操作か着弾時に再確認
        $last = Get-LastActivityTime $j
        if (((Get-Date) - $last).TotalMinutes -lt $ThresholdMin) {
            # 秒切り捨て等で数十秒早着した場合の保険：3分後に同じ時限を掛け直して再走する
            # （会話が再開されればStopフックの30分上書きがこの再走を消す）
            $t = (Get-Date).AddMinutes(3)
            schtasks /create /tn "kizami_sl_once_$($TargetId.Substring(0,8))" /tr "wscript.exe C:\Users\bukol\Documents\kizami\scripts\idle_sl_watcher.vbs $TargetId" /sc once /st $t.ToString('HH:mm') /sd $t.ToString('yyyy/MM/dd') /f | Out-Null
            Add-Content $log "$(Get-Date -Format s) $TargetId skip: not idle at fire time (retry armed +3min)"
            return
        }
    }
    Invoke-AutoSl $j
    return
}

$now = Get-Date
# 全プロジェクト置き場の直下を走査（サブエージェントの記録は下層なので拾わない）
# 粗い窓＝ファイル更新が48時間以内。無操作判定は中身の実会話時刻で行う（開き窓のaway_summary等に騙されない）
$cands = Get-ChildItem "C:\Users\bukol\.claude\projects\*\*.jsonl" |
    Where-Object { ($now - $_.LastWriteTime).TotalHours -le $WindowHours } |
    Sort-Object LastWriteTime
foreach ($j in $cands) {
    $last = Get-LastActivityTime $j
    if (($now - $last).TotalMinutes -lt $ThresholdMin) { continue }
    Invoke-AutoSl $j
}

# 後段：窓（48時間）を未蒸留のまま越えたセッションは一度だけ通知する（電源断で窓を素通りする穴の手当て）
$olds = Get-ChildItem "C:\Users\bukol\.claude\projects\*\*.jsonl" |
    Where-Object { $h = ($now - $_.LastWriteTime).TotalHours; $h -gt $WindowHours -and $h -le 336 }
foreach ($j in $olds) {
    $id = $j.BaseName
    $warn = Join-Path $markers "$id.win_warn"
    if (Test-Path $warn) { continue }
    # 記録済み時刻がファイル最終更新以降なら未蒸留分なし＝開かずにスキップ
    $timeRec = Join-Path $markers "$id.sl_time"
    $since = [datetime]::MinValue
    if (Test-Path $timeRec) {
        try { $since = [datetime]::Parse((Get-Content $timeRec -First 1)) } catch {}
    }
    if ($since -ge $j.LastWriteTime) { continue }
    if ((Get-LastActivityTime $j) -le $since) { continue }
    # 中身が薄いものは通知に値しない（判定は一度きり＝この後win_warnを焼くので毎回は走らない）
    Push-Location C:\Users\bukol\Documents\session_logs
    $sa = @(); if ($since -gt [datetime]::MinValue) { $sa = @('--since', $since.ToUniversalTime().ToString('o')) }
    $t = & uv run python _sl_dump.py $j.FullName --times @sa 2>$null | Out-String
    Pop-Location
    Set-Content $warn (Get-Date).ToString('o')
    $np = -1
    if ($t -match 'pairs:\s*(\d+)') { $np = [int]$Matches[1] }
    if ($np -ge 0 -and $np -lt 3) { continue }
    Add-Content $log "$(Get-Date -Format s) $id window-exit: undistilled ($np pairs) age=$([int](($now - $j.LastWriteTime).TotalHours))h"
    Send-Toast 'kizami: 窓越え' "未蒸留のまま48h越え（$($id.Substring(0,8))…）。畳む: pwsh -NoProfile -File C:\kz\scripts\idle_sl_watcher.ps1 -TargetId $id -Force"
}
