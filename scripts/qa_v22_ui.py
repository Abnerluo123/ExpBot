"""Local three-purchase browser QA. Copies old samples; no model calls or credentials."""
from __future__ import annotations
import copy,io,json,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from PIL import Image,ImageDraw,ImageFont
from app.server import Workspace,AppServer
from app import flow_engine as flow
from app.config.settings import Settings

if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='replace')
root=ROOT/'.qa-v22-ui'/uuid.uuid4().hex[:10];root.mkdir(parents=True)
ws=Workspace(root/'data');ws.settings=Settings(name='界面测试',advisor='本地验收',project_name='v2.2修复验收',declare_date='20261109',output_dir=str(root/'exports'),pdf_dpi=300)
run=flow.create_run(ws,{});rid=run['id'];private=ws.state['runs'][rid]
old=json.loads((ROOT/'.qa-v21-live/flow-905202fcc2/data/workspace.json').read_text(encoding='utf-8'))['materials']
invoice=next(Path(m['original']) for m in old.values() if m['category']=='invoice' and str(m['original']).endswith('.pdf'))
payment=next(Path(m['original']) for m in old.values() if m['category']=='payment' and str(m['original']).endswith('.png'))
order=ROOT/'.qa-v21-live/live-smart-stitch.png'
rows=[];groups=[];font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',36)
for index in range(3):
    mids=[]
    for role in ('invoice','order','payment'):
        if index==0:
            path={'invoice':invoice,'order':order,'payment':payment}[role]
            content=path.read_bytes();name=f'真实样例副本-{role}'+path.suffix
        else:
            image=Image.new('RGB',(1400,2000),'white');draw=ImageDraw.Draw(image)
            draw.text((70,100),f'合成测试材料 · 不用于报销\n采购 {index+1} / {role}\n只验证界面与本地导出\n示例单位\n91110000MA01234567\n示例园区东区实验楼130室',font=font,fill='#22685d',spacing=35)
            for y in range(600,1800,100):draw.line((70,y,1330,y),fill='#d7e3d7',width=2)
            b=io.BytesIO();image.save(b,'PNG');image.close();content=b.getvalue();name=f'合成测试{index+1}-{role}.png'
        m=ws.import_bytes(content,name,run['inbox_order_id']);flow.add_material(ws,rid,m['id']);mids.append(m['id'])
        facts={'buyer_name':'示例单位','taxpayer_id':'91110000MA01234567','address':'示例园区东区实验楼130室'}
        rows.append({'material_id':m['id'],'category':role,'facts':facts})
    groups.append({'key':f'g{index+1}','title':('真实样例副本 · 居中与去边' if index==0 else f'合成采购 {index+1} · 可排除'), 'material_ids':mids,
                   'checks':[{'code':code,'status':'pass','message':'本地测试记录','material_ids':mids} for code in ('personal_info','address','completeness','clarity')],'stitch_sets':[]})
flow._apply_review(ws,private,{'materials':rows,'groups':groups,'unassigned':[],'trash':[],'warnings':[]})
private['_review_source_version']=private['_source_version']
for group in private['review']['groups']:
    product=flow._make_one(ws,rid,group,flow._settings(ws,private,ws.order(group['order_id'])),False,True)
    product['_source_version']=private['_source_version'];product['_material_fingerprints']=copy.deepcopy(flow._fingerprints(ws,private))
    private['production'].append(product)
    ws.state['plans'][product['plan_id']]={'id':product['plan_id'],'kind':'flow-production','order_id':group['order_id'],'run_id':rid,'created':flow._now(),'applied':True}
private.update(stage='ready',status='idle');ws.save()
server=AppServer(('127.0.0.1',0),ws)
info={'url':f'http://127.0.0.1:{server.server_port}/#run/{rid}','run_id':rid,'data_dir':str(ws.root),'groups':3,'materials':9}
(ROOT/'artifacts/qa-v22-ui-current.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(info,ensure_ascii=False),flush=True)
server.serve_forever()
