-- Dictate.app: starts dictation (Python) and stays open while it runs, so macOS asks for the
-- microphone, Accessibility and Input Monitoring in the name of "Dictate" (the app that started it).
-- Built by install.sh with osacompile; Info.plist gets LSUIElement (no Dock icon) and a microphone text.
property pyPID : ""

on appDir()
	return POSIX path of (path to application support folder from user domain) & "dictate"
end appDir

on startDictation()
	set cmd to "cd " & quoted form of appDir() & " && exec ./venv/bin/python -X utf8 dictate.py >> dictate.log 2>&1"
	set pyPID to do shell script "(" & cmd & ") > /dev/null 2>&1 & echo $!"
end startDictation

on dictationRunning()
	if pyPID is "" then return false
	try
		do shell script "kill -0 " & pyPID
		return true
	on error
		return false
	end try
end dictationRunning

on run
	startDictation()
end run

on idle
	if not dictationRunning() then quit -- dictation ended ("Stop dictation" in the menu)
	return 5
end idle

on reopen
	-- Opened again (Spotlight, Finder, the installer) while running: show the big settings window;
	-- if dictation ended a moment ago (an update stops it), start it again instead.
	if dictationRunning() then
		do shell script "cd " & quoted form of appDir() & " && (./venv/bin/python -X utf8 bigui.py settings > /dev/null 2>&1 &)"
	else
		startDictation()
	end if
end reopen

on quit
	if dictationRunning() then
		do shell script "kill " & pyPID
	end if
	continue quit
end quit
