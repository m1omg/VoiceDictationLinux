"""Windows backends for dictate, written with ctypes only.

Nothing here touches a Windows DLL at import time, so the module imports on any OS (the unit tests
check the structure layouts on Linux). Structures use fixed-width types: ctypes.wintypes.DWORD is
8 bytes on 64-bit Linux, so it would make those checks meaningless.
"""
from __future__ import annotations

import ctypes
import functools
import logging
import os
import sysconfig
import time
from pathlib import Path

log = logging.getLogger("dictate")

BOOL, UINT, DWORD, WORD = ctypes.c_int32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint16
HANDLE = ctypes.c_void_p
ERROR_ALREADY_EXISTS = 183


@functools.cache
def kernel32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetTickCount64.restype = ctypes.c_uint64
    k.QueryUnbiasedInterruptTime.argtypes = [ctypes.POINTER(ctypes.c_uint64)]
    k.QueryUnbiasedInterruptTime.restype = BOOL
    k.CreateMutexW.argtypes = [ctypes.c_void_p, BOOL, ctypes.c_wchar_p]
    k.CreateMutexW.restype = HANDLE
    k.CloseHandle.argtypes = [HANDLE]
    k.CloseHandle.restype = BOOL
    return k


@functools.cache
def user32():
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.OpenInputDesktop.argtypes = [DWORD, BOOL, DWORD]
    u.OpenInputDesktop.restype = HANDLE
    u.GetUserObjectInformationW.argtypes = [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD)]
    u.GetUserObjectInformationW.restype = BOOL
    u.CloseDesktop.argtypes = [HANDLE]
    u.CloseDesktop.restype = BOOL
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


def _add_dll_dirs(dirs) -> None:
    """For DLLs loaded by path (os.add_dll_directory) and for plain LoadLibrary calls (PATH)."""
    for d in dirs:
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
    k = kernel32()
    k.GetShortPathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, DWORD]
    k.GetShortPathNameW.restype = DWORD
    buf = ctypes.create_unicode_buffer(1024)
    n = k.GetShortPathNameW(path, buf, len(buf))
    return buf.value if 0 < n < len(buf) else path
