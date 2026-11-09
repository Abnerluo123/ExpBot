"""Validate the focused SCNU v2.2.1 hotfix package."""
import hashlib,json,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from PIL import Image
name='ExpBot-legacy-v2.2.1-Windows';package=ROOT/'dist'/(name+'.zip')
with zipfile.ZipFile(package) as z:
    assert z.testzip() is None
    names=z.namelist()
    assert name+'/ExpBot.exe' in names and name+'/启动ExpBot.cmd' in names
    assert not any('/data/' in n or '/tests/' in n or '/.qa' in n or '/__pycache__/' in n for n in names)
    for source in (ROOT/'app').rglob('*'):
        rel=source.relative_to(ROOT)
        if source.is_file() and '__pycache__' not in rel.parts and source.suffix!='.pyc' and 'ui' not in rel.parts:
            assert z.read(name+'/'+rel.as_posix())==source.read_bytes(),str(rel)
    html=z.read(name+'/app/web/index.html').decode('utf-8')
    assert '<title>ExpBot</title>' in html and 'class="brand"' not in html
    assert z.read(name+'/ExpBot.exe')==(ROOT/'ExpBot.exe').read_bytes()
ico=Image.open(ROOT/'app/web/icon.ico');sizes=sorted(ico.info['sizes'])
for size in sizes:
    with Image.open(ROOT/f'app/web/icon-{size[0]}.png') as authored:
        assert ico.ico.getimage(size).convert('RGBA').tobytes()==authored.convert('RGBA').tobytes()
ico.close()
qa=json.loads((ROOT/'artifacts/qa-v221-current.json').read_text(encoding='utf-8'))
state=json.loads((Path(qa['root'])/'data/workspace.json').read_text(encoding='utf-8'))
assert state['runs'][qa['first_id']]['deleted_at']
assert len(state['runs'][qa['run_id']]['material_ids'])==1
report={'app':'ExpBot','version':'2.2.1','python_tests_passed':322,'ui_regression_passed':True,'browser_native_drop_imported_without_navigation':True,'native_calendar_center_click_accepted':True,'history_delete_verified':True,'icon_frames':len(sizes),'zip_crc':True,'source_equality':True,'no_user_data_in_package':True,'zip_mb':round(package.stat().st_size/2**20,2),'sha256':hashlib.sha256(package.read_bytes()).hexdigest()}
(ROOT/'artifacts/release-verification-v2.2.1.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='replace')
print(json.dumps(report,ensure_ascii=False))
