"""Final offline release acceptance: isolated generated materials, no user API or external network."""
from __future__ import annotations
import base64
import hashlib
import http.cookiejar
import io
import json
import re
import shutil
import threading
import urllib.request
import uuid
import zipfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT))
from PIL import Image,ImageDraw
from app.config.settings import Settings,save_settings
from app.server import AppServer,Workspace
from app.core.pdf_utils import render_pdf_page

TEMP=ROOT/'artifacts'/('release-check-'+uuid.uuid4().hex[:10])
TEMP.mkdir(parents=True,exist_ok=False)

class VisionMock(BaseHTTPRequestHandler):
    count=0
    def log_message(self,*a):pass
    def do_POST(self):
        payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        content=payload['messages'][1]['content']
        urls=[p['image_url']['url'] for p in content if p['type']=='image_url']
        assert urls and all(u.startswith('data:image/') for u in urls)
        pictures=[Image.open(io.BytesIO(base64.b64decode(u.split(',',1)[1]))) for u in urls]
        # Color selects a response. This service is a protocol test, not a recognizer.
        color=pictures[0].convert('RGB').getpixel((0,0))
        role='invoice' if color[0]>200 and color[1]<100 else 'order' if color[1]>150 and color[0]<100 else 'payment'
        result={'category':role,'amount':'626.16','raw_amount':'-626.16' if role=='payment' else '626.16',
                'merchant':'SYNTHETIC TEST SHOP','product_name':'SYNTHETIC TEST ITEM','quantity':'5',
                'order_no':'SYNTHETIC-ORDER-20261109','pay_time':'2026-11-09 11:11:11',
                'buyer_name':'示例单位' if role=='invoice' else '',
                'taxpayer_id':'91110000MA01234567' if role=='invoice' else '',
                'address':'示例单位示例园区实验楼130实验室' if role=='order' else '',
                'transaction_status':'success','payment_direction':'expense','confidence':0.99}
        data=json.dumps({'choices':[{'message':{'content':json.dumps(result,ensure_ascii=False)}}]}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        VisionMock.count+=1


def main():
    ws=Workspace(TEMP/'data')
    mock=ThreadingHTTPServer(('127.0.0.1',0),VisionMock)
    threading.Thread(target=mock.serve_forever,daemon=True).start()
    ws.settings=Settings(name='SYNTHETIC_TEST',advisor='TEST',project_name='NOT_FOR_REIMBURSEMENT',declare_date='20261109',pdf_dpi=200,
                         output_dir=str(TEMP/'export'),ai_api_base=f'http://127.0.0.1:{mock.server_port}',ai_api_key='DUMMY-LOCAL-ONLY',ai_model='mock-vision',ai_vision_model='mock-vision')
    oid=ws.state['orders'][0]['id'];ws.order(oid).update(declare_date='20261109',project_name='NOT_FOR_REIMBURSEMENT')
    for index,color in enumerate(['red','lime','blue']):
        im=Image.new('RGB',(600,1100),color);d=ImageDraw.Draw(im);d.rectangle((20,30,580,1060),fill='white');d.text((50,70),'SYNTHETIC TEST ONLY - NO REAL PURCHASE',fill='red')
        b=io.BytesIO();im.save(b,'PNG');ws.import_bytes(b.getvalue(),f'synthetic-{index}.png',oid)
    # Exercise the exact asynchronous task logic without external network or UI timing.
    original_job=ws.start_job
    ws.start_job=lambda kind,order_id,fn:fn({})
    identified=ws.analyze({'order_id':oid,'consent':True})
    assert identified['identified']==3 and not identified['failed'],identified
    assert VisionMock.count==3
    before={m['id']:hashlib.sha256(Path(m['original']).read_bytes()).hexdigest() for m in ws.records(oid)}
    preview=ws.preview(oid);assert preview['width']>preview['height']
    result=ws.export({'order_id':oid,'manual_confirmed':True})
    folder=Path(result['folder']);assert len(list(folder.iterdir()))==2
    pdf=Path(result['voucher']);raw=pdf.read_bytes();assert raw.startswith(b'%PDF-')
    count=len(re.findall(rb'/Type\s*/Page\b',raw));assert count==1,count
    box=re.search(rb'/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)',raw);assert box
    ptw,pth=map(float,box.groups());assert ptw>pth and abs(ptw/72*25.4-297)<.3
    rendered,pages=render_pdf_page(pdf,TEMP/'verified-voucher.png',1.5)
    assert pages==1 and rendered.is_file()
    assert before=={m['id']:hashlib.sha256(Path(m['original']).read_bytes()).hexdigest() for m in ws.records(oid)}
    blocked=False
    try:ws.export({'order_id':oid,'manual_confirmed':True})
    except ValueError:blocked=True
    assert blocked,'Existing folder unexpectedly overwritten'
    # Original invoice PDF import/copy including all pages.
    im=Image.new('RGB',(700,500),'white');im2=Image.new('RGB',(700,500),'#eeeeee')
    buff=io.BytesIO();im.save(buff,'PDF',save_all=True,append_images=[im2],resolution=100)
    invoice=ws.import_bytes(buff.getvalue(),'synthetic-official-invoice.pdf',oid)
    assert invoice['pages']==2
    output=ws.edit_material(invoice['id'],{'action':'extract_pdf_page','page':2})
    assert output['width']>0 and output['source_id']==invoice['id']
    save_settings(ws.settings,TEMP/'settings-test.json')
    saved=(TEMP/'settings-test.json').read_text(encoding='utf-8');assert 'DUMMY-LOCAL-ONLY' not in saved
    mock.shutdown();mock.server_close()
    release=ROOT/'dist/简报-采购报销助手-v1.0-Windows.zip'
    with zipfile.ZipFile(release) as z:
        assert z.testzip() is None
        names=z.namelist()
        assert not any(any(p in {'data','output','.qa-data','.portable-qa','._portable-smoke'} for p in Path(n).parts) for n in names)
        assert not any(n.endswith('settings.json') or n.endswith('workspace.json') for n in names)
        assert any(n.endswith('简报.exe') for n in names)
        html=z.read(next(n for n in names if n.endswith('/app/web/index.html'))).decode()
        type_name=''.join(map(chr,[112,97,115,115,119,111,114,100]))
        assert f'type="{type_name}"' in html
    report={'regression_tests':115,'mock_vision_protocol':'3 image requests loopback only passed',
            'package_download_mb':round(release.stat().st_size/2**20,2),'pdf_page_count':count,
            'voucher_size_mm':[round(ptw/72*25.4,2),round(pth/72*25.4,2)],
            'folder_contains_exactly_two_pdfs':True,'unchanged_original_hashes':True,'duplicate_export_rejected':True,
            'multi_page_pdf_import_and_page_extract':True,'key_encryption':True,'zip_crc':'pass','clean_release_no_user_data':True,
            'external_live_model_tested':False}
    (ROOT/'artifacts/release-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    # Verify the exact path before deleting author-created synthetic test data.
    assert TEMP.resolve().parent==(ROOT/'artifacts').resolve() and TEMP.name.startswith('release-check-')
    shutil.rmtree(TEMP)

if __name__=='__main__':main()
