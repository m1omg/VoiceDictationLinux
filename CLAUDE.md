# Claude Code: setting up Voice Dictation on this computer

You are helping someone install push-to-talk dictation from this repository: hold the dictation key
(numpad Del by default), speak, release, and the text is typed into the focused app. README.md
describes the features; this file is your playbook for installing, adapting and verifying it, on
Linux, Windows and macOS. It works out of the box on GNOME Wayland with an NVIDIA GPU and on
Cinnamon X11 (LMDE 7) with an AMD GPU. Windows and macOS pass automated tests on GitHub's machines
(processor only) but haven't been used by a person yet; other Linux desktops have code paths that
still need a first real test.

## Ground rules
- **Dictation may be an accessibility need.**
  - Keep the user's typing to a minimum: ask with multiple-choice questions, and ask everything
    you need up front (the installer's four questions are below; pass the answers as variables).
  - Then work autonomously.
  - Say in advance whether and when you will need them at the computer.
- **Live tool output isn't visible to them until a command finishes.** Never run "speak now"
  tests that depend on their timing: `--selftest` starts recording as soon as it launches, so
  warn them before you run it.
- **Be careful with their home directory.** The installer writes only:
  - Linux: `~/.local/share/dictate`, `~/.config/dictate`, `~/.local/state/dictate`, two launchers
    in `~/.local/share/applications`, and a systemd unit or an autostart entry;
  - Windows: `%LOCALAPPDATA%\dictate`, two Start menu shortcuts and one in the Startup folder;
  - macOS: `~/Library/Application Support/dictate`, `~/Applications/Dictate.app` and a LaunchAgent.

  Don't edit or delete the user's other files without asking.
- **No windows or clipboard in tests while they work.** Don't pop up focus-stealing test windows
  or take over the clipboard. The unit tests and benchmarks in `tests/` are safe: no windows,
  microphone, clipboard or keystrokes. `x11_paste.py`, `bigui_test.py` and `e2e_x11.py` use a
  private Xvfb. `e2e_desktop.py` and `windows_paste.py` (Windows, macOS) open a window and take the
  focus: run them only when the user agrees to leave the computer alone for a minute.
- **Root and administrator commands are typed by the user** (suggest `! sudo …`). Keep them to a
  minimum; Windows and macOS need none.
- **Don't `pkill -f` a pattern that also appears in your own command line;** it kills your own
  shell. Use PIDs.
- **macOS: don't rebuild or re-sign `Dictate.app` without need.** macOS gives the permissions to
  that app; a re-signed app silently loses Accessibility. install.sh rebuilds it only when
  `packaging/macos/Dictate.applescript` changes.

## 1. Survey (read-only)
**Linux**
```bash
echo "$XDG_SESSION_TYPE / $XDG_CURRENT_DESKTOP"; grep PRETTY /etc/os-release; uname -m
lspci | grep -iE 'vga|3d'; nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
grep -h gfx_target_version /sys/class/kfd/kfd/topology/nodes/*/properties   # AMD: 100301 = gfx1031
getfacl /dev/kfd 2>/dev/null | grep user:                                    # AMD: ROCm needs access
ls -l /dev/uinput; getfacl /dev/uinput 2>/dev/null | grep user:
command -v pw-record parecord notify-send curl
systemctl --user is-active graphical-session.target          # active → systemd service, else autostart
busctl --user list | grep -i StatusNotifierWatcher           # tray host present?
```
**Windows** (PowerShell)
```powershell
[Environment]::OSVersion.VersionString; $env:PROCESSOR_ARCHITECTURE
Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores; (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB
```
**macOS**
```bash
sw_vers; uname -m; sysctl -n machdep.cpu.brand_string hw.physicalcpu hw.memsize
```

## 2. Decide
| Found | Means |
|---|---|
| GNOME, Wayland | backend `wayland`; tested path |
| KDE Plasma, Wayland (e.g. **Bazzite**) | backend `wayland`; untested; see "KDE" below |
| Cinnamon / XFCE / MATE, X11 (e.g. **LMDE**, Mint) | backend `x11`; tested on Cinnamon; see "X11" below |
| Sway / Hyprland / other wlroots | not supported yet (no GlobalShortcuts portal); see "wlroots" |
| Windows 10/11 x64 | `install.cmd`; see "Windows" below |
| macOS 11+ (Intel or Apple silicon) | `bash install.sh`; processor only; see "macOS" below |
| NVIDIA GPU and driver (Linux, Windows) | large-v3-turbo on CUDA, live typing available |
| AMD RX 6000 or newer on Linux (gfx103x, gfx110x, gfx115x, gfx120x) | large-v3-turbo on ROCm, live typing available; ~3.5 GB more download |
| AMD on Windows | only gfx1030 (RX 6800/6900), gfx1100-1102 (RX 7000), gfx1150/1151, gfx1200/1201 (RX 9000): no `HSA_OVERRIDE_GFX_VERSION` there; others run on the processor |
| AMD, but `/dev/kfd` not accessible (Linux) | user adds the udev rule (README → Troubleshooting); works at once |
| No usable GPU (Intel, older AMD, Mac, none) | processor: see "Choosing a model" |
| Neither `pw-record` nor `parecord` (Linux) | user installs one. Debian/LMDE: `sudo apt install pipewire-bin` or `pulseaudio-utils`. Bazzite already has PipeWire. |
| `/dev/uinput` not writable (Wayland only) | user adds a udev rule (README → Troubleshooting) and reboots |

Immutable systems (Bazzite, Silverblue): the installer needs no system packages. Avoid
`rpm-ostree install` unless something is really missing.

**Choosing a model** (processor, int8, `cpu_beam_size = 2`; a 4-core Xeon at 2.1 GHz, per ~9 s
FLEURS sentence; README → Accuracy and speed has the per-OS numbers):

| Model | English WER | Slovak WER | Time |
|---|---|---|---|
| tiny | 8.8 % | 78 % | 0.5 s |
| base | 5.5 % | 72 % | 0.7 s |
| small | 4.5 % | 34 % | 1.7–2.1 s |
| medium | 3.2 % | 15 % | 3.8–4.8 s |
| large-v3-turbo | 3.9 % | 7.0 % | 3.9–4.0 s |

English is fine from base up; Slovak needs large-v3-turbo (small gets a third of the words wrong),
or a model fine-tuned for Slovak beside the general one (KInIT's `small-sk`: 1.5 % on FLEURS at
1.6 s per sentence on a 6-core desktop processor; see "Models fine-tuned for Slovak" below).
Offer the trade-off to the user: speed or accuracy. A 2-core laptop (2017 MacBook Air) hasn't been
measured and will be slower; `tests/bench_asr.py MODEL --cpu` measures it on their machine.

## 3. Install
Ask the installer's questions up front, in one multiple-choice round: the interface language
(English / Slovenčina: installer, menus, messages), the dictation language (English / Slovak /
both), model (see above), key (numpad Del; Right Ctrl on laptops; Right Option on Macs; or a
combination such as Ctrl+Alt+D), large text (off / big status panel / panel and big settings
window). Then run it without questions, in the background (the first run downloads 0.5–4 GB):
```bash
DICTATE_UI_LANGUAGE=sk DICTATE_LANGUAGE=sk DICTATE_MODEL=large-v3-turbo DICTATE_KEY=KP_Delete DICTATE_LARGE_UI=off bash install.sh
```
```powershell
$env:DICTATE_UI_LANGUAGE="en"; $env:DICTATE_LANGUAGE="en"; $env:DICTATE_MODEL="small"; $env:DICTATE_KEY="Control_R"
$env:DICTATE_LARGE_UI="off"; powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
```
- Without `DICTATE_UI_LANGUAGE` and without a terminal, the installers take the saved choice, else
  English for an install from before the question existed (`DICTATE_KEEP=keep`), else the system's
  language. With a terminal they ask first (`Language / Jazyk`), in both languages.
- It's safe to re-run: it keeps the settings file, the choices (`DICTATE_KEEP=keep` skips the
  "keep your choices?" question) and the models.
- `DICTATE_NO_AUTOSTART=1` skips start-at-login while testing. Otherwise the installers run
  `dictate.py --start-at-login=saved`: on, unless the menu's *Start at login* was switched off
  (`state.json` "autostart").
- It ends by starting dictation and printing `--check`.

## 4. Verify without the user
**Linux**
```bash
D=~/.local/share/dictate
$D/venv/bin/python $D/dictate.py --check        # every line should say ok (info lines are fine)
python3 tests/fetch_fleurs.py
$D/venv/bin/python tests/bench_asr.py           # GPU targets: en ~4 % WER, sk ~6-7 %, ~0.35-0.5 s per sentence
$D/venv/bin/python tests/bench_live.py          # GPU targets: live WER ≈ one-shot; first words ~2 s (en)
for t in unit_keys unit_models unit_audio unit_portability unit_windows unit_macos unit_topbar unit_setup unit_paster unit_switch \
         unit_capture unit_login unit_i18n unit_popup unit_websettings unit_update unit_convert unit_qwen
do $D/venv/bin/python tests/$t.py; done
dbus-run-session -- $D/venv/bin/python tests/unit_tray.py
XVFB=… $D/venv/bin/python tests/x11_paste.py   # X11 desktops: key grab, combinations, repeats, paste, clipboard
XVFB=… $D/venv/bin/python tests/bigui_test.py  # large text: panel, focus, settings window by keyboard
XVFB=… $D/venv/bin/python tests/e2e_x11.py     # a whole dictation with a stand-in recorder
journalctl --user -u dictate -n 30              # or: tail $D/dictate.log  (autostart desktops)
```
**Windows:** the same scripts with `%LOCALAPPDATA%\dictate\venv\Scripts\python.exe -X utf8`; the
log is `%LOCALAPPDATA%\dictate\dictate.log`. **macOS:** `~/Library/Application Support/dictate`.
`--check` reports `FAIL microphone` on a machine without one (CI).

**GitHub Actions:** `.github/workflows/platforms.yml` runs all of this on Windows, macOS (Intel and
Apple silicon) and Ubuntu: non-interactive install with tiny, `--check`, unit tests, the copy the
installer started (and stopping it), FLEURS accuracy and speed for the models in its `models`
input, `windows_paste.py`, and `e2e_desktop.py` / `e2e_x11.py`. It is started by hand (Actions →
"Windows, macOS and Linux" → Run workflow) once it is on the default branch.

## 5. Verify with the user (one short session; announce it first)
1. **Linux Wayland:** the first start shows the desktop's shortcut dialog, which they approve.
   **macOS:** they allow Dictate under Microphone, Accessibility and Input Monitoring (README).
   **X11, Windows:** nothing to approve.
2. They open a text editor, hold the key, say a sentence, and release. Check the log for
   `transcribed … in … s` and `typed; release -> done … s`. Then a tap, a sentence, and a second
   tap: the log says `tapped: dictating until the next press`.
3. Repeat in a terminal. Check that the key itself no longer reaches the app (numpad Del types no
   "." and deletes nothing; Right Ctrl/Option alone does nothing).
4. With *Type while speaking* on and a longer sentence, words should appear while the key is
   held. The log says `first live words typed X s after the key went down`.
5. Check that the tray icon is visible and its menu switches English / Slovenčina / Auto, the
   main model and graphics card / processor.
6. **A lone-modifier key** (Right Ctrl, Right Option): a shortcut with it still works (Right
   Ctrl+C copies; on a Slovak Mac layout Right Option+2 types @); the log says the dictation was
   cancelled.
7. **Large text**, if they use it: the panel appears while dictating without taking the focus (the
   text still lands in the editor); the settings window works with Tab, arrows, Space and Esc.
8. **Windows:** an app run as administrator gets the text on the clipboard with a notification;
   Win+V clipboard history doesn't show dictated text. **macOS:** the earlier clipboard comes back.

## Platform paths: what to check and how to fix
- **X11** (`X11Hotkey`, `XTestKeyboard` and `SelectionOwner(bridged=False)` in dictate.py), tested
  on LMDE 7 with Cinnamon 6.6:
  - The key is grabbed on the root window with the combination's modifiers, for all NumLock and
    CapsLock combinations.
  - The grab is released at once, so our own Shift+Insert reaches the app during the hold. (The
    key's release then reaches the app too; that stray KeyRelease is harmless.)
  - The release is detected by polling `query_keymap()`. A lone modifier (Right Ctrl) held with
    another key is a shortcut: the controller gets `cancel` and drops the recording.
  - Auto-repeat is turned off for that one key, again on every XI2 HierarchyChanged event (a
    keyboard that is plugged in, or comes back after a suspend, starts out repeating), and again
    as soon as a repeat arrives. Each repeat re-activates the grab: one long hold used to become
    ~20 dictations, and live typing's paste was swallowed.
  - **"another program already uses KP_Delete":** unbind the key in the desktop's keyboard
    settings. Under muffin a core grab can succeed even when the window manager holds the key
    through an XI2 grab, so don't rely on that error alone.
  - `tests/x11_paste.py` checks all of this in a private Xvfb, without touching the user's
    screen. LMDE lacks Xvfb, but `apt-get download xvfb && dpkg -x xvfb_*.deb root` gives a working
    binary without root.
  - If XTEST is missing, install the X server's XTEST support (standard on Xorg).
  - **Clipboard:** apps ask our process directly; there's no compositor bridge to wait for.
  - **Tray:** Cinnamon shows StatusNotifierItems through xapp-sn-watcher, but labels may not
    appear. That's cosmetic.
- **KDE Plasma Wayland (Bazzite):**
  - Same portal flow as GNOME. KDE shows its own approval dialog; `Deactivated` (key release) is
    supported in Plasma 6.
  - `SelectionOwner(bridged=True)` waits up to 0.4 s for the compositor to fetch `TARGETS` after
    we take the X11 selections. If the log keeps saying "the clipboard handoff could not be
    confirmed", KWin isn't fetching them eagerly: construct it with `bridged=False` when
    `XDG_CURRENT_DESKTOP` contains KDE, and re-test.
  - `/dev/uinput` is usually writable on Bazzite (Steam udev rules).
- **wlroots (Sway, Hyprland):**
  - There's no GlobalShortcuts portal. The cleanest port is to bind the key in the compositor
    (`bindsym --release` exists in Sway, `bindr` in Hyprland) to a tiny client that tells the
    daemon "press" or "release" over a Unix socket. Feed those into `Controller.emit`, the same
    path the portal uses.
  - Typing through uinput and the XWayland clipboard should work as on GNOME.
- **Windows** (`windows.py`, ctypes only):
  - The key: a `WH_KEYBOARD_LL` hook on its own thread. Numpad Del is scan code 0x53 without the
    extended flag (the Delete key has it). The callback only decides and queues; Windows silently
    drops slow hooks, so the hook is renewed every 60 s while the key is up. Keys other programs
    inject count (remappers, on-screen keyboards); only ours, marked `dwExtraInfo = MARK`, pass.
  - Which modifiers are down comes from `GetAsyncKeyState` (the hook misses releases, e.g. after
    Win+L). A release the hook missed is inferred when the key's repeats stop for 1.5 s. A
    combination with Alt or Win sends the unassigned key 0xE8 before it is swallowed, so Windows
    doesn't open the window menu or Start.
  - A lone modifier held with another key: the hook cancels and replays the modifier and the key.
  - Typing: Shift+Insert through one `SendInput` call (`sizeof(INPUT)` must be 40; unit-tested).
  - Clipboard: a message-only window with delayed rendering, so `WM_RENDERFORMAT` tells us the app
    fetched the text. If nothing fetches it, the app may be elevated (UIPI drops our keys): the
    text is left on the clipboard. Our text is marked out of clipboard history and cloud sync.
  - One copy per session (`Local\io.github.m1omg.dictate` mutex); starting it again opens the
    settings window. Under `pythonw.exe` there is no stderr: it logs to `dictate.log`.
  - GPU: cuBLAS (`cublasLt64_12.dll`, then `cublas64_12.dll`) and ROCm (`amdhip64_7.dll`,
    `hipblas.dll`) are preloaded by path; models go to CTranslate2 as 8.3 short paths when the
    profile path isn't ASCII. The NVIDIA and AMD paths haven't run on real hardware yet.
  - `install.ps1` is ASCII and runs on Windows PowerShell 5.1: stderr of native programs under
    `$ErrorActionPreference = "Stop"` becomes an error (hence `Invoke-Quiet`), TLS 1.2 is switched
    on, and a running copy is stopped first because Windows keeps its DLLs locked. If
    `import ctranslate2` fails, the Visual C++ runtime from onnxruntime is copied next to it.
- **macOS** (`macos.py`, pyobjc):
  - Permissions belong to `Dictate.app`, a stay-open AppleScript applet (`osacompile -s`) that
    starts Python and quits when it ends; macOS asks in its name. Its Info.plist gets the bundle id,
    `LSUIElement` and the microphone text (osacompile's template already has that key: `Set`, else
    `Add`). It is ad-hoc signed once; a stamp file records the script's checksum.
  - The key: a `CGEventTap` on its own run loop. An active tap needs Accessibility (and Input
    Monitoring to see keys); without them the tap can't be created: the tray shows a problem, the
    right Settings pane opens, and it retries every 3 s. Modifiers come as `flagsChanged` events;
    left and right are told apart by device bits (Right Option 0x40).
  - Typing: Cmd+V from a private event source with explicit flags (a held Option doesn't turn it
    into Cmd+Option+V), marked so our own tap ignores it. The pasteboard text is marked transient
    and kept off Universal Clipboard (`NSPasteboardContentsCurrentHostOnly`).
  - A key without a Mac keycode (Pause, Menu) falls back to Right Option, with a notification.
    When macOS disables the tap (a slow callback, secure input), it is re-enabled, and a release
    missed meanwhile is caught with `CGEventSourceKeyState`.
  - The menu bar icon is a template image (it follows light and dark menu bars); pystray's
    `_assert_image` is replaced for that, so check it after a pystray upgrade.
  - pystray needs the main thread (AppKit): the controller runs on another thread, and SIGTERM
    (the applet quitting) arrives through `PyObjCTools.MachSignals`.
  - Intel Macs: Python 3.12 and `onnxruntime<1.20` (the last x86_64 wheels for macOS 11–12).
  - Don't quit the app with `osascript -e 'quit app "Dictate"'` in automation: it waits for an
    Automation permission. `pkill -TERM -f "venv/bin/python -X utf8 dictate.py"` stops Python, and
    the applet quits within 5 s.
- **Large text** (`bigui.py`, Tk): the panel and the settings window are separate processes. Text
  is drawn with Pillow (FreeType) into images, because the private Python's Tk on Linux has no Xft
  and falls back to tiny bitmap fonts. Marks (dot, tick, radio, check box) are drawn as shapes:
  many fonts lack ● ○ ☑ ✓.
  - The panel never takes focus: override-redirect and an empty input shape on X11;
    `WS_EX_NOACTIVATE|TOOLWINDOW|TRANSPARENT` on Windows, where the window is mapped once,
    off-screen, when the panel starts (with dictation) and is only moved after that, since a Tk
    window activates whenever it is shown; an AppKit `NSPanel` with
    `NSWindowStyleMaskNonactivatingPanel` on macOS (a Tk window brings its app to the front). All
    its timing uses `after()` or `callLater` with wall-clock delays, never frames.
  - The settings window's buttons are `tk.Label`s with two pictures each (with and without the
    focus frame), so they look the same on every system (Aqua ignores most `tk.Button` options).
    It polls `state.json` and `dictate-status.json` every second and rebuilds when what it shows
    changes. On a Retina Mac its pictures are scaled up by Tk (slightly soft); the panel is drawn
    at the screen's pixel density.
  - Key capture reads the key's code where Tk's key name is unreliable: on Windows the virtual-key
    code (`event.keycode`) and the extended flag (0x40000 in `event.state`), because Tk names the
    numpad by its character, and with NumLock off its Del is Delete without the extended flag; on
    macOS the keycode in the top byte (`event.keycode >> 24`), because Tk names Command `Meta_L`
    and fn `Super_L`, and Option changes letters (∂). Windows repeats held modifiers: a key
    already down is ignored.

## How it works (for debugging)
- **The model stays loaded.** The `Worker` thread loads it at start-up and keeps it. Never load the
  model per key press; that's what makes dictation tools feel slow.
- **Models and devices.** The menu's choices (`UiState`, `state.json`) say which model runs on the
  graphics card and which on the processor, and where to run. `Worker.want()` queues a load; it
  happens on the worker thread when nothing is being dictated (a dictation during a load waits for
  it). At start-up a chosen model that isn't downloaded (dictation restarted during its download)
  is replaced by an installed one, then downloaded. The old model is freed first (`release_memory()`: `gc` and glibc's `malloc_trim`, or memory
  grows with each switch). A missing model is downloaded by a `dictate.py --download NAME`
  subprocess (`ModelSwitch`); `models.download()` fills `models/.partial-NAME`, checks every file,
  then renames it. The GPU ladder tries the compute types CTranslate2 reports (float16,
  int8_float16, int8, float32); a failed or hung GPU load (`Watchdog.LOAD_LIMIT`) falls back to
  the processor.
- **Models fine-tuned for Slovak** (`models.SLOVAK`, KInIT's Whisper fine-tunes at pinned revisions):
  `state.json` "gpu_slovak" / "cpu_slovak" name one per device (or ""). `Transcriber.load()` loads
  it beside the general model with the same device and compute type. KInIT's models write plain
  lowercase words without punctuation (fine-tuned on normalized text; prompting doesn't bring it
  back), so `run(language="sk")` without word timings runs both models and `transfer_format()` puts
  the general model's punctuation and capitals on the Slovak model's words (difflib alignment:
  equal words keep the styled form, a replaced word takes over capitals and punctuation, a missing
  one leaves its sentence end). FLEURS sk: words 2.1 %, counting punctuation and capitals 5.3 %
  (turbo alone 6.4 % / 8.9 %), 0.7 s per sentence. Live passes (word timings) stay with the general
  model: in timestamp mode the fine-tunes drop even more and live typing found no sentence ends
  (a 60 s instant dictation went from 6.8 % to 18.2 %). `detect()` and every other language use the
  general model (their language detection is poor: base-sk 0/30, small-sk 12/30; English 70 %).
- **Qwen3-ASR for English** (`models.QWEN`, `state.json` "english_model", GPU only): `QwenEngine` runs
  `qwen_worker.py` with the Qwen environment's Python (`models.qwen_venv()`: a uv venv from the
  program's base Python with PyTorch from download.pytorch.org/whl/cu128, transformers 4.57.6,
  accelerate, soundfile, librosa, nagisa, and qwen-asr 0.0.6 `--no-deps`: its own list adds gradio
  and flask). Protocol: a ready line, then per pass a JSON line + float32 samples on stdin, a JSON
  answer on stdout (the worker moves library prints to stderr). `run(language="en")` without word
  timings sends `speech_only()` audio (VAD, so no long silences) with the vocabulary as `context`; a
  failed pass falls back to Whisper; a pass past `PASS_LIMIT` kills the process (a late answer
  would be taken for the next one). Only `platform == "cuda"`. Qwen isn't told the language: if it
  hears Czech/Slovak/Polish (`QWEN_SLOVAK`), the recording is done again as Slovak and
  `Transcriber.heard` tells `Worker._final_text` (s.language = "sk"); another language falls back to
  Whisper in English. RTX 3060, all loaded: en 2.6 % (0.51 s per sentence), sk 2.1 % (0.69 s),
  Slovak taken for English: 10/10 redone, 1.4 %. Qwen on the CPU: 9 s per sentence. The Qwen
  environment is ~4 GB to download, 7 GB on disk; the install retries a step once and cleans uv's
  cache (NVIDIA's server timed out once at uv's default 30 s). A choice is 6 parts (device, GPU model, CPU
  model, GPU Slovak, CPU Slovak, English); `ModelSwitch.plan()` compares what would run with what runs, and a
  missing Slovak model is downloaded while the general one keeps working without it. Download =
  KInIT's safetensors into `models/.partial-NAME-hf`, `convert.py` into `models/.partial-NAME`,
  `complete()`, rename. `convert.py` fills CTranslate2's `WhisperSpec` from numpy arrays the way
  its PyTorch-based `WhisperLoader` does: the output was byte for byte the same as
  `ct2-transformers-converter --quantization float16 --copy_files tokenizer.json
  preprocessor_config.json` for large-v3-turbo-sk (`tests/unit_convert.py` runs a tiny random one).
  FLEURS (GPU): turbo-sk sk 2.1 % vs turbo 6.4 %; FLEURS is in KInIT's training data, so prefer
  their held-out numbers for comparisons (turbo-sk 5.6 %, medium-sk 6.3 %, small-sk 8.6 %, base-sk
  14.0 % vs turbo 18.4 %).
- **The settings window** writes `state.json`; the daemon's `StateWatcher` polls its mtime every
  0.5 s and applies changes (a new key restarts the program). The daemon writes
  `RUNTIME_DIR/dictate-status.json` for the window, and the window's *Stop dictation* writes `quit`
  to `RUNTIME_DIR/dictate-command` (Linux with systemd: `systemctl --user stop`).
- **Keys** (`keys.py`): one trigger string (`KP_Delete`, `Control_R`, `Ctrl+Alt+D`) mapped to an
  X11 keysym and modifier mask, the portal's format, a Windows scan code / virtual key, and a macOS
  keycode or modifier bit. Letters, digits and typing keys alone are refused.
- **Typing is layout-independent.**
  - Text goes to CLIPBOARD and PRIMARY as `UTF8_STRING` only. Offering STRING as well corrupts
    accents on GNOME (mutter #5057).
  - Then Shift+Insert is pressed (Cmd+V on macOS). GUI apps paste CLIPBOARD; terminals (VTE,
    kitty, …) paste PRIMARY.
  - Before pressing it, the paster waits until Ctrl/Alt/Shift/Win are up (a combination key such
    as Ctrl+Alt+D would otherwise turn it into Ctrl+Alt+Shift+Insert).
  - Claude Code collapses pastes over 800 characters, so text is pasted in chunks of at most 750.
- **Live typing (`Streamer`, LocalAgreement).**
  - The window is the audio since the last committed sentence, re-transcribed every 0.4 s.
  - Commit the prefix that the last two passes agree on, minus its last word (unless that word
    ends a sentence and the speaker has paused for 0.8 s).
  - After 2 s of silence (`Worker.QUIET`, Silero VAD) live passes wait for speech, and the final
    pass skips the silence when nothing is pending. Whisper invents words for windows of silence,
    and identical passes over silence agree with each other, so they used to get typed (worst in
    tap mode, where people pause for long).
  - On the processor with live typing off, no preview passes run (they would delay the final one).
  - Auto-detect waits for 1.5 s of speech; `tests/bench_detect.py` shows Slovak is confidently
    misread as English below that. Real Slovak dictation was still read as English at 95-100 %
    after ~2 s, and whisper then translates. So when nothing was typed live, the final pass
    detects again on the whole recording (log: `language: sk after all`). With live typing, until
    the first word is typed every pass checks the language again (a flip restarts the streamer,
    which costs nothing as nothing was typed), and no word is typed before 3 s of speech
    (`Worker.AUTO_TYPE_AFTER`; settled words wait in `Session.held`).
  - A tap (shorter than `Controller.TAP`) latches the dictation only while the menu's *Tap to start
    and stop* is on (`state.json` "tap", config `tap_to_toggle`); off, a short press is just a
    short hold.
  - The status pop-up (`TopBar.popup_mode()`: "large" = `big_panel`, else `state.json` "popup"
    auto/on/off, auto = small while the loaded model runs on the processor, `Transcriber.on_cpu`,
    also after a graphics card failed): the same panel process
    (`bigui.py panel`) at about 16 px and 26 em wide ("size": "small"). While the whole recording
    is transcribed, `Worker._final` sends `working` = {"since": time.time(), "expected": pace x
    seconds}; `Worker.pace` (seconds of work per second of audio, an average over dictations of 2 s
    or more) starts over with every model load. The panel redraws itself every 0.5 s from that
    (`bigui.progress_view`): seconds, a bar, *About N s left*, *Almost done…*, *Taking longer
    than usual…* (past 2 x expected + 5 s); without an estimate, a piece travels along the bar.
    Live-typed dictations send no count (only their tail is transcribed) (`tests/unit_popup.py`).
  - Start at login (menu, settings window, installers): `login_item()` picks the systemd unit's
    enable link (when `~/.config/systemd/user/dictate.service` exists), the XDG autostart entry,
    the LaunchAgent, or the Startup-folder shortcut (a copy of the Start menu's `Dictate.lnk`);
    `starts_at_login()` reads the system every time (also the desktop's `Hidden=` /
    `X-GNOME-Autostart-enabled=false` and Windows' StartupApproved switch), so it can't drift
    from what the system does. Switching never stops or starts the running copy
    (`tests/unit_login.py`).
  - Instant typing (`state.json` "instant", config `instant_typing`; needs a keyboard with
    `backspace()`, i.e. uinput or XTEST, so not yet Windows/macOS): every live pass renders the
    whole text (committed + pending words, `render_words`) and `Worker._show` deletes only the end
    that differs from `Session.shown`, then types the rest (`PasteItem.delete`, `.full`). It never
    deletes more than the dictation typed. No 3 s hold in auto mode; instead the language is
    re-checked until `Worker.AUTO_SETTLED` (6 s) of speech and a flip restarts the streamer, which
    retypes; after release the whole recording may overrule the language once more
    (`_final_instant`). Measured: first words 1.5 s (en) / 1.9 s (sk); characters taken back ~20
    per English and ~90 per Slovak sentence; final WER equal to the safe mode
    (`tests/bench_live.py --quick`, `tests/unit_instant.py`).
  - Short clips in auto mode (< `Worker.SHORT` = 2 s of speech, `speech_total`): an "en" verdict
    keeps the previous dictation's language if that wasn't English (whisper takes short Slovak for
    English, rarely the reverse). Vocabulary hints are left out below 1 s of speech.
  - Bigger models: large-v3 and large-v2 are offered; on FLEURS neither beats large-v3-turbo for
    both languages (v3: 3.9 % / 6.6 %, v2: 3.2 % / 10.2 %, both ~1.5x slower).
  - No vocabulary hints on windows shorter than 2 s (Whisper echoes them).
  - Earlier, simpler variants were clearly worse (Slovak WER 15.9 %, invented words). Re-run
    `tests/bench_live.py` after any change.
- **Updates** (`update.py`, standard library only): *Check for updates* asks GitHub's API for main's
  newest commit and the git blob SHA-1 of its root files, and compares them with the installed
  program files (`PROGRAM`: what the installers copy), so it works however the program was
  installed (git, ZIP, an earlier update); files that differ count as older. *Install the update*
  runs `update.py --run SHA DATE` as a process of its own: from inside the systemd service
  (`/proc/self/cgroup`) through `systemd-run --user --unit=dictate-update`, since the installer's
  `systemctl --user restart` ends every process in the service; elsewhere detached (its name
  isn't matched by the installers' `pkill -f …dictate.py` or install.ps1's stop filter). It
  downloads `codeload.github.com/…/zip/SHA`, refuses paths outside its folder, backs up the
  program files (and the unit) to `previous/`, and runs that version's installer with
  `DICTATE_KEEP=keep` and the saved interface language, without a terminal. Success = a dictation
  with another pid reports a loaded model in `dictate-status.json` and is still there 5 s later
  (`READY_LIMIT` 300 s); otherwise the previous files come back (files the update added go) and
  dictation is restarted the installer's way. The outcome goes to `update-result.json`, which
  the running dictation's `StateWatcher` picks up and announces (the new version starts before
  the update knows it worked). It mustn't import the program's packages: on Windows the
  installer has to replace their files. The venv itself isn't rolled back. GitHub's API allows
  60 checks an hour per address. CI updates the Windows and macOS installs to the version under
  test (`update.py --run $GITHUB_SHA`); `tests/unit_update.py` stands in for GitHub on 127.0.0.1.
  The Linux/systemd path was tested live on the CachyOS PC (2026-10-07): the tray's clicks sent
  over D-Bus (dbusmenu `Event` 34, then 35) ran the update in `dictate-update.service`, which
  outlived the service's restart; new version ready 11 s after the click.
- **Interface language** (`i18n.py`): English is the source; `t("Sounds")` looks it up in `SK` while
  `state.json` "ui_language" is "sk". Values go in through placeholders (`t("Hold {key} to dictate",
  key=…)`, positional-only, so a placeholder may be called `text`), never f-strings: the table
  couldn't match them. Texts reaching `t()` through a variable (model descriptions, colour names,
  clipboard reasons, `LOGIN_KINDS`) are listed in `tests/unit_i18n.py`, which checks that every
  text has a translation with the same placeholders and none is left over. Logs stay English: a
  reason like "the screen is locked" is kept in English and translated where it is shown.
  install.sh's lines are `L 'English' 'Slovak'` (printf `%s` for values); install.ps1 stays ASCII
  for Windows PowerShell 5, so its Slovak is written as `\u` escapes that `L` decodes (values
  through `-f`); the test compares both with the table. The menu's *Menu language · Jazyk ponúk*
  submenu and heading are in both languages on purpose.
- **Settings for screen readers** (`websettings.py`): `bigui.SettingsModel` holds the rows and the
  actions of the settings window without Tk; `bigui.Settings` draws them as Tk pictures, and the
  web page renders the same rows as HTML (radio groups by id prefix, named from `state()`'s
  "groups"). The page's script redraws only when the rows' ids change and otherwise updates in
  place, so the focus and the screen reader's place stay. Requests must carry the right
  `Host: 127.0.0.1:<port>` (DNS rebinding), the random token in the path, and for changes
  `Content-Type: application/json` (other sites can't send that without a preflight); a CSP nonce
  allows only the page's own script and style. It writes `RUNTIME_DIR/dictate-web.json` (a second
  start reopens the running page) and ends 30 s after the last request (the page asks every
  second). `screen_reader_on()` (AT-SPI's `ScreenReaderEnabled` on Linux, `SPI_GETSCREENREADER`,
  `NSWorkspace.isVoiceOverEnabled`) makes `bigui.py settings` open the page instead
  (`tests/unit_websettings.py`).
- **Touchpads in the settings window:** Tk 9 on macOS reports two-finger scrolling as
  `<TouchpadScroll>` (dx and dy packed into one number, `touchpad_dy`), not `<MouseWheel>`.
- **AMD (ROCm):**
  - CTranslate2's ROCm build isn't on PyPI. install.sh and install.ps1 extract it from the
    release zip (checksums pinned; update them with the version in requirements.txt), and install
    AMD's ROCm 7 runtime wheels (`rocm-sdk-core`, `rocm-sdk-libraries`, `rocm-sdk-device-<gfx>`)
    from repo.amd.com/rocm/whl-multi-arch. ROCm 10 (stable.repo.amd.com/rocm/whl-next) changes the
    SONAMEs, so it needs a CTranslate2 built against it.
  - The Linux build links `libamdhip64.so.7`, `libhipblas.so.3` and `libhiprand.so.1` directly, so
    `preload_gpu_libraries()` must run before anything imports ctranslate2 or faster_whisper.
  - Its kernels cover gfx1030, gfx1100-1102, gfx1150/1151 and gfx1200/1201. On Linux, other RDNA2
    cards (RX 6700 XT = gfx1031) run the gfx1030 code under `HSA_OVERRIDE_GFX_VERSION=10.3.0`,
    which dictate.py derives from the installed `rocm-sdk-device-gfx*` package.
  - CTranslate2's default allocator (`hipMallocAsync`) hung forever in the first allocation on an
    RX 6700 XT (ROCm 7.14.1, kernel 6.12; `AMD_LOG_LEVEL=3` stops at "VMEM alloc: pool"), so
    dictate.py sets `CT2_CUDA_ALLOCATOR=cub_caching`.
  - That allocator is per thread: a GPU `StorageView` kept past its model segfaults at
    interpreter exit. Keep GPU tensors inside the model's lifetime in test scripts.
  - RX 6700 XT, LMDE 7: one-shot WER en 3.9 % / sk 5.9 %, 0.40 s / 0.48 s per sentence,
    auto-detect 15/15 and 30/30; release -> typed about 0.8 s with live typing.
- **Hangs.** `Watchdog` restarts the program (`os.execv`, same PID; a new process on Windows) when
  one model pass runs longer than 30 s plus a quarter of its audio, after dumping all thread
  stacks to the log (at most once per 10 minutes). A hang was seen once on ROCm (04:36,
  2026-10-03), cause unknown; a candidate is faster-whisper's seek loop, which doesn't advance
  when the model emits two timestamps at 0.00 and the segment has no words. Read the stacks before
  changing anything.
- **Packaging pitfalls already solved:**
  - A private uv Python, so a system Python upgrade can't break the venv (3.14; 3.12 on macOS).
  - ctranslate2 4.8.2 only needs `libcublas.so.12` (`cublas64_12.dll`); it comes from pip and is
    preloaded with ctypes. CPU-only machines don't get the CUDA wheels.
  - Models are pre-downloaded with `snapshot_download` and loaded from local folders with
    `HF_HUB_OFFLINE=1` (faster-whisper's own downloader breaks with newer huggingface-hub).
    `--setup` and `--download` switch it off for themselves.
  - `av<19` is pinned.
  - `XDG_CACHE_HOME` is redirected because onnxruntime would write into `~/.cache`.
  - `pw-record` and `pw-play` run with `LC_ALL=C` because they parse numbers with the locale.
  - Windows and macOS record through PortAudio (sounddevice); a device that can't do 16 kHz is
    resampled block by block (`tests/unit_audio.py`), and the device list is re-read after each
    recording so a headset plugged in later becomes the default.
- **Test-harness pitfalls:**
  - `timeout` without `--foreground` stops interactive children.
  - A harness that owns the clipboard overwrites the user's clipboard. Save and restore it.
  - Point every OS's folders at a temporary one: XDG variables on Linux, `LOCALAPPDATA` on
    Windows, `HOME` on macOS. Redirecting only the XDG ones let a test rewrite the real
    `state.json` on Windows.
  - `e2e_desktop.py` replaces sounddevice with a stand-in module through `PYTHONPATH`; it plays
    FLEURS files in real time.
  - Pillow deprecates `Image.getdata()`: count pixels with numpy.
  - Arch/CachyOS has no Xvfb installed: fetch `xorg-server-xvfb` for the installed xorg-server
    version from an Arch mirror into a scratch folder (check its sha256 against the mirror's
    `extra.db`) and point `XVFB=` at it; no root needed. On CachyOS, `x11_paste.py`'s four paste
    checks fail with that Xvfb (also for older commits); they pass on GitHub's Ubuntu.
  - install.ps1 can be parsed on Linux with PowerShell's tarball
    (`[System.Management.Automation.Language.Parser]::ParseFile`); set
    `POWERSHELL_TELEMETRY_OPTOUT=1` and point `XDG_CACHE_HOME`/`XDG_DATA_HOME` elsewhere, or it
    writes `~/.cache/powershell` and `~/.local/share/powershell`.
  - The settings page's script can be tested in headless Chrome (`--headless=new --dump-dom
    --virtual-time-budget=…`) with `HOME` and `--user-data-dir` in a scratch folder.
  - GitHub's runners have no microphone and no GPU. A workflow can be started by hand only from
    the default branch: to test another branch before it is merged, add a temporary `push`
    trigger for it (and `[skip ci]` in a commit message pushes without a run).
