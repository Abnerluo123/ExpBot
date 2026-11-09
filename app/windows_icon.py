"""Apply the app's own multi-size icon to its isolated Edge app window."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

_ICON_HANDLES = []
_APP_NAME = 'ExpBot'
_APP_ID = 'ExpBot.Desktop.v1.0.0'


def apply_window_icon(process_id: int, icon_path: Path, launcher_path: Path):
    if os.name != 'nt' or not icon_path.is_file():
        return
    import ctypes
    import uuid
    from ctypes import wintypes as w

    class GUID(ctypes.Structure):
        _fields_ = [('Data1', w.DWORD), ('Data2', w.WORD), ('Data3', w.WORD), ('Data4', ctypes.c_ubyte * 8)]
        @classmethod
        def parse(cls, text):
            return cls.from_buffer_copy(uuid.UUID(text).bytes_le)
    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [('fmtid', GUID), ('pid', w.DWORD)]
    class VALUE(ctypes.Union):
        _fields_ = [('pointer', ctypes.c_void_p), ('padding', ctypes.c_ulonglong * 2)]
    class PROPVARIANT(ctypes.Structure):
        _fields_ = [('vt', w.WORD), ('r1', w.WORD), ('r2', w.WORD), ('r3', w.WORD), ('value', VALUE)]

    user = ctypes.WinDLL('user32', use_last_error=True)
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    ole = ctypes.OleDLL('ole32')
    shcore = ctypes.WinDLL('shcore', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    user.EnumWindows.argtypes = [callback_type, w.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    user.IsWindowVisible.argtypes = [w.HWND]
    user.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    user.SetWindowTextW.argtypes = [w.HWND, w.LPCWSTR]
    user.GetSystemMetrics.argtypes = [ctypes.c_int]
    user.GetSystemMetrics.restype = ctypes.c_int
    get_dpi = getattr(user, 'GetDpiForWindow', None)
    get_metric_for_dpi = getattr(user, 'GetSystemMetricsForDpi', None)
    set_thread_dpi = getattr(user, 'SetThreadDpiAwarenessContext', None)
    monitor_from_window = getattr(user, 'MonitorFromWindow', None)
    get_dpi_for_monitor = getattr(shcore, 'GetDpiForMonitor', None)
    if get_dpi:
        get_dpi.argtypes = [w.HWND]
        get_dpi.restype = w.UINT
    if get_metric_for_dpi:
        get_metric_for_dpi.argtypes = [ctypes.c_int, w.UINT]
        get_metric_for_dpi.restype = ctypes.c_int
    if set_thread_dpi:
        set_thread_dpi.argtypes = [w.HANDLE]
        set_thread_dpi.restype = w.HANDLE
    if monitor_from_window:
        monitor_from_window.argtypes = [w.HWND, w.DWORD]
        monitor_from_window.restype = w.HMONITOR
    if get_dpi_for_monitor:
        get_dpi_for_monitor.argtypes = [w.HMONITOR, ctypes.c_int, ctypes.POINTER(w.UINT), ctypes.POINTER(w.UINT)]
        get_dpi_for_monitor.restype = ctypes.c_long
    user.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT]
    user.LoadImageW.restype = w.HANDLE
    user.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM, w.UINT, w.UINT, ctypes.POINTER(ctypes.c_size_t)]
    shell.SHGetPropertyStoreForWindow.argtypes = [w.HWND, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    shell.SHGetPropertyStoreForWindow.restype = ctypes.c_long
    iid = GUID.parse('886d8eeb-8cf2-4446-8d02-cdba1dbdcf99')
    fmtid = GUID.parse('9f4c2855' + '-9f79-4b39-a8d0-e1d42de1d5f3')
    # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2: Python workers default to
    # unaware, which virtualizes GetSystemMetrics/GetDpiForSystem/Monitor DPI
    # back to 96 even on a 200% display.
    DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = w.HANDLE(-4)

    def properties(hwnd):
        store = ctypes.c_void_p()
        if shell.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(iid), ctypes.byref(store)) < 0:
            return
        table = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        set_value = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(table[6])
        commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(table[7])
        release = ctypes.WINFUNCTYPE(w.ULONG, ctypes.c_void_p)(table[2])
        try:
            relaunch_path = launcher_path.with_name(_APP_NAME + '.exe')
            for pid, text in ((5, _APP_ID), (3, str(icon_path) + ',0'),
                              (2, '"' + str(relaunch_path) + '"'), (4, _APP_NAME)):
                buffer = ctypes.create_unicode_buffer(text)
                value = PROPVARIANT(); value.vt = 31
                value.value.pointer = ctypes.cast(buffer, ctypes.c_void_p).value
                key = PROPERTYKEY(fmtid, pid)
                set_value(store, ctypes.byref(key), ctypes.byref(value))
            commit(store)
        finally:
            release(store)

    def window_dpi(hwnd):
        dpi = int(get_dpi(hwnd)) if get_dpi else 0
        if dpi:
            return dpi
        # Fallback when the HWND is not yet DPI-attributed: read the monitor
        # under a per-monitor-aware thread (unaware callers see 96 here).
        if monitor_from_window and get_dpi_for_monitor:
            monitor = monitor_from_window(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
            if monitor:
                x = w.UINT(); y = w.UINT()
                if get_dpi_for_monitor(monitor, 0, ctypes.byref(x), ctypes.byref(y)) >= 0 and x.value:
                    return int(x.value)
        return 96

    def icon_dimensions(dpi):
        def metric(index, fallback):
            value = get_metric_for_dpi(index, dpi) if get_metric_for_dpi and dpi else 0
            return value or user.GetSystemMetrics(index) or fallback

        # SM_CXSMICON is 16 logical px; Win11 taskbar buttons render at ~24
        # logical px (measured 48 device px at 200% DPI). Supplying only the
        # SM_* size makes the shell upscale ICON_SMALL (32→48 at 200%) and the
        # taskbar looks pixelated while the Chromium titlebar stays crisp from
        # its own high-res favicon paint path.
        scale = max(dpi, 96) / 96
        sm_small = metric(49, 16)
        sm_large = metric(11, 32)
        taskbar = max(1, round(24 * scale))   # Win11 taskbar glyph
        caption = max(1, round(16 * scale))  # title-bar / SM_CXSMICON logical
        app = max(1, round(32 * scale))      # SM_CXICON logical
        small = max(sm_small, caption, taskbar)
        large = max(sm_large, app, small)
        return (small, small), (large, large)

    handles = {}

    def apply(hwnd):
        properties(hwnd)
        user.SetWindowTextW(hwnd, _APP_NAME)
        small, large = icon_dimensions(window_dpi(hwnd))
        # WM_SETICON accepts ICON_SMALL/ICON_BIG only; ICON_SMALL2 (2) is
        # query-only and can overwrite the large icon in DefWindowProc.
        for kind, dimensions in ((0, small), (1, large)):
            if dimensions not in handles:
                handle = user.LoadImageW(None, str(icon_path), 1, *dimensions, 0x10)
                if handle:
                    handles[dimensions] = handle
                    _ICON_HANDLES.append(handle)
            handle = handles.get(dimensions)
            if handle:
                result = ctypes.c_size_t()
                user.SendMessageTimeoutW(hwnd, 0x80, kind, handle, 2, 500, ctypes.byref(result))

    # Match the window's real monitor DPI before any metric/LoadImage calls.
    previous_dpi_ctx = set_thread_dpi(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) if set_thread_dpi else None
    initialized = ole.CoInitializeEx(None, 2) in (0, 1)
    try:
        found = set()
        # Edge paints its favicon a little later; reapply after that update.
        for _ in range(24):
            def visit(hwnd, _):
                pid = w.DWORD()
                user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if user.IsWindowVisible(hwnd):
                    title = ctypes.create_unicode_buffer(256)
                    user.GetWindowTextW(hwnd, title, 256)
                    # A second launch may forward to the existing isolated Edge
                    # process. Its app window retains this exact page title.
                    owned = pid.value == process_id or title.value == _APP_NAME
                    if owned:
                        name = ctypes.create_unicode_buffer(80)
                        user.GetClassNameW(hwnd, name, 80)
                        if name.value == 'Chrome_WidgetWin_1':
                            apply(hwnd); found.add(hwnd)
                return True
            user.EnumWindows(callback_type(visit), 0)
            if found and _ >= 10:
                break
            time.sleep(.5)
    finally:
        if initialized:
            ole.CoUninitialize()
        if set_thread_dpi and previous_dpi_ctx is not None:
            set_thread_dpi(previous_dpi_ctx)


def schedule_window_icon(process_id, icon_path, launcher_path):
    def work():
        try:
            apply_window_icon(process_id, Path(icon_path), Path(launcher_path))
        except Exception:
            # A taskbar decoration cannot interrupt the local document workflow.
            pass
    threading.Thread(target=work, name='jianbao-window-icon', daemon=True).start()
