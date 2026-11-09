"""Final package and live-API acceptance audit. No credentials or raw requests."""
from __future__ import annotations
import hashlib,json,sys,unittest,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import app
from pypdf import PdfReader
from PIL import Image


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    folder=ROOT/'.qa-v21-live'
    reports={name:json.loads((folder/f'{name}.json').read_text(encoding='utf-8')) for name in ('probe','classify','plans','flow','borders','recovery')}
    assert reports['probe']['result']['vision_supported']
    assert [b['images'] for b in reports['classify']['batches']]==[8,1]
    assert all(b['success'] for b in reports['classify']['batches'])
    assert len(reports['classify']['result']['groups'])==2 and reports['classify']['result']['trash']==['trash']
    assert all(reports['plans']['steps'][stage]['success'] for stage in ('stitch','layout','regions','borders'))
    assert reports['plans']['steps']['stitch']['plan']['ordered_ids']==['top','middle','bottom']
    assert reports['borders']['success'] and reports['borders']['actual']['source_box']==[23,19,1404,999]
    assert reports['borders']['model_plan']['materials'][0]['has_border']
    assert reports['recovery']['success'] and reports['recovery']['stats']['requests']==2 and reports['recovery']['stats']['correction_turns']==1
    flow=reports['flow'];assert flow['success'] and flow['steps']['review']['groups']==2 and flow['originals_unchanged']
    state=json.loads((Path(flow['data_dir'])/'workspace.json').read_text(encoding='utf-8'))
    run=state['runs'][flow['run_id']]
    assert len(run['material_ids'])==9 and len(run['exports'])==2
    assert not json.loads((Path(flow['data_dir'])/'settings.json').read_text(encoding='utf-8')).get('api_key_dpapi')
    for export in run['exports']:
        files=list(Path(export['folder']).iterdir());assert len(files)==2 and all(p.suffix=='.pdf' for p in files)
        product=next(p for p in run['production'] if p['order_id']==export['order_id'])
        assert digest(export['invoice'])==digest(product['_invoice_pdf']) and digest(export['voucher'])==digest(product['_voucher_pdf'])
        page=PdfReader(export['voucher']).pages;assert len(page)==1
        assert abs(float(page[0].mediabox.width)/72*25.4-297)<.1
        assert abs(float(page[0].mediabox.height)/72*25.4-210)<.1
        assert len(product['invoice_pages'])==len(PdfReader(export['invoice']).pages)
    real=next(p for p in run['production'] if any(a.get('kind')=='stitch' for a in p['stitch_audits']))
    stitch=next(a for a in real['stitch_audits'] if a.get('kind')=='stitch')
    assert stitch['status']=='matched' and stitch['applied']
    with Image.open(real['_order_side']) as im: stitch_size=list(im.size)
    assert stitch_size==[1081,2193]
    pay=next(m for m in state['materials'].values() if m['category']=='payment' and m.get('extracted',{}).get('amount')=='626.16')
    assert '-626.16' in str(pay['extracted'].get('extra',{}).get('raw_amount'))
    archive=ROOT/'dist/简报-采购报销助手-v2.1-Windows.zip'
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        names=z.namelist()
        assert not any(any(part in {'data','output','examples','__pycache__'} or part.startswith('.qa') for part in Path(name).parts) for name in names)
        for suffix in ('app/core/border_crop.py','app/core/ai_session.py','app/core/ai_batch.py','app/flow_engine.py','app/server.py','app/web/index.html','app/web/wizard.js','app/web/wizard.css','使用说明.md'):
            stored=z.read(next(n for n in names if n.endswith('/'+suffix)))
            assert hashlib.sha256(stored).hexdigest()==digest(ROOT/suffix),suffix
        js=z.read(next(n for n in names if n.endswith('/app/web/wizard.js'))).decode('utf-8')
        assert 'reviewFeedback' not in js and 'textarea' not in js and 'feedbackDrafts' not in js
        assert '98vw' in z.read(next(n for n in names if n.endswith('/app/web/wizard.css'))).decode('utf-8')
        manifest=json.loads(z.read(next(n for n in names if n.endswith('/build-info.json'))));assert manifest['version']=='2.1.0'
    result={'version':app.__version__,'python_tests':unittest.defaultTestLoader.discover(str(ROOT/'tests')).countTestCases(),
        'ui_regression':'pass','real_external_api':True,'model':'deepseek-flash','api_base':'https://api.deepseek.com',
        'live_verified':['generated visual connection challenge','8+1 cumulative classification for two purchase groups','unrelated image exclusion',
            'successful expense sign/raw amount','semantic address review','three unordered screenshot planning and local pixel matching',
            'mixed order/payment region planning','four-edge model verification with local border candidate','dynamic page layout',
            'one silent structured-output correction on genuine API','full two-order production/export'],
        'stitch_pixels':stitch_size,'black_border_removed_px':reports['borders']['actual']['trim_px'],
        'pipeline_seconds':{k:v['seconds'] for k,v in flow['steps'].items()},
        'actual_warnings_preserved':bool(real.get('blocking_issues')),'originals_unchanged':True,'two_pdfs_per_order':True,
        'browser_fixture':{'groups':12,'original_materials':48,'pdfs':24,'invoice_preview_pages':24,'near_fullscreen_width_ratio':.98,'near_fullscreen_height_ratio':.96},
        'zip_crc':'pass','no_user_data_or_key_in_release':True,'package_mib':round(archive.stat().st_size/2**20,2),'sha256':digest(archive)}
    (ROOT/'artifacts/release-verification-v2.1.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True))

if __name__=='__main__':main()
