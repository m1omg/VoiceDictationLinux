#!/bin/sh
# Voice Dictation for Linux and macOS: on this computer, transcribe as the version of 2026-10-04 did
# (one language per dictation, one pass: faster) and give the vocabulary to English dictations only:
#
#   classic_transcription = true
#   vocabulary_languages = ["en"]
#
# It updates the program first when it is older than the newest version on GitHub (the program's own
# updater: every setting, choice and model stays), then restarts dictation. Run it again any time: it
# changes only what isn't set yet. The settings file is saved next to itself before a change.
# (Since 2026-11-03 classic is the default and the menu's Transcription switches it; a choice made there
# stays, as config.toml only gives its starting value.)
#
#   sh classic-mode.sh              set both, update the program if needed
#   sh classic-mode.sh --undo       remove both again (the defaults)
#   sh classic-mode.sh --dry-run    only show what would change
#   sh classic-mode.sh --no-update  don't update the program
#
# Without a copy of the repository:
#   curl -fsSL https://raw.githubusercontent.com/m1omg/VoiceDictationLinux/main/classic-mode.sh | sh
#   curl -fsSL https://raw.githubusercontent.com/m1omg/VoiceDictationLinux/main/classic-mode.sh | sh -s -- --undo
#
# Plain POSIX sh (macOS's old bash 3.2 and dash run it too). Run it in a terminal on that computer, not
# over SSH: starting dictation again needs the desktop session.
set -eu

REPO=m1omg/VoiceDictationLinux
UNDO=0 DRY=0 UPDATE=1
case ${LC_ALL:-${LC_MESSAGES:-${LANG:-}}} in sk*) SK=1 ;; *) SK=0 ;; esac
say() { if [ "$SK" = 1 ]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi; }  # (English, Slovak)

for arg in "$@"; do
  case $arg in
    --undo) UNDO=1 ;;
    --dry-run) DRY=1 ;;
    --no-update) UPDATE=0 ;;
    -h | --help)
      echo "sh classic-mode.sh [--undo] [--dry-run] [--no-update]"
      exit 0 ;;
    *)
      say "Unknown option: $arg (see --help)" "Neznáma voľba: $arg (pozri --help)" >&2
      exit 2 ;;
  esac
done

case $(uname -s) in
  Darwin)
    APP_DIR="$HOME/Library/Application Support/dictate"
    CONFIG="$APP_DIR/config.toml"
    UNIT="" ;;
  Linux)
    APP_DIR="$HOME/.local/share/dictate"
    CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/dictate/config.toml"
    UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/dictate.service" ;;
  *)
    say "Linux and macOS only (on Windows edit %LOCALAPPDATA%\\dictate\\config.toml)." \
        "Len Linux a macOS (vo Windows uprav %LOCALAPPDATA%\\dictate\\config.toml)."
    exit 1 ;;
esac
PY="$APP_DIR/venv/bin/python"
if [ ! -x "$PY" ] || [ ! -f "$APP_DIR/dictate.py" ]; then
  say "Dictate isn't installed here (looked in $APP_DIR)." "Dictate tu nie je nainštalovaný (hľadal som v $APP_DIR)."
  exit 1
fi

# Starting dictation again needs the desktop session: over SSH only a systemd service can be restarted.
CAN_START=1
if [ -n "${SSH_CONNECTION:-}" ] && [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && { [ -z "$UNIT" ] || [ ! -f "$UNIT" ]; }; then
  CAN_START=0
fi
[ "${DICTATE_NO_RESTART:-}" = 1 ] && CAN_START=0  # (for tests)

# --- the settings file ----------------------------------------------------------------------------
# Python checks the result as TOML: the two settings have their values (or are gone, --undo) and every
# other setting is exactly as before, else nothing is written. Exit 0: changed, 3: nothing to change.
set +e
"$PY" - "$CONFIG" "$UNDO" "$DRY" "$SK" <<'PYTHON'
import os
import re
import sys
import time
import tomllib
from pathlib import Path

path, undo, dry, sk = Path(sys.argv[1]), sys.argv[2] == "1", sys.argv[3] == "1", sys.argv[4] == "1"
WANT = {"classic_transcription": True, "vocabulary_languages": ["en"]}
LINE = {"classic_transcription": "classic_transcription = true", "vocabulary_languages": 'vocabulary_languages = ["en"]'}
MARK = "# classic-mode.sh: transcribe as on 2026-10-04 (one language, one pass); the vocabulary for English only"


def say(en, sk_text):
    print(sk_text if sk else en)


old = path.read_text(encoding="utf-8") if path.exists() else ""
try:
    before = tomllib.loads(old)
except tomllib.TOMLDecodeError as e:
    say(f"{path} is not valid TOML ({e}); nothing changed.", f"{path} nie je platný TOML ({e}); nič som nezmenil.")
    sys.exit(1)
lines = old.splitlines(keepends=True)
if lines and not lines[-1].endswith("\n"):
    lines[-1] += "\n"


def depth(line: str) -> int:
    """Brackets a line opens minus those it closes, outside strings and comments (a multi-line array)."""
    count, quote = 0, None
    for ch in line:
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            break
        else:
            count += (ch == "[") - (ch == "]")
    return count


def first_table() -> int:
    return next((i for i, line in enumerate(lines) if re.match(r"\s*\[", line)), len(lines))


def span(key: str):
    """The top-level line(s) that set key: (first, end), or None."""
    top = first_table()
    for i in range(top):
        if re.match(rf"\s*{key}\s*=", lines[i]):
            end, open_ = i + 1, depth(lines[i].split("=", 1)[1])
            while open_ > 0 and end < top:
                open_ += depth(lines[end])
                end += 1
            return i, end
    return None


changes, missing = [], []
if undo:
    # The lines that set them go; so does the block this script added (its comment line, and the blank
    # line it added between the block and what follows or precedes it).
    gone = set()
    for key in WANT:
        where = span(key)
        if where:
            gone.update(range(*where))
            changes.append(f"- {key}")
    marks = [i for i, line in enumerate(lines) if line.rstrip("\n") == MARK]
    gone.update(marks)
    if marks:
        first, last = min(gone), max(gone) + 1
        if 0 < first and last < len(lines) and not lines[first - 1].strip() and not lines[last].strip():
            gone.add(last)
        elif last == len(lines) and first > 0 and not lines[first - 1].strip():
            gone.add(first - 1)
    lines = [line for i, line in enumerate(lines) if i not in gone]
for key, want in ({} if undo else WANT).items():
    where = span(key)
    if key in before and before[key] == want:
        continue
    elif where:
        lines[where[0]:where[1]] = [LINE[key] + "\n"]
        changes.append(f"~ {LINE[key]}")
    else:
        missing.append(key)
if missing:
    block = ([MARK + "\n"] if MARK + "\n" not in lines else []) + [LINE[key] + "\n" for key in missing]
    at = first_table()
    while at < len(lines) and at > 0 and lines[at - 1].lstrip().startswith("#"):  # (above a table's comments)
        at -= 1
    if at == len(lines) and lines and lines[-1].strip():
        block.insert(0, "\n")
    elif at < len(lines):
        block.append("\n")
    lines[at:at] = block
    changes += [f"+ {LINE[key]}" for key in missing]
new = "".join(lines)

want_after = {k: v for k, v in before.items() if not (undo and k in WANT)}
if not undo:
    want_after.update(WANT)
try:
    after = tomllib.loads(new)
except tomllib.TOMLDecodeError as e:
    after = e
if after != want_after:
    say(f"Couldn't change {path} safely (the result would differ in other settings: {after}); nothing changed.",
        f"{path} sa nedal bezpečne zmeniť (výsledok by sa líšil aj v iných nastaveniach: {after}); nič som nezmenil.")
    sys.exit(1)
if not changes:
    if undo:
        say(f"{path}: neither is set, nothing to change.", f"{path}: ani jedno nie je nastavené, niet čo meniť.")
    else:
        say(f"{path}: already set, nothing to change.", f"{path}: už je nastavené, niet čo meniť.")
    sys.exit(3)
say(f"{path}:", f"{path}:")
for change in changes:
    print("   " + change)
if dry:
    say("(--dry-run: nothing written)", "(--dry-run: nič som nezapísal)")
    sys.exit(0)
path.parent.mkdir(parents=True, exist_ok=True)
if path.exists():
    backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    backup.write_bytes(path.read_bytes())
    say(f"   (the previous file: {backup.name})", f"   (pôvodný súbor: {backup.name})")
tmp = path.with_name(path.name + ".tmp")
tmp.write_text(new, encoding="utf-8")
os.replace(tmp, path)
PYTHON
code=$?
set -e
case $code in
  0) CHANGED=1 ;;
  3) CHANGED=0 ;;
  *) exit 1 ;;
esac
[ "$DRY" = 1 ] && exit 0

# --- the program ----------------------------------------------------------------------------------
UPDATED=0
supports() { grep -q 'classic_transcription' "$APP_DIR/dictate.py" && grep -q 'def unsure_english' "$APP_DIR/dictate.py"; }

update_from_zip() {  # an install without the updater (before October 2026): the newest version's installer
  if ! command -v curl > /dev/null 2>&1; then
    say "curl is missing: update Dictate by hand (install.sh from GitHub)." "Chýba curl: aktualizuj Dictate ručne (install.sh z GitHubu)."
    return 1
  fi
  work=$(mktemp -d "${TMPDIR:-/tmp}/dictate-update.XXXXXX")
  rc=0
  if curl -fsSL -o "$work/main.zip" "https://codeload.github.com/$REPO/zip/refs/heads/main" < /dev/null \
      && "$PY" -m zipfile -e "$work/main.zip" "$work/src" < /dev/null; then
    for src in "$work"/src/*/; do
      (cd "$src" && DICTATE_KEEP=keep bash install.sh < /dev/null) || rc=$?
      break
    done
  else
    rc=1
  fi
  [ -n "$work" ] && [ -d "$work" ] && rm -rf "$work"
  return $rc
}

if [ "$UPDATE" = 1 ] && [ "$UNDO" = 0 ]; then
  if [ "$CAN_START" = 0 ] && [ -z "${DICTATE_NO_RESTART:-}" ]; then
    say "Not updating over SSH (dictation couldn't start again): run this in a terminal on that computer." \
        "Cez SSH neaktualizujem (diktovanie by sa nevedelo znova spustiť): spusti to v termináli na tom počítači."
  elif [ -f "$APP_DIR/update.py" ]; then
    say "Checking GitHub for a newer version…" "Kontrolujem na GitHube novšiu verziu…"
    info=$(cd "$APP_DIR" && "$PY" -c 'import update; r = update.check(); print(r.state, r.sha or "-", r.date or "-", r.error or "-")' < /dev/null 2> /dev/null) \
      || info="error - - unexpected"
    set -- $info
    case $1 in
      current) say "   the newest version is installed" "   nainštalovaná je najnovšia verzia" ;;
      available)
        say "   installing the version of $3 (dictation stops for a moment; a few minutes)…" \
            "   inštalujem verziu z $3 (diktovanie sa na chvíľu zastaví; pár minút)…"
        if (cd "$APP_DIR" && "$PY" update.py --run "$2" "$3" < /dev/null); then
          UPDATED=1
          say "   updated, and dictation runs again" "   aktualizované a diktovanie znova beží"
        else
          say "   the update failed; the previous version was put back (details: $APP_DIR/update.log)" \
              "   aktualizácia zlyhala, vrátil som predchádzajúcu verziu (podrobnosti: $APP_DIR/update.log)"
        fi ;;
      *)
        say "   GitHub couldn't be checked ($4)." "   GitHub sa nedal skontrolovať ($4)."
        if ! supports; then
          say "   trying the newest installer directly…" "   skúšam priamo najnovší inštalátor…"
          update_from_zip && UPDATED=1
        fi ;;
    esac
  else
    say "This Dictate has no updater yet: installing the newest version from GitHub (a few minutes)…" \
        "Tento Dictate ešte nemá aktualizátor: inštalujem najnovšiu verziu z GitHubu (pár minút)…"
    update_from_zip && UPDATED=1
  fi
fi

# --- dictation ------------------------------------------------------------------------------------
if ! supports && [ "$UNDO" = 0 ]; then
  say "Note: the installed program is older than these settings, so they have no effect until it is updated." \
      "Pozor: nainštalovaný program je starší ako tieto nastavenia, takže nebudú mať účinok, kým sa neaktualizuje."
fi
if [ "$CHANGED" = 1 ] && [ "$UPDATED" = 0 ]; then
  if [ "$CAN_START" = 0 ] || [ ! -f "$APP_DIR/update.py" ]; then
    say "Restart Dictate to apply them (its menu: Quit, then start it again; or log out and in)." \
        "Reštartuj Dictate, aby sa prejavili (v jeho menu Ukončiť a znova spustiť, alebo sa odhlás a prihlás)."
  else
    say "Restarting dictation…" "Reštartujem diktovanie…"
    if (cd "$APP_DIR" && "$PY" -c 'import sys, update
old = update.running_pid()
update.restart_dictation(lambda text: None)
sys.exit(0 if update.wait_ready(old, 180) else 1)' < /dev/null); then
      say "   dictation runs with the new settings" "   diktovanie beží s novými nastaveniami"
    else
      say "   dictation hasn't started yet: look at $APP_DIR/dictate.log, or start it from the app menu" \
          "   diktovanie sa zatiaľ nespustilo: pozri $APP_DIR/dictate.log, alebo ho spusti z ponuky aplikácií"
    fi
  fi
elif [ "$CHANGED" = 0 ] && [ "$UPDATED" = 0 ]; then
  say "Nothing to do." "Niet čo robiť."
fi
