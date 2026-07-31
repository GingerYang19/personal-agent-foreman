-- SendHelper: 监工台按键注入助手（稳定外壳，请勿修改本文件）
--
-- 设计要点：本 App 是 TCC 责任进程，辅助功能/自动化授权绑定其二进制哈希。
-- 任何改动都会使授权失效并需用户重新勾选，故把真实逻辑外置到 send_logic.scpt，
-- 由本壳在自身进程内 load + run —— 逻辑更新无需重建本 App，授权得以长期保留。
--
-- 由 server.py 通过 `open -W [-g] -a SendHelper.app` 启动。
-- 参数经 ~/.personal-hub/send_task.txt 传入，结果写回 send_result.txt。

on run
	set hubDir to (POSIX path of (path to home folder)) & ".personal-hub/"
	set resultFile to hubDir & "send_result.txt"
	try
		set logicScript to load script (POSIX file (hubDir & "send_logic.scpt"))
		run logicScript
	on error errMsg
		do shell script "echo " & quoted form of ("ERR: " & errMsg) & " > " & quoted form of resultFile
	end try
end run
