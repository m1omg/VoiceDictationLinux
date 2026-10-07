"""Every interface text has its Slovak translation, with the same placeholders, and none is built
with an f-string (which the translation table could never match). Also: switching the language
switches the tray menu, and the installer's first question has the right Slovak texts in
install.ps1 (which writes them as \\u escapes, as Windows PowerShell 5 reads files as ANSI).

    python3 tests/unit_i18n.py        (any Python 3.11+ with numpy)
"""
import ast
import os
import re
import string
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="dictate-test-"))
os.environ.update(XDG_STATE_HOME=str(TMP / "state"), XDG_CONFIG_HOME=str(TMP / "config"), XDG_RUNTIME_DIR=str(TMP),
                  LOCALAPPDATA=str(TMP), HOME=str(TMP), USERPROFILE=str(TMP))
import i18n  # noqa: E402

checks = []


def check(name, got, want):
    checks.append((name, got == want, got, want))


def fields(text):
    return sorted({f[1] for f in string.Formatter().parse(text) if f[1] is not None})


SOURCES = ["dictate.py", "bigui.py", "websettings.py", "keys.py", "models.py", "macos.py", "windows.py"]
used, bad = {}, []
for name in SOURCES:
    tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "t" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                used[arg.value] = f"{name}:{node.lineno}"
            elif not isinstance(arg, ast.Name) and not (isinstance(arg, ast.Subscript) or isinstance(arg, ast.Call)):
                bad.append(f"{name}:{node.lineno}")  # an f-string or an expression: can't be in the table
# Texts that reach t() through a variable: the model descriptions, colour scheme names, how dictation
# starts at login, the reasons a dictation was left on the clipboard, and the problems.
sys.path.insert(0, str(ROOT))
import bigui  # noqa: E402
import dictate  # noqa: E402
import models  # noqa: E402
import keys  # noqa: E402
indirect = ([info for _, _, info in models.MODELS.values()] + list(bigui.SCHEME_LABELS.values())
            + list(dictate.LOGIN_KINDS.values()) + [dictate.LANGUAGES["auto"]]
            + list(keys.LABELS.values()) + list(keys.MAC_LABELS.values()) + list(keys.WIN_LABELS.values())
            + ["the screen is locked", "the computer was asleep", "transcribing took too long",
               "the paste keys could not be pressed",
               "the app didn't take it (it may run as administrator, or not paste with Shift+Insert)",
               "muted", "no speech", "transcription error", "recommended", "current"])
for text in indirect:
    used.setdefault(text, "(indirect)")
# The installers' own lines: L 'English' 'Slovak' in install.sh, L "English" "Slovak (\\u escapes)" in install.ps1.
decode = lambda s: re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m[1], 16)), s).replace('`"', '"')  # noqa: E731
sh_pairs = re.findall(r"""L '([^']*)' '([^']*)'""", (ROOT / "install.sh").read_text(encoding="utf-8"))
ps1_pairs = [(decode(en), decode(sk)) for en, sk in
             re.findall(r'L "((?:[^"`]|`.)*)" "((?:[^"`]|`.)*)"', (ROOT / "install.ps1").read_text(encoding="ascii"))]
for en, _sk in sh_pairs + ps1_pairs:
    used.setdefault(en, "(installer)")
check("no t() call with an f-string or expression", bad, [])
wrong_values = []  # (a placeholder without its value raises KeyError, but only when that text is shown)
for name in SOURCES:
    for node in ast.walk(ast.parse((ROOT / name).read_text(encoding="utf-8"))):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "t" and node.args
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            wanted = {f.split("!")[0] for f in fields(node.args[0].value)}
            if len(node.args) > 1 or {k.arg for k in node.keywords} != wanted:
                wrong_values.append(f"{name}:{node.lineno}")
check("every t() call gives exactly the values its text needs", wrong_values, [])


def local_names(fn):
    """Names bound in a function's own scope (a local t would hide t() in the whole function)."""
    names = {a.arg for a in fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs}
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            continue
        if isinstance(node, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
        stack.extend(ast.iter_child_nodes(node))
    return names


hidden = []
for name in SOURCES:
    for fn in ast.walk(ast.parse((ROOT / name).read_text(encoding="utf-8"))):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and "t" in local_names(fn):
            calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "t"]
            if calls:
                hidden.append(f"{name}:{fn.lineno} {fn.name}()")
# (A timestamp called t in Controller.handle() made t("Waiting for the keyboard shortcut…") raise there.)
check("no function calls t() while a local t hides it", hidden, [])
missing = [f"{where}: {text!r}" for text, where in used.items() if text not in i18n.SK]
check("every text has a Slovak translation", missing, [])
mismatched = [text for text in used if text in i18n.SK and fields(text) != fields(i18n.SK[text])]
check("the same placeholders in both languages", mismatched, [])
unused = [text for text in i18n.SK if text not in used]
check("no translation without a text (left over after a change)", unused, [])
same = [text for text in used if i18n.SK.get(text) == text and re.search(r"[a-z]{4}", text)]
print(f"info: {len(used)} texts; untranslated on purpose (same in both): {same}")

# Switching the language switches the menu.
import tray  # noqa: E402
ui = dictate.UiState(dictate.load_config())
top = dictate.TopBar.__new__(dictate.TopBar)
top.ui, top.MenuItem, top.switch = ui, tray.MenuItem, None
top.lock = __import__("threading").Lock()
top.state = {"download": None, "key": None}
ui.set(ui_language="sk")
i18n.set_language(ui.ui_language)
labels = [item.label for item in top.menu()]
check("in Slovak the menu is Slovak", "Zvuky" in labels and "Sounds" not in labels, True)
check("the menu-language submenu is in both languages", "Menu language · Jazyk ponúk" in labels, True)
ui.set(ui_language="en")
i18n.set_language(ui.ui_language)
check("back in English", "Sounds" in [item.label for item in top.menu()], True)

# The installers' Slovak lines are the same as in the table (install.ps1 decoded from its \u escapes).
check("install.ps1 has translated lines", len(ps1_pairs) > 5, True)
check("install.ps1's Slovak lines match the translation table", [p for p in ps1_pairs if i18n.SK.get(p[0]) != p[1]], [])
check("install.sh has translated lines", len(sh_pairs) > 5, True)
check("install.sh's Slovak lines match the translation table", [p for p in sh_pairs if i18n.SK.get(p[0]) != p[1]], [])

for name, good, got, want in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}" + ("" if good else f": got {got!r}, want {want!r}"))
sys.exit(0 if all(c[1] for c in checks) else 1)
