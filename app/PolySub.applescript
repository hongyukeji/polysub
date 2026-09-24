-- PolySub 拖放 App（由 install.sh 编译到项目目录下的 PolySub.app）
-- 拖放视频或文件夹：按 config.sh 里的默认语言加入队列
-- 双击：选视频，再选字幕语言
property langs : {"zh-Hans  简体中文", "zh-Hant  繁體中文", "en  English", "ja  日本語", "ko  한국어", "fr  Français", "de  Deutsch", "es  Español"}

on enqueue(theItems, lang)
	set args to ""
	if lang is not "" then set args to " -t " & quoted form of lang
	repeat with f in theItems
		set args to args & " " & quoted form of POSIX path of f
	end repeat
	do shell script "nohup \"$HOME/.local/bin/polysub-queue\"" & args & " >/dev/null 2>&1 &"
end enqueue

on open theItems
	enqueue(theItems, "")
end open

on run
	set picked to choose file with prompt "选择要生成字幕的视频（可多选）" with multiple selections allowed
	set choice to choose from list langs with prompt "字幕语言：" default items {item 1 of langs}
	if choice is false then return
	set lang to first word of (item 1 of choice)
	if (item 1 of choice) starts with "zh-" then set lang to text 1 thru 7 of (item 1 of choice)
	enqueue(picked, lang)
end run
