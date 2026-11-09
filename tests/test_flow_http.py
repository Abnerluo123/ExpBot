"""HTTP acceptance for the compact wizard; all materials and keys are synthetic."""
from __future__ import annotations

import copy
import http.cookiejar
import io
import json
import shutil
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app import flow_engine as flow
from app.config.defaults import REQUIRED_ADDRESS
from app.config.settings import Settings
from app.core.models import ExtractedInfo, ImageCategory
from app.core.validator import validate_extracted
from app.server import AppServer, Workspace


class FlowHttpTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / 'output' / ('flow-http-' + uuid.uuid4().hex[:12])
        self.root.mkdir(parents=True)
        self.ws = Workspace(self.root / 'data')
        self.ws.settings = Settings(name='TEST', advisor='TEST', project_name='SYNTHETIC',
                                    declare_date='20261109', output_dir=str(self.root / 'exports'))
        self.server = AppServer(('127.0.0.1', 0), workspace=self.ws)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with self.opener.open(self.base + '/') as response:
            self.html = response.read().decode('utf-8')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        assert self.root.resolve().parent == (Path(__file__).resolve().parents[1] / 'output').resolve()
        shutil.rmtree(self.root)

    def post(self, path, data):
        request = urllib.request.Request(self.base + path, data=json.dumps(data).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'X-App-Token': self.server.token}, method='POST')
        with self.opener.open(request) as response:
            return json.load(response)

    def state(self):
        with self.opener.open(self.base + '/api/state') as response:
            return json.load(response)

    def upload(self, run_id, name='order-invoice-payment.png'):
        buffer = io.BytesIO()
        Image.new('RGB', (120, 180), '#f2f5f8').save(buffer, 'PNG')
        request = urllib.request.Request(self.base + '/api/upload?' + urllib.parse.urlencode({'run_id': run_id, 'name': name}),
            data=buffer.getvalue(), headers={'Content-Type': 'application/octet-stream', 'X-App-Token': self.server.token}, method='POST')
        with self.opener.open(request) as response:
            return json.load(response)

    def grouped_run(self, count=2):
        public = self.post('/api/flow/new', {})
        materials = [self.upload(public['id'], f'purchase-{index}.png') for index in range(count)]
        with self.ws.lock:
            run = self.ws.state['runs'][public['id']]
            flow._apply_review(self.ws, run, {
                'materials': [{'material_id': m['id'], 'category': 'order', 'facts': {'amount': '10.00'}} for m in materials],
                'groups': [{'key': f'purchase-{index}', 'title': f'采购{index + 1}', 'material_ids': [m['id']], 'checks': []}
                           for index, m in enumerate(materials)],
                'unassigned': [], 'trash': [], 'warnings': []})
            run.update(stage='review', _review_source_version=run['_source_version'])
            self.ws.save()
        return flow.public_run(self.ws, public['id']), materials

    def test_remove_order_preserves_local_source_download_and_other_purchase(self):
        run, materials = self.grouped_run()
        removed_oid, remaining_oid = run['order_ids']
        mid = materials[0]['id']
        original = Path(self.ws.material(mid)['original']).read_bytes()
        with patch.object(flow, '_make_one', side_effect=AssertionError('removal cannot produce PDFs')):
            removed = self.post('/api/flow/remove-order', {'run_id': run['id'], 'order_id': removed_oid, 'revision': run['revision']})
        self.assertEqual(removed['order_ids'], [remaining_oid])
        self.assertEqual(removed['revision'], run['revision'] + 1)
        self.assertEqual(removed['review']['groups'][0]['material_ids'], [materials[1]['id']])
        self.assertEqual(removed['review']['trash'], [mid])
        self.assertIn('原始材料仍保留在本地', self.ws.state['runs'][run['id']]['_excluded_groups'][-1]['message'])
        self.assertIn(mid, removed['material_ids'])
        with self.opener.open(self.base + f'/media/{mid}/original') as response:
            self.assertEqual(response.read(), original)
        state = self.state()
        self.assertIn(mid, state['materials'])
        self.assertFalse(state['materials'][mid]['selected'])
        self.assertNotIn('original', state['materials'][mid])
        saved = next(r for r in state['flow']['runs'] if r['id'] == run['id'])
        self.assertEqual(saved['order_ids'], [remaining_oid])
        self.assertEqual(saved['production'], [])
        self.assertEqual(saved['exports'], [])
        self.assertFalse((self.ws.root / 'plans').exists())

    def test_remove_all_orders_archives_without_creating_empty_pdfs(self):
        run, materials = self.grouped_run(count=1)
        with patch.object(flow, '_make_one', side_effect=AssertionError('excluded purchase cannot make PDFs')):
            removed = self.post('/api/flow/remove-order', {'run_id': run['id'], 'order_id': run['order_ids'][0], 'revision': run['revision']})
            made = self.post('/api/flow/make', {'run_id': run['id'], 'confirmed': True})
        self.assertEqual(removed['stage'], 'done')
        self.assertTrue(made['result']['archived'])
        self.assertEqual(removed['order_ids'], [])
        self.assertEqual(removed['production'], [])
        self.assertEqual(removed['exports'], [])
        self.assertFalse((self.ws.root / 'plans').exists())
        self.assertFalse((self.root / 'exports').exists())
        self.assertTrue(Path(self.ws.material(materials[0]['id'])['original']).is_file())
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post('/api/flow/export', {'run_id': run['id'], 'confirmed': True})
        self.assertEqual(caught.exception.code, 400)

    def test_remove_order_rejects_stale_revision_and_live_jobs(self):
        public, _ = self.grouped_run(count=1)
        run = self.ws.state['runs'][public['id']]
        body = {'run_id': run['id'], 'order_id': run['order_ids'][0], 'revision': run['revision'] - 1}
        before = copy.deepcopy(self.ws.state)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post('/api/flow/remove-order', body)
        self.assertEqual(caught.exception.code, 400)
        self.assertIn('批次已更新', json.load(caught.exception)['error'])
        self.assertEqual(self.ws.state, before)
        body['revision'] = run['revision']
        for status in ('running', 'pending-job'):
            with self.subTest(status=status):
                with self.ws.lock:
                    run['status'] = 'running' if status == 'running' else 'idle'
                    if status == 'pending-job':
                        self.ws.jobs['pending-exclusion-http'] = {'id': 'pending-exclusion-http', 'kind': 'flow-make',
                            'run_id': run['id'], 'order_id': run['inbox_order_id'], 'status': 'pending'}
                before = copy.deepcopy(self.ws.state)
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.post('/api/flow/remove-order', body)
                self.assertEqual(caught.exception.code, 400)
                self.assertIn('正在处理', json.load(caught.exception)['error'])
                self.assertEqual(self.ws.state, before)

    def test_new_flow_only_collects_and_upload_does_not_classify(self):
        with patch('app.core.classifier.load_image_item', side_effect=AssertionError('import must not classify')):
            run = self.post('/api/flow/new', {})
            self.assertEqual(run['stage'], 'collect')
            material = self.upload(run['id'], '电子发票-order-payment.png')
        self.assertEqual(material['category'], 'unknown')
        state = self.state()
        saved = next(r for r in state['flow']['runs'] if r['id'] == run['id'])
        self.assertIn(material['id'], saved['material_ids'])
        self.assertEqual(state['flow']['active_run_id'], run['id'])
        self.assertEqual(state['jobs'], [])
        self.assertNotIn('session', saved)
        self.assertNotIn('original', state['materials'][material['id']])

    def test_wizard_entry_has_start_history_and_local_assets(self):
        self.assertIn('简报', self.html)
        self.assertIn('/wizard.js', self.html)
        self.assertIn('/wizard.css', self.html)
        self.assertNotIn('__APP_TOKEN__', self.html)
        for asset in ('/wizard.js', '/wizard.css'):
            with self.opener.open(self.base + asset) as response:
                self.assertEqual(response.status, 200)

    def test_upload_blocks_mutation_during_processing(self):
        run = self.post('/api/flow/new', {})
        with self.ws.lock:
            self.ws.state['runs'][run['id']]['status'] = 'running'
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.upload(run['id'])
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.ws.state['materials'], {})

    def test_unknown_flow_endpoint_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.post('/api/flow/unknown', {})

    def test_api_test_uses_saved_key_and_exposes_only_result(self):
        self.ws.settings.ai_api_key = 'SYNTHETIC-SECRET-DO-NOT-ECHO'
        called = threading.Event()
        def connection(client):
            self.assertEqual(client.key, self.ws.settings.ai_api_key)
            called.set()
            return {'connected': True, 'vision_supported': True, 'message': 'synthetic passed', 'model': client.model}
        with patch('app.core.ai_session.ConversationVisionClient.test_connection', connection):
            job = self.post('/api/ai/test', {})
            self.assertTrue(called.wait(3))
        self.assertEqual(job['kind'], 'api-test')
        self.assertNotIn(self.ws.settings.ai_api_key, json.dumps(job))
        public = self.state()
        self.assertNotIn(self.ws.settings.ai_api_key, json.dumps(public))
        actual = next(j for j in public['jobs'] if j['id'] == job['id'])
        self.assertEqual(actual['status'], 'done')
        self.assertTrue(actual['result']['vision_supported'])

    def test_bad_test_endpoint_never_sends_a_request(self):
        self.ws.settings.ai_api_key = 'SYNTHETIC'
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('must not send')):
            # Call the workspace method directly so the local HTTP client remains usable.
            with self.assertRaises(RuntimeError):
                self.ws.test_ai({'ai_api_base': 'http://example.invalid/v1'})


class SemanticAddressTests(unittest.TestCase):
    def test_ai_verified_abbreviation_keeps_original_and_passes(self):
        info = ExtractedInfo(amount='10.00', address='实验楼130', extra={'address_review': {
            'status': 'pass', 'message': '同一学校校区上下文明确，实验楼130指向目标实验室',
            'reference': REQUIRED_ADDRESS, 'source': 'ai'}})
        issues = validate_extracted(info, ImageCategory.ORDER)
        self.assertNotIn('bad_address', {i.code for i in issues})
        self.assertEqual(info.address, '实验楼130')

    def test_ai_uncertain_address_is_a_review_warning(self):
        info = ExtractedInfo(address='130', extra={'address_review': {
            'status': 'review', 'message': '无法确认楼栋，请补充地址截图',
            'reference': REQUIRED_ADDRESS, 'source': 'ai'}})
        issues = validate_extracted(info, ImageCategory.ORDER)
        self.assertIn('address_review', {i.code for i in issues})
        self.assertNotIn('bad_address', {i.code for i in issues})

    def test_ai_failed_address_remains_visible(self):
        info = ExtractedInfo(address='其他校区', extra={'address_review': {
            'status': 'fail', 'message': '可见地址为不同校区',
            'reference': REQUIRED_ADDRESS, 'source': 'ai'}})
        issues = validate_extracted(info, ImageCategory.ORDER)
        self.assertIn('address_mismatch', {i.code for i in issues if i.level == 'error'})


if __name__ == '__main__':
    unittest.main()
