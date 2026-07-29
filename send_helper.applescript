-- SendHelper: 监工台按键注入助手
-- 由 server.py 通过 `open -W -a SendHelper.app` 启动（LaunchServices 使其成为独立 TCC 责任进程，
-- 规避 launchd 后台服务的 Platform Binary 授权限制）。
-- 参数经 ~/.personal-hub/send_task.txt 传入（第1行=应用名，第2行=粘贴前聚焦快捷键或空，
-- 第3行可选=activate 表示仅激活应用不注入按键），
-- 结果写回 ~/.personal-hub/send_result.txt（ok 或 ERR: 描述）。
-- 注: launchd 后台服务无法直接抖焦点，故跳转置前也经本助手 activate。
-- 按键直发给前台应用，不 tell process —— Electron 应用(如 Qoder)的
-- System Events 进程名是 "Electron"，与应用名不一致，按进程名定位会失败。

on run
	set hubDir to (POSIX path of (path to home folder)) & ".personal-hub/"
	set taskFile to hubDir & "send_task.txt"
	set resultFile to hubDir & "send_result.txt"
	try
		set taskData to read POSIX file taskFile as «class utf8»
		set lns to paragraphs of taskData
		set appName to item 1 of lns
		set preKey to item 2 of lns
		set modeFlag to ""
		if (count of lns) ≥ 3 then set modeFlag to item 3 of lns
		tell application appName to activate
		delay 0.8
		if modeFlag is not "activate" then
			tell application "System Events"
				if preKey is not "" then
					keystroke preKey using command down
					delay 0.5
				end if
				keystroke "v" using command down
				delay 0.4
				key code 36
			end tell
		end if
		do shell script "echo ok > " & quoted form of resultFile
	on error errMsg
		do shell script "echo " & quoted form of ("ERR: " & errMsg) & " > " & quoted form of resultFile
	end try
end run
