# Stopフックから呼ばれる腕木：ターンが終わるたび「30分後にこのセッションを畳め」の時限タスクを仕掛け直す
# stdinにフック情報のJSON（session_id等）が来る。時限はセッションごとに1本＝/fで常に上書き（＝先送り）
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
# 自動/slの写し（--fork-session）の中では時限を仕掛けない。写しは巡回が畳む対象ではない（2026-11-23）
if ($env:KIZAMI_AUTO_SL_FORK -eq '1') { exit 0 }
try {
    $raw = [Console]::In.ReadToEnd()
    $in = $raw | ConvertFrom-Json
} catch { exit 0 }
$sid = $in.session_id
if (-not $sid -or $sid.Length -lt 8) { exit 0 }
$id8 = $sid.Substring(0, 8)
$t = (Get-Date).AddMinutes(30)
$vbs = 'C:\Users\bukol\Documents\kizami\scripts\idle_sl_watcher.vbs'
schtasks /create /tn "kizami_sl_once_$id8" /tr "wscript.exe $vbs $sid" /sc once `
    /st $t.ToString('HH:mm') /sd $t.ToString('yyyy/MM/dd') /f | Out-Null
exit 0
