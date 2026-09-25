-- PolySub 拖放 App（由 install.sh 编译到项目目录下的 PolySub.app）
-- 双击：打开图形界面
-- 拖放视频或文件夹：按配置里的默认字幕语言加入后台队列（不打开窗口）
on open theItems
	set args to ""
	repeat with f in theItems
		set args to args & " " & quoted form of POSIX path of f
	end repeat
	do shell script "nohup \"$HOME/.local/bin/polysub-queue\"" & args & " >/dev/null 2>&1 &"
end open

on run
	do shell script "nohup \"$HOME/.local/bin/polysub\" gui >/dev/null 2>&1 &"
end run
