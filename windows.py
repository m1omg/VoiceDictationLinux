"""Windows backends for dictate, written with ctypes only:

  Hotkey     the push-to-talk key through a low-level keyboard hook (it sees every key before the
             apps do, so the key and its repeats can be kept from them)
  Keyboard   Shift+Insert through SendInput
  Clipboard  the text offered with delayed rendering, so we learn when an app fetches it (the same
             interface as dictate's X11 SelectionOwner, so the Paster works unchanged)
  and small helpers: single instance, sleep and screen-lock detection, GPU libraries, check().

Nothing here touches a Windows DLL at import time, so the module imports on any OS (the unit tests
check the structure layouts on Linux). Structures use fixed-width types: ctypes.wintypes.DWORD is
8 bytes on 64-bit Linux, so it would make those checks meaningless.
"""
from __future__ import annotations

import ctypes
import functools
import logging
import os
import queue
import sysconfig
import threading
import time
from pathlib import Path

import keys

log = logging.getLogger("dictate")

BOOL, UINT, DWORD, WORD, LONG = ctypes.c_int32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint16, ctypes.c_int32
HANDLE = ctypes.c_void_p
WPARAM, LPARAM, LRESULT = ctypes.c_size_t, ctypes.c_ssize_t, ctypes.c_ssize_t
FUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)  # the same calling convention on 64-bit
HOOKPROC = FUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)
WNDPROC = FUNCTYPE(LRESULT, HANDLE, UINT, WPARAM, LPARAM)
ERROR_ALREADY_EXISTS = 183
WH_KEYBOARD_LL, WM_KEYDOWN, WM_SYSKEYDOWN, WM_TIMER, WM_APP = 13, 0x0100, 0x0104, 0x0113, 0x8000
WM_RENDERFORMAT, WM_RENDERALLFORMATS, WM_DESTROYCLIPBOARD = 0x0305, 0x0306, 0x0307
LLKHF_EXTENDED, LLKHF_INJECTED = 0x01, 0x10
INPUT_KEYBOARD, KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 1, 0x1, 0x2
CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
MARK = 0x44494354  # "DICT" in dwExtraInfo: our own key events
MODIFIER_GROUPS = {0xA0: "shift", 0xA1: "shift", 0x10: "shift", 0xA2: "ctrl", 0xA3: "ctrl", 0x11: "ctrl",
                   0xA4: "alt", 0xA5: "alt", 0x12: "alt", 0x5B: "super", 0x5C: "super"}
GROUP_KEYS = {"shift": (0xA0, 0xA1), "ctrl": (0xA2, 0xA3), "alt": (0xA4, 0xA5), "super": (0x5B, 0x5C)}
REPEAT_GAP = 1.5  # seconds: a key held on a keyboard repeats more often than this
MASK_KEY = 0xE8  # an unassigned key, sent when Alt or Win is part of our key: their release opens no menu


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", DWORD), ("scanCode", DWORD), ("flags", DWORD), ("time", DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", WORD), ("wScan", WORD), ("dwFlags", DWORD), ("time", DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):  # the largest member: without it sizeof(INPUT) is wrong and SendInput fails
    _fields_ = [("dx", LONG), ("dy", LONG), ("mouseData", DWORD), ("dwFlags", DWORD), ("time", DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", DWORD), ("u", INPUTUNION)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", HANDLE), ("message", UINT), ("wParam", WPARAM), ("lParam", LPARAM), ("time", DWORD),
                ("pt_x", LONG), ("pt_y", LONG), ("lPrivate", DWORD)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", HANDLE), ("hIcon", HANDLE), ("hCursor", HANDLE), ("hbrBackground", HANDLE),
                ("lpszMenuName", ctypes.c_wchar_p), ("lpszClassName", ctypes.c_wchar_p)]


@functools.cache
def kernel32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, res, args in [
            ("GetTickCount64", ctypes.c_uint64, []), ("QueryUnbiasedInterruptTime", BOOL, [ctypes.POINTER(ctypes.c_uint64)]),
            ("CreateMutexW", HANDLE, [ctypes.c_void_p, BOOL, ctypes.c_wchar_p]), ("CloseHandle", BOOL, [HANDLE]),
            ("GetModuleHandleW", HANDLE, [ctypes.c_wchar_p]), ("GetCurrentThreadId", DWORD, []),
            ("GlobalAlloc", HANDLE, [UINT, ctypes.c_size_t]), ("GlobalLock", ctypes.c_void_p, [HANDLE]),
            ("GlobalUnlock", BOOL, [HANDLE]), ("GlobalFree", HANDLE, [HANDLE]),
            ("GetShortPathNameW", DWORD, [ctypes.c_wchar_p, ctypes.c_wchar_p, DWORD])]:
        getattr(k, name).restype, getattr(k, name).argtypes = res, args
    return k


@functools.cache
def user32():
    u = ctypes.WinDLL("user32", use_last_error=True)
    for name, res, args in [
            ("OpenInputDesktop", HANDLE, [DWORD, BOOL, DWORD]),
            ("GetUserObjectInformationW", BOOL, [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD)]),
            ("CloseDesktop", BOOL, [HANDLE]),
            ("SetWindowsHookExW", HANDLE, [ctypes.c_int, HOOKPROC, HANDLE, DWORD]),
            ("CallNextHookEx", LRESULT, [HANDLE, ctypes.c_int, WPARAM, LPARAM]),
            ("UnhookWindowsHookEx", BOOL, [HANDLE]),
            ("GetMessageW", BOOL, [ctypes.POINTER(MSG), HANDLE, UINT, UINT]),
            ("DispatchMessageW", LRESULT, [ctypes.POINTER(MSG)]),
            ("SetTimer", ctypes.c_size_t, [HANDLE, ctypes.c_size_t, UINT, ctypes.c_void_p]),
            ("SendInput", UINT, [UINT, ctypes.POINTER(INPUT), ctypes.c_int]),
            ("GetAsyncKeyState", ctypes.c_short, [ctypes.c_int]),
            ("RegisterClassW", ctypes.c_uint16, [ctypes.POINTER(WNDCLASSW)]),
            ("CreateWindowExW", HANDLE, [DWORD, ctypes.c_wchar_p, ctypes.c_wchar_p, DWORD, ctypes.c_int, ctypes.c_int,
                                         ctypes.c_int, ctypes.c_int, HANDLE, HANDLE, HANDLE, ctypes.c_void_p]),
            ("DefWindowProcW", LRESULT, [HANDLE, UINT, WPARAM, LPARAM]),
            ("PostMessageW", BOOL, [HANDLE, UINT, WPARAM, LPARAM]),
            ("OpenClipboard", BOOL, [HANDLE]), ("CloseClipboard", BOOL, []), ("EmptyClipboard", BOOL, []),
            ("SetClipboardData", HANDLE, [UINT, HANDLE]), ("GetClipboardData", HANDLE, [UINT]),
            ("GetClipboardOwner", HANDLE, []), ("RegisterClipboardFormatW", UINT, [ctypes.c_wchar_p]),
            ("GetForegroundWindow", HANDLE, [])]:
        getattr(u, name).restype, getattr(u, name).argtypes = res, args
    return u


def sleep_offset() -> float:
    """Grows while the PC sleeps: GetTickCount64 counts sleep, the unbiased interrupt time doesn't."""
    k = kernel32()
    unbiased = ctypes.c_uint64()
    k.QueryUnbiasedInterruptTime(ctypes.byref(unbiased))
    return k.GetTickCount64() / 1000 - unbiased.value / 1e7


_mutex = None


def single_instance():
    """A per-session named mutex. A copy started by restart_self() waits for its predecessor."""
    global _mutex
    k = kernel32()
    deadline = time.monotonic() + (10 if os.environ.get("DICTATE_RESTARTED") else 0)
    while True:
        ctypes.set_last_error(0)
        handle = k.CreateMutexW(None, False, "Local\\io.github.m1omg.dictate")
        if handle and ctypes.get_last_error() != ERROR_ALREADY_EXISTS:
            _mutex = handle
            return handle
        if handle:
            k.CloseHandle(handle)
        if time.monotonic() > deadline:
            return None
        time.sleep(0.2)


def release_single_instance() -> None:
    global _mutex
    if _mutex:
        kernel32().CloseHandle(_mutex)
        _mutex = None


def screen_locked() -> bool:
    """True while the lock screen (or a UAC prompt) has the input: then we can't open the input desktop."""
    u = user32()
    desk = u.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
    if not desk:
        return True
    try:
        name = ctypes.create_unicode_buffer(64)
        if not u.GetUserObjectInformationW(desk, 2, name, ctypes.sizeof(name), None):  # UOI_NAME
            return False
        return name.value.lower() != "default"
    finally:
        u.CloseDesktop(desk)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", DWORD), ("Data2", WORD), ("Data3", WORD), ("Data4", ctypes.c_ubyte * 8)]


FOLDER_IDS = {"Programs": "a77f5d77-2e2b-44c3-a6a2-aba601054a51", "Startup": "b97d20bb-f46a-4c97-ba10-5e3608430854"}
# Where Settings > Apps > Startup and Task Manager record a Startup-folder shortcut they switched off.
STARTUP_APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"


def known_folder(name: str) -> Path:
    """The Start menu's "Programs" or "Startup" folder (a policy can move them, so ask the shell)."""
    import uuid
    shell32, ole32 = ctypes.WinDLL("shell32"), ctypes.WinDLL("ole32")
    shell32.SHGetKnownFolderPath.restype = LONG
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), DWORD, HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    ole32.CoTaskMemFree.restype, ole32.CoTaskMemFree.argtypes = None, [ctypes.c_void_p]
    guid = GUID.from_buffer_copy(uuid.UUID(FOLDER_IDS[name]).bytes_le)
    path = ctypes.c_wchar_p()
    result = shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path))
    try:
        if result != 0:
            raise OSError(f"Windows didn't say where the {name} folder is (error {result & 0xFFFFFFFF:#x})")
        return Path(path.value)
    finally:
        ole32.CoTaskMemFree(ctypes.cast(path, ctypes.c_void_p))


def startup_disabled(name: str) -> bool:
    """Whether the Startup apps settings or Task Manager switched off the Startup-folder shortcut name
    (the first byte of its value is odd then; no value means it runs)."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_APPROVED) as key:
            data, _kind = winreg.QueryValueEx(key, name)
    except OSError:
        return False
    return isinstance(data, bytes) and bool(data) and data[0] & 1 == 1


def allow_startup(name: str) -> None:
    """Undo that switch, so the shortcut runs at the next login."""
    import winreg
    if startup_disabled(name):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_APPROVED, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)


def _add_dll_dirs(dirs) -> None:
    """For DLLs loaded by path (os.add_dll_directory) and for plain LoadLibrary calls (PATH)."""
    for d in dirs:
        if Path(d).is_dir():
            os.add_dll_directory(str(d))
    os.environ["PATH"] = os.pathsep.join([*map(str, dirs), os.environ.get("PATH", "")])


def preload_gpu_libraries() -> tuple[str, list[str]]:
    """Load the pip-installed GPU libraries by path before ctranslate2 needs them: CTranslate2's CUDA
    build loads cuBLAS lazily by name, and its ROCm build links amdhip64_7.dll and hipblas.dll, which
    AMD's wheels put where CTranslate2's own loader doesn't look. Returns ("cuda"|"rocm"|"cpu", paths)."""
    site = Path(sysconfig.get_paths()["purelib"])
    rocm_core, rocm_libs = site / "_rocm_sdk_core/bin", site / "_rocm_sdk_libraries/bin"
    if (rocm_core / "amdhip64_7.dll").exists():
        os.environ.setdefault("CT2_CUDA_ALLOCATOR", "cub_caching")  # as on Linux (see dictate.py)
        _add_dll_dirs([rocm_core, rocm_libs])
        paths = [rocm_core / "amdhip64_7.dll", rocm_libs / "hipblas.dll"]
        for path in paths:
            ctypes.WinDLL(str(path))
        return "rocm", [str(p) for p in paths]
    cublas = site / "nvidia/cublas/bin"
    if (cublas / "cublas64_12.dll").exists():
        _add_dll_dirs(sorted((site / "nvidia").glob("*/bin")))
        paths = [cublas / "cublasLt64_12.dll", cublas / "cublas64_12.dll"]  # Lt first: cublas needs it
        for path in paths:
            ctypes.WinDLL(str(path))
        return "cuda", [str(p) for p in paths]
    return "cpu", []


def short_path(path: str) -> str:
    """The 8.3 form of a path (ASCII, so C/C++ libraries that open files by narrow path manage)."""
    buf = ctypes.create_unicode_buffer(1024)
    n = kernel32().GetShortPathNameW(path, buf, len(buf))
    return buf.value if 0 < n < len(buf) else path


def key_input(vk: int, scan: int = 0, up: bool = False, extended: bool = False) -> INPUT:
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    return INPUT(type=INPUT_KEYBOARD, u=INPUTUNION(ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, dwExtraInfo=MARK)))


def send(inputs: list[INPUT]) -> int:
    """All events in one call, so no real key event can land between them."""
    array = (INPUT * len(inputs))(*inputs)
    return user32().SendInput(len(inputs), array, ctypes.sizeof(INPUT))


class Hotkey(threading.Thread):
    """The push-to-talk key through a low-level keyboard hook. Windows silently drops a hook whose
    callback is slow, so the callback decides at once, and the hook is renewed every minute while the
    key is up. A combination (Ctrl+Alt+D) matches when exactly its modifiers are down, as Windows
    knows them. A modifier on its own (Right Ctrl) is kept from the system while it is held; if
    another key goes down with it, that was a shortcut: the dictation is cancelled and the modifier
    and the key are replayed. A held key that stops repeating went up where the hook couldn't see it."""

    def __init__(self, cfg, emit):
        super().__init__(name="win-key", daemon=True)
        self.cfg, self.emit = cfg, emit
        self.trigger = keys.parse(cfg.trigger)
        self.vk, self.scan, self.extended = keys.windows(self.trigger)
        self.down = False  # our key is down (and kept from the system)
        self.replayed = False  # the system was given the modifier after all (a shortcut)
        self.mods: set[str] = set()  # modifiers down, as the hook saw them
        self.injected = False  # the press came from a program (which sends no repeats)
        self.last_seen = 0.0  # the last press or repeat of our key
        self.renewed = 0.0
        self.proc = HOOKPROC(self._callback)  # referenced for as long as the hook may call it
        self.hook = None

    def modifiers(self) -> set[str]:
        """The modifier groups down now, as Windows knows them. The hook's own record misses key-ups
        that happen where it can't see them: after Win+L, the Win key goes up on the lock screen."""
        try:
            u = user32()
            return {group for group, vks in GROUP_KEYS.items() if any(u.GetAsyncKeyState(vk) & 0x8000 for vk in vks)}
        except (OSError, AttributeError):  # not Windows (the unit tests): what the hook saw
            return set(self.mods)

    def matches(self, kb) -> bool:
        return ((self.vk is None or kb.vkCode == self.vk) and (self.scan is None or kb.scanCode == self.scan)
                and (self.extended is None or bool(kb.flags & LLKHF_EXTENDED) == self.extended))

    def _callback(self, code, wparam, lparam):
        if code == 0:  # HC_ACTION
            kb = KBDLLHOOKSTRUCT.from_address(lparam)
            if kb.dwExtraInfo != MARK:  # keys sent by other programs count (key remappers, on-screen keyboards)
                try:
                    if self._key(kb, wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)):
                        return 1  # kept from the system and the apps
                except Exception:
                    log.exception("keyboard hook")
        return user32().CallNextHookEx(None, code, wparam, lparam)

    def _key(self, kb, down: bool) -> bool:
        """Handle one key event; True keeps it from the system."""
        if self.matches(kb):
            if down:
                self.last_seen = time.monotonic()
                if self.down:
                    return not self.replayed  # a repeat
                if self.trigger.lone_modifier or self.modifiers() == set(self.trigger.mods):
                    self.down, self.replayed = True, False
                    self.injected = bool(kb.flags & LLKHF_INJECTED)
                    if self.trigger.mods & {"alt", "super"}:  # Alt or Win alone would open a menu
                        send([key_input(MASK_KEY), key_input(MASK_KEY, up=True)])
                    self.emit("press", time.monotonic())
                    return True
                return False  # e.g. the D of Ctrl+Shift+D when the key is Ctrl+Alt+D
            if not self.down:
                return False
            self.down = False
            self.emit("release", time.monotonic())
            return not self.replayed  # the system saw the replayed press: it must see the release
        group = MODIFIER_GROUPS.get(kb.vkCode)
        if group:
            (self.mods.add if down else self.mods.discard)(group)
        if down and self.down and self.trigger.lone_modifier and not self.replayed:
            self.replayed = True
            self.emit("cancel")
            send([key_input(self.vk, extended=self.vk in (0xA3, 0xA5, 0x5B, 0x5C)),  # right Ctrl/Alt, Win: E0 keys
                  key_input(kb.vkCode, kb.scanCode, extended=bool(kb.flags & LLKHF_EXTENDED))])
            return True  # this key went out again just now, after the modifier
        return False

    def tick(self, now: float) -> None:
        """Twice a second. A key held on a keyboard repeats; none for REPEAT_GAP means it went up where
        the hook couldn't see it (the lock screen, an administrator's window) or Windows dropped the
        hook, so it is taken as released rather than recording on and typing into another window."""
        if self.down and not self.injected and now - self.last_seen > REPEAT_GAP:
            log.info("the key's release was not seen; taking it as released")
            self.down = False
            self.emit("release", self.last_seen)

    def _install(self):
        return user32().SetWindowsHookExW(WH_KEYBOARD_LL, self.proc, kernel32().GetModuleHandleW(None), 0)

    def run(self):
        u = user32()
        self.hook = self._install()
        if not self.hook:
            self.emit("fatal", f"The dictation key could not be set up (error {ctypes.get_last_error()}).")
            return
        log.info("push-to-talk key: %s (Windows keyboard hook)", self.trigger)
        self.emit("key_ready", self.cfg.trigger)
        u.SetTimer(None, 0, 500, None)
        self.renewed = time.monotonic()
        msg = MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_TIMER:
                now = time.monotonic()
                self.tick(now)
                if not self.down and now - self.renewed > 60:  # renew the hook in case Windows dropped it
                    u.UnhookWindowsHookEx(self.hook)
                    self.hook = self._install()
                    self.renewed = now
            u.DispatchMessageW(ctypes.byref(msg))


class Keyboard:
    """Presses Shift+Insert with SendInput: virtual-key codes, so the layout doesn't matter; works in
    GUI apps and in terminals (Windows Terminal, the console, PuTTY, Git Bash)."""

    def __init__(self):
        user32()  # raises OSError if this isn't Windows
        self.lock = threading.Lock()

    def shift_insert(self) -> None:
        with self.lock:
            n = send([key_input(0x10, 0x2A), key_input(0x2D, 0x52, extended=True),
                      key_input(0x2D, 0x52, up=True, extended=True), key_input(0x10, 0x2A, up=True)])
        if n != 4:
            raise OSError(f"SendInput sent {n} of 4 key events (error {ctypes.get_last_error()})")

    def modifiers_held(self) -> bool:
        """Ctrl, Alt, Shift or Win down: our Shift+Insert would arrive as e.g. Ctrl+Shift+Insert."""
        return any(user32().GetAsyncKeyState(vk) & 0x8000 for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C))

    def close(self) -> None:
        pass


class Clipboard(threading.Thread):
    """The clipboard, with the interface of dictate's SelectionOwner. Text is offered with delayed
    rendering: Windows asks our window for it (WM_RENDERFORMAT) when an app pastes, which tells the
    Paster that the paste happened. Our text is kept out of clipboard history and the cloud clipboard.
    All clipboard calls run on this thread; it never blocks, because a pasting app waits for it."""

    def __init__(self):
        super().__init__(name="clipboard", daemon=True)
        self.jobs: queue.Queue = queue.Queue()
        self.connected = threading.Event()
        self.served = threading.Condition()
        self.hwnd = None
        self.text, self.requests, self.owned = "", 0, False
        self.wndproc = WNDPROC(self._wndproc)  # referenced for as long as the window lives

    # --- called from other threads ---
    def call(self, fn, *args, timeout=3.0):
        if not self.connected.wait(timeout):
            log.warning("clipboard: not ready")
            return None
        done, box = threading.Event(), {}

        def job():
            try:
                box["value"] = fn(*args)
            except Exception as e:
                log.warning("clipboard: %s failed: %r", fn.__name__, e)
            finally:
                done.set()
        self.jobs.put(job)
        user32().PostMessageW(self.hwnd, WM_APP, 0, 0)
        done.wait(timeout)
        return box.get("value")

    def publish(self, text: str, immediate: bool = False) -> bool:
        """Put text on the clipboard. immediate: the data itself, so it stays when dictate quits."""
        return bool(self.call(self._publish, text, immediate))

    def counts(self):
        return self.requests, 0

    def wait_served(self, base, timeout: float) -> bool:
        with self.served:
            return self.served.wait_for(lambda: (self.requests, 0) != tuple(base), timeout)

    def read_clipboard(self):
        return self.call(self._read)

    def restore_clipboard(self, data: bytes) -> bool:
        return bool(self.call(self._restore, data))

    # --- the clipboard thread ---
    def run(self):
        u, k = user32(), kernel32()
        cls = WNDCLASSW(lpfnWndProc=self.wndproc, hInstance=k.GetModuleHandleW(None), lpszClassName="DictateClipboard")
        u.RegisterClassW(ctypes.byref(cls))
        self.hwnd = u.CreateWindowExW(0, "DictateClipboard", "dictate clipboard", 0, 0, 0, 0, 0, HANDLE(-3),  # HWND_MESSAGE
                                      None, cls.hInstance, None)
        if not self.hwnd:
            log.error("clipboard: no window (error %d)", ctypes.get_last_error())
            return
        self.formats = [u.RegisterClipboardFormatW(name) for name in (
            "ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory", "CanUploadToCloudClipboard")]
        self.connected.set()
        msg = MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.DispatchMessageW(ctypes.byref(msg))

    def _wndproc(self, hwnd, message, wparam, lparam):
        u = user32()
        if message == WM_APP:
            while True:
                try:
                    self.jobs.get_nowait()()
                except queue.Empty:
                    break
            return 0
        if message == WM_RENDERFORMAT:  # an app pastes: hand over the text now
            if wparam == CF_UNICODETEXT:
                u.SetClipboardData(CF_UNICODETEXT, self._global(self.text))
                with self.served:
                    self.requests += 1
                    self.served.notify_all()
            return 0
        if message == WM_RENDERALLFORMATS:  # we are going away: leave the text behind
            if self._open():
                if u.GetClipboardOwner() == self.hwnd:
                    u.SetClipboardData(CF_UNICODETEXT, self._global(self.text))
                u.CloseClipboard()
            return 0
        if message == WM_DESTROYCLIPBOARD:  # someone else took the clipboard (copied something)
            self.owned = False
            return 0
        return u.DefWindowProcW(hwnd, message, wparam, lparam)

    def _open(self) -> bool:
        for _ in range(10):  # another app may have it open for a moment
            if user32().OpenClipboard(self.hwnd):
                return True
            time.sleep(0.02)
        return False

    @staticmethod
    def _global(text: str):
        data = (text + "\0").encode("utf-16-le")
        k = kernel32()
        handle = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
        ctypes.memmove(k.GlobalLock(handle), data, len(data))
        k.GlobalUnlock(handle)
        return handle  # the clipboard owns it after SetClipboardData

    def _mark(self) -> None:
        """Keep our text out of clipboard history, the cloud clipboard and clipboard managers."""
        k, u = kernel32(), user32()
        for fmt in self.formats:
            handle = k.GlobalAlloc(GMEM_MOVEABLE, 4)
            ctypes.memmove(k.GlobalLock(handle), b"\0\0\0\0", 4)
            k.GlobalUnlock(handle)
            u.SetClipboardData(fmt, handle)

    def _publish(self, text: str, immediate: bool) -> bool:
        if not self._open():
            return False
        u = user32()
        try:
            u.EmptyClipboard()  # our window becomes the owner (we get WM_DESTROYCLIPBOARD first if we were)
            self.text, self.owned = text, True
            with self.served:
                self.requests = 0
            u.SetClipboardData(CF_UNICODETEXT, self._global(text) if immediate else None)  # None: delayed
            self._mark()
            return True
        finally:
            u.CloseClipboard()

    def _read(self):
        """The clipboard's text as UTF-8 bytes, or None (empty, or not text, e.g. an image)."""
        if self.owned:
            return self.text.encode("utf-8")
        if not self._open():
            return None
        try:
            handle = user32().GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            pointer = kernel32().GlobalLock(handle)
            try:
                return ctypes.wstring_at(pointer).encode("utf-8") if pointer else None
            finally:
                kernel32().GlobalUnlock(handle)
        finally:
            user32().CloseClipboard()

    def _restore(self, data: bytes) -> bool:
        if not self.owned:  # the user copied something meanwhile: keep it
            return False
        return self._publish(data.decode("utf-8", "replace"), True)


def check(cfg, report) -> None:
    """--check on Windows."""
    try:
        import sounddevice as sd
        mic = sd.query_devices(kind="input")
        report("microphone", True, f"{mic['name']} (default input)")
    except Exception as e:
        report("microphone", False, f"no input device ({e})")
    hook = user32().SetWindowsHookExW(WH_KEYBOARD_LL, HOOKPROC(lambda c, w, lp: user32().CallNextHookEx(None, c, w, lp)),
                                      kernel32().GetModuleHandleW(None), 0)
    report("keyboard hook", bool(hook), "can be installed" if hook else f"error {ctypes.get_last_error()}")
    if hook:
        user32().UnhookWindowsHookEx(hook)
    report("SendInput", ctypes.sizeof(INPUT) == 40, f"INPUT is {ctypes.sizeof(INPUT)} bytes")
    try:
        keys.windows(keys.parse(cfg.trigger))
        report("key", True, keys.label(keys.parse(cfg.trigger), "win32"))
    except ValueError as e:
        report("key", False, str(e))
    start = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs"
    report("Start menu shortcut", (start / "Dictate.lnk").exists(), str(start / "Dictate.lnk"))
    report("start at login", (start / "Startup/Dictate.lnk").exists(), str(start / "Startup/Dictate.lnk"), required=False)
    arch = Path(sysconfig.get_paths()["purelib"]) / "_rocm_sdk_core/lib/llvm/bin/amdgpu-arch.exe"
    if arch.exists():
        import subprocess
        out = subprocess.run([str(arch)], capture_output=True, text=True, creationflags=0x08000000).stdout.split()
        report("AMD GPU", bool(out), ", ".join(out) or "amdgpu-arch found none")
