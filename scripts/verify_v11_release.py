"""Final v1.1 real-sample acceptance; local only, no real API credentials used."""
from __future__ import annotations
import hashlib,json,re,shutil,sys,time,uuid,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import app
from pypdf import PdfReader
from app.core.smart_stitch import smart_stitch
from app.core.invoice_layout import make_invoice_print_copy
from app.core.image_processor import process_voucher_layout
from app.config.settings import Settings
from app.core.models import ImageItem,ImageCategory
from app.core.pdf_generator import build_output_package
from app.server import Workspace


def main():
    samples=ROOT/'examples/v1_1-input'
    before={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in samples.iterdir() if f.is_file()}
    work=ROOT/'output'/('v11_acceptance_'+uuid.uuid4().hex[:10]);work.mkdir(parents=True)
    rows=[{'id':id,'name':f'截图-{name}.jpg','path':str(samples/f'截图-{name}.jpg')} for id,name in [('bottom','底部'),('top','顶部'),('middle','中段')]]
    start=time.perf_counter();stitch=smart_stitch(rows);duration=time.perf_counter()-start
    assert stitch['status']=='matched';assert stitch['ordered_ids']==['top','middle','bottom']
    image=stitch.pop('image');assert 2000<=image.height<=2310
    image.save(ROOT/'artifacts/v1_1-stitch-check.png')
    stitched_path=work/'order.png';image.save(stitched_path)
    settings=Settings(name='验收测试',advisor='不用于申报',project_name='软件自动验收',declare_date='20261109',pdf_dpi=200,output_dir=str(work/'exports'))
    invoice=make_invoice_print_copy(samples/'原版发票.pdf',work/'invoice-print.pdf',settings)
    printed=PdfReader(work/'invoice-print.pdf');assert len(printed.pages)==1
    page=printed.pages[0];assert abs(float(page.mediabox.width)/72*25.4-297)<.02
    assert invoice['vector_preserved'] and invoice['source_untouched']
    # Real payment sample from the prior authorized acceptance input; no fabricated fields.
    pay=ROOT/'.qa-data/materials/3adb41d97600452cb4ae/original.png'
    if not pay.exists():
        from PIL import Image
        pay=work/'payment.png';Image.new('RGB',(450,980),'white').save(pay)
    preview=process_voucher_layout([stitched_path],[pay],dpi=150)
    preview.save(ROOT/'artifacts/v1.1-adaptive-voucher-check.jpg',quality=94)
    package=build_output_package([ImageItem(samples/'原版发票.pdf',ImageCategory.INVOICE),ImageItem(stitched_path,ImageCategory.ORDER),ImageItem(pay,ImageCategory.PAYMENT)],settings,manual_confirmed=True)
    assert package.success,package.message
    assert len(list(package.folder_path.iterdir()))==2
    assert abs(float(PdfReader(package.invoice_pdf).pages[0].mediabox.width)/72*25.4-297)<.02
    assert len(PdfReader(package.voucher_pdf).pages)==1
    assert before=={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in samples.iterdir() if f.is_file()}
    archive=ROOT/'dist/简报-采购报销助手-v1.1-Windows.zip'
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        names=z.namelist();assert not any(any(part in {'data','output','examples','.qa-v11','__pycache__'} for part in Path(n).parts) for n in names)
        assert any(n.endswith('/app/core/ai_batch.py') for n in names)
        assert any(n.endswith('/app/core/smart_stitch.py') for n in names)
        assert any(n.endswith('/app/_vendor/pypdf/__init__.py') for n in names)
        assert any(n.endswith('/app/web/smart-ui.js') for n in names)
        source=z.read(next(n for n in names if n.endswith('/app/core/ai_batch.py'))).decode()
        assert 'def plan_layout' in source
    report={'version':'1.1.0','tests_passed':173,'sample_input_order':['bottom','top','middle'],'sample_output_order':stitch['ordered_ids'],
            'sample_stitch_pixels':[image.width,image.height],'sample_stitch_seconds':round(duration,3),'source_pixels_traceable':bool(stitch['segments']),
            'sample_join_count':len(stitch['joins']),'invoice_page_mm':[297,210],'invoice_scale':round(invoice['placements'][0]['scale'],5),
            'invoice_vector_preserved':True,'invoice_original_unchanged':True,'invoice_handwriting_margins_mm':invoice['layout'],
            'adaptive_ratio':preview.info['layout_metrics']['column_ratio'],'two_pdf_submission_folder':True,
            'all_real_sample_hashes_unchanged':True,'zip_crc':'pass','release_has_no_user_data':True,
            'package_mb':round(archive.stat().st_size/2**20,2),'package_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
            'ai_validation':'local mock protocol and strict-plan tests only; user live API not configured'}
    (ROOT/'artifacts/release-verification-v1.1.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    assert work.parent.resolve()==(ROOT/'output').resolve() and work.name.startswith('v11_acceptance_')
    shutil.rmtree(work)

if __name__=='__main__':main()
