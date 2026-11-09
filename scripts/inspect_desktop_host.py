import ctypes,json,sys
from pathlib import Path
from PIL import ImageGrab
from ctypes import wintypes as w
u=ctypes.WinDLL('user32',use_last_error=True);u.SetThreadDpiAwarenessContext.argtypes=[ctypes.c_void_p];u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
u.GetWindowTextW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int];u.GetClassNameW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int];u.GetWindowThreadProcessId.argtypes=[w.HWND,ctypes.POINTER(w.DWORD)]
rows=[]
@ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
def visit(h,p):
 text=ctypes.create_unicode_buffer(256);u.GetWindowTextW(h,text,256);pid=w.DWORD();u.GetWindowThreadProcessId(h,ctypes.byref(pid))
 if 'SCNU' in text.value or pid.value==34948:
  cls=ctypes.create_unicode_buffer(120);u.GetClassNameW(h,cls,120);rows.append({'hwnd':h,'pid':pid.value,'title':text.value,'class':cls.value})
 return True
u.EnumWindows.argtypes=[type(visit),w.LPARAM];u.EnumWindows(visit,0)
print(json.dumps(rows,ensure_ascii=True))
ImageGrab.grab().save(Path(__file__).resolve().parents[1]/'artifacts/v222-desktop-diagnostic.png')
