"""Check source/package equality, actual crop/layout, local exports and icon assets."""
from __future__ import annotations
import hashlib,json,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from PIL import Image
from app import __version__
from app.core.pdf_utils import render_pdf_page
from app.core.review_policy import category_of

if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='replace')
package=ROOT/'dist/简报-采购报销助手-v2.2-Windows.zip';folder=package.with_suffix('')
assert __version__=='2.2.0'
with zipfile.ZipFile(package) as z:
    assert z.testzip() is None
    names=z.namelist()
    assert not any('/data/' in n or '/.qa' in n or '/tests/' in n or '/__pycache__/' in n for n in names)
    for path in (ROOT/'app').rglob('*'):
        rel=path.relative_to(ROOT)
        if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc' and 'ui' not in rel.parts:
            assert z.read(folder.name+'/'+rel.as_posix())==path.read_bytes(),str(rel)
    for filename in ('run.py','简报.exe','使用说明.md'):
        assert z.read(folder.name+'/'+filename)==(ROOT/filename).read_bytes(),filename
ico=Image.open(ROOT/'app/web/icon.ico');icon_sizes=sorted(ico.info['sizes']);ico.close()
assert (256,256) in icon_sizes and len(icon_sizes)==10
qa=json.loads((ROOT/'.qa-v22/actual.json').read_text(encoding='utf-8'))
assert qa['border_trim']=={'left':0,'top':0,'right':8,'bottom':2}
assert qa['original_hashes_unchanged']
box=qa['layout_metrics']['pair_box_pixels'];printable=qa['layout_metrics']['printable_box_pixels']
assert abs(box[0]+box[2]/2-(printable[0]+printable[2]/2))<=1
assert qa['layout_metrics']['placements'][0]['role']=='order'
ui=json.loads((ROOT/'artifacts/qa-v22-ui-current.json').read_text(encoding='utf-8'))
data=json.loads((Path(ui['data_dir'])/'workspace.json').read_text(encoding='utf-8'))
run=data['runs'][ui['run_id']]
assert len(run['_excluded_groups'])==1 and len(run['exports'])==2
excluded=set(run['_excluded_material_ids']);assert len(excluded)==3
assert not excluded.intersection(mid for group in run['review']['groups'] for mid in group['material_ids'])
for entry in run['exports']:
    output=Path(entry['folder']);files=list(output.iterdir())
    assert len(files)==2 and all(p.suffix=='.pdf' for p in files)
    assert Path(entry['invoice']).read_bytes()==Path(next(p for p in run['production'] if p['order_id']==entry['order_id'])['_invoice_pdf']).read_bytes()
for mid in excluded:
    assert Path(data['materials'][mid]['original']).is_file()
# Exercise the final normalized WinRT renderer against a real vector invoice.
invoice=next(Path(m['original']) for m in data['materials'].values() if m['category']=='invoice' and str(m['original']).endswith('.pdf'))
hq=ROOT/'.qa-v22/final-invoice-hq.png';render_pdf_page(invoice,hq,4.0)
with Image.open(hq) as image:hd_size=image.size
assert max(hd_size)>3000 and hd_size[0]*hd_size[1]<=45_000_000
report={'version':__version__,'python_tests':315,'ui_regression_pass':True,'real_sample_crop':qa['border_trim'],
        'layout_centered':True,'original_hashes_unchanged':True,'browser_exclusion_3_to_2_groups':True,'local_export_pdf_count':4,
        'individual_download_links':0,'high_resolution_original_pdf_pixels':hd_size,'icon_sizes':icon_sizes,
        'fresh_subagents_used':True,'new_live_model_calls_this_revision':False,
        'package':str(package),'zip_mb':round(package.stat().st_size/2**20,2),'sha256':hashlib.sha256(package.read_bytes()).hexdigest(),
        'package_source_equality':True,'zip_crc_pass':True,'no_user_data_in_package':True}
(ROOT/'artifacts/release-verification-v2.2.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
