"""Exercise real Windows window-icon/property calls on our own temporary window."""
import ctypes,json,os,sys
from pathlib import Path
from unittest.mock import patch
from ctypes import wintypes as w
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.windows_icon import apply_window_icon
from PIL import Image
if os.name!='nt':raise SystemExit('Windows only')
user=ctypes.WinDLL('user32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
PROC=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,w.HWND,w.UINT,w.WPARAM,w.LPARAM)
class CLASS(ctypes.Structure):
    _fields_=[('style',w.UINT),('proc',PROC),('extra1',ctypes.c_int),('extra2',ctypes.c_int),('instance',w.HINSTANCE),('icon',w.HICON),('cursor',w.HANDLE),('brush',w.HBRUSH),('menu',w.LPCWSTR),('name',w.LPCWSTR)]
user.DefWindowProcW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];user.DefWindowProcW.restype=ctypes.c_ssize_t
user.RegisterClassW.argtypes=[ctypes.POINTER(CLASS)]
user.CreateWindowExW.argtypes=[w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.HWND,w.HMENU,w.HINSTANCE,ctypes.c_void_p];user.CreateWindowExW.restype=w.HWND
user.SendMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];user.SendMessageW.restype=ctypes.c_ssize_t
user.DestroyWindow.argtypes=[w.HWND]
kernel.GetModuleHandleW.restype=w.HMODULE
callback=PROC(lambda hwnd,msg,wp,lp:user.DefWindowProcW(hwnd,msg,wp,lp))
cls=CLASS();cls.proc=callback;cls.instance=kernel.GetModuleHandleW(None);cls.name='Chrome_WidgetWin_1'
assert user.RegisterClassW(ctypes.byref(cls))
hwnd=user.CreateWindowExW(0,cls.name,'JianBao icon QA',0x10CF0000,0,0,160,120,None,None,cls.instance,None)
assert hwnd
try:
    with patch('app.windows_icon.time.sleep',lambda _:None):
        apply_window_icon(os.getpid(),ROOT/'app/web/icon.ico',ROOT/'ExpBot.exe')
    handles=[user.SendMessageW(hwnd,0x7f,kind,0) for kind in (0,1,2)]
    assert all(handles),handles
    icon=Image.open(ROOT/'app/web/icon.ico');sizes=sorted(icon.info['sizes']);icon.close()
    assert (256,256) in sizes and (16,16) in sizes
    # Windows' ICO loader supplied separate native handles for small/large modes.
    report={'real_Windows_icon_messages_passed':True,'native_icon_handles':len(handles),'embedded_ico_sizes':sizes,'launcher_bytes':(ROOT/'ExpBot.exe').stat().st_size}
    (ROOT/'artifacts/v22-windows-icon-check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))
finally:
    user.DestroyWindow(hwnd)
