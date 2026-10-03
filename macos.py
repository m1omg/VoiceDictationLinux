"""macOS backends for dictate, written with pyobjc (Quartz, AppKit).

The pyobjc imports happen inside the functions and classes, so the module imports on any OS.
"""
from __future__ import annotations

import logging

log = logging.getLogger("dictate")


def screen_locked() -> bool:
    try:
        import Quartz
        session = Quartz.CGSessionCopyCurrentDictionary() or {}
        return bool(session.get("CGSSessionScreenIsLocked", False))
    except Exception as e:
        log.debug("screen lock query failed: %s", e)
        return False
