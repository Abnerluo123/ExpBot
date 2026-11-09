"""Isolated integration tests using clearly-labelled synthetic documents only."""
from __future__ import annotations

import hashlib
import http.cookiejar
import io
import json
import os
import shutil
import uuid
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from app.config.settings import Settings, get_settings, save_settings
from app.server import AppServer, Workspace


def synthetic_bytes(size=(600, 1100), color='white'):
    im = Image.new('RGB', size, color)
    d = ImageDraw.Draw(im)
    d.rectangle((20, 20, size[0]-20, size[1]-20), outline='#667788', width=3)
    d.text((45, 50), 'SYNTHETIC TEST ONLY - NOT A REAL RECEIPT', fill='red')
    d.text((45, 140), 'TEST ITEM - 5 PIECES - 626.16 CNY', fill='black')
    b = io.BytesIO(); im.save(b, 'PNG'); return b.getvalue()


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        # Normal inherited Windows permissions; tempfile's private 0700 mode breaks child access in confined Windows.
        self.root = Path(__file__).resolve().parents[1] / 'output' / ('integration_' + uuid.uuid4().hex[:12])
        self.root.mkdir(parents=True, exist_ok=False)
        self.ws = Workspace(self.root / 'data')
        self.ws.settings = Settings(name='TEST_USER', advisor='TEST_ADVISOR', project_name='SYNTHETIC_NOT_FOR_CLAIM',
                                    declare_date='20261109', output_dir=str(self.root/'export'), pdf_dpi=150)
        self.order_id = self.ws.state['orders'][0]['id']
        self.ws.order(self.order_id).update(declare_date='20261109', project_name='SYNTHETIC_NOT_FOR_CLAIM')

    def tearDown(self):
        assert self.root.resolve().parent == (Path(__file__).resolve().parents[1] / 'output').resolve()
        shutil.rmtree(self.root)

    def add(self, name='order.png', size=(600, 1100)):
        return self.ws.import_bytes(synthetic_bytes(size), name, self.order_id)

    def test_original_unchanged_after_crop_and_undo(self):
        m=self.add()
        original=Path(self.ws.material(m['id'])['original'])
        checksum=hashlib.sha256(original.read_bytes()).hexdigest()
        self.ws.edit_material(m['id'], {'action':'crop','box':[0.1,0.1,0.5,0.6]})
        self.assertEqual(self.ws.material(m['id'])['width'],300)
        self.assertNotEqual(self.ws.material(m['id'])['working'],str(original))
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(),checksum)
        self.ws.edit_material(m['id'],{'action':'undo'})
        self.assertEqual(self.ws.material(m['id'])['working'],str(original))

    def test_split_keeps_source_and_excludes_it(self):
        m=self.add('combined.png', (1200,800))
        out=self.ws.edit_material(m['id'],{'action':'split','box':[0,0,0.5,1],'category':'payment'})
        self.assertEqual(out['category'],'payment')
        self.assertFalse(self.ws.material(m['id'])['selected'])
        self.assertEqual(out['source_id'],m['id'])
        self.assertEqual(len(self.ws.records(self.order_id)),2)

    def test_stitch_order_and_selection(self):
        a=self.add('order-top.png',(500,800));b=self.add('order-bottom.png',(500,700))
        out=self.ws.stitch({'ids':[a['id'],b['id']],'category':'order','direction':'vertical'})
        self.assertEqual((out['width'],out['height']),(500,1500))
        self.assertFalse(self.ws.material(a['id'])['selected'])
        self.assertFalse(self.ws.material(b['id'])['selected'])

    def test_crop_bounds_reject_without_changes(self):
        m=self.add()
        with self.assertRaises(ValueError):
            self.ws.edit_material(m['id'],{'action':'crop','box':[0.8,0,0.9,1]})
        self.assertEqual(self.ws.material(m['id'])['revision'],0)

    def test_orders_are_separate(self):
        a=self.add()
        second=self.ws.add_order({'declare_date':'20261109'})
        self.ws.update_material(a['id'],{'order_id':second['id']})
        self.assertFalse(self.ws.records(self.order_id))
        self.assertEqual(len(self.ws.records(second['id'])),1)
        self.assertEqual(second['order_index'],2)

    def test_bad_upload_type(self):
        with self.assertRaises(ValueError):self.ws.import_bytes(b'not an image','bad.exe',self.order_id)
        with self.assertRaises(ValueError):self.ws.import_bytes(b'not pdf','bad.pdf',self.order_id)

    def test_preview_landscape_and_no_artifacts_in_export(self):
        inv=self.add('invoice.png',(1000,700));o=self.add('order.png');p=self.add('payment.png')
        for m,c in [(inv,'invoice'),(o,'order'),(p,'payment')]:self.ws.update_material(m['id'],{'category':c})
        result=self.ws.preview(self.order_id)
        self.assertAlmostEqual(result['width']/result['height'],297/210,places=2)
        preview_file=self.ws.order(self.order_id)['preview_file']
        self.assertTrue((self.ws.root/'previews'/preview_file).is_file())
        with patch.object(self.ws,'start_job',side_effect=lambda kind,oid,fn:fn({})):
            exported=self.ws.export({'order_id':self.order_id,'manual_confirmed':True})
        folder=Path(exported['folder'])
        self.assertEqual(len(list(folder.iterdir())),2)
        self.assertTrue(all(p.suffix=='.pdf' for p in folder.iterdir()))
        self.assertTrue(Path(exported['voucher']).read_bytes().startswith(b'%PDF-'))

    def test_unknowns_need_manual_confirmation(self):
        with self.assertRaises(ValueError):self.ws.export({'order_id':self.order_id})
        with self.assertRaises(ValueError):self.ws.analyze({'order_id':self.order_id,'consent':False})

    def test_state_persists_without_embedded_key(self):
        self.add()
        self.ws.save()
        restored=Workspace(self.ws.root)
        self.assertEqual(len(restored.records(self.order_id)),1)
        self.assertNotIn('original',next(iter(restored.public_state()['materials'].values())))

    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_api_key_encrypted_and_redacted(self):
        settings=Settings(ai_api_key='sk-LOCAL-SYNTHETIC-KEY', name='Synthetic')
        path=self.root/'settings.json'
        save_settings(settings,path)
        payload=path.read_text(encoding='utf-8')
        self.assertNotIn(settings.ai_api_key,payload)
        self.assertIn('api_key_dpapi',payload)
        self.assertEqual(get_settings(path).ai_api_key,settings.ai_api_key)
        self.ws.settings=settings
        public=self.ws.public_state()['settings']
        self.assertEqual(public['ai_api_key'],'')
        self.assertTrue(public['has_api_key'])

    def test_local_raster_pdf_tool(self):
        a=self.add();b=self.add('payment.png')
        result=self.ws.pdf_tool({'ids':[b['id'],a['id']]})
        self.assertEqual(result['pages'],2)
        self.assertIn('图片版',result['name'])
        self.assertTrue(Path(result['path']).exists())


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_root=Path(__file__).resolve().parents[1]/'output'/('http_integration_'+uuid.uuid4().hex[:12])
        cls.test_root.mkdir(parents=True,exist_ok=False)
        cls.ws=Workspace(cls.test_root/'data')
        cls.server=AppServer(('127.0.0.1',0),workspace=cls.ws)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'
        cls.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with cls.opener.open(cls.url+'/') as r:cls.html=r.read().decode()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(3)
        assert cls.test_root.resolve().parent == (Path(__file__).resolve().parents[1]/'output').resolve()
        shutil.rmtree(cls.test_root)

    def post(self,path,data,token=True):
        headers={'Content-Type':'application/json'}
        if token:headers['X-App-Token']=self.server.token
        req=urllib.request.Request(self.url+path,data=json.dumps(data).encode(),headers=headers,method='POST')
        return self.opener.open(req)

    def test_main_has_local_assets_only(self):
        self.assertIn('简报',self.html)
        self.assertNotIn('__APP_TOKEN__',self.html)
        self.assertIn('src="/wizard.js"',self.html)
        with self.opener.open(self.url+'/wizard.css') as r:self.assertEqual(r.status,200)
        with self.opener.open(self.url+'/wizard.js') as r:self.assertEqual(r.status,200)

    def test_session_state_response(self):
        with self.opener.open(self.url+'/api/state') as r:
            data=json.load(r)
        self.assertIn('orders',data)
        self.assertNotIn('api_key_dpapi',data['settings'])

    def test_write_requires_session_token(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.post('/api/orders',{},token=False)
        with self.post('/api/orders',{'title':'SYNTHETIC TEST ONLY'}) as r:self.assertEqual(r.status,200)

    def test_upload_http_and_preview_thumb(self):
        oid=self.ws.state['orders'][0]['id']
        req=urllib.request.Request(self.url+f'/api/upload?order_id={oid}&name=synthetic.png',data=synthetic_bytes(),headers={'X-App-Token':self.server.token,'Content-Type':'application/octet-stream'},method='POST')
        with self.opener.open(req) as r:m=json.load(r)
        with self.opener.open(self.url+m['thumb_url']) as r:self.assertEqual(r.headers.get_content_type(),'image/jpeg')


if __name__=='__main__':unittest.main()
