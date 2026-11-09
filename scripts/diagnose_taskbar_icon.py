"""Read-only diagnose of ExpBot HWND icons, DPI, and taskbar pixels."""
from __future__ import annotations

import ctypes
import json
import os
import sys
from ctypes import wintypes as w
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'taskbar-icon-diagnose'
OUT.mkdir(parents=True, exist_ok=True)
APP_TITLE = 'ExpBot'
ICON_PATH = ROOT / 'app' / 'web' / 'icon.ico'

if os.name != 'nt':
    raise SystemExit('Windows only')

user = ctypes.WinDLL('user32', use_last_error=True)
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
shell = ctypes.WinDLL('shell32', use_last_error=True)
gdi = ctypes.WinDLL('gdi32', use_last_error=True)
shcore = ctypes.WinDLL('shcore', use_last_error=True)

# DPI awareness context constants
DPI_AWARENESS_CONTEXT_UNAWARE = w.HANDLE(-1)
DPI_AWARENESS_CONTEXT_SYSTEM_AWARE = w.HANDLE(-2)
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE = w.HANDLE(-3)
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = w.HANDLE(-4)

user.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM), w.LPARAM]
user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
user.IsWindowVisible.argtypes = [w.HWND]
user.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
user.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user.SendMessageW.restype = ctypes.c_ssize_t
user.GetSystemMetrics.argtypes = [ctypes.c_int]
user.GetSystemMetrics.restype = ctypes.c_int
user.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT]
user.LoadImageW.restype = w.HANDLE
user.DestroyIcon.argtypes = [w.HICON]
user.GetIconInfo.argtypes = [w.HICON, ctypes.c_void_p]
user.GetDC.argtypes = [w.HWND]
user.ReleaseDC.argtypes = [w.HWND, w.HDC]
user.PrintWindow.argtypes = [w.HWND, w.HDC, w.UINT]
gdi.GetObjectW.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p]
gdi.DeleteObject.argtypes = [w.HGDIOBJ]
gdi.CreateCompatibleDC.argtypes = [w.HDC]
gdi.CreateCompatibleBitmap.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int]
gdi.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
gdi.BitBlt.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HDC, ctypes.c_int, ctypes.c_int, w.DWORD]
gdi.DeleteDC.argtypes = [w.HDC]
gdi.GetDIBits.argtypes = [w.HDC, w.HBITMAP, w.UINT, w.UINT, ctypes.c_void_p, ctypes.c_void_p, w.UINT]

get_dpi = getattr(user, 'GetDpiForWindow', None)
get_sys_dpi = getattr(user, 'GetDpiForSystem', None)
get_metric_for_dpi = getattr(user, 'GetSystemMetricsForDpi', None)
get_thread_dpi = getattr(user, 'GetThreadDpiAwarenessContext', None)
get_window_dpi_ctx = getattr(user, 'GetWindowDpiAwarenessContext', None)
are_dpi_equal = getattr(user, 'AreDpiAwarenessContextsEqual', None)
set_thread_dpi = getattr(user, 'SetThreadDpiAwarenessContext', None)
get_dpi_for_monitor = getattr(shcore, 'GetDpiForMonitor', None)
get_process_dpi = getattr(shcore, 'GetProcessDpiAwareness', None)
get_awareness_from_ctx = getattr(user, 'GetAwarenessFromDpiAwarenessContext', None)

if get_dpi:
    get_dpi.argtypes = [w.HWND]
    get_dpi.restype = w.UINT
if get_sys_dpi:
    get_sys_dpi.argtypes = []
    get_sys_dpi.restype = w.UINT
if get_metric_for_dpi:
    get_metric_for_dpi.argtypes = [ctypes.c_int, w.UINT]
    get_metric_for_dpi.restype = ctypes.c_int
if get_thread_dpi:
    get_thread_dpi.argtypes = []
    get_thread_dpi.restype = w.HANDLE
if get_window_dpi_ctx:
    get_window_dpi_ctx.argtypes = [w.HWND]
    get_window_dpi_ctx.restype = w.HANDLE
if are_dpi_equal:
    are_dpi_equal.argtypes = [w.HANDLE, w.HANDLE]
    are_dpi_equal.restype = w.BOOL
if set_thread_dpi:
    set_thread_dpi.argtypes = [w.HANDLE]
    set_thread_dpi.restype = w.HANDLE
if get_awareness_from_ctx:
    get_awareness_from_ctx.argtypes = [w.HANDLE]
    get_awareness_from_ctx.restype = ctypes.c_int
if get_process_dpi:
    get_process_dpi.argtypes = [w.HANDLE, ctypes.POINTER(ctypes.c_int)]
    get_process_dpi.restype = ctypes.c_long
if get_dpi_for_monitor:
    get_dpi_for_monitor.argtypes = [w.HMONITOR, ctypes.c_int, ctypes.POINTER(w.UINT), ctypes.POINTER(w.UINT)]
    get_dpi_for_monitor.restype = ctypes.c_long

class ICONINFO(ctypes.Structure):
    _fields_ = [
        ('fIcon', w.BOOL),
        ('xHotspot', w.DWORD),
        ('yHotspot', w.DWORD),
        ('hbmMask', w.HBITMAP),
        ('hbmColor', w.HBITMAP),
    ]

class BITMAP(ctypes.Structure):
    _fields_ = [
        ('bmType', ctypes.c_long),
        ('bmWidth', ctypes.c_long),
        ('bmHeight', ctypes.c_long),
        ('bmWidthBytes', ctypes.c_long),
        ('bmPlanes', w.WORD),
        ('bmBitsPixel', w.WORD),
        ('bmBits', ctypes.c_void_p),
    ]

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', w.DWORD),
        ('biWidth', ctypes.c_long),
        ('biHeight', ctypes.c_long),
        ('biPlanes', w.WORD),
        ('biBitCount', w.WORD),
        ('biCompression', w.DWORD),
        ('biSizeImage', w.DWORD),
        ('biXPelsPerMeter', ctypes.c_long),
        ('biYPelsPerMeter', ctypes.c_long),
        ('biClrUsed', w.DWORD),
        ('biClrImportant', w.DWORD),
    ]

class GUID(ctypes.Structure):
    _fields_ = [('Data1', w.DWORD), ('Data2', w.WORD), ('Data3', w.WORD), ('Data4', ctypes.c_ubyte * 8)]
    @classmethod
    def parse(cls, text):
        import uuid
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)

class PROPERTYKEY(ctypes.Structure):
    _fields_ = [('fmtid', GUID), ('pid', w.DWORD)]

class VALUE(ctypes.Union):
    _fields_ = [('pointer', ctypes.c_void_p), ('padding', ctypes.c_ulonglong * 2)]

class PROPVARIANT(ctypes.Structure):
    _fields_ = [('vt', w.WORD), ('r1', w.WORD), ('r2', w.WORD), ('r3', w.WORD), ('value', VALUE)]

shell.SHGetPropertyStoreForWindow.argtypes = [w.HWND, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
shell.SHGetPropertyStoreForWindow.restype = ctypes.c_long

AWARENESS_NAMES = {
    0: 'DPI_AWARENESS_INVALID',
    1: 'DPI_AWARENESS_UNAWARE',
    2: 'DPI_AWARENESS_SYSTEM_AWARE',
    3: 'DPI_AWARENESS_PER_MONITOR_AWARE',
}
PROCESS_AWARENESS = {
    0: 'PROCESS_DPI_UNAWARE',
    1: 'PROCESS_SYSTEM_DPI_AWARE',
    2: 'PROCESS_PER_MONITOR_DPI_AWARE',
}


def ctx_label(ctx):
    if not ctx or not are_dpi_equal:
        return f'raw={ctypes.cast(ctx, ctypes.c_void_p).value if ctx else None}'
    labels = []
    for name, handle in (
        ('UNAWARE', DPI_AWARENESS_CONTEXT_UNAWARE),
        ('SYSTEM', DPI_AWARENESS_CONTEXT_SYSTEM_AWARE),
        ('PER_MONITOR', DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE),
        ('PER_MONITOR_V2', DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2),
    ):
        try:
            if are_dpi_equal(ctx, handle):
                labels.append(name)
        except Exception:
            pass
    if get_awareness_from_ctx:
        try:
            a = get_awareness_from_ctx(ctx)
            labels.append(AWARENESS_NAMES.get(a, f'awareness={a}'))
        except Exception as exc:
            labels.append(f'awareness_err={exc}')
    return '|'.join(labels) or f'unknown({ctypes.cast(ctx, ctypes.c_void_p).value})'


def icon_bitmap_info(hicon):
    if not hicon:
        return None
    info = ICONINFO()
    if not user.GetIconInfo(hicon, ctypes.byref(info)):
        return {'error': 'GetIconInfo failed', 'handle': hicon}
    result = {'handle': int(hicon), 'fIcon': bool(info.fIcon), 'hotspot': [info.xHotspot, info.yHotspot]}
    for key, hbmp in (('color', info.hbmColor), ('mask', info.hbmMask)):
        if not hbmp:
            result[key] = None
            continue
        bmp = BITMAP()
        ok = gdi.GetObjectW(hbmp, ctypes.sizeof(bmp), ctypes.byref(bmp))
        result[key] = {
            'ok': bool(ok),
            'width': bmp.bmWidth,
            'height': bmp.bmHeight,
            'planes': bmp.bmPlanes,
            'bits': bmp.bmBitsPixel,
        } if ok else {'ok': False}
        gdi.DeleteObject(hbmp)
    return result


def load_probe(icon_path, cx, cy, flags=0x10):
    handle = user.LoadImageW(None, str(icon_path), 1, cx, cy, flags)
    info = icon_bitmap_info(handle)
    if handle:
        user.DestroyIcon(handle)
    return {'requested': [cx, cy], 'flags': flags, 'loaded': info}


def read_props(hwnd):
    ole = ctypes.OleDLL('ole32')
    initialized = ole.CoInitializeEx(None, 2) in (0, 1)
    store = ctypes.c_void_p()
    iid = GUID.parse('886d8eeb-8cf2-4446-8d02-cdba1dbdcf99')  # IPropertyStore
    # SHGetPropertyStoreForWindow wants IPropertyStore IID
    # Actually windows_icon uses 99198ed1... which is IPropertyStore
    iid = GUID.parse('886d8eeb-8cf2-4446-8d02-cdba1dbdcf99')
    # The code uses '886d8eeb-8cf2-4446-8d02-cdba1dbdcf99' which is also IPropertyStore
    iid = GUID.parse('886d8eeb-8cf2-4446-8d02-cdba1dbdcf99')
    hr = shell.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(iid), ctypes.byref(store))
    if hr < 0 or not store:
        if initialized:
            ole.CoUninitialize()
        return {'hr': hr, 'error': 'SHGetPropertyStoreForWindow failed'}
    table = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    get_value = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT)
    )(table[5])
    release = ctypes.WINFUNCTYPE(w.ULONG, ctypes.c_void_p)(table[2])
    fmtid = GUID.parse('9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3')
    names = {2: 'RelaunchCommand', 3: 'RelaunchIconResource', 4: 'RelaunchDisplayNameResource', 5: 'AppUserModelID'}
    out = {}
    try:
        for pid, name in names.items():
            key = PROPERTYKEY(fmtid, pid)
            value = PROPVARIANT()
            hr2 = get_value(store, ctypes.byref(key), ctypes.byref(value))
            text = None
            if hr2 >= 0 and value.vt == 31 and value.value.pointer:
                text = ctypes.wstring_at(value.value.pointer)
            out[name] = {'hr': hr2, 'vt': value.vt, 'text': text}
    finally:
        release(store)
        if initialized:
            ole.CoUninitialize()
    return out


def find_windows():
    found = []
    @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def visit(hwnd, _):
        if not user.IsWindowVisible(hwnd):
            return True
        title = ctypes.create_unicode_buffer(256)
        user.GetWindowTextW(hwnd, title, 256)
        if title.value != APP_TITLE:
            return True
        name = ctypes.create_unicode_buffer(80)
        user.GetClassNameW(hwnd, name, 80)
        pid = w.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = w.RECT()
        user.GetWindowRect(hwnd, ctypes.byref(rect))
        found.append({
            'hwnd': int(hwnd),
            'class': name.value,
            'pid': pid.value,
            'rect': [rect.left, rect.top, rect.right, rect.bottom],
        })
        return True
    user.EnumWindows(visit, 0)
    return found


def metrics_at(dpi):
    if not get_metric_for_dpi or not dpi:
        return None
    return {
        'SM_CXICON': get_metric_for_dpi(11, dpi),
        'SM_CYICON': get_metric_for_dpi(12, dpi),
        'SM_CXSMICON': get_metric_for_dpi(49, dpi),
        'SM_CYSMICON': get_metric_for_dpi(50, dpi),
    }


def process_awareness(pid):
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return {'error': f'OpenProcess failed last={ctypes.get_last_error()}'}
    try:
        if not get_process_dpi:
            return {'error': 'GetProcessDpiAwareness missing'}
        val = ctypes.c_int(-1)
        hr = get_process_dpi(handle, ctypes.byref(val))
        return {'hr': hr, 'value': val.value, 'name': PROCESS_AWARENESS.get(val.value, 'unknown')}
    finally:
        kernel.CloseHandle(handle)


def capture_taskbar_strip():
    """Grab bottom taskbar band via ImageGrab; crop green-ish icon candidates later."""
    try:
        from PIL import ImageGrab, Image
    except Exception as exc:
        return {'error': f'PIL missing: {exc}'}
    # Full virtual screen via ImageGrab
    shot = ImageGrab.grab(all_screens=True)
    path = OUT / 'desktop-full.png'
    shot.save(path)
    w0, h0 = shot.size
    # Bottom 80 px strip (taskbar-ish)
    strip = shot.crop((0, max(0, h0 - 96), w0, h0))
    strip_path = OUT / 'taskbar-strip.png'
    strip.save(strip_path)
    # Find green badge-ish pixels (#14834e ~ 20,131,78) in strip for icon candidates
    px = strip.load()
    hits = []
    for y in range(strip.height):
        for x in range(0, strip.width, 2):
            r, g, b = px[x, y][:3]
            if 10 <= r <= 50 and 100 <= g <= 160 and 40 <= b <= 110 and g > r + 40 and g > b + 20:
                hits.append((x, y))
    clusters = []
    for x, y in hits:
        placed = False
        for c in clusters:
            if abs(c['cx'] - x) < 40 and abs(c['cy'] - y) < 40:
                c['points'].append((x, y))
                c['cx'] = sum(p[0] for p in c['points']) / len(c['points'])
                c['cy'] = sum(p[1] for p in c['points']) / len(c['points'])
                placed = True
                break
        if not placed:
            clusters.append({'cx': float(x), 'cy': float(y), 'points': [(x, y)]})
    crops = []
    for i, c in enumerate(sorted(clusters, key=lambda c: -len(c['points']))[:8]):
        xs = [p[0] for p in c['points']]
        ys = [p[1] for p in c['points']]
        pad = 8
        box = (
            max(0, min(xs) - pad),
            max(0, min(ys) - pad),
            min(strip.width, max(xs) + pad + 1),
            min(strip.height, max(ys) + pad + 1),
        )
        crop = strip.crop(box)
        cpath = OUT / f'taskbar-green-cluster-{i}.png'
        crop.save(cpath)
        # nearest-neighbor upscale x8 for pixel inspection
        up = crop.resize((crop.width * 8, crop.height * 8), Image.Resampling.NEAREST)
        upath = OUT / f'taskbar-green-cluster-{i}-x8.png'
        up.save(upath)
        crops.append({
            'index': i,
            'points': len(c['points']),
            'box_in_strip': box,
            'size': list(crop.size),
            'path': str(cpath),
            'upscaled': str(upath),
        })
    return {
        'desktop': str(path),
        'desktop_size': [w0, h0],
        'strip': str(strip_path),
        'green_hits': len(hits),
        'clusters': crops,
    }


def main():
    report = {
        'caller': {
            'pid': os.getpid(),
            'thread_dpi_ctx': ctx_label(get_thread_dpi()) if get_thread_dpi else None,
            'system_dpi': get_sys_dpi() if get_sys_dpi else None,
            'GetSystemMetrics': {
                'SM_CXICON': user.GetSystemMetrics(11),
                'SM_CYICON': user.GetSystemMetrics(12),
                'SM_CXSMICON': user.GetSystemMetrics(49),
                'SM_CYSMICON': user.GetSystemMetrics(50),
            },
            'metrics_for_dpi': {
                str(d): metrics_at(d) for d in (96, 120, 144, 168, 192, 240)
            },
        },
        'ico_exists': ICON_PATH.is_file(),
        'ico_path': str(ICON_PATH),
    }

    # What LoadImage returns under various DPI-size requests WITHOUT mutating app
    probes = []
    for size in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256):
        probes.append(load_probe(ICON_PATH, size, size, 0x10))  # LR_LOADFROMFILE
    # Also try LR_DEFAULTSIZE (0) and 0,0 with DEFAULTSIZE
    probes.append(load_probe(ICON_PATH, 0, 0, 0x10 | 0x40))  # LR_DEFAULTSIZE
    report['loadimage_probes'] = probes

    # Simulate current windows_icon metric path under caller context
    dpi_sim = get_dpi and None
    # no hwnd yet
    report['current_code_path_simulation'] = {
        'note': 'windows_icon uses GetDpiForWindow then GetSystemMetricsForDpi; if dpi=0 falls back to GetSystemMetrics',
        'if_dpi_96': metrics_at(96),
        'if_dpi_144': metrics_at(144),
        'if_dpi_192': metrics_at(192),
        'unaware_GetSystemMetrics': report['caller']['GetSystemMetrics'],
    }

    windows = find_windows()
    report['matched_windows'] = []
    for win in windows:
        hwnd = win['hwnd']
        entry = dict(win)
        # DPI as seen by THIS (diagnostic) thread
        entry['GetDpiForWindow_as_caller'] = int(get_dpi(hwnd)) if get_dpi else None
        entry['window_dpi_ctx'] = ctx_label(get_window_dpi_ctx(hwnd)) if get_window_dpi_ctx else None
        entry['process_dpi_awareness'] = process_awareness(win['pid'])
        entry['metrics_for_window_dpi'] = metrics_at(entry['GetDpiForWindow_as_caller'])

        # Temporarily switch diagnostic thread to PER_MONITOR_V2 and re-query
        if set_thread_dpi:
            prev = set_thread_dpi(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
            try:
                entry['GetDpiForWindow_as_pmv2'] = int(get_dpi(hwnd)) if get_dpi else None
                entry['metrics_for_window_dpi_pmv2'] = metrics_at(entry['GetDpiForWindow_as_pmv2'])
                entry['thread_ctx_while_pmv2'] = ctx_label(get_thread_dpi()) if get_thread_dpi else None
            finally:
                set_thread_dpi(prev)

        # ICON handles currently attached (read-only WM_GETICON)
        icons = {}
        for kind, label in ((0, 'ICON_SMALL'), (1, 'ICON_BIG'), (2, 'ICON_SMALL2')):
            h = user.SendMessageW(hwnd, 0x7F, kind, 0)  # WM_GETICON
            icons[label] = icon_bitmap_info(h) if h else None
            icons[label + '_handle'] = int(h) if h else 0
        # class icons via GetClassLongPtr
        try:
            get_class = user.GetClassLongPtrW
            get_class.argtypes = [w.HWND, ctypes.c_int]
            get_class.restype = ctypes.c_size_t
            GCLP_HICON = -14
            GCLP_HICONSM = -34
            for idx, label in ((GCLP_HICON, 'class_HICON'), (GCLP_HICONSM, 'class_HICONSM')):
                h = get_class(hwnd, idx)
                icons[label] = icon_bitmap_info(h) if h else None
                icons[label + '_handle'] = int(h) if h else 0
        except Exception as exc:
            icons['class_error'] = str(exc)
        entry['icons'] = icons
        entry['property_store'] = read_props(hwnd)
        report['matched_windows'].append(entry)

    report['taskbar_capture'] = capture_taskbar_strip()

    # ICO embedded sizes via PIL
    try:
        from PIL import Image
        ico = Image.open(ICON_PATH)
        report['ico_embedded_sizes'] = sorted(list(ico.info.get('sizes') or []))
        ico.close()
    except Exception as exc:
        report['ico_embedded_sizes_error'] = str(exc)

    out_json = OUT / 'report.json'
    out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f'\nWrote {out_json}')


if __name__ == '__main__':
    main()
