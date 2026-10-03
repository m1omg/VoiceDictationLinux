-- Dictate.app: starts dictation (Python) and stays open while it runs, so macOS asks for the
-- microphone, Accessibility and Input Monitoring in the name of "Dictate" (the app that started it).
-- Built by install.sh with osacompile; Info.plist gets LSUIElement (no Dock icon) and a microphone text.
property pyPID : ""

on appDir()
	return POSIX path of (path to application support folder from user domain) & "dictate"
end appDir

on run
	set cmd to "cd " & quoted form of appDir() & " && exec ./venv/bin/python -X utf8 dictate.py >> dictate.log 2>&1"
	set pyPID to do shell script "(" & cmd & ") > /dev/null 2>&1 & echo $!"
end run

on idle
	if pyPID is not "" then
		try
			do shell script "kill -0 " & pyPID
		on error
			quit -- dictation ended ("Stop dictation" in the menu)
		end try
	end if
	return 5
end idle

on reopen
	-- Opened again (e.g. from Spotlight) while running: show the big settings window.
	do shell script "cd " & quoted form of appDir() & " && (./venv/bin/python -X utf8 bigui.py settings > /dev/null 2>&1 &)"
end reopen

on quit
	if pyPID is not "" then
		try
			do shell script "kill " & pyPID
		end try
	end if
	continue quit
end quit
