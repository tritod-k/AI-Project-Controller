"""Conservative local PID probe: timestamps are observations, never death evidence."""

import os
import socket


def probe_owner(owner):
    if owner.hostname != socket.gethostname() or owner.process_id <= 0:
        return "UNKNOWN"
    if owner.process_id == os.getpid():
        return "ALIVE"
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, owner.process_id)
        if not handle:
            return "DEAD" if ctypes.get_last_error() == 87 else "UNKNOWN"
        try:
            code = wintypes.DWORD()
            kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return "UNKNOWN"
            return "ALIVE" if code.value == 259 else "DEAD"
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(owner.process_id, 0)
    except ProcessLookupError:
        return "DEAD"
    except OSError:
        return "UNKNOWN"
    return "ALIVE"
