# First tests on a real computer

Short checklists for the first tests by a person, one per system. Each takes 10–15 minutes.
When something doesn't work, note the step and send the log (where it is is said below).

## Getting the version to test
Until it is merged, the new version is on a branch. Download it as a ZIP:
<https://github.com/m1omg/VoiceDictationLinux/archive/refs/heads/claude/admiring-volta-k5eqtk.zip>.
It unpacks into the folder `VoiceDictationLinux-claude-admiring-volta-k5eqtk`; run the installer
from there, as the README describes for your system.

## Linux (GNOME on Wayland, and Cinnamon on X11)
Install over the current version: `bash install.sh` in the unpacked folder. It keeps your settings
and models; Enter accepts each suggestion.
1. In a text editor: hold numpad Del, say a sentence, release: the text is typed, as before. Then
   tap, speak, tap.
2. The top-bar menu: *Main model ▸*, *Run on ▸* and *Large text ▸* open as submenus.
3. *Main model → base*: a notification announces the download (145 MB), the menu shows its
   progress, then it switches; dictate once. Then switch back to large-v3-turbo.
4. *Run on → Processor*: dictate once (slower). Then back to *Graphics card*.
5. *Settings window… → Change it…*, press Ctrl+Alt+D. On GNOME, approve the new shortcut. Dictate
   with Ctrl+Alt+D held, in the editor and in a terminal. Then *Use numpad Del again*.
6. *Large text → Big status panel*: dictate. The panel shows *Listening* and then *Typed*, and
   the text still lands in the editor (the panel doesn't take the focus).
7. The settings window with the keyboard only: Tab and the arrow keys move the thick frame, Space
   chooses, Esc closes.

Log: `journalctl --user -u dictate -n 50` (GNOME) or `~/.local/share/dictate/dictate.log`
(Cinnamon).

## Windows 10 or 11
Install: double-click `install.cmd` in the unpacked folder (no administrator rights needed). Enter
accepts each suggestion. Depending on the graphics card it downloads about 0.5–4 GB.
1. In Notepad: hold the dictation key (numpad Del, or Right Ctrl on a laptop), say a sentence,
   release: the text is typed. Then tap, speak, tap.
2. The same in Windows Terminal and in a text field in a browser.
3. If the key is Right Ctrl: Right Ctrl+C still copies.
4. Win+V: the dictated text is not in the clipboard history, and what you had copied before is
   back on the clipboard.
5. Notepad started with *Run as administrator*: the text is not typed there; a notification says
   it is on the clipboard, and Ctrl+V pastes it.
6. Lock the screen (Win+L) and unlock it; put the computer to sleep and wake it: dictation still
   works.
7. The microphone icon's menu (next to the clock, maybe under the **^** arrow): switch the speech
   model and *Run on*. With an NVIDIA graphics card, or an AMD RX 6800/6900, 7000 or 9000 series
   one, *Graphics card* should be available.
8. *Large text → Big status panel*: dictate into Notepad; the panel appears, and the text still
   lands in Notepad.
9. Restart Windows: dictation starts by itself (the icon appears).

Log: `%LOCALAPPDATA%\dictate\dictate.log`. Please also send what this prints, in a Command Prompt
(it names the graphics card and what runs on it):
```
%LOCALAPPDATA%\dictate\venv\Scripts\python.exe %LOCALAPPDATA%\dictate\dictate.py --check
```

## MacBook Air (2017, macOS 12 or older)
Install: in Terminal, `cd` into the unpacked folder (in Downloads) and run `bash install.sh`. The
suggestions for this Mac are the *base* model for English (*small* for Slovak) and Right Option as
the key.
1. macOS asks to let **Dictate** use the microphone, and to allow it under Accessibility and Input
   Monitoring (System Preferences → Security & Privacy → Privacy). Allow all three; the dialogs
   should name *Dictate*, not Python or Terminal.
2. In TextEdit: hold Right Option, say a sentence, release: the text is typed. Then tap, speak,
   tap.
3. Left Option still types its characters (on a Slovak layout Option+2 types @). Right Option
   with another key works as before, and that dictation is dropped.
4. Copy something before dictating: after the text is typed, it is back on the clipboard.
5. Speed: in Terminal, `tail -f ~/Library/"Application Support"/dictate/dictate.log` shows
   `transcribed … in X s` after each dictation. Note X for *base* and for *small* (switch in the
   menu bar menu, *Main model*).
6. *Large text → Big status panel*: dictate into TextEdit; the panel appears, and the text still
   lands in TextEdit.
7. Log out and in again: dictation starts by itself (the icon appears in the menu bar).

Log: `~/Library/Application Support/dictate/dictate.log`.
