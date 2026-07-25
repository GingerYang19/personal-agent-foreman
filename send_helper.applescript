-- SendHelper: 监工台按键注入助手
-- 由 server.py 通过 `open -W -a SendHelper.app` 启动（LaunchServices 使其成为独立 TCC 责任进程，
-- 规避 launchd 后台服务的 Platform Binary 授权限制）。
-- 参数经 ~/.personal-hub/send_task.txt 传入（第1行=进程名，第2行=粘贴前聚焦快捷键或空），
-- 结果写回 ~/.personal-hub/send_result.txt（ok 或 ERR: 描述）。

on run
	set hubDir to (POSIX path of (path to home folder)) & ".personal-hub/"
	set taskFile to hubDir & "send_task.txt"
	set resultFile to hubDir & "send_result.txt"
	try
		set taskData to read POSIX file taskFile as «class utf8»
		set lns to paragraphs of taskData
		set procName to item 1 of lns
		set preKey to item 2 of lns
		tell application procName to activate
		delay 0.5
		tell application "System Events" to tell process procName
			if preKey is not "" then
				keystroke preKey using command down
				delay 0.5
			end if
			keystroke "v" using command down
			delay 0.4
			key code 36
		end tell
		do shell script "echo ok > " & quoted form of resultFile
	on error errMsg
		do shell script "echo " & quoted form of ("ERR: " & errMsg) & " > " & quoted form of resultFile
	end try
end run
