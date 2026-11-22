' kizami通知ヘルパー。引数: Title Body [Seconds]（0=クリックまで残る・既定）
' 自動/slの通知は「ユーザー不在時に出る」のが常態なので、時限で消すと確実に見逃す
Dim title, body, sec
If WScript.Arguments.Count < 2 Then WScript.Quit
title = WScript.Arguments(0)
body = WScript.Arguments(1)
sec = 0
If WScript.Arguments.Count > 2 Then sec = CInt(WScript.Arguments(2))
CreateObject("WScript.Shell").Popup body, sec, title, 64
