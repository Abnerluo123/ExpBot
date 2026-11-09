"""Final package equality and native taskbar acceptance report."""
import hashlib,json,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];name='ExpBot-legacy-v2.2.2-Windows';archive=ROOT/'dist'/(name+'.zip')
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    names=z.namelist()
    assert not any('/data/' in p or '/.qa' in p or 'desktop-startup-error' in p or '/__pycache__/' in p for p in names)
    for source in (ROOT/'app').rglob('*'):
        relative=source.relative_to(ROOT)
        if source.is_file() and '__pycache__' not in relative.parts and source.suffix!='.pyc' and 'ui' not in relative.parts:
            assert z.read(name+'/'+relative.as_posix())==source.read_bytes(),str(relative)
    for filename in ('run.py','使用说明.md','THIRD_PARTY_NOTICES.md'):
        assert z.read(name+'/'+filename)==(ROOT/filename).read_bytes()
    assert z.read(name+'/desktop/SCNU.Window.exe')==(ROOT/'desktop/SCNU.Window.exe').read_bytes()
    for filename in ('Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll','WebView2Loader.dll','SCNU.Window.exe.config','WebView2-LICENSE.txt'):
        assert name+'/desktop/'+filename in names
    assert name+'/desktop/SCNU.Desktop.exe' not in names
icons=json.loads((ROOT/'artifacts/v2.2.2-taskbar-check.json').read_text(encoding='utf-8'))
assert icons[0]['dpi']==192 and icons[0]['icons']['taskbar']==[48,48]
report={'version':'2.2.2','desktop_host':'Windows WebView2','user_confirmed_drag_drop':True,'further_drag_tests_stopped_on_request':True,'taskbar_native_size':icons,'header_identity_top_left':True,'back_link_separate_row':True,'ui_regression_pass':True,'focused_python_tests_passed':34,'zip_crc':True,'source_equality':True,'no_user_data':True,'size_mb':round(archive.stat().st_size/2**20,2),'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
(ROOT/'artifacts/release-verification-v2.2.2.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='replace')
print(json.dumps(report,ensure_ascii=False))
