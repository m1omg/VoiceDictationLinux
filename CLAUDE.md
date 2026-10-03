# Claude Code: setting up Voice Dictation for Linux on this computer

You are helping someone install push-to-talk dictation from this repository: hold numpad Del,
speak, release, and the text is typed into the focused app. README.md describes the features; this
file is your playbook for installing, adapting and verifying it. It works out of the box on GNOME
Wayland with an NVIDIA GPU and on Cinnamon X11 (LMDE 7) with an AMD GPU; other desktops have code
paths that still need a first real test.

## Ground rules
- **Dictation may be an accessibility need.**
  - Keep the user's typing to a minimum: ask with multiple-choice questions, and ask everything
    you need up front.
  - Then work autonomously.
  - Say in advance whether and when you will need them at the computer.
- **Live tool output isn't visible to them until a command finishes.** Never run "speak now"
  tests that depend on their timing: `--selftest` starts recording as soon as it launches, so
  warn them before you run it.
- **Be careful with their home directory.**
  - The installer only writes to `~/.local/share/dictate` and `~/.config/dictate`, one launcher in
    `~/.local/share/applications`, and a systemd unit or an autostart entry.
  - Don't edit or delete the user's other files without asking.
- **No windows or clipboard in tests while they work.** Don't pop up focus-stealing test windows
  or take over the clipboard. Everything in `tests/` is safe: no windows, microphone, clipboard
  or keystrokes.
- **Root commands are typed by the user** (suggest `! sudo …`). Keep them to a minimum.
- **Don't `pkill -f` a pattern that also appears in your own command line;** it kills your own
  shell. Use PIDs.

## 1. Survey (read-only)
```bash
echo "$XDG_SESSION_TYPE / $XDG_CURRENT_DESKTOP"; grep PRETTY /etc/os-release
lspci | grep -iE 'vga|3d'; nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
grep -h gfx_target_version /sys/class/kfd/kfd/topology/nodes/*/properties   # AMD: 100301 = gfx1031
getfacl /dev/kfd 2>/dev/null | grep user:                                    # AMD: ROCm needs access
ls -l /dev/uinput; getfacl /dev/uinput 2>/dev/null | grep user:
command -v pw-record parecord notify-send curl
systemctl --user is-active graphical-session.target          # active → systemd service, else autostart
busctl --user list | grep -i StatusNotifierWatcher           # tray host present?
```

## 2. Decide
| Found | Means |
|---|---|
| GNOME, Wayland | backend `wayland`; tested path |
| KDE Plasma, Wayland (e.g. **Bazzite**) | backend `wayland`; untested; see "KDE" below |
| Cinnamon / XFCE / MATE, X11 (e.g. **LMDE**, Mint) | backend `x11`; untested; see "X11" below |
| Sway / Hyprland / other wlroots | not supported yet (no GlobalShortcuts portal); see "wlroots" |
| NVIDIA GPU and driver | defaults: large-v3-turbo on CUDA, live typing available |
| AMD RX 6000 or newer (gfx103x, gfx110x, gfx115x, gfx120x) | large-v3-turbo on ROCm, live typing available; ~3.5 GB more download |
| AMD, but `/dev/kfd` not accessible | user adds the udev rule (README → Troubleshooting, "AMD GPU not used"); works at once |
| No usable GPU (Intel, older AMD, none) | CPU: multilingual `small` model; live typing starts off |
| Neither `pw-record` nor `parecord` | user installs one. Debian/LMDE: `sudo apt install pipewire-bin` or `pulseaudio-utils`. Bazzite already has PipeWire. |
| `/dev/uinput` not writable (Wayland only) | user adds a udev rule (README → Troubleshooting) and reboots |

Immutable systems (Bazzite, Silverblue): the installer needs no system packages. Avoid
`rpm-ostree install` unless something is really missing.

## 3. Install
Run `bash install.sh` (in the background; the first run downloads about 4 GB). It's safe to
re-run. It creates the settings file only if it's missing and picks systemd or autostart by
itself. To skip start-at-login while testing, use `DICTATE_NO_AUTOSTART=1 bash install.sh`.

## 4. Verify without the user
```bash
D=~/.local/share/dictate
$D/venv/bin/python $D/dictate.py --check        # every line should say ok
python3 tests/fetch_fleurs.py
$D/venv/bin/python tests/bench_asr.py           # GPU targets: en ~4 % WER, sk ~6-7 %, ~0.35-0.5 s per sentence
$D/venv/bin/python tests/bench_live.py          # GPU targets: live WER ≈ one-shot; first words ~2 s (en)
XVFB=… $D/venv/bin/python tests/x11_paste.py   # X11 desktops: key grab, repeats, paste, clipboard
journalctl --user -u dictate -n 30              # or: tail $D/dictate.log  (autostart desktops)
```
On a CPU-only machine, use `bench_asr.py` to decide between `small` and `large-v3-turbo` for
`fallback_model` (accuracy vs. speed). Leave live typing off unless a pass takes well under 0.4 s.

## 5. Verify with the user (one short session; announce it first)
1. **Wayland:** the first start shows the desktop's shortcut dialog, which they approve.
   **X11:** nothing to approve.
2. They open a text editor, hold numpad Del, say a sentence, and release. Check the log for
   `transcribed … in … s` and `typed; release -> done … s`. Then a tap, a sentence, and a second
   tap: the log says `tapped: dictating until the next press`.
3. Repeat in a terminal. Check that numpad Del itself no longer types "." or deletes characters.
4. With *Type while speaking* on and a longer sentence, words should appear while the key is
   held. The log says `first live words typed X s after the key went down`.
5. Check that the tray icon is visible and its menu switches English / Slovenčina / Auto.

## Desktop paths: what to check and how to fix
- **X11** (`X11Hotkey`, `XTestKeyboard` and `SelectionOwner(bridged=False)` in dictate.py), tested
  on LMDE 7 with Cinnamon 6.6:
  - The key is grabbed on the root window for all NumLock and CapsLock combinations.
  - The grab is released at once, so our own Shift+Insert reaches the app during the hold. (The
    key's release then reaches the app too; that stray KeyRelease is harmless.)
  - The release is detected by polling `query_keymap()`.
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

## How it works (for debugging)
- **The model stays loaded.** `Transcriber.load()` runs once at start-up. Never load the model per
  key press; that's what makes dictation tools feel slow.
- **Typing is layout-independent.**
  - Text goes to CLIPBOARD and PRIMARY as `UTF8_STRING` only. Offering STRING as well corrupts
    accents on GNOME (mutter #5057).
  - Then Shift+Insert is pressed. GUI apps paste CLIPBOARD; terminals (VTE, kitty, …) paste
    PRIMARY.
  - Claude Code collapses pastes over 800 characters, so text is pasted in chunks of at most 750.
- **Live typing (`Streamer`, LocalAgreement).**
  - The window is the audio since the last committed sentence, re-transcribed every 0.4 s.
  - Commit the prefix that the last two passes agree on, minus its last word (unless that word
    ends a sentence and the speaker has paused for 0.8 s).
  - Auto-detect waits for 1.5 s of speech; `tests/bench_detect.py` shows Slovak is confidently
    misread as English below that.
  - No vocabulary hints on windows shorter than 2 s (Whisper echoes them).
  - Earlier, simpler variants were clearly worse (Slovak WER 15.9 %, invented words). Re-run
    `tests/bench_live.py` after any change.
- **AMD (ROCm):**
  - CTranslate2's ROCm build isn't on PyPI. install.sh extracts it from the release zip
    (checksum pinned; update it with the version in requirements.txt), and installs AMD's ROCm
    7 runtime wheels (`rocm-sdk-core`, `rocm-sdk-libraries`, `rocm-sdk-device-<gfx>`) from
    repo.amd.com/rocm/whl-multi-arch. ROCm 10 (stable.repo.amd.com/rocm/whl-next) changes the
    SONAMEs, so it needs a CTranslate2 built against it.
  - The build links `libamdhip64.so.7`, `libhipblas.so.3` and `libhiprand.so.1` directly, so
    `preload_gpu_libraries()` must run before anything imports ctranslate2 or faster_whisper.
  - Its kernels cover gfx1030, gfx1100-1102, gfx1150/1151 and gfx1200/1201. Other RDNA2 cards
    (RX 6700 XT = gfx1031) run the gfx1030 code under `HSA_OVERRIDE_GFX_VERSION=10.3.0`, which
    dictate.py derives from the installed `rocm-sdk-device-gfx*` package.
  - CTranslate2's default allocator (`hipMallocAsync`) hung forever in the first allocation on an
    RX 6700 XT (ROCm 7.14.1, kernel 6.12; `AMD_LOG_LEVEL=3` stops at "VMEM alloc: pool"), so
    dictate.py sets `CT2_CUDA_ALLOCATOR=cub_caching`.
  - That allocator is per thread: a GPU `StorageView` kept past its model segfaults at
    interpreter exit. Keep GPU tensors inside the model's lifetime in test scripts.
  - RX 6700 XT, LMDE 7: one-shot WER en 3.9 % / sk 5.9 %, 0.40 s / 0.48 s per sentence,
    auto-detect 15/15 and 30/30; release -> typed about 0.8 s with live typing.
- **Packaging pitfalls already solved:**
  - A private uv Python, so a system Python upgrade can't break the venv.
  - ctranslate2 4.8.2 only needs `libcublas.so.12`; it comes from pip and is preloaded with
    ctypes.
  - Models are pre-downloaded with `snapshot_download` and loaded from local folders with
    `HF_HUB_OFFLINE=1` (faster-whisper's own downloader breaks with newer huggingface-hub).
  - `av<19` is pinned.
  - `XDG_CACHE_HOME` is redirected because onnxruntime would write into `~/.cache`.
  - `pw-record` and `pw-play` run with `LC_ALL=C` because they parse numbers with the locale.
- **Test-harness pitfalls:**
  - `timeout` without `--foreground` stops interactive children.
  - A harness that owns the clipboard overwrites the user's clipboard. Save and restore it.
