"""Isolated browser QA server and loopback-only synthetic vision API.

This script never contacts an external API or uses user credentials/documents.
The mock is a protocol fixture, not an assessment of any real model's accuracy.
"""
from __future__ import annotations

import json
import random
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageDraw, ImageFont
from app.server import AppServer, Workspace
from app.core.ai_session import ConversationVisionClient

qa_name = sys.argv[1] if len(sys.argv) > 1 else uuid.uuid4().hex[:10]
if not re.fullmatch(r'[a-f0-9]{10}', qa_name):
    raise SystemExit('QA name must be a 10-character hex identifier')
QA = ROOT / '.qa-v20' / qa_name
INPUTS = QA / 'inputs'
INPUTS.mkdir(parents=True, exist_ok=True)
COUNTS = {'review_image_counts': [], 'review_turn_lengths': [], 'production_calls': 0, 'active_production': 0, 'max_production': 0, 'production_message_counts': []}
if (QA / 'mock-metrics.json').is_file():
    COUNTS.update(json.loads((QA / 'mock-metrics.json').read_text(encoding='utf-8')))
COUNTS['active_production'] = 0
COUNTS.setdefault('production_message_counts', [])
LOCK = threading.Lock()
EXPECTED = {}


def make_inputs():
    font = ImageFont.load_default(size=18)
    for group in 'ABCD':
        document = Image.new('RGB', (400, 920), 'white')
        draw = ImageDraw.Draw(document)
        draw.text((20, 7), f'SYNTHETIC QA {group} - NOT FOR CLAIM', font=font, fill='red')
        for y in range(45, 890, 47):
            rng = random.Random(y + ord(group) * 100)
            for x in range(25, 365, 12):
                for gy in range(7):
                    bits = rng.randrange(1, 31)
                    for gx in range(5):
                        if bits & (1 << gx):
                            draw.rectangle((x + gx * 2, y + gy * 2, x + gx * 2 + 1, y + gy * 2 + 1), fill='#384758')
        document.crop((0, 0, 400, 460)).save(INPUTS / f'{group}-order-top.png')
        document.crop((0, 350, 400, 920)).save(INPUTS / f'{group}-order-bottom.png')
        for category, size in [('invoice', (720, 460)), ('payment', (360, 720))]:
            im = Image.new('RGB', size, 'white')
            d = ImageDraw.Draw(im)
            d.rectangle((15, 15, size[0] - 15, size[1] - 15), outline='#96a7b8', width=2)
            lines = ['SYNTHETIC QA ONLY', 'NOT A REAL RECEIPT', f'GROUP {group} / {category.upper()}', f'TEST AMOUNT: {100 + ord(group) - 65}.00', 'DO NOT SUBMIT FOR REIMBURSEMENT']
            for n, line in enumerate(lines):
                d.text((26, 45 + n * 48), line, font=font, fill='red' if n < 2 else '#334455')
            im.save(INPUTS / f'{group}-{category}.png')
    Image.new('RGB', (240, 180), '#d2e0d9').save(INPUTS / 'unrelated-trash.png')


def latest_manifest(messages):
    content = messages[-1]['content']
    for part in content:
        if part.get('type') == 'text':
            try:
                value = json.loads(part['text'])
                if isinstance(value, dict) and 'known_material_ids' in value:
                    return value
            except (ValueError, TypeError):
                pass
    raise ValueError('No session manifest')


def review_result(messages):
    manifest = latest_manifest(messages)
    by_id = {}
    for message in messages:
        if message.get('role') == 'assistant':
            previous = json.loads(message['content'])
            by_id.update({m['material_id']: m for m in previous.get('materials', [])})
    for row in manifest.get('new_materials', []):
        name = row['name']
        if name.startswith('unrelated'):
            material = {'material_id': row['id'], 'category': 'trash', 'group_key': '', 'facts': {}, 'evidence': ['Synthetic unrelated color tile, no purchase facts']}
        else:
            group = name[0]
            category = 'invoice' if 'invoice' in name else 'payment' if 'payment' in name else 'order'
            facts = {'amount': f'{100 + ord(group) - 65}.00', 'merchant': 'Synthetic Merchant ' + group, 'product_name': 'Synthetic Fixture ' + group,
                     'quantity': '1', 'pay_time': '2026-11-09 10:00', 'order_no': 'SYNTHETIC-ORDER-' + group,
                     'buyer_name': '示例单位' if category == 'invoice' else '', 'taxpayer_id': '91110000MA01234567' if category == 'invoice' else '',
                     'address': '实验楼130' if category == 'order' else '', 'raw_text_summary': 'Clearly labelled synthetic QA fixture',
                     'extra': {'transaction_status': 'success', 'payment_direction': 'expense'}}
            material = {'material_id': row['id'], 'category': category, 'group_key': group, 'facts': facts, 'evidence': ['Synthetic fixture label ' + name]}
        by_id[row['id']] = material
    known = manifest['known_material_ids']
    # Revisit-only turns keep the previously extracted result and grouping.
    materials = [by_id[mid] for mid in known]
    groups = []
    for group in 'ABCD':
        members = [m for m in materials if m['group_key'] == group]
        if not members:
            continue
        mids = [m['material_id'] for m in members]
        checks = [{'code': code, 'status': 'pass', 'message': 'Synthetic protocol fixture evidence; no real-model assessment', 'material_ids': mids} for code in ('address', 'buyer', 'tax_id', 'amount')]
        order_ids = [m['material_id'] for m in members if m['category'] == 'order']
        groups.append({'key': group, 'title': '演示采购 ' + group + ' · 测试材料', 'material_ids': mids, 'checks': checks,
                       'stitch_sets': [{'category': 'order', 'material_ids': order_ids}] if len(order_ids) > 1 else []})
    return {'materials': materials, 'groups': groups, 'unassigned': [], 'trash': [m['material_id'] for m in materials if m['category'] == 'trash'], 'warnings': []}


def batch_manifest(content):
    for part in content:
        if part.get('type') == 'text':
            text = part.get('text', '')
            position = text.find('{')
            if position >= 0:
                try:
                    value = json.loads(text[position:])
                    if isinstance(value, dict) and 'materials' in value:
                        return value['materials']
                except ValueError:
                    pass
    return []


class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        messages = payload['messages']
        system = messages[0]['content']
        try:
            if '连续会话采购材料视觉' in system:
                with LOCK:
                    COUNTS['review_image_counts'].append(sum(p.get('type') == 'image_url' for m in messages if isinstance(m.get('content'), list) for p in m['content']))
                    COUNTS['review_turn_lengths'].append(len(messages))
                result = review_result(messages)
            elif '只看图并输出严格JSON' in system:
                result = dict(EXPECTED)
            else:
                with LOCK:
                    COUNTS['production_calls'] += 1
                    COUNTS['production_message_counts'].append(len(messages))
                    COUNTS['active_production'] += 1
                    COUNTS['max_production'] = max(COUNTS['max_production'], COUNTS['active_production'])
                try:
                    # Allow the four worker requests to overlap for observable QA.
                    threading.Event().wait(.15)
                    content = messages[-1]['content']
                    rows = batch_manifest(content)
                    if '乱序截图' in system:
                        ordered = sorted(rows, key=lambda r: ('bottom' in r['name'], r['name']))
                        mids = [r['material_id'] for r in ordered]
                        result = {'ordered_ids': mids, 'crops': [], 'joins': [{'above_id': a, 'below_id': b, 'overlap_hint': .2, 'reason': 'Synthetic overlapping source pattern'} for a, b in zip(mids, mids[1:])], 'confidence': .99, 'warnings': []}
                    else:
                        result = {'layout': {'layout_column_ratio': .54, 'layout_vertical_align': 'top', 'layout_left_mm': 77, 'layout_right_mm': 66, 'layout_top_mm': 30, 'layout_bottom_mm': 26, 'layout_gap_mm': 4}, 'crops': [], 'legibility': 'good', 'warnings': [], 'reason': 'Synthetic layout protocol fixture'}
                finally:
                    with LOCK:
                        COUNTS['active_production'] -= 1
            data = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(result, ensure_ascii=False)}}]}).encode('utf-8')
            self.send_response(200)
        except Exception as exc:
            data = json.dumps({'error': type(exc).__name__}).encode('utf-8')
            self.send_response(500)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        (QA / 'mock-metrics.json').write_text(json.dumps(COUNTS, indent=2), encoding='utf-8')


def main():
    make_inputs()
    original_challenge = ConversationVisionClient._connection_challenge
    def challenge():
        mime, image, expected = original_challenge()
        EXPECTED.clear(); EXPECTED.update(expected)
        return mime, image, expected
    ConversationVisionClient._connection_challenge = staticmethod(challenge)
    mock = ThreadingHTTPServer(('127.0.0.1', 0), MockHandler)
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    ws = Workspace(QA / 'data')
    ws.settings.output_dir = str(QA / 'exports')
    if len(sys.argv) > 1:
        ws.settings.ai_api_base = f'http://127.0.0.1:{mock.server_port}/v1'
    app = AppServer(('127.0.0.1', 0), workspace=ws)
    info = {'url': f'http://127.0.0.1:{app.server_port}/', 'api_base': f'http://127.0.0.1:{mock.server_port}/v1',
            'key': 'SYNTHETIC-LOCAL-QA-ONLY', 'inputs': str(INPUTS), 'data_dir': str(ws.root), 'metrics': str(QA / 'mock-metrics.json')}
    (ROOT / 'artifacts' / 'qa-v20-current.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(info, ensure_ascii=False), flush=True)
    try:
        app.serve_forever()
    finally:
        app.server_close(); mock.shutdown(); mock.server_close()


if __name__ == '__main__':
    main()
