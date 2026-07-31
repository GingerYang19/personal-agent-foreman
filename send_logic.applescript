-- 监工台发话注入逻辑（可热更新，改动无需重建 SendHelper.app）
--
-- 由 SendHelper.app 在其自身进程内 load script + run 执行，因此继承 SendHelper 的
-- 辅助功能/自动化授权；修改本文件只需 osacompile 重新生成 send_logic.scpt，
-- 不触碰 App 二进制，用户授权保持有效。
--
-- 入参 ~/.personal-hub/send_task.txt：
--   第1行 = 应用名（tell application 用）
--   第2行 = 粘贴前聚焦输入框的 Cmd 快捷键，空则跳过
--   第3行 = 可选，"activate" 表示仅激活应用不注入按键
-- 出参 ~/.personal-hub/send_result.txt：ok 或 ERR: 描述
--
-- 注：按键直发前台应用而不 tell process —— Electron 应用(如 Qoder)的
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

		if modeFlag is "activate" then
			do shell script "echo ok > " & quoted form of resultFile
			return
		end if

		-- 轮询等待目标应用出现可见窗口：深链新建窗口/冷启动耗时不定，
		-- 固定延时会让粘贴落空。最多等 10 秒，末次错误据实回报。
		set winReady to false
		set lastErr to ""
		repeat 40 times
			try
				tell application "System Events"
					set matched to (every application process whose name is appName)
					if (count of matched) is 0 then
						set matched to (every application process whose frontmost is true)
					end if
					if (count of matched) > 0 then
						if (count of windows of (item 1 of matched)) > 0 then set winReady to true
					end if
				end tell
			on error e
				set lastErr to e
			end try
			if winReady then exit repeat
			delay 0.25
		end repeat

		if not winReady then
			if lastErr is not "" then
				do shell script "echo " & quoted form of ("ERR: " & lastErr) & " > " & quoted form of resultFile
			else
				do shell script "echo " & quoted form of ("ERR: " & appName & " 无可见窗口，注入取消（消息已在剪贴板）") & " > " & quoted form of resultFile
			end if
			return
		end if

		-- 窗口出现后给前端渲染与输入框自动聚焦留时间
		delay 1.2

		tell application "System Events"
			if preKey is not "" then
				keystroke preKey using command down
				delay 0.6
			end if
			keystroke "v" using command down
			delay 0.5
			key code 36
		end tell
		do shell script "echo ok > " & quoted form of resultFile
	on error errMsg
		do shell script "echo " & quoted form of ("ERR: " & errMsg) & " > " & quoted form of resultFile
	end try
end run
