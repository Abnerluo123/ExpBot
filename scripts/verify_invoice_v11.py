from pathlib import Path
import json,hashlib,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.config.settings import Settings
from app.core.invoice_layout import make_invoice_print_copy
from pypdf import PdfReader
src=ROOT/'examples/v1_1-input/原版发票.pdf'
sha=hashlib.sha256(src.read_bytes()).hexdigest()
out=ROOT/'artifacts/v1.1-发票留白验收.pdf';out.parent.mkdir(exist_ok=True)
r=make_invoice_print_copy(src,out,Settings())
assert hashlib.sha256(src.read_bytes()).hexdigest()==sha
p=PdfReader(out).pages[0]
assert abs(float(p.mediabox.width)/72*25.4-297)<.01
assert abs(float(p.mediabox.height)/72*25.4-210)<.01
report={**r,'source_sha256':sha,'print_copy_sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'page_mm':[297,210]}
(ROOT/'artifacts/invoice-verification-v1.1.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
