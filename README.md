# Voice Dictation for Linux, Windows and macOS

Hold a key, talk, let go, and your words are typed into whatever app has focus. It's like
ChatGPT's dictation, but system-wide and fully local. OpenAI's Whisper (via faster-whisper) runs on
your own computer: the large-v3-turbo model on an NVIDIA or AMD graphics card, or a smaller model
on the processor of any other computer. Nothing leaves your computer.

- **Push-to-talk** on the numpad **Del / "."** key, **Right Ctrl** (laptops) or **Right Option**
  (Macs), or any key or key combination you choose. Or **tap** it to start and tap again to stop.
- Types into **any app**: terminals (including Claude Code), browsers, editors, chat apps
- **Accurate**: punctuation and capitals included, filler words ("um", "uh") removed, custom
  vocabulary hints
- **English, Slovak or auto-detect**, switchable from the menu (other languages:
  [easy to add](#languages))
- **Type while speaking** (optional): words appear about 1–2 s behind your voice
- **Fast**: about 0.5 s from releasing the key to the finished text on an NVIDIA GPU, under 1 s on
  an AMD RX 6700 XT
- **Runs on modest computers too**: five model sizes, from tiny (76 MB) to large-v3-turbo, picked
  and downloaded from the menu, on the graphics card or on the processor
- **Large text** for low vision: a big status panel while you dictate, and a big settings window
  you can use with the keyboard alone, in the size and colours you choose
- **Layout-proof**: works with any keyboard layout and all accents; your clipboard is restored
  afterwards
- **Private**: the microphone is open only while you hold the key, and logs never contain your
  words

## Contents
[Requirements](#requirements) · [Install](#install) · [Using it](#using-it) ·
[Speech models](#speech-models) · [The dictation key](#the-dictation-key) ·
[Large text](#large-text-for-low-vision) · [Settings](#settings) · [How it works](#how-it-works) ·
[Platform support](#platform-support) · [Accuracy and speed](#accuracy-and-speed) ·
[Languages](#languages) · [Troubleshooting](#troubleshooting) · [Uninstall](#uninstall) ·
[Privacy](#privacy) · [Setting it up with Claude Code](#setting-it-up-with-claude-code)

## Requirements
**Everywhere:** a microphone, an internet connection while installing, and no administrator or
root rights (except once for some Linux setups, below). Disk space: about 1 GB with a small model on
the processor, about 4 GB with large-v3-turbo on an NVIDIA GPU, about 6.5 GB on an AMD GPU
(because of ROCm).

**Linux**
- A desktop on x86_64 (or ARM64, processor only). GNOME on Wayland and Cinnamon on X11 are tested;
  KDE Plasma (Wayland) and other X11 desktops (XFCE, MATE) are implemented but not yet tested; see
  [Platform support](#platform-support).
- **Graphics card (optional):**
  - an NVIDIA GPU with a current driver (CUDA 12 capable, driver 525 or newer). The installer brings
    its own CUDA libraries, so no CUDA toolkit is needed; or
  - an AMD Radeon RX 6000 series or newer (RDNA2 and later, including Radeon 780M iGPUs). The
    installer brings its own ROCm, so no system ROCm is needed, only the kernel's standard `amdgpu`
    driver. Once, as root, your user needs access to `/dev/kfd`
    ([Troubleshooting](#troubleshooting): "AMD GPU not used").
- **Audio:** PipeWire (`pw-record`) or PulseAudio (`parecord`).
- **Tools:** `curl`. `notify-send` is optional; it's used for error notifications.
- **Wayland only:** write access to `/dev/uinput`. Most distros grant it once Steam or its udev
  rules are installed; otherwise see [Troubleshooting](#troubleshooting).
- **Top-bar menu (optional):** a StatusNotifierItem host. It's built into KDE and Cinnamon; GNOME
  needs the *AppIndicator and KStatusNotifierItem Support* extension.

**Windows**
- Windows 10 or 11, 64-bit, with an Intel or AMD processor.
- **Graphics card (optional):** an NVIDIA GPU with a current driver, or an AMD Radeon RX 6800 / 6900,
  RX 7000 or RX 9000 series card, or a Radeon 880M / 890M / 8060S. Other graphics (Intel, other
  AMD cards) can't run the model: it runs on the processor instead.

**macOS**
- macOS 11 (Big Sur) or newer, on an Intel or Apple silicon Mac. The model runs on the processor
  (CTranslate2, the engine underneath, has no Mac GPU support). An older Mac such as a 2017
  MacBook Air manages the tiny and base models; see [Speech models](#speech-models).

## Install
The installer asks four questions, each with a suggestion for your computer that **Enter**
accepts: the language, the speech model (it shows the graphics card, processor and memory it found),
the dictation key, and whether you want [large text](#large-text-for-low-vision). Then it downloads
the model, checks that it works, and starts dictation. Running it again updates the program and
keeps your settings, choices and models.

### Linux
```bash
git clone https://github.com/m1omg/VoiceDictationLinux.git
cd VoiceDictationLinux
bash install.sh
```
- Everything goes into `~/.local/share/dictate`: a private Python 3.14 (so distro upgrades can't
  break it), the Python packages and the models. Settings go to `~/.config/dictate/config.toml`.
- It adds **Dictate** and **Dictate Settings** to your app menu and starts dictation at every login
  (a systemd user service on GNOME and KDE, an autostart entry on other desktops).
- **Wayland, first start only:** your desktop asks you to approve the keyboard shortcut. Click
  *Add* (or *Allow*); the approval is permanent.
- **Update:** `git pull && bash install.sh`.

### Windows
1. On this page, click **Code → Download ZIP**, then extract it (right-click → *Extract All*).
2. In the extracted folder, double-click **install.cmd**. If Windows warns that the file came from
   the internet, choose *Run* (or *More info → Run anyway*).
3. Answer the questions in the window that opens.
- Everything goes into `%LOCALAPPDATA%\dictate`: a private Python, the packages, the models,
  settings (`config.toml`) and the log. Nothing is installed system-wide.
- It adds **Dictate** and **Dictate Settings** to the Start menu and a shortcut to the Startup
  folder, so dictation starts at every login.
- The microphone icon appears in the taskbar's notification area. Windows may tuck it under the
  **^** arrow; drag it next to the clock to keep it in sight.
- **Update:** download the ZIP again and run `install.cmd` again.

### macOS
1. On this page, click **Code → Download ZIP**. Safari unpacks it into your Downloads folder.
2. Open **Terminal** and run:
   ```bash
   cd ~/Downloads/VoiceDictationLinux-main
   bash install.sh
   ```
3. Answer the questions. Then macOS asks to let **Dictate** use the microphone, and to allow it under
   **Accessibility** and **Input Monitoring** (System Settings → Privacy & Security; on macOS 12 and
   older: System Preferences → Security & Privacy → Privacy). Allow all three; dictation works as
   soon as they are on.
- Everything goes into `~/Library/Application Support/dictate`, plus the small **Dictate** app in
  `~/Applications` (the permissions are given to it) and a login item that starts it.
- The microphone icon appears in the menu bar.
- **Update:** download the ZIP again and run `bash install.sh` again. The permissions stay.

### Installing without questions
Set the answers beforehand, for example to install on many computers:
`DICTATE_LANGUAGE=en|sk|auto`, `DICTATE_MODEL=tiny|base|small|medium|large-v3-turbo`,
`DICTATE_KEY=KP_Delete|Control_R|Alt_R|"Ctrl+Alt+D"|…`, `DICTATE_LARGE_UI=off|panel|both`;
`DICTATE_NO_AUTOSTART=1` skips start-at-login. On Linux and macOS:
`DICTATE_MODEL=small bash install.sh`; on Windows, in PowerShell:
`$env:DICTATE_MODEL = "small"; .\install.cmd`.

## Using it
- **Hold the key, speak, release.** Or **tap** it (shorter than 0.4 s), speak as long as you like,
  and tap it again to stop. (Switch tapping off with *Tap to start and stop* in the menu.)
  - High beep: listening.
  - Lower beep: stopped.
  - Low double beep: nothing heard, or the microphone is muted.
- **The microphone icon** (Linux: top bar; Windows: notification area; macOS: menu bar) shows the
  language: **EN**, **SK** or **AUTO**, and a filled circle while it records. Its menu:
  - **English / Slovenčina / Auto-detect (English / Slovak).** Auto-detect decides per dictation
    and only chooses between those two (plain Whisper might pick Czech or Polish). It waits for
    about 1.5 s of speech before deciding; Slovak can be mistaken for English below that. Until
    the first word is typed it keeps checking, and with *Type while speaking* on it types nothing
    before 3 s of speech (real Slovak can look English for about 2 s, and typed words can't be
    taken back). With *Type while speaking* off, or before anything has been typed, the whole
    recording decides again at the end. For the fastest start, pick the language yourself.
  - **Type while speaking.** Words are typed while you talk, about 1–2 s behind your voice; when
    you pause after a sentence, its last word follows within about a second, and the rest lands
    when you let go. Accuracy is the same as waiting for the whole sentence (see
    [Accuracy and speed](#accuracy-and-speed)), but a word, once typed, isn't corrected
    afterwards. Switch it off to get the whole text at once when you let go.
  - **Instantly, correcting itself as it goes** (with *Type while speaking*): the words heard so
    far, as the top bar shows them, go into the app at once (first words after about 1.5–2 s, in
    Slovak too) and are corrected with Backspace while they settle, so the start of a sentence can
    visibly change a few times. It only ever deletes what it typed itself in that dictation;
    don't click elsewhere while dictating. In auto-detect it keeps checking the language for the
    first 6 s and retypes in the other one if it guessed wrong.
  - **Tap to start and stop:** whether a quick tap starts a dictation that lasts until the next
    press. Holding the key works either way.
  - **Sounds:** the beeps, on or off.
  - **Speech model:** see [Speech models](#speech-models). A model that isn't downloaded yet shows
    its size; picking it downloads it, then switches to it.
  - **Run on: graphics card / processor.** The graphics card is greyed out when there is none that
    can run the model.
  - **Large text:** the big status panel, and whether clicking the icon opens the big settings
    window; see [Large text](#large-text-for-low-vision).
  - **Settings window…:** every choice above, plus the dictation key, in large print.
  - **Open settings file** ([Settings](#settings)).
  - **Stop dictation.** Frees the memory the model uses. Start it again with **Dictate** from your
    app menu, Start menu or Applications folder.
- Your choices are remembered (in `state.json`, next to the settings file; on Linux in
  `~/.local/state/dictate`).
- **Linux commands:**
  ```bash
  systemctl --user status dictate         # running? (systemd desktops: GNOME, KDE)
  journalctl --user -u dictate -f         # live log: timings only, never your words
  systemctl --user restart dictate        # apply settings changes
  tail -f ~/.local/share/dictate/dictate.log   # the log on autostart desktops (Cinnamon, XFCE, …)
  ```
  On Windows and macOS the log is `dictate.log` in the program folder.

## Speech models
All are multilingual. Bigger models are usually more accurate and always slower; on a graphics
card even the biggest is fast.

| Model | Download | English | Slovak | Suits |
|---|---|---|---|---|
| tiny | 76 MB | rough | unusable | old or slow computers, English only |
| base | 145 MB | good | unusable | 2-core laptops (e.g. a 2017 MacBook Air), English |
| small | 484 MB | very good | poor | computers without a graphics card, English |
| medium | 1.5 GB | excellent | fair | rarely the best choice: large-v3-turbo is as fast and much better in Slovak |
| large-v3-turbo | 1.6 GB | excellent | very good | a graphics card; Slovak on a 4-core or faster processor |
| large-v3 | 3.1 GB | excellent | very good | measured as accurate as large-v3-turbo, 1.5x slower; a graphics card |
| large-v2 | 3.1 GB | excellent | good | slightly better English, worse Slovak, 1.5x slower; a graphics card |

Measured word error rates and times are under [Accuracy and speed](#accuracy-and-speed).
- **What the installer suggests:** large-v3-turbo on a graphics card with at least 2 GB of memory.
  Without one, for English: small with 4 or more processor cores, otherwise base. For Slovak or
  auto-detect: large-v3-turbo with 4 or more cores and 6 GB of memory (about 4 s per sentence),
  otherwise small. Tiny or base with very little memory. On a graphics card it also downloads that
  model for the processor, in case the card can't be used.
- **Switching:** *Speech model* in the menu or the settings window. The menu lists the model for
  where dictation runs now: switch *Run on* first to choose the other one. A dictation started
  while a model loads still records and waits for it.
- **Live typing** starts switched off without a graphics card: repeated live passes are too heavy
  for most processors. Switch it on in the menu to try it.

## The dictation key
- **Defaults:** numpad **Del / "."** on keyboards with a numpad; the installer suggests **Right Ctrl**
  on laptops (Windows, Linux X11) and **Right Option** on Macs.
- **Any key or combination** works: `F13`, `Ctrl+Alt+D`, `Super+Shift+H`, … Choose it in the
  installer, or in the settings window (*Change it…*, then press the new key or combination; Esc
  cancels).
- **A modifier on its own** (Right Ctrl, Right Option): if you press another key while holding it,
  that was a shortcut, not dictation. The recording is dropped and the shortcut works as usual, so
  Right Ctrl+C still copies, and Right Option+2 still types @ on a Slovak Mac keyboard.
- Letters, digits, Space, Enter, Tab, Esc and Delete only work with a modifier (`Ctrl+Space`),
  so they still type normally.
- **Wayland (GNOME, KDE):** the desktop owns global shortcuts. It shows its own dialog to approve
  the key, and you can change it there.
- In `config.toml`, `trigger` takes the same names (X11 keysyms: `KP_Delete`, `Control_R`, `Alt_R`,
  `Super_R`, `F1`…`F24`, `Insert`, `Pause`, `Scroll_Lock`, `Menu`, letters and digits; Macs have no
  Pause, Scroll Lock or Menu key). On Linux a single key can be any X11 key name, such as
  `ISO_Level3_Shift` (AltGr) or `XF86Launch5`.

## Large text, for low vision
Each part is switched on by itself, in the menu (*Large text*), in the settings window, or by the
installer's last question:
- **Big status panel:** a band at the bottom (or top) of the screen while you dictate: *Listening*
  (with a red dot) and the words as they are recognised, *Transcribing…*, then *Typed* (with a tick)
  and the text, or what went wrong (*Not typed. It is on the clipboard: paste it with Ctrl+V* and
  why, *Only silence was recorded: is the microphone muted?*, *Nothing was heard*). It never takes
  the keyboard focus, and hides 3 s after the text is typed (8 s after a problem).
- **Big settings window:** every choice as a large button: language, live typing, sounds, speech
  model (with download sizes), graphics card or processor, the dictation key, the panel, text size,
  colours, and stop. It works with the keyboard alone: **Tab** or the **arrow keys** move (a thick
  frame shows where you are), **Space** or **Enter** choose, **Esc** closes. It opens from the menu (*Settings window…*), from the
  **Dictate Settings** launcher (Linux app menu, Windows Start menu), by clicking the icon if you
  switch that on (Linux, Windows), on Windows also by starting Dictate again while it runs, and on a
  Mac by opening the Dictate app again (Spotlight: *Dictate*). On a Mac with a Retina screen its
  text is a little soft (the panel's is sharp).
- **Text size** 1× to 3× (default 2×) and **colours**: yellow on black (default), white on black,
  black on white, black on yellow. Both apply to the panel and the window.

## Settings
`config.toml`: on Linux in `~/.config/dictate`, on Windows in `%LOCALAPPDATA%\dictate`, on macOS in
`~/Library/Application Support/dictate`. Every line is optional; restart dictation after editing
(*Stop dictation*, then start Dictate again).

| Setting | Default | Meaning |
|---|---|---|
| `vocabulary` | a few tech words | words to spell exactly like this (keep it short) |
| `beam_size` | `5` | on a graphics card: 1 = fastest, 5 = most accurate |
| `cpu_beam_size` | `2` | the same on the processor, where a narrower search saves time |
| `remove_fillers` | `true` | drop um/uh/erm (ehm/eee in Slovak) |
| `trailing_space` | `true` | end each dictation with a space |
| `restore_clipboard` | `true` | put back what you had copied |
| `paste_chunk_chars` | `750` | paste long text in parts (Claude Code collapses pastes over 800 chars); 0 = off |
| `sound_volume` | `0.5` | beep volume, 0–1 |
| `tail_ms` | `200` | keep recording this long after release, so the last word isn't cut |
| `max_seconds` | `300` | safety limit for one dictation |
| `backend` | `"auto"` | Linux: `"wayland"` (portal + uinput) or `"x11"` (key grab + XTEST); auto picks from the session |
| `app_id` | `"io.github.m1omg.VoiceDictationLinux"` | Linux: identity the desktop stores the shortcut under; needs a matching `<app_id>.desktop` |
| `shortcut_id` | `"push-to-talk"` | Linux Wayland: the shortcut's name; a new key gets a name derived from it |

Starting values for what the menu and the settings window choose; once a choice is made there or
by the installer, it is kept in `state.json` and these no longer apply:

| Setting | Default | Meaning |
|---|---|---|
| `trigger` | `"KP_Delete"` | the [dictation key](#the-dictation-key) |
| `model` | `"large-v3-turbo"` | the model on a graphics card |
| `fallback_model` | `"small"` | the model on the processor |
| `device` | `"auto"` | `"auto"` (the graphics card when one works), `"gpu"` or `"cpu"` |
| `language`, `live_typing`, `instant_typing`, `tap_to_toggle`, `sounds` | `"en"`, `true`, `false`, `true`, `true` | the menu's first choices |
| `big_panel`, `big_settings` | `false`, `false` | [large text](#large-text-for-low-vision) parts |
| `ui_scale`, `ui_colors`, `panel_position` | `2.0`, `"yellow-on-black"`, `"bottom"` | their size, colours and place |

## How it works
```
key ──► recorder ──► Whisper ──► text ──► clipboard ──► paste keys ──► focused app
         (only while    (kept loaded; live
          the key is     passes every 0.4 s)
          held)
```
| | Key | Recorder | Clipboard | Paste keys |
|---|---|---|---|---|
| Linux, Wayland | GlobalShortcuts portal | pw-record / parecord | X11 selections via XWayland | Shift+Insert through uinput |
| Linux, X11 | key grab on the root window | pw-record / parecord | X11 selections | Shift+Insert through XTEST |
| Windows | low-level keyboard hook | PortAudio | clipboard with delayed rendering | Shift+Insert through SendInput |
| macOS | event tap | PortAudio | pasteboard | Cmd+V |

- **The key.** The desktop or the system hands the key to dictation before any app sees it, so
  apps never receive "." or Delete. On X11 the grab is handed back immediately and the release is
  found by polling, so typing during the hold reaches the app.
- **Speech.** faster-whisper is loaded once and stays in memory (large-v3-turbo: about 2 GB of GPU
  memory). Loading the model per key press is what makes most dictation tools feel slow. NVIDIA
  GPUs run CTranslate2's CUDA build, AMD GPUs its ROCm build with AMD's ROCm runtime from pip, all
  inside the program folder; processors use int8 arithmetic.
- **Typing.** The text is put on the clipboard (on Linux also the PRIMARY selection), then the paste
  keys are pressed. This works in GUI apps and terminals alike, with every keyboard layout and
  character, which simulated typing can't manage.
  - On GNOME, the X11 selections are owned through XWayland. It's the only way to set the
    clipboard there without stealing focus.
  - On Windows the clipboard learns when the app fetches the text, and the text is kept out of
    clipboard history (Win+V) and the cloud clipboard. On macOS it's marked as transient, so
    clipboard managers skip it.
  - Afterwards the previous clipboard text is restored, unless you copied something in the
    meantime. Images can't be restored.
- **Live typing (LocalAgreement).**
  - While you talk, the audio since the last finished sentence is transcribed again every 0.4 s.
  - A word is typed only once two consecutive passes agree on it *and* on the word that follows,
    so its punctuation is settled too. The last word of a sentence also goes out once you pause
    after it for 0.8 s.
  - After 2 s of silence the passes wait for you to speak again: Whisper invents words ("Thank
    you.", "Bye.") for windows of silence.
  - Finished text that has left the window is passed back as the prompt.
  - This is the method from [whisper-streaming](https://github.com/ufal/whisper_streaming)
    (Macháček et al.).
- **Safety.**
  - It never types while the screen is locked, after a sleep, or more than 15 s after you let go.
    In those cases the text is left on the clipboard with a notification.
  - Control characters are stripped, so a terminal never receives an Enter.

## Platform support

| System | Status |
|---|---|
| GNOME 48+, Wayland | **tested** (GNOME 50, CachyOS, RTX 3060) |
| KDE Plasma 6, Wayland (Bazzite, Kubuntu, …) | should work, not yet tested |
| Cinnamon (LMDE, Mint), XFCE, MATE, X11 | **tested** (Cinnamon 6.6, LMDE 7, RX 6700 XT) |
| Sway, Hyprland, other wlroots | not supported yet |
| Windows 10 / 11 | automated tests pass on GitHub's Windows Server 2025 machines; not yet tried by a person; NVIDIA and AMD graphics not yet tested |
| macOS 11+ | automated tests pass on GitHub's macOS 15 machines (Intel and Apple silicon); not yet tried by a person |

The automated tests (`.github/workflows/platforms.yml`, started by hand in the Actions tab) install
the program with the tiny model, check it, measure accuracy and speed, and dictate recorded
sentences into a text field. Reports and fixes for the untested parts are welcome:
[TESTING.md](TESTING.md) has short checklists for a first test, and [CLAUDE.md](CLAUDE.md) lists
what to check in depth.

## Accuracy and speed
Word error rate (lower is better) on real recorded sentences
([FLEURS](https://huggingface.co/datasets/google/fleurs)).

**large-v3-turbo on a graphics card:**

| | RTX 3060 (CachyOS) English | Slovak | RX 6700 XT (LMDE 7) English | Slovak |
|---|---|---|---|---|
| one-shot (release, then type) | 3.9 % | 7.0 % | 3.9 % | 5.9 % |
| live typing, long dictations | 2.7 % / 6.2 % | 6.8 % / 8.7 % | 2.7 % / 6.2 % | 6.8 % / 10.4 % |
| auto-detect picks the right language | 15/15 | 30/30 | 15/15 | 30/30 |
| transcription time, ~9 s sentence | ~0.35 s | ~0.38 s | ~0.40 s | ~0.48 s |
| live typing: first words appear after | ~1.9 s | ~3–4 s | ~2.0 s | ~4 s |
| instant typing: first words appear after | ~1.5 s | ~1.9 s | | |
| instant typing in auto-detect | ~2.6 s | ~3.4 s | | |

Instant typing ends with the same text as live typing (same error rates); on the way it took back
about 20 characters per English sentence and 90 per Slovak one (the start of a Slovak sentence
settles later). Auto-detect with live typing (not instant) types nothing before 3 s of speech, so
its first words come after about 5–6 s.

**Bigger models on the RTX 3060** (one-shot, same sentences): large-v3 3.9 % English / 6.6 % Slovak
at ~0.54 / 0.72 s per sentence; large-v2 3.2 % / 10.2 % at ~0.55 / 0.73 s. Neither beats
large-v3-turbo for both languages, which is why it stays the default.

(For live typing, the one-shot rates on the same long dictations were 1.3 % / 6.2 % in English and
6.8 % / 7.8 % in Slovak.)

**On the processor** (int8, `cpu_beam_size = 2`). Auto-detect picked the right language every time
(15/15, 30/30). Word error rates (the ranges are the different machines below; their processors
round int8 arithmetic slightly differently):

| Model | English | Slovak |
|---|---|---|
| tiny | 8.8 % | 78–79 % |
| base | 5.5–6.2 % | 70–73 % |
| small | 3.2–4.5 % | 32–36 % |
| medium | 3.2 % | 15 % |
| large-v3-turbo | 3.9 % | 7.0 % |

Seconds per ~9 s English sentence (a Slovak one takes up to 40 % longer):

| Processor | tiny | base | small | medium | large-v3-turbo |
|---|---|---|---|---|---|
| 4-core Xeon, 2.1 GHz (Linux) | 0.53 | 0.71 | 1.65 | 3.75 | 3.89 |
| GitHub's Linux machine (4 vCPU) | 0.40 | 0.73 | 2.17 | | |
| GitHub's Windows machine (4 vCPU) | 0.48 | 0.95 | 3.16 | | |
| GitHub's Mac, Intel (4 cores) | 0.79 | 1.02 | 3.57 | | |
| GitHub's Mac, Apple M1 (3 cores) | 0.34 | 0.65 | 1.86 | | |

A 2-core laptop such as a 2017 MacBook Air hasn't been measured yet; expect it to be slower than
these. `tests/bench_asr.py MODEL --cpu` measures any machine.

Reproduce with the scripts in `tests/`. They need no microphone, windows or user:
```bash
python3 tests/fetch_fleurs.py                                  # ~27 MB of test sentences
~/.local/share/dictate/venv/bin/python tests/bench_asr.py      # accuracy, speed, auto-detect
~/.local/share/dictate/venv/bin/python tests/bench_asr.py small --cpu    # another model, on the processor
~/.local/share/dictate/venv/bin/python tests/bench_live.py     # live typing timing and accuracy
~/.local/share/dictate/venv/bin/python tests/bench_detect.py   # language detection vs speech length
```
(Windows: `%LOCALAPPDATA%\dictate\venv\Scripts\python.exe`; macOS:
`~/Library/"Application Support"/dictate/venv/bin/python`.)

## Languages
- Whisper itself knows about 100 languages.
- The menu offers English and Slovak, and auto-detect chooses between those two. To change the
  pair, edit `LANGUAGES` and `AUTO_LANGUAGES` near the top of `dictate.py`.
- Accuracy varies by language, so check it with `tests/bench_asr.py`. It downloads the FLEURS
  configs named in `tests/fetch_fleurs.py`.

## Troubleshooting
- **Self-checks.** `--check` lists what was found and what's missing. `--selftest` records 10 s
  right away and prints what it heard (stop dictation first).
  ```bash
  ~/.local/share/dictate/venv/bin/python ~/.local/share/dictate/dictate.py --check     # Linux
  ~/.local/share/dictate/venv/bin/python ~/.local/share/dictate/dictate.py --selftest 10 --language sk
  ~/.local/share/dictate/venv/bin/python ~/.local/share/dictate/dictate.py --portal-test 30   # Wayland key events
  ```
  Windows (PowerShell): `& "$env:LOCALAPPDATA\dictate\venv\Scripts\python.exe" "$env:LOCALAPPDATA\dictate\dictate.py" --check`.
  macOS: `cd ~/Library/"Application Support"/dictate && ./venv/bin/python dictate.py --check`.
- **"Dictation copied to the clipboard" instead of typing.** Press Ctrl+V (Cmd+V on a Mac). The
  notification says why:
  - the screen was locked, or the computer was asleep;
  - Linux: if it says `/dev/uinput`, allow access (root):
    `echo 'KERNEL=="uinput", TAG+="uaccess"' | sudo tee /etc/udev/rules.d/60-uinput.rules`, then
    reboot;
  - Windows: *the app didn't take it*: the app runs as administrator (Windows doesn't let a
    normal program type into it), or it doesn't paste with Shift+Insert.
- **"Microphone is muted":** only silence was recorded. Check the headset mute switch and the
  input device in your sound settings (on a Mac also that Dictate may use the microphone).
- **The model runs on the processor although you have a graphics card.** `--check` says why.
  - Linux, AMD (`/dev/kfd: no access`, or the log says `on cpu/int8`). ROCm needs `/dev/kfd`,
    which most distros open only to the `render` group. Allow the logged-in user, once, as root
    (works at once, no logout):
    ```bash
    echo 'SUBSYSTEM=="kfd", KERNEL=="kfd", TAG+="uaccess"' | sudo tee /etc/udev/rules.d/70-kfd-uaccess.rules
    sudo udevadm control --reload && sudo udevadm trigger --action=change /dev/kfd
    ```
    Or join the group (`sudo usermod -aG render $USER`) and log in again. Then restart dictation.
  - NVIDIA: update the driver. A graphics card with less than 2 GB of memory is too small for
    large-v3-turbo: pick a smaller model for it in the menu.
  - Windows, AMD: only the cards listed under [Requirements](#requirements) have code in
    CTranslate2's Windows build.
- **The key does nothing.**
  - Linux Wayland: the shortcut dialog may have been cancelled. Run `systemctl --user restart
    dictate` to see it again.
  - Linux X11: *another program already uses* the key: unbind it in the desktop's keyboard
    settings, or choose another key.
  - macOS: Dictate needs both **Accessibility** and **Input Monitoring** (Privacy & Security).
    If it's listed and switched on but still deaf, remove it with − and run `bash install.sh` again.
  - Also check the log.
- **Windows: the icon is missing.** It's under the **^** arrow next to the clock.
- **Windows: "CTranslate2 can't load".** Install the
  [Microsoft Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe), then run
  `install.cmd` again.
- **Stuck on "Transcribing…".** If a model pass hangs, dictation restarts itself after about
  30 s (a notification says so) and writes every thread's stack to its log. That dictation is
  lost. Keep the log lines starting at `a model pass has run for`; they show where it hung.
- **Words appear late in live typing.** That's expected: words wait until they're stable. Pick
  the language instead of auto-detect.
- **Slow on an older computer.** Pick a smaller model in the menu; tiny and base are fine for
  English.
- **GPU memory.** About 2 GB stays reserved while it runs. Use *Stop dictation* before a heavy game.

## Uninstall
**Linux**
```bash
systemctl --user disable --now dictate 2>/dev/null; pkill -f .local/share/dictate/dictate.py
rm -f ~/.config/systemd/user/dictate.service ~/.config/autostart/dictate.desktop \
      ~/.local/share/applications/io.github.m1omg.VoiceDictationLinux.desktop \
      ~/.local/share/applications/io.github.m1omg.VoiceDictationLinux.settings.desktop
systemctl --user daemon-reload
gio trash ~/.local/share/dictate ~/.config/dictate ~/.local/state/dictate   # or rm -r
```
On GNOME, also remove the stored shortcut:
`dconf reset -f /org/gnome/settings-daemon/global-shortcuts/io.github.m1omg.VoiceDictationLinux/`

On AMD, the GPU access rule can stay (other ROCm apps use it too) or go:
`sudo rm /etc/udev/rules.d/70-kfd-uaccess.rules`

**Windows:** *Stop dictation* in the menu, then in PowerShell:
```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\dictate"
Remove-Item -Force "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Dictate*.lnk", "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Dictate.lnk"
```

**macOS:** *Stop dictation* in the menu, then in Terminal:
```bash
launchctl bootout gui/$(id -u)/io.github.m1omg.VoiceDictationLinux 2>/dev/null
rm -rf ~/Applications/Dictate.app ~/Library/LaunchAgents/io.github.m1omg.VoiceDictationLinux.plist \
       ~/Library/"Application Support"/dictate
tccutil reset All io.github.m1omg.VoiceDictationLinux   # forget its permissions
```

## Privacy
- Transcription runs on your computer. After installation nothing is downloaded or uploaded,
  except a speech model you pick in the menu that isn't downloaded yet (from Hugging Face, once).
- The microphone stream exists only while the key is held. GNOME and macOS show their microphone
  indicator then.
- Logs contain timings, never your words (unless you run with `-v`).
- Nothing is saved except your settings and choices.

## Setting it up with Claude Code
Open a terminal in the downloaded folder, run `claude`, and say *"set up dictation on this
computer"*. [CLAUDE.md](CLAUDE.md) tells Claude how to install it, adapt it to your computer and
verify it, with as little of your time and typing as possible.

## Credits
[faster-whisper](https://github.com/SYSTRAN/faster-whisper), [CTranslate2](https://github.com/OpenNMT/CTranslate2),
[OpenAI Whisper](https://github.com/openai/whisper), LocalAgreement from
[whisper-streaming](https://github.com/ufal/whisper_streaming), [jeepney](https://gitlab.com/takluyver/jeepney),
[python-xlib](https://github.com/python-xlib/python-xlib), [pystray](https://github.com/moses-palmer/pystray),
[python-sounddevice](https://github.com/spatialaudio/python-sounddevice) (PortAudio), [PyObjC](https://github.com/ronaldoussoren/pyobjc),
[Pillow](https://github.com/python-pillow/Pillow), [psutil](https://github.com/giampaolo/psutil), [uv](https://github.com/astral-sh/uv).
Benchmarks use Google's FLEURS dataset (CC BY 4.0).
