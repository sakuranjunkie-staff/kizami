' watcherを完全無窓で起動する透明ラッパー（タスクスケジューラはこれを呼ぶ）
' 引数があればそのセッションの狙い撃ち（時限タスク用）、無ければ全体走査（掃除役用）
Dim cmd
cmd = "pwsh -NoProfile -ExecutionPolicy Bypass -File C:\Users\bukol\Documents\kizami\scripts\idle_sl_watcher.ps1"
If WScript.Arguments.Count > 0 Then cmd = cmd & " -TargetId " & WScript.Arguments(0)
CreateObject("WScript.Shell").Run cmd, 0, False
