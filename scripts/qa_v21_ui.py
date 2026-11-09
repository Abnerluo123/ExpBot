"""Many-purchase UI fixture server. No external model calls or real user files."""
from __future__ import annotations
import copy
import io
import json
import shutil
import sys
import uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from PIL import Image,ImageDraw,ImageFont
from app.server import Workspace,AppServer
from app import flow_engine as engine
from app.core.invoice_layout import invoice_image_page
from app.core.pdf_generator import generate_voucher_pdf


def main():
    root=ROOT/'.qa-v21-ui'/uuid.uuid4().hex[:10];root.mkdir(parents=True)
    ws=Workspace(root/'data')
    ws.settings.name='界面验收';ws.settings.advisor='测试';ws.settings.project_name='批量报销界面测试';ws.settings.pdf_dpi=150
    run=engine.create_run(ws,{})
    private=ws.state['runs'][run['id']]
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',24)
    for index in range(12):
        title=f'测试采购 {index+1:02d} · 多份材料'
        originals=[]
        for role in ('invoice','order','order','payment'):
            number=len(originals)
            im=Image.new('RGB',(500,700) if role!='invoice' else (850,600),'white')
            draw=ImageDraw.Draw(im);draw.rectangle((18,18,im.width-18,im.height-18),outline='#b2c4ba',width=2)
            draw.text((38,55),f'合成测试 · 不用于报销\n{title}\n{role} / 材料 {number+1}\n金额：{index+20}.00元',font=font,fill='#325e55',spacing=24)
            buffer=io.BytesIO()
            if role=='invoice':
                second=im.copy();ImageDraw.Draw(second).text((38,400),'第二页：附加明细，必须完整预览',font=font,fill='#b54141')
                im.save(buffer,'PDF',save_all=True,append_images=[second],resolution=100)
                second.close();name=f'测试{index+1:02d}-发票两页.pdf'
            else:
                im.save(buffer,'PNG');name=f'测试{index+1:02d}-{role}-{number}.png'
            material=ws.import_bytes(buffer.getvalue(),name,run['inbox_order_id']);engine.add_material(ws,run['id'],material['id']);originals.append(material['id']);im.close()
        order=ws.add_order({'title':title});order.update(flow_run_id=run['id'],flow_group_key=f'g{index+1}')
        group={'key':f'g{index+1}','title':title,'material_ids':originals,'order_id':order['id'],
               'checks':[{'code':c,'status':'pass','message':'合成界面验收记录','material_ids':originals} for c in ('address','buyer','tax_id','amount','payment','completeness')],'stitch_sets':[]}
        for mid,role in zip(originals,('invoice','order','order','payment')):
            ws.material(mid).update(order_id=order['id'],category=role)
        private['order_ids'].append(order['id']);private['review']['groups'].append(group)
        pid=uuid.uuid4().hex[:20];folder=ws.root/'plans'/pid;folder.mkdir(parents=True)
        cfg=ws.order_settings(order['id']);cfg.pdf_dpi=150
        _,_,voucher=generate_voucher_pdf([ws.material(originals[1])['working']],[ws.material(originals[3])['working']],folder/'voucher.pdf',settings=cfg)
        voucher.thumbnail((1200,900));voucher.save(folder/'voucher-preview.jpg');voucher.close()
        first=invoice_image_page(ws.material(originals[0])['working'],cfg,dpi=100);first.save(folder/'invoice-page-1.png');first.close()
        page2=Image.open(folder/'invoice-page-1.png').convert('RGB');ImageDraw.Draw(page2).text((300,500),'第二页预览',font=font,fill='red');page2.save(folder/'invoice-page-2.png');page2.close()
        shutil.copy2(ws.material(originals[0])['original'],folder/'invoice.pdf')
        private['production'].append({'order_id':order['id'],'title':title,'plan_id':pid,'status':'ready','preview_url':f'/plan-preview/{pid}/voucher-preview.jpg',
            'invoice_url':f'/plan-preview/{pid}/invoice-page-1.png','voucher_pdf_url':f'/plan-preview/{pid}/voucher.pdf','invoice_pdf_url':f'/plan-preview/{pid}/invoice.pdf',
            'invoice_pages':[{'url':f'/plan-preview/{pid}/invoice-page-{n}.png','page':n,'total_pages':2,'kind':'invoice'} for n in (1,2)],
            'voucher_pages':[{'url':f'/plan-preview/{pid}/voucher-preview.jpg','page':1,'total_pages':1,'kind':'voucher'}], 'warnings':[],'layout':{}})
    private.update(stage='ready',status='idle')
    for product in private['production']:
        product['status']='ready'
    private['review']['issues']=[]
    ws.save()
    server=AppServer(('127.0.0.1',0),ws)
    info={'url':f'http://127.0.0.1:{server.server_port}/#run/{run["id"]}','run_id':run['id'],'data_dir':str(ws.root),'groups':12,'originals':48,'invoice_preview_pages':24}
    (ROOT/'artifacts/qa-v21-ui-current.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(info,ensure_ascii=False),flush=True)
    server.serve_forever()

if __name__=='__main__':main()
