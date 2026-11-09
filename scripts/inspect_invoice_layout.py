from pathlib import Path
from PIL import Image
import json
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app
from pypdf import PdfReader

parent=ROOT/'.dsh-office-58e3b3d0aeea478d9a7b0c8d8857b20b'
reports=[]
for label,file,render in [('original','原版发票.pdf','original'),('submitted','提交版发票.pdf','submitted')]:
    p=ROOT/'examples/v1_1-input'/file
    reader=PdfReader(p)
    page=reader.pages[0]
    im=Image.open(parent/render/'page-0001.png').convert('RGB')
    gray=im.convert('L')
    ink=gray.point(lambda x:255 if x<210 else 0).getbbox()
    width_mm=float(page.mediabox.width)/72*25.4
    height_mm=float(page.mediabox.height)/72*25.4
    l,t,r,b=ink
    reports.append({'kind':label,'pages':len(reader.pages),'page_mm':[width_mm,height_mm],'content_bbox_pixels':ink,
                    'margins_mm':{'left':l/im.width*width_mm,'top':t/im.height*height_mm,'right':(im.width-r)/im.width*width_mm,'bottom':(im.height-b)/im.height*height_mm}})
print(json.dumps(reports,ensure_ascii=False,indent=2))
out=ROOT/'artifacts/invoice-sample-layout-v1.1.json';out.parent.mkdir(exist_ok=True);out.write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
