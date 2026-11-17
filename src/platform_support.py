#!/usr/bin/env python3
"""
Platform abstraction: everything that differs between Raspberry Pi/Linux (X11)
and Windows lives here.

  Linux  : mpv IPC via Unix socket, window moves via xdotool, priority via nice
  Windows: mpv IPC via named pipe, window moves via Win32 SetWindowPos,
           priority via process priority class, mpv killed with Python via a Job Object
"""

import glob
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import uuid

IS_WINDOWS = sys.platform.startswith("win")

PLATFORM_DEFAULTS = {"vo": "gpu", "hwdec": "auto-safe"} if IS_WINDOWS else {}


# =============================================================================
# mpv executable
# =============================================================================

_mpv_cache = None


def mpv_executable(settings=None):
    """Explicit setting > PATH > common Windows install locations > ./mpv/mpv.exe"""
    global _mpv_cache
    configured = (settings or {}).get("mpv_path")
    if configured and os.path.isfile(configured):
        return configured
    if _mpv_cache and os.path.isfile(_mpv_cache):
        return _mpv_cache
    found = shutil.which("mpv")
    if not found and IS_WINDOWS:
        project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        patterns = [
            os.path.join(project, "mpv", "mpv.exe"),
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "*", "mpv.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "*", "mpv.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "*", "mpv.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages", "*", "mpv.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages", "*", "*", "mpv.exe"),
            os.path.join(os.path.expanduser("~"), "scoop", "shims", "mpv.exe"),
            os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "chocolatey", "bin", "mpv.exe"),
        ]
        for pat in patterns:
            hits = glob.glob(pat)
            if hits:
                found = hits[0]
                break
    _mpv_cache = found or "mpv"
    return _mpv_cache


# =============================================================================
# IPC (Unix socket on Linux, named pipe on Windows)
# =============================================================================

def new_ipc_path(zone_id):
    tag = f"mpv-mv-zone-{zone_id}-{uuid.uuid4().hex[:8]}"
    if IS_WINDOWS:
        return r"\\.\pipe" + "\\" + tag
    return os.path.join(tempfile.gettempdir(), tag + ".sock")


def ipc_exists(path):
    if not path:
        return False
    if IS_WINDOWS:
        try:
            return os.path.basename(path) in os.listdir("\\\\.\\pipe\\")
        except OSError:
            return False
    return os.path.exists(path)


def ipc_cleanup(path):
    if not path or IS_WINDOWS:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _read_reply(read_chunk, rid, deadline):
    buf = b""
    while time.time() < deadline:
        chunk = read_chunk()
        if not chunk:
            return None
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("request_id") == rid:
                return msg
    return None


def ipc_request(path, command, rid, timeout=1.0):
    """Send one JSON command and return the reply with matching request_id.
    Raises on connection errors."""
    data = (json.dumps(command) + "\n").encode()
    deadline = time.time() + timeout

    if not IS_WINDOWS:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(path)
            sock.sendall(data)
            return _read_reply(lambda: sock.recv(65536), rid, deadline)

    # Windows: blocking file I/O on the pipe, bounded by a worker thread
    result = {}

    def worker():
        f = None
        for _ in range(20):
            try:
                f = open(path, "r+b", buffering=0)
                break
            except OSError as e:
                if getattr(e, "winerror", None) == 231:   # ERROR_PIPE_BUSY
                    time.sleep(0.02)
                    continue
                result["error"] = e
                return
        if f is None:
            result["error"] = OSError("pipe busy")
            return
        try:
            f.write(data)
            result["reply"] = _read_reply(lambda: f.read(65536), rid, deadline)
        except OSError as e:
            result["error"] = e
        finally:
            try:
                f.close()
            except OSError:
                pass

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout)
    if "error" in result:
        raise result["error"]
    return result.get("reply")


# =============================================================================
# Process start (priority, kill-with-parent)
# =============================================================================

def build_launch(cmd, nice=0):
    """Return (cmd, popen_kwargs) for starting mpv with lower priority."""
    if IS_WINDOWS:
        flags = 0x08000000                      # CREATE_NO_WINDOW (no stray console)
        if nice >= 15:
            flags |= 0x00000040                 # IDLE_PRIORITY_CLASS
        elif nice > 0:
            flags |= 0x00004000                 # BELOW_NORMAL_PRIORITY_CLASS
        return cmd, {"creationflags": flags}
    if nice > 0 and shutil.which("nice"):
        cmd = ["nice", "-n", str(min(int(nice), 19))] + cmd
    return cmd, {"start_new_session": True}


_job = None


def attach_to_job(process):
    """Windows: put mpv into a Job Object with KILL_ON_JOB_CLOSE so every tile
    disappears when the Python server stops or crashes (no orphaned windows)."""
    global _job
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if _job is None:
            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ulonglong) for n in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class BASIC(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                            ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t),
                            ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class EXTENDED(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC),
                            ("IoInfo", IO_COUNTERS),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            k32.CreateJobObjectW.restype = wintypes.HANDLE
            job = k32.CreateJobObjectW(None, None)
            info = EXTENDED()
            info.BasicLimitInformation.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            k32.SetInformationJobObject(wintypes.HANDLE(job), 9,   # JobObjectExtendedLimitInformation
                                        ctypes.byref(info), ctypes.sizeof(info))
            _job = job
        k32.AssignProcessToJobObject(wintypes.HANDLE(_job), wintypes.HANDLE(int(process._handle)))
    except Exception as e:  # noqa: BLE001
        print(f"Job object: {e}")


# =============================================================================
# Windows: monitors and windows (Win32 via ctypes)
# =============================================================================

_U32 = None


def _user32():
    """user32 with explicit signatures (64-bit safe handle passing)."""
    global _U32
    if _U32 is None:
        import ctypes
        from ctypes import wintypes
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.FindWindowW.restype = wintypes.HWND
        u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.SetWindowPos.restype = wintypes.BOOL
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.GetWindowRect.restype = wintypes.BOOL
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        _U32 = u
    return _U32


def enable_dpi_awareness():
    """Without this, Windows display scaling (125 %/150 %) would distort coordinates."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
        except Exception:  # noqa: BLE001
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception as e:  # noqa: BLE001
        print(f"DPI awareness: {e}")


def monitors():
    """List of monitors in EnumDisplayMonitors order (same order mpv's --screen uses)."""
    if not IS_WINDOWS:
        return []
    import ctypes
    from ctypes import wintypes
    user32 = _user32()

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]

    result = []
    PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                              ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def cb(hmon, hdc, rect, lparam):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(wintypes.HMONITOR(hmon), ctypes.byref(info)):
            r = info.rcMonitor
            result.append({"index": len(result), "name": info.szDevice,
                           "x": r.left, "y": r.top,
                           "width": r.right - r.left, "height": r.bottom - r.top,
                           "primary": bool(info.dwFlags & 1)})
        return True

    user32.EnumDisplayMonitors(None, None, PROC(cb), 0)
    return result


def monitor_origin(index):
    mons = monitors()
    if not mons:
        return 0, 0
    m = mons[index] if 0 <= int(index) < len(mons) else next((m for m in mons if m["primary"]), mons[0])
    return m["x"], m["y"]


def _find_window(title):
    return _user32().FindWindowW(None, title)


def win_move(title, x, y, w, h):
    """Move/resize a window to absolute virtual-desktop coordinates. Returns True if found."""
    from ctypes import wintypes
    user32 = _user32()
    hwnd = _find_window(title)
    if not hwnd:
        return False
    HWND_TOPMOST = wintypes.HWND(-1)
    SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0010, 0x0040
    return bool(user32.SetWindowPos(hwnd, HWND_TOPMOST, int(x), int(y), int(w), int(h),
                                    SWP_NOACTIVATE | SWP_SHOWWINDOW))


def win_rect(title):
    import ctypes
    from ctypes import wintypes
    hwnd = _find_window(title)
    if not hwnd:
        return None
    r = wintypes.RECT()
    if not _user32().GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return {"x": r.left, "y": r.top, "width": r.right - r.left, "height": r.bottom - r.top}


def win_raise(title):
    from ctypes import wintypes
    hwnd = _find_window(title)
    if not hwnd:
        return False
    SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x0002, 0x0001, 0x0010
    return bool(_user32().SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0,
                                       SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE))


def kill_orphaned_tiles():
    """Windows: close mpv windows left over from a crashed previous run ("mvzone-*")."""
    if not IS_WINDOWS:
        return 0
    import ctypes
    from ctypes import wintypes
    user32 = _user32()
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    pids = set()
    PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, lparam):
        buf = ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(hwnd, buf, 64)
        if buf.value.startswith("mvzone-"):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pids.add(pid.value)
        return True

    user32.EnumWindows(PROC(cb), 0)
    k32.OpenProcess.restype = wintypes.HANDLE
    for pid in pids:
        h = k32.OpenProcess(0x0001, False, pid)          # PROCESS_TERMINATE
        if h:
            k32.TerminateProcess(wintypes.HANDLE(h), 0)
            k32.CloseHandle(wintypes.HANDLE(h))
    return len(pids)


# =============================================================================
# System stats on Windows (psutil, optional)
# =============================================================================

def windows_stats():
    st = {"cores": os.cpu_count() or 1}
    try:
        import psutil
        st["cpu_percent"] = psutil.cpu_percent(interval=None)
        st["load"] = round(st["cpu_percent"] / 100 * st["cores"], 2)
        vm = psutil.virtual_memory()
        st["mem_total_mb"] = vm.total // (1024 * 1024)
        st["mem_avail_mb"] = vm.available // (1024 * 1024)
    except Exception:  # noqa: BLE001
        pass
    try:
        import platform
        st["model"] = f"Windows {platform.release()} ({platform.node()})"
    except Exception:  # noqa: BLE001
        pass
    return st
