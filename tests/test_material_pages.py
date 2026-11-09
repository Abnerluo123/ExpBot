"""Original multi-page material previews list all pages and render on demand."""
from __future__ import annotations

import http.cookiejar
import io
import json
import shutil
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from app.server import AppServer, Workspace


class MaterialPageTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / 'output' / ('material-pages-' + uuid.uuid4().hex[:10])
        self.root.mkdir(parents=True)
        self.ws = Workspace(self.root / 'data')
        self.oid = self.ws.state['orders'][0]['id']
        images = [Image.new('RGB', (240, 180), c) for c in ('#f0d0d0', '#d0f0d0', '#d0d0f0')]
        buffer = io.BytesIO()
        images[0].save(buffer, 'PDF', save_all=True, append_images=images[1:])
        def first(source, target, scale=2.5):
            images[0].save(target, 'PNG')
            return target, 3
        with patch('app.server.render_pdf_page', side_effect=first):
            self.material = self.ws.import_bytes(buffer.getvalue(), 'multi-page-invoice.pdf', self.oid)
        for image in images:
            image.close()
        self.server = AppServer(('127.0.0.1', 0), self.ws)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with self.opener.open(self.base + '/') as response:
            response.read()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3)
        assert self.root.resolve().parent == (Path(__file__).resolve().parents[1] / 'output').resolve()
        shutil.rmtree(self.root)

    def test_lists_every_page_without_rendering_or_mutating_material(self):
        before = self.ws.material(self.material['id'])['working']
        with patch('app.server.render_pdf_page', side_effect=AssertionError('metadata must not render')):
            with self.opener.open(self.base + '/api/material-pages?id=' + self.material['id']) as response:
                result = json.load(response)
        self.assertEqual([p['page'] for p in result['pages']], [1, 2, 3])
        self.assertEqual(result['total_pages'], 3)
        self.assertTrue(result['original_url'].endswith('/original'))
        self.assertEqual(self.ws.material(self.material['id'])['working'], before)

    def test_requested_page_renders_once_and_preserves_current_working_page(self):
        before = self.ws.material(self.material['id'])['working']
        def render(source, target, scale, index):
            self.assertEqual(index, 1)
            self.assertEqual(scale, 4.0)
            with Image.new('RGB', (240, 180), '#d0f0d0') as image:
                image.save(target, 'PNG')
            return target, 3
        with patch('app.server.render_pdf_page', side_effect=render) as rendered:
            for _ in range(2):
                with self.opener.open(self.base + '/media/' + self.material['id'] + '/page/2') as response:
                    self.assertEqual(response.headers.get_content_type(), 'image/png')
                    self.assertTrue(response.read().startswith(b'\x89PNG'))
            self.assertEqual(rendered.call_count, 1)
        self.assertEqual(self.ws.material(self.material['id'])['working'], before)

    def test_legacy_production_preview_is_lazily_rendered_from_saved_pdf(self):
        pid = uuid.uuid4().hex[:20]
        folder = self.ws.root / 'plans' / pid; folder.mkdir(parents=True)
        shutil.copy2(self.ws.material(self.material['id'])['original'], folder / 'invoice.pdf')
        self.ws.state['plans'][pid] = {'id': pid, 'kind': 'flow-production'}
        before = (folder / 'invoice.pdf').read_bytes()
        def render(source, target, scale, index):
            self.assertEqual(Path(source), folder / 'invoice.pdf')
            self.assertEqual((scale, index), (4.0, 1))
            with Image.new('RGB', (480, 360), '#d0f0d0') as image:
                image.save(target, 'PNG')
            return target, 3
        with patch('app.server.render_pdf_page', side_effect=render) as renderer:
            for _ in range(2):
                with self.opener.open(self.base + f'/plan-preview/{pid}/invoice-hq-page-2.png') as response:
                    self.assertEqual(response.headers.get_content_type(), 'image/png')
                    self.assertTrue(response.read().startswith(b'\x89PNG'))
            self.assertEqual(renderer.call_count, 1)
        self.assertEqual((folder / 'invoice.pdf').read_bytes(), before)

    def test_unsafe_page_path_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.opener.open(self.base + '/media/' + self.material['id'] + '/page/0')
        with self.assertRaises(urllib.error.HTTPError):
            self.opener.open(self.base + '/media/' + self.material['id'] + '/page/not-a-number')


if __name__ == '__main__':
    unittest.main()
