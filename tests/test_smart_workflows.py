"""Server integration of review-first smart plans, using only local synthetic data."""
from pathlib import Path
import copy,io,shutil,unittest,uuid
from unittest.mock import patch
from PIL import Image,ImageDraw
from app.server import Workspace
from app.config.settings import Settings
from app.smart_workflows import start_plan,apply_plan,public_plan
from app.core.grouping import propose_groups
ROOT=Path(__file__).resolve().parents[1]

class SmartWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.root=ROOT/'output'/('smart_flow_'+uuid.uuid4().hex[:10]);self.root.mkdir(parents=True)
        self.ws=Workspace(self.root/'data');self.oid=self.ws.state['orders'][0]['id']
        self.ws.settings=Settings(ai_api_key='DUMMY-LOCAL',ai_api_base='http://127.0.0.1:12345',ai_model='mock',ai_vision_model='mock')
        self.ws.start_job=lambda k,oid,fn:fn({})
    def tearDown(self):
        assert self.root.resolve().parent==(ROOT/'output').resolve();shutil.rmtree(self.root)
    def add(self,name='synthetic.png',color='red'):
        b=io.BytesIO();im=Image.new('RGB',(200,400),color);ImageDraw.Draw(im).text((12,30),'SYNTHETIC TEST ONLY',fill='white');im.save(b,'PNG')
        return self.ws.import_bytes(b.getvalue(),name,self.oid)['id']
    def inject_plan(self,kind,ids,result):
        pid=uuid.uuid4().hex[:20]
        self.ws.state['plans'][pid]={'id':pid,'kind':kind,'order_id':self.oid,'material_ids':ids,'revisions':{x:self.ws.material(x)['revision'] for x in ids},'assignments':{x:self.oid for x in ids},'result':result,'created':'test','applied':False}
        return pid
    def test_requires_ai_consent(self):
        mid=self.add()
        with self.assertRaises(ValueError):start_plan(self.ws,{'order_id':self.oid,'kind':'orders','ids':[mid],'use_ai':True})
    def test_stale_plan_does_not_move_sources(self):
        mid=self.add();pid=self.inject_plan('layout',[mid],{'layout':{'layout_column_ratio':.4}})
        self.ws.update_material(mid,{'category':'order'})
        with self.assertRaises(ValueError):apply_plan(self.ws,{'plan_id':pid,'confirmed':True})
        self.assertNotIn('layout',self.ws.order(self.oid))
    def test_unresolved_is_not_applied_as_stitched_document(self):
        a=self.add();b=self.add('b.png','blue');pid=self.inject_plan('stitch',[a,b],{'status':'unresolved'})
        with self.assertRaises(ValueError):apply_plan(self.ws,{'plan_id':pid,'confirmed':True})
        self.assertEqual(len(self.ws.records(self.oid)),2)
        self.assertTrue(self.ws.material(a)['selected'])
    def test_groups_move_selected_only_with_evidence_and_keep_pending_separate(self):
        a,b,c=self.add('a.png'),self.add('b.png'),self.add('pending.png')
        groups=[{'key':'g1','title':'FIRST','material_ids':[a], 'confidence':.9,'evidence':[{'material_id':a,'visible_fact':'TEST ORDER A'}],'roles':[{'material_id':a,'category':'order'}],'stitch_sets':[]},
                {'key':'g2','title':'SECOND','material_ids':[b],'confidence':.9,'evidence':[{'material_id':b,'visible_fact':'TEST ORDER B'}],'roles':[{'material_id':b,'category':'payment'}],'stitch_sets':[]}]
        pid=self.inject_plan('orders',[a,b,c],{'groups':groups,'unassigned':[c],'warnings':[]})
        with self.assertRaises(ValueError):apply_plan(self.ws,{'plan_id':pid})
        result=apply_plan(self.ws,{'plan_id':pid,'confirmed':True})
        self.assertEqual(len(result['order_ids']),2)
        self.assertNotEqual(self.ws.material(a)['order_id'],self.ws.material(b)['order_id'])
        self.assertNotIn(self.ws.material(c)['order_id'],result['order_ids'])
        self.assertEqual(self.ws.material(a)['category'],'order')
        with self.assertRaises(ValueError):apply_plan(self.ws,{'plan_id':pid,'confirmed':True})
    def test_layout_is_per_order_not_global(self):
        a=self.add();pid=self.inject_plan('layout',[a],{'layout':{'layout_column_ratio':.3},'legibility':'review'})
        result=apply_plan(self.ws,{'plan_id':pid,'confirmed':True})
        self.assertEqual(self.ws.order_settings(self.oid).extra['layout_column_ratio'],.3)
        self.assertNotIn('layout_column_ratio',self.ws.settings.extra)
    def test_local_smart_plan_has_review_before_source_changes(self):
        a=self.add();b=self.add('b.png','blue')
        mocked={'image':Image.new('RGB',(200,800),'white'),'ordered_ids':[b,a],'joins':[],'segments':[],'confidence':.99,'requires_review':True,'status':'matched','warnings':[]}
        with patch('app.smart_workflows.smart_stitch',return_value=mocked):
            plan=start_plan(self.ws,{'order_id':self.oid,'kind':'stitch','ids':[a,b],'use_ai':False})
        pid=plan['plan_id'];self.assertTrue(self.ws.material(a)['selected'])
        entry=public_plan(self.ws,pid);self.assertEqual(entry['result']['source_mode'],'pixels')
        applied=apply_plan(self.ws,{'plan_id':pid,'confirmed':True})
        self.assertFalse(self.ws.material(a)['selected']);self.assertFalse(self.ws.material(b)['selected'])
        self.assertEqual(self.ws.material(applied['material_id'])['source_ids'],[a,b])
        self.assertIn('stitch_audit',self.ws.material(applied['material_id']))
    def test_batch_job_scoped_to_workspace_records(self):
        a=self.add();other=self.ws.add_order({'title':'Other'});b=self.add('b.png');self.ws.update_material(b,{'order_id':other['id']})
        with patch('app.smart_workflows.BatchVisionClient.plan_orders',return_value={'groups':[],'unassigned':[a,b],'warnings':['unknown']}) as client:
            created=start_plan(self.ws,{'order_id':self.oid,'kind':'orders','scope':'workspace','use_ai':True,'consent':True})
        rows=client.call_args.args[0];self.assertEqual({r['id'] for r in rows},{a,b})
        self.assertFalse(public_plan(self.ws,created['plan_id'])['applied'])

if __name__=='__main__':unittest.main()
