"""Read the actual desktop window icon pixel dimensions without changing it."""
import ctypes,json,sys
from ctypes import wintypes as w
from pathlib import Path
u=ctypes.WinDLL('user32');g=ctypes.WinDLL('gdi32')
u.SetThreadDpiAwarenessContext.argtypes=[ctypes.c_void_p];u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
u.EnumWindows.argtypes=[ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM),w.LPARAM]
u.GetWindowThreadProcessId.argtypes=[w.HWND,ctypes.POINTER(w.DWORD)]
u.GetWindowTextW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int];u.GetClassNameW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int]
u.GetDpiForWindow.argtypes=[w.HWND];u.SendMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];u.SendMessageW.restype=ctypes.c_void_p
class ICON(ctypes.Structure):_fields_=[('icon',w.BOOL),('x',w.DWORD),('y',w.DWORD),('mask',w.HBITMAP),('color',w.HBITMAP)]
class BMP(ctypes.Structure):_fields_=[('type',w.LONG),('width',w.LONG),('height',w.LONG),('bytes',w.LONG),('planes',w.WORD),('bits',w.WORD),('data',ctypes.c_void_p)]
u.GetIconInfo.argtypes=[w.HICON,ctypes.POINTER(ICON)];g.GetObjectW.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p];g.DeleteObject.argtypes=[w.HANDLE]
results=[]
@ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
def visit(hwnd,_):
    title=ctypes.create_unicode_buffer(256);cls=ctypes.create_unicode_buffer(100)
    u.GetWindowTextW(hwnd,title,256);u.GetClassNameW(hwnd,cls,100)
    pid=w.DWORD();u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
    if title.value=='ExpBot' and cls.value.startswith('WindowsForms10') and (len(sys.argv)<2 or pid.value==int(sys.argv[1])):
        row={'dpi':u.GetDpiForWindow(hwnd),'icons':{}}
        for kind,label in ((0,'taskbar'),(1,'large')):
            handle=u.SendMessageW(hwnd,0x7f,kind,0);info=ICON();assert u.GetIconInfo(handle,ctypes.byref(info))
            bmp=BMP();g.GetObjectW(info.color,ctypes.sizeof(bmp),ctypes.byref(bmp));row['icons'][label]=[bmp.width,bmp.height]
            g.DeleteObject(info.color);g.DeleteObject(info.mask)
        results.append(row)
    return True
u.EnumWindows(visit,0)
assert results,'Native desktop window must be running'
for row in results:assert row['icons']['taskbar'][0]>=round(24*row['dpi']/96),row
root=Path(__file__).resolve().parents[1]
(root/'artifacts/v2.2.2-taskbar-check.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
print(json.dumps(results))
