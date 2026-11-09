"""Reproduce the reported frame and layout using immutable prior real sample pixels."""
from __future__ import annotations
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from PIL import Image
from app.core.border_crop import crop_black_borders
from app.core.image_processor import process_voucher_layout
from app.core.pdf_generator import generate_voucher_pdf
from app.config.settings import Settings

OUT=ROOT/'.qa-v22';OUT.mkdir(exist_ok=True)
source=ROOT/'.qa-v21-live/live-smart-stitch.png'
pay=ROOT/'.qa-v21-live/flow-905202fcc2/data/materials'
materials=json.loads((ROOT/'.qa-v21-live/flow-905202fcc2/data/workspace.json').read_text(encoding='utf-8'))['materials']
payment=next(Path(m['original']) for m in materials.values() if m['category']=='payment' and m['name'].lower().endswith(('.jpg','.png')))
before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (source,payment)}
r=crop_black_borders(source)
try:r['image'].save(OUT/'order-no-frame.png')
finally:r['image'].close()
cfg=Settings(pdf_dpi=300)
_,_,page=generate_voucher_pdf([OUT/'order-no-frame.png'],[payment],OUT/'reported-voucher-fixed.pdf',settings=cfg,crop_whitespace=False)
metrics=page.info['layout_metrics'];page.save(OUT/'reported-voucher-fixed.png');page.close()
assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (source,payment)}
assert r['trim_px']['right']==8 and r['trim_px']['bottom']==2,r['trim_px']
placements=metrics['placements'];assert placements[0]['role']=='order' and placements[1]['role']=='payment'
report={'border_trim':r['trim_px'],'source_box':r['source_box'],'original_hashes_unchanged':True,'layout_metrics':metrics,'pdf':str(OUT/'reported-voucher-fixed.pdf'),'payment_source':payment.name}
(OUT/'actual.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
