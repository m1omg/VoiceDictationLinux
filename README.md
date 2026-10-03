# Voice Dictation for Linux

Hold a key, talk, let go, and your words are typed into whatever app has focus. It's like
ChatGPT's dictation, but system-wide and fully local. OpenAI's Whisper (large-v3-turbo, via
faster-whisper) runs on your own GPU (NVIDIA or AMD), and nothing leaves your computer.

- **Push-to-talk** on the numpad **Del / "."** key (configurable), or **tap** it to start and tap
  again to stop
- Types into **any app**: terminals (including Claude Code), browsers, editors, chat apps
- **Accurate**: punctuation and capitals included, filler words ("um", "uh") removed, custom
  vocabulary hints
- **English, Slovak or auto-detect**, switchable from a top-bar menu (other languages:
  [easy to add](#languages))
- **Type while speaking** (optional): words appear about 1–2 s behind your voice
- **Fast**: about 0.5 s from releasing the key to the finished text on an NVIDIA GPU, under 1 s on
  an AMD RX 6700 XT
- **Layout-proof**: works with any keyboard layout and all accents; your clipboard is restored
  afterwards
- **Private**: the microphone is open only while you hold the key, and logs never contain your
  words

## Contents
[Requirements](#requirements) · [Install](#install) · [Using it](#using-it) · [Settings](#settings) ·
[How it works](#how-it-works) · [Desktop support](#desktop-support) · [Accuracy and speed](#accuracy-and-speed) ·
[CPU-only machines](#cpu-only-machines) · [Languages](#languages) · [Troubleshooting](#troubleshooting) ·
[Uninstall](#uninstall) · [Privacy](#privacy) · [Setting it up with Claude Code](#setting-it-up-with-claude-code)

## Requirements
- A Linux desktop on x86_64. GNOME on Wayland and Cinnamon on X11 are tested. KDE Plasma (Wayland)
  and other X11 desktops (XFCE, MATE) are implemented but not yet tested; see
  [Desktop support](#desktop-support).
- **Recommended:** a GPU, either
  - an NVIDIA GPU with a current driver (CUDA 12 capable, driver 525 or newer). The installer brings
    its own CUDA libraries, so no CUDA toolkit is needed; or
  - an AMD Radeon RX 6000 series or newer (RDNA2 and later, including Radeon 780M iGPUs). The
    installer brings its own ROCm, so no system ROCm is needed, only the kernel's standard `amdgpu`
    driver. Once, as root, your user needs access to `/dev/kfd`
    ([Troubleshooting](#troubleshooting): "AMD GPU not used").

  Without such a GPU, a smaller model runs on the CPU ([CPU-only machines](#cpu-only-machines)).
- **Audio:** PipeWire (`pw-record`) or PulseAudio (`parecord`).
- **Tools:** `curl`. `notify-send` is optional; it's used for error notifications.
- **Wayland only:** write access to `/dev/uinput`. Most distros grant it once Steam or its udev
  rules are installed; otherwise see [Troubleshooting](#troubleshooting).
- **Top-bar menu (optional):** a StatusNotifierItem host. It's built into KDE and Cinnamon; GNOME
  needs the *AppIndicator and KStatusNotifierItem Support* extension.
- About 4 GB of disk space (about 6.5 GB with an AMD GPU, because of ROCm).

## Install
```bash
git clone https://github.com/m1omg/VoiceDictationLinux.git
cd VoiceDictationLinux
bash install.sh
```
- **No root needed** (on AMD GPUs, except once for GPU access). The installer puts everything into
  `~/.local/share/dictate`: a private Python 3.14 (so distro upgrades can't break it), the Python
  packages, and about 2 GB of models.
- It creates `~/.config/dictate/config.toml` and a "Dictate" launcher, and sets the program to
  **start at every login**. That's a systemd user service on GNOME and KDE, or an autostart
  entry on other desktops.
- **Wayland, first start only:** your desktop asks you to approve the keyboard shortcut. Click
  *Add* (or *Allow*); the approval is permanent.
- **Update:** `git pull && bash install.sh`. It keeps your settings and models.

## Using it
- **Hold numpad Del, speak, release.** Or **tap** it (shorter than 0.4 s), speak as long as you like,
  and tap it again to stop.
  - High beep: listening.
  - Lower beep: stopped.
  - Low double beep: nothing heard, or the microphone is muted.
- **The microphone icon** in the top bar shows the language: **EN**, **SK** or **AUTO**. While
  recording it reads **● EN** plus a preview of what it hears. Its menu:
  - **English / Slovenčina / Auto-detect (English / Slovak).** Auto-detect decides per dictation
    and only chooses between those two (plain Whisper might pick Czech or Polish). It waits for
    about 1.5 s of speech before deciding; Slovak can be mistaken for English below that. For
    the fastest start, pick the language yourself.
  - **Type while speaking.** Words are typed while you talk, about 1–2 s behind your voice; when
    you pause after a sentence, its last word follows within about a second, and the rest lands
    when you let go. Accuracy is the same as waiting for the whole sentence (see
    [Accuracy and speed](#accuracy-and-speed)), but a word, once typed, isn't corrected
    afterwards. Switch it off to get the whole text at once when you let go.
  - **Sounds:** the beeps, on or off.
  - **Open settings file.**
  - **Stop dictation.** Frees the GPU. Start it again from your app menu with **Dictate**.
- Menu choices are remembered in `~/.local/state/dictate/state.json`.
- **Commands:**
  ```bash
  systemctl --user status dictate         # running? (systemd desktops: GNOME, KDE)
  journalctl --user -u dictate -f         # live log: timings only, never your words
  systemctl --user restart dictate        # apply settings changes
  tail -f ~/.local/share/dictate/dictate.log   # the log on autostart desktops (Cinnamon, XFCE, …)
  ```

## Settings
`~/.config/dictate/config.toml` (every line optional; restart after editing):

| Setting | Default | Meaning |
|---|---|---|
| `model` | `"large-v3-turbo"` | model used on an NVIDIA GPU (a folder in `~/.local/share/dictate/models`) |
| `fallback_model` | `"small"` | model used on the CPU when no GPU can be used |
| `beam_size` | `5` | 1 = fastest, 5 = most accurate |
| `vocabulary` | a few tech words | words to spell exactly like this (keep it short) |
| `remove_fillers` | `true` | drop um/uh/erm (ehm/eee in Slovak) |
| `trailing_space` | `true` | end each dictation with a space |
| `restore_clipboard` | `true` | put back what you had copied |
| `paste_chunk_chars` | `750` | paste long text in parts (Claude Code collapses pastes over 800 chars); 0 = off |
| `sound_volume` | `0.5` | beep volume, 0–1 |
| `tail_ms` | `200` | keep recording this long after release, so the last word isn't cut |
| `max_seconds` | `300` | safety limit for one dictation |
| `backend` | `"auto"` | `"wayland"` (portal + uinput) or `"x11"` (key grab + XTEST); auto picks from the session |
| `trigger` | `"KP_Delete"` | the key (a keysym name such as `KP_Insert`, `F13`) |
| `shortcut_id` | `"push-to-talk"` | change it together with `trigger` so the desktop asks again |
| `app_id` | `"io.github.m1omg.VoiceDictationLinux"` | identity the desktop stores the shortcut under; needs a matching `<app_id>.desktop` |
| `language`, `live_typing`, `sounds` | `"en"`, `true`, `true` | starting values for the menu choices |

## How it works
```
key ──► recorder ──► Whisper on the GPU ──► text ──► clipboard + PRIMARY ──► Shift+Insert ──► focused app
(portal or     (pw-record,     (faster-whisper, kept                   (X11 selections)    (uinput or XTEST)
 X11 grab)      only while      loaded; live passes
                held)           every 0.4 s)
```
- **The key.**
  - On Wayland it's registered through the desktop's *GlobalShortcuts* portal. The desktop grabs
    the key, so apps never see "." or Delete, and reports press and release.
  - On X11 it's a key grab on the root window. The grab is handed back immediately and the release
    is found by polling, so typing during the hold reaches the app.
- **Speech.** faster-whisper with large-v3-turbo in fp16 is loaded once at login and stays in GPU
  memory (about 2 GB). Loading the model per key press is what makes most dictation tools feel
  slow. NVIDIA GPUs run CTranslate2's CUDA build; AMD GPUs run its ROCm build with AMD's ROCm
  runtime from pip, all inside `~/.local/share/dictate`.
- **Typing.** The text is put on the clipboard and the PRIMARY selection, then Shift+Insert is
  pressed. This works in GUI apps and terminals alike (terminals paste PRIMARY on Shift+Insert),
  with every keyboard layout and character, which simulated typing can't manage.
  - On GNOME, the X11 selections are owned through XWayland. It's the only way to set the
    clipboard there without stealing focus.
  - Afterwards the previous clipboard text is restored, unless you copied something in the
    meantime. Images can't be restored.
- **Live typing (LocalAgreement).**
  - While you talk, the audio since the last finished sentence is transcribed again every 0.4 s.
  - A word is typed only once two consecutive passes agree on it *and* on the word that follows,
    so its punctuation is settled too. The last word of a sentence also goes out once you pause
    after it for 0.8 s.
  - Finished text that has left the window is passed back as the prompt.
  - This is the method from [whisper-streaming](https://github.com/ufal/whisper_streaming)
    (Macháček et al.).
- **Safety.**
  - It never types while the screen is locked, after a suspend, or more than 15 s after you let go.
    In those cases the text is left on the clipboard with a notification.
  - Control characters are stripped, so a terminal never receives an Enter.

## Desktop support

| Desktop | Session | Key | Typing | Status |
|---|---|---|---|---|
| GNOME 48+ | Wayland | GlobalShortcuts portal | uinput, XWayland clipboard | **tested** (GNOME 50, CachyOS) |
| KDE Plasma 6 (Bazzite, Kubuntu, …) | Wayland | GlobalShortcuts portal | uinput, XWayland clipboard | should work, not yet tested |
| Cinnamon (LMDE, Mint), XFCE, MATE | X11 | X11 key grab | XTEST, X11 clipboard | **tested** (Cinnamon 6.6, LMDE 7) |
| Sway, Hyprland, other wlroots | Wayland | (none) | (none) | not supported yet |

Reports and fixes for the untested ones are welcome; [CLAUDE.md](CLAUDE.md) lists what to check.

## Accuracy and speed
Measured on an RTX 3060 with real recorded sentences ([FLEURS](https://huggingface.co/datasets/google/fleurs)),
word error rate (lower is better):

| | English | Slovak |
|---|---|---|
| one-shot (release, then type) | 3.9 % | 7.0 % |
| live typing, long dictations | 2.7 % / 6.2 % (one-shot 1.3 % / 6.2 %) | 6.8 % / 8.7 % (one-shot 6.8 % / 7.8 %) |
| auto-detect picks the right language | 15/15 | 30/30 |
| transcription time, ~9 s sentence | ~0.35 s | ~0.38 s |
| live typing: first words appear after | ~1.9 s | ~3–4 s |

On an RX 6700 XT (ROCm, LMDE 7), with the same sentences:

| | English | Slovak |
|---|---|---|
| one-shot (release, then type) | 3.9 % | 5.9 % |
| live typing, long dictations | 2.7 % / 6.2 % (one-shot 1.3 % / 6.2 %) | 6.8 % / 10.4 % (one-shot 6.8 % / 7.8 %) |
| auto-detect picks the right language | 15/15 | 30/30 |
| transcription time, ~9 s sentence | ~0.40 s | ~0.48 s |
| live typing: first words appear after | ~2.0 s | ~4 s |

Reproduce with the scripts in `tests/`. They need no microphone, windows or user:
```bash
python3 tests/fetch_fleurs.py                                  # ~27 MB of test sentences
~/.local/share/dictate/venv/bin/python tests/bench_asr.py      # accuracy, speed, auto-detect
~/.local/share/dictate/venv/bin/python tests/bench_live.py     # live typing timing and accuracy
~/.local/share/dictate/venv/bin/python tests/bench_detect.py   # language detection vs speech length
```

## CPU-only machines
- Without a supported GPU, the multilingual `small` model runs on the CPU. The installer then starts
  with live typing off, because repeated live passes are too heavy for a CPU.
- Expect roughly 1–2 s per short dictation and noticeably lower accuracy than large-v3-turbo.
- With a fast CPU, `fallback_model = "large-v3-turbo"` is more accurate but slower. Measure with
  `tests/bench_asr.py`.
- Intel GPUs, and AMD GPUs older than the RX 6000 series, fall back to the CPU: CTranslate2 has no
  build for them.

## Languages
- Whisper itself knows about 100 languages.
- The menu offers English and Slovak, and auto-detect chooses between those two. To change the
  pair, edit `LANGUAGES` and `AUTO_LANGUAGES` near the top of `dictate.py`.
- Accuracy varies by language, so check it with `tests/bench_asr.py`. It downloads the FLEURS
  configs named in `tests/fetch_fleurs.py`.

## Troubleshooting
- **Self-checks.** Stop the service first: `systemctl --user stop dictate`.
  ```bash
  ~/.local/share/dictate/venv/bin/python ~/.local/share/dictate/dictate.py --check
  ~/.local/share/dictate/venv/bin/python ~/.local/share/dictate/dictate.py --selftest 10 --language sk   # talk right away
  ~/.local/share/dictate/venv/bin/python ~/.local/share/dictate/dictate.py --portal-test 30              # Wayland key events
  ```
- **The key does nothing (Wayland).** The shortcut dialog may have been cancelled. Run
  `systemctl --user restart dictate` to see it again. Also check the log.
- **"Dictation copied to the clipboard" instead of typing.**
  - The screen was locked, or the PC was asleep: press Ctrl+V.
  - If it says `/dev/uinput`, allow access (root):
    `echo 'KERNEL=="uinput", TAG+="uaccess"' | sudo tee /etc/udev/rules.d/60-uinput.rules`, then
    reboot.
- **AMD GPU not used** (`--check` says `/dev/kfd: no access`, or the log says `on cpu/int8`). ROCm
  needs `/dev/kfd`, which most distros open only to the `render` group. Allow the logged-in user,
  once, as root (works at once, no logout):
  ```bash
  echo 'SUBSYSTEM=="kfd", KERNEL=="kfd", TAG+="uaccess"' | sudo tee /etc/udev/rules.d/70-kfd-uaccess.rules
  sudo udevadm control --reload && sudo udevadm trigger --action=change /dev/kfd
  ```
  Or join the group (`sudo usermod -aG render $USER`) and log in again. Then restart dictation.
- **"Microphone is muted":** only silence was recorded. Check the headset mute switch and the
  input device in your sound settings.
- **Words appear late in live typing.** That's expected: words wait until they're stable. Pick
  the language instead of auto-detect.
- **GPU memory.** About 2 GB stays reserved while it runs. Use *Stop dictation* before a heavy game.

## Uninstall
```bash
systemctl --user disable --now dictate 2>/dev/null; pkill -f .local/share/dictate/dictate.py
rm -f ~/.config/systemd/user/dictate.service ~/.config/autostart/dictate.desktop \
      ~/.local/share/applications/io.github.m1omg.VoiceDictationLinux.desktop
systemctl --user daemon-reload
gio trash ~/.local/share/dictate ~/.config/dictate ~/.local/state/dictate   # or rm -r
```
On GNOME, also remove the stored shortcut:
`dconf reset -f /org/gnome/settings-daemon/global-shortcuts/io.github.m1omg.VoiceDictationLinux/`

On AMD, the GPU access rule can stay (other ROCm apps use it too) or go:
`sudo rm /etc/udev/rules.d/70-kfd-uaccess.rules`

## Privacy
- Transcription runs locally. After installation nothing is downloaded or uploaded
  (`HF_HUB_OFFLINE=1`).
- The microphone stream exists only while the key is held. GNOME shows its microphone indicator
  then.
- Logs contain timings, never your words (unless you run with `-v`).
- Nothing is saved except your settings and menu choices.

## Setting it up with Claude Code
Open a terminal in the cloned folder, run `claude`, and say *"set up dictation on this computer"*.
[CLAUDE.md](CLAUDE.md) tells Claude how to install it, adapt it to your desktop and verify it,
with as little of your time and typing as possible.

## Credits
[faster-whisper](https://github.com/SYSTRAN/faster-whisper), [CTranslate2](https://github.com/OpenNMT/CTranslate2),
[OpenAI Whisper](https://github.com/openai/whisper), LocalAgreement from
[whisper-streaming](https://github.com/ufal/whisper_streaming), [jeepney](https://gitlab.com/takluyver/jeepney),
[python-xlib](https://github.com/python-xlib/python-xlib), [uv](https://github.com/astral-sh/uv). Benchmarks use
Google's FLEURS dataset (CC BY 4.0).
