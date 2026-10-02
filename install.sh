#!/usr/bin/env bash
# Voice Dictation for Linux: per-user install, no root needed. Safe to re-run: it updates the
# program and keeps your settings and downloaded models.
#
#   bash install.sh                 install or update, set up start-at-login, start it
#   DICTATE_NO_AUTOSTART=1 bash install.sh    the same, without start-at-login (e.g. for testing)
#
# Everything goes to ~/.local/share/dictate (program, private Python, models, caches) plus a
# settings file in ~/.config/dictate, a launcher, and a systemd unit or an autostart entry.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
D="$HOME/.local/share/dictate"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}/dictate"
APPS="$HOME/.local/share/applications"
UV_VERSION="0.12.21"
UV_TARBALL="uv-x86_64-unknown-linux-gnu.tar.gz"

# Keep every cache and download inside $D (the defaults would write to ~/.cache, ~/.local/bin, ~/.nv).
export UV_CACHE_DIR="$D/cache/uv" UV_PYTHON_INSTALL_DIR="$D/python" UV_PYTHON_CACHE_DIR="$D/cache/uv-python" \
       UV_PYTHON_BIN_DIR="$D/python/bin" UV_PYTHON_INSTALL_BIN=0 UV_NO_CONFIG=1 UV_MANAGED_PYTHON=1 \
       HF_HOME="$D/cache/hf" HF_HUB_DISABLE_TELEMETRY=1 CUDA_CACHE_PATH="$D/cache/nv" XDG_CACHE_HOME="$D/cache"

say() { printf '\n==> %s\n' "$*"; }
[[ "$(uname -m)" == x86_64 ]] || { echo "Sorry, only x86_64 is supported." >&2; exit 1; }
for tool in curl tar sha256sum; do
  command -v "$tool" >/dev/null || { echo "Please install '$tool' first." >&2; exit 1; }
done
if ! command -v pw-record >/dev/null && ! command -v parecord >/dev/null; then
  echo "Please install a recorder first: pw-record (PipeWire) or parecord (pulseaudio-utils)." >&2; exit 1
fi
if command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then GPU=nvidia; else GPU=none; fi
if systemctl --user is-active --quiet graphical-session.target 2>/dev/null; then MODE=systemd; else MODE=autostart; fi

mkdir -p "$D/cache" "$D/models"
chmod 700 "$D"

say "Program files"
install -m 644 "$SRC/dictate.py" "$SRC/tray.py" "$SRC/requirements.txt" "$SRC/README.md" "$D/"

if [[ ! -x "$D/bin/uv" ]]; then
  say "Downloading uv $UV_VERSION (Python package manager, kept inside $D)"
  url="https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$UV_TARBALL"
  curl -fsSL -o "$D/cache/$UV_TARBALL" "$url"
  curl -fsSL -o "$D/cache/$UV_TARBALL.sha256" "$url.sha256"
  (cd "$D/cache" && sha256sum -c "$UV_TARBALL.sha256")
  mkdir -p "$D/bin"
  tar -xzf "$D/cache/$UV_TARBALL" -C "$D/bin" --strip-components=1 "uv-x86_64-unknown-linux-gnu/uv"
  rm -f "$D/cache/$UV_TARBALL" "$D/cache/$UV_TARBALL.sha256"
fi
UV="$D/bin/uv"

if [[ ! -x "$D/venv/bin/python" ]]; then
  say "Private Python 3.14 (independent of the system Python, so distro upgrades can't break it)"
  "$UV" python install 3.14
  "$UV" venv --python 3.14 "$D/venv"
fi

say "Python packages"
"$UV" pip install --python "$D/venv/bin/python" -r "$D/requirements.txt"

say "Speech models (about 2 GB the first time)"
"$D/venv/bin/python" - <<'PY'
from pathlib import Path
from huggingface_hub import snapshot_download

models = Path.home() / ".local/share/dictate/models"
files = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
for repo, name in [("mobiuslabsgmbh/faster-whisper-large-v3-turbo", "large-v3-turbo"),  # GPU
                   ("Systran/faster-whisper-small", "small")]:  # CPU fallback, multilingual
    if not (models / name / "model.bin").exists():
        print(f"    {repo} -> models/{name}", flush=True)
        snapshot_download(repo, local_dir=models / name, allow_patterns=files)
# large-v3 family models need preprocessor_config.json to select 128 mel bins.
assert (models / "large-v3-turbo/preprocessor_config.json").exists()
PY
"$UV" cache clean >/dev/null 2>&1 || true

say "Settings"
if [[ -e "$CONF/config.toml" ]]; then
  echo "    keeping your $CONF/config.toml"
else
  install -D -m 644 "$SRC/config.example.toml" "$CONF/config.toml"
  if [[ $GPU == none ]]; then  # a CPU can't keep up with repeated live passes
    printf '\n# No NVIDIA GPU found at install time: the small CPU model is used and live typing starts off.\nlive_typing = false\n' >> "$CONF/config.toml"
  fi
  echo "    created $CONF/config.toml"
fi
APP_ID=$(sed -n 's/^app_id *= *"\([^"]*\)".*/\1/p' "$CONF/config.toml" | head -n 1)
APP_ID=${APP_ID:-io.github.m1omg.VoiceDictationLinux}

if [[ $MODE == systemd ]]; then
  START="systemctl --user restart dictate.service"
else
  START="sh -c 'exec \"$D/venv/bin/python\" \"$D/dictate.py\" >> \"$D/dictate.log\" 2>&1'"
fi

say "Launcher ($APP_ID.desktop: also the identity the desktop stores the shortcut under)"
mkdir -p "$APPS"
if [[ -e "$APPS/$APP_ID.desktop" ]]; then
  echo "    keeping $APPS/$APP_ID.desktop"
else
  cat > "$APPS/$APP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Dictate
Comment=Start push-to-talk dictation (hold numpad Del to talk)
Exec=$START
Icon=audio-input-microphone
Terminal=false
Categories=Utility;Accessibility;
EOF
fi

if [[ "${DICTATE_NO_AUTOSTART:-}" == 1 ]]; then
  say "Skipping start-at-login (DICTATE_NO_AUTOSTART=1)"
elif [[ $MODE == systemd ]]; then
  say "Start at login: systemd user service"
  mkdir -p "$HOME/.config/systemd/user"
  install -m 644 "$SRC/packaging/dictate.service" "$HOME/.config/systemd/user/dictate.service"
  systemctl --user daemon-reload
  systemctl --user enable dictate.service
  systemctl --user restart dictate.service
else
  say "Start at login: autostart entry (this desktop doesn't use a systemd graphical session)"
  mkdir -p "$HOME/.config/autostart"
  cat > "$HOME/.config/autostart/dictate.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Dictate
Comment=Push-to-talk dictation
Exec=$START
Icon=audio-input-microphone
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF
  pkill -f "$D/dictate.py" 2>/dev/null || true  # restart a copy started by an earlier install
  sleep 1
  setsid sh -c "exec \"$D/venv/bin/python\" \"$D/dictate.py\" >> \"$D/dictate.log\" 2>&1" < /dev/null &
fi

say "Checking the installation"
"$D/venv/bin/python" "$D/dictate.py" --check || true
[[ $GPU == none ]] && echo "Note: no NVIDIA GPU found; the CPU model 'small' will be used (see README: CPU-only machines)."
echo
echo "Done. Hold numpad Del, speak, release. The first start asks your desktop to approve the key (Wayland)."
