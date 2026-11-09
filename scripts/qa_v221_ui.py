"""Fast isolated history/drop/calendar QA, without API calls or user data."""
import json,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.server import Workspace,AppServer
from app import flow_engine as flow
if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='replace')
root=ROOT/'.qa-v221'/uuid.uuid4().hex[:10];ws=Workspace(root/'data')
ws.settings.name='罗响';ws.settings.advisor='金骁';ws.settings.project_name='RAL智能肌肉康复设备';ws.settings.output_dir=str(root/'output')
first=flow.create_run(ws,{'profile':{'declare_date':'20261029'}})
second=flow.create_run(ws,{'profile':{'declare_date':'20261106'}})
ws.save();server=AppServer(('127.0.0.1',0),ws)
info={'url':f'http://127.0.0.1:{server.server_port}/#history','run_id':second['id'],'first_id':first['id'],'root':str(root)}
(ROOT/'artifacts/qa-v221-current.json').write_text(json.dumps(info,ensure_ascii=False),encoding='utf-8')
print(json.dumps(info,ensure_ascii=False),flush=True);server.serve_forever()
