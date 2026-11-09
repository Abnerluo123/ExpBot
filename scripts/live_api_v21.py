"""Explicitly authorized DeepSeek acceptance tests. Credentials exist in memory only.

Set JIANBAO_TEST_API_KEY for this process. This script never persists credentials,
request image data or authorization headers and is excluded from the portable app.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from PIL import Image, ImageDraw, ImageFont, ImageOps
from app.config.settings import Settings
from app.core.ai_session import ConversationVisionClient
from app.core.ai_batch import BatchVisionClient
from app.core.ai_vision import _NoRedirectHandler

OUT = ROOT / '.qa-v21-live'
OUT.mkdir(exist_ok=True)


def settings():
    key = os.environ.pop('JIANBAO_TEST_API_KEY', '').strip()
    if not key:
        raise RuntimeError('The authorized temporary API credential is not available in this process')
    return Settings(name='软件验收', advisor='测试', project_name='真实API验收', declare_date='20261109',
                    ai_api_base=os.environ.get('JIANBAO_TEST_API_BASE', 'https://api.deepseek.com'),
                    ai_vision_model=os.environ.get('JIANBAO_TEST_MODEL', 'deepseek-flash'),
                    ai_model=os.environ.get('JIANBAO_TEST_MODEL', 'deepseek-flash'), ai_api_key=key,
                    output_dir=str(OUT / 'exports'), pdf_dpi=200)


def redact(value, cfg):
    if isinstance(value, str):
        return value.replace(cfg.ai_api_key, '[redacted]')
    if isinstance(value, list):
        return [redact(v, cfg) for v in value]
    if isinstance(value, dict):
        return {k: redact(v, cfg) for k, v in value.items() if str(k).lower() not in {'authorization', 'ai_api_key'}}
    return value


def save(name, report, cfg):
    safe = redact(report, cfg)
    target = OUT / (name + '.json')
    target.write_text(json.dumps(safe, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    summary = {'saved': target.name, 'mode': safe.get('mode', name)}
    if 'batches' in safe:
        summary['batches'] = safe['batches']
    if isinstance(safe.get('result'), dict):
        summary['groups'] = len(safe['result'].get('groups', []))
        if 'vision_supported' in safe['result']:
            summary['result'] = safe['result']
    if 'steps' in safe:
        summary['steps'] = {k: {'success': v.get('success'), 'seconds': v.get('seconds')} if isinstance(v, dict) else v for k, v in safe['steps'].items()}
    if 'success' in safe:
        summary['success'] = safe['success']
    if 'error' in safe:
        summary['error'] = safe['error']
    print(json.dumps(summary, ensure_ascii=False, default=str), flush=True)
    return safe


def diagnostics(cfg):
    result = {}
    opener = urllib.request.build_opener(_NoRedirectHandler())
    request = urllib.request.Request(cfg.ai_api_base.rstrip('/') + '/models', headers={'Authorization': 'Bearer ' + cfg.ai_api_key})
    try:
        with opener.open(request, timeout=40) as response:
            body = json.load(response)
            result['models_status'] = response.status
            result['models'] = [row.get('id') for row in body.get('data', []) if isinstance(row, dict)]
    except urllib.error.HTTPError as error:
        result['models_status'] = error.code
        result['error'] = error.read(3000).decode('utf-8', errors='replace')
        error.close()
    except Exception as error:
        result['network_error'] = type(error).__name__ + ': ' + str(error)
    return redact(result, cfg)


def probe(cfg):
    client = ConversationVisionClient(cfg)
    start = time.monotonic()
    result = client.test_connection()
    report = {'mode': 'probe', 'real_external_api': True, 'model': client.model, 'base': client.base,
              'seconds': round(time.monotonic() - start, 2), 'result': result}
    if not result.get('vision_supported'):
        report['diagnostics'] = diagnostics(cfg)
    return save('probe', report, cfg)


def record(material_id, path, category='unknown', extracted=None):
    path = Path(path)
    with Image.open(path) as im:
        width, height = im.size
    return {'id': material_id, 'name': path.name, 'path': str(path), 'category': category,
            'width': width, 'height': height, 'extracted': extracted or {}}


def prepare():
    folder = OUT / 'inputs'
    folder.mkdir(exist_ok=True)
    samples = ROOT / 'examples/v1_1-input'
    source_paths = [('real-bottom', samples / '截图-底部.jpg'), ('real-invoice', ROOT / 'examples/示例-电子发票.png'),
                    ('real-payment', ROOT / '.qa-data/materials/3adb41d97600452cb4ae/original.png'),
                    ('real-top', samples / '截图-顶部.jpg'), ('real-middle', samples / '截图-中段.jpg')]
    rows = [record(mid, path) for mid, path in source_paths]
    # A separate, conspicuously marked synthetic purchase gives a known expected
    # second group, without changing any real receipt or pretending it is genuine.
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 29)
    small = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 23)
    for category, title in [('invoice', '测试发票'), ('order', '测试订单'), ('payment', '测试付款记录')]:
        path = folder / ('synthetic-' + category + '.png')
        image = Image.new('RGB', (860, 1150), 'white')
        d = ImageDraw.Draw(image)
        d.rectangle((18, 18, 842, 1132), outline='#8899aa', width=2)
        lines = ['软件自动验收 · 合成资料 · 不可报销', title, '测试商品：实验用连接线套装', '测试商户：自动验收样例商店',
                 '订单号：TEST-B-20261109-7731', '数量：3套', '总额 / 实付：88.20元', '时间：2026-11-09 12:30:00']
        if category == 'invoice':
            lines += ['购买方：示例单位', '税号：91110000MA01234567']
        elif category == 'order':
            lines += ['收货地址：示例单位示例园区东区实验楼130']
        else:
            lines += ['交易成功 · 支出 · 已支付', '付款方式：测试银行卡']
        for n, text in enumerate(lines):
            d.text((42, 45 + n * 78), text, font=small if len(text) > 29 else font, fill='#b32929' if n == 0 else '#20252a')
        image.save(path)
        rows.append(record('test-' + category, path))
    trash = folder / 'unrelated.png'
    image = Image.new('RGB', (600, 360), '#e5f0e9')
    ImageDraw.Draw(image).text((40, 120), '与采购无关的风景占位图片', font=font, fill='#31755a')
    image.save(trash)
    rows.append(record('trash', trash))
    # A mixed original-image composition is a test fixture only; its components
    # are unaltered source pixels, with an explicitly added outer black border.
    with Image.open(source_paths[3][1]) as im:
        left = im.convert('RGB'); left.thumbnail((900, 1100))
    with Image.open(source_paths[2][1]) as im:
        right = im.convert('RGB'); right.thumbnail((500, 1100))
    canvas = Image.new('RGB', (left.width + right.width + 28, max(left.height, right.height)), 'white')
    canvas.paste(left, (0, 0)); canvas.paste(right, (left.width + 28, 0))
    bordered = ImageOps.expand(canvas, border=(23, 19, 31, 27), fill='black')
    bordered.save(folder / 'mixed-black-border.png')
    return rows


def classify(cfg):
    rows = prepare()
    client = ConversationVisionClient(cfg)
    session = None
    report = {'mode': 'classify', 'real_external_api': True, 'model': client.model, 'batches': [], 'expected_groups': 2, 'expected_trash': ['trash']}
    for start in range(0, len(rows), 8):
        subset = rows[start:start + 8]
        begin = time.monotonic()
        try:
            response = client.analyze_batch(subset, session, profile={'name': cfg.name, 'advisor': cfg.advisor, 'project_name': cfg.project_name}, final=start + 8 >= len(rows))
            session = response['session']
            report['batches'].append({'images': len(subset), 'seconds': round(time.monotonic() - begin, 2), 'success': True,
                                      'groups': len(response['result']['groups']), 'turn': session['revision']})
            # Complete safe model results are local QA artifacts, never delivered
            # as authentic reimbursement evidence or included in the app bundle.
            save('classify-latest-result', response['result'], cfg)
            (OUT / 'classification-session.json').write_text(json.dumps(redact(session, cfg), ensure_ascii=False, indent=2), encoding='utf-8')
            report['result'] = response['result']
        except Exception as error:
            report['batches'].append({'images': len(subset), 'seconds': round(time.monotonic() - begin, 2), 'success': False, 'error': str(error)})
            break
    return save('classify', report, cfg)


def plans(cfg):
    prepare()
    client = BatchVisionClient(cfg)
    report = {'mode': 'plans', 'real_external_api': True, 'model': client.model, 'steps': {}}
    rows = [record('bottom', ROOT / 'examples/v1_1-input/截图-底部.jpg', 'order'),
            record('top', ROOT / 'examples/v1_1-input/截图-顶部.jpg', 'order'),
            record('middle', ROOT / 'examples/v1_1-input/截图-中段.jpg', 'order')]
    begin = time.monotonic()
    try:
        plan = client.plan_stitch(rows)
        from app.core.smart_stitch import smart_stitch
        output = smart_stitch(rows, plan=plan)
        image = output.pop('image')
        path = OUT / 'live-smart-stitch.png'; image.save(path); image.close()
        report['steps']['stitch'] = {'success': True, 'seconds': round(time.monotonic() - begin, 2), 'plan': plan, 'local_result': output}
        sides = [record('order-side', path, 'order'), record('payment-side', ROOT / '.qa-data/materials/3adb41d97600452cb4ae/original.png', 'payment')]
        begin = time.monotonic()
        layout = client.plan_layout(sides, constraints={'min_left_mm': 77, 'min_right_mm': 8, 'min_top_mm': 24, 'min_bottom_mm': 24, 'min_gap_mm': 4, 'order_on_left': False})
        report['steps']['layout'] = {'success': True, 'seconds': round(time.monotonic() - begin, 2), 'plan': layout}
    except Exception as error:
        report['steps']['stitch_or_layout_error'] = str(error)
    if hasattr(client, 'plan_borders'):
        begin = time.monotonic()
        try:
            candidate = {'box': [23/1435,19/1026,1381/1435,980/1026], 'margins_px': [23,19,31,27]}
            result = client.plan_borders([{**record('mixed', OUT / 'inputs/mixed-black-border.png'), 'border_candidate': candidate}])
            report['steps']['borders'] = {'success': True, 'seconds': round(time.monotonic() - begin, 2), 'plan': result}
        except Exception as error:
            report['steps']['borders'] = {'success': False, 'error': str(error)}
    mixed = record('mixed', OUT / 'inputs/mixed-black-border.png', 'other', {'extra': {'regions': [
        {'category': 'order', 'box': [0, 0, .67, 1]}, {'category': 'payment', 'box': [.67, 0, .33, 1]}]}})
    begin = time.monotonic()
    try:
        from app.flow_engine import _mixed_records
        warnings, audits = [], []
        derived = _mixed_records([mixed], OUT, client, warnings, audits)
        report['steps']['regions'] = {'success': len(derived) >= 2, 'seconds': round(time.monotonic() - begin, 2),
            'roles': [r['category'] for r in derived], 'warnings': warnings, 'audits': audits}
    except Exception as error:
        report['steps']['regions'] = {'success': False, 'error': str(error)}
    return save('plans', report, cfg)


def flow(cfg):
    """Run the real application worker path with two orders and nine inputs."""
    import threading
    import uuid
    from app.server import Workspace
    from app import flow_engine as engine
    from app.config.settings import save_settings
    from pypdf import PdfReader
    rows = prepare()
    # Use the original vector PDF for this full application pass.
    rows[1] = {**rows[1], 'path': str(ROOT / 'examples/v1_1-input/原版发票.pdf'), 'name': '原版发票.pdf'}
    root = OUT / ('flow-' + uuid.uuid4().hex[:10])
    ws = Workspace(root / 'data')
    ws.settings = copy.deepcopy(cfg)
    ws.settings.output_dir = str(root / 'exports')
    run = engine.create_run(ws, {})
    for row in rows:
        path = Path(row['path'])
        material = ws.import_bytes(path.read_bytes(), row['name'], run['inbox_order_id'])
        engine.add_material(ws, run['id'], material['id'])
    original_hashes = {m['id']: hashlib.sha256(Path(m['original']).read_bytes()).hexdigest() for m in ws.state['materials'].values()}
    report = {'mode': 'flow', 'real_external_api': True, 'model': cfg.ai_vision_model, 'run_id': run['id'], 'data_dir': str(ws.root), 'steps': {}}
    def wait_job(job):
        worker = ws._flow_workers.get(job['id'])
        if worker:
            worker.join(900)
            if worker.is_alive():
                raise RuntimeError('Application worker exceeded acceptance deadline')
        actual = copy.deepcopy(ws.jobs[job['id']])
        if actual['status'] != 'done':
            raise RuntimeError(actual.get('message', 'Worker failed'))
        return actual
    try:
        begin = time.monotonic()
        reviewed = wait_job(engine.start_review(ws, {'run_id': run['id'], 'consent': True}))
        public = engine.public_run(ws, run['id'])
        report['steps']['review'] = {'success': True, 'seconds': round(time.monotonic() - begin, 2), 'summary': public['session_summary'], 'groups': len(public['review']['groups']), 'issues': public['review']['issues'], 'result': reviewed.get('result')}
        # Synthetic documents are explicitly marked not for claim, so accepting
        # their review warning is required for exercising the preparation path.
        begin = time.monotonic()
        made = wait_job(engine.start_make(ws, {'run_id': run['id'], 'confirmed': True, 'force': bool(public['review']['issues']), 'reason': '软件验收：合成材料不可申报，测试制作流程'}))
        production = engine.public_run(ws, run['id'])['production']
        report['steps']['make'] = {'success': all(p.get('status') == 'ready' for p in production), 'seconds': round(time.monotonic() - begin, 2), 'result': made.get('result'), 'production': production}
        if not report['steps']['make']['success']:
            raise RuntimeError('At least one production failed; inspect safe production result')
        begin = time.monotonic()
        exported = wait_job(engine.start_export(ws, {'run_id': run['id'], 'confirmed': True, 'force': True, 'reason': '测试导出到隔离目录，不作报销提交'}))
        checks = []
        for item in ws.state['runs'][run['id']]['exports']:
            files = list(Path(item['folder']).iterdir())
            assert len(files) == 2 and all(f.suffix == '.pdf' for f in files)
            invoice, voucher = PdfReader(item['invoice']), PdfReader(item['voucher'])
            assert len(voucher.pages) == 1
            checks.append({'order_id': item['order_id'], 'files': 2, 'invoice_pages': len(invoice.pages), 'voucher_pages': 1})
        report['steps']['export'] = {'success': len(checks) == 2, 'seconds': round(time.monotonic() - begin, 2), 'checks': checks, 'result': exported.get('result')}
        assert all(hashlib.sha256(Path(ws.material(mid)['original']).read_bytes()).hexdigest() == digest for mid, digest in original_hashes.items())
        report['originals_unchanged'] = True
        report['success'] = all(v['success'] for v in report['steps'].values())
    except Exception as error:
        report['error'] = str(error)
        report['success'] = False
    finally:
        ws.settings.ai_api_key = ''
        save_settings(ws.settings, ws.root / 'settings.json')
        report['public_final'] = engine.public_run(ws, run['id'])
        save('flow', report, cfg)
    return report


def borders(cfg):
    prepare()
    from app.core.border_crop import crop_black_borders
    path = OUT / 'inputs/mixed-black-border.png'
    candidate = crop_black_borders(path)
    l,t,r,b = candidate['source_box']; width,height = candidate['source_size'];candidate['image'].close()
    hint = {'box':[l/width,t/height,(r-l)/width,(b-t)/height], 'margins_px':[l,t,width-r,height-b]}
    client = BatchVisionClient(cfg)
    start = time.monotonic()
    plan = client.plan_borders([{**record('mixed',path),'border_candidate':hint}])
    proposals = plan.get('materials',plan.get('borders',[]))
    actual = crop_black_borders(path, proposals[0] if proposals else None)
    image=actual.pop('image');image.save(OUT/'border-removed.png');image.close()
    return save('borders', {'mode':'borders','real_external_api':True,'success':actual['source_box']==[23,19,1404,999],
                           'seconds':round(time.monotonic()-start,2),'candidate':hint,'model_plan':plan,'actual':actual},cfg)


def recovery(cfg):
    client=ConversationVisionClient(cfg)
    actual_request=client._request
    corrupted=False
    def request(messages, **kwargs):
        nonlocal corrupted
        result,text=actual_request(messages,**kwargs)
        if not corrupted:
            corrupted=True
            result['materials'][0]['group_key']=None
            text=json.dumps(result,ensure_ascii=False)
        return result,text
    client._request=request
    start=time.monotonic()
    try:
        response=client.analyze_batch([record('test-payment',OUT/'inputs/synthetic-payment.png')], final=True)
        return save('recovery',{'mode':'recovery','real_external_api':True,'success':True,'seconds':round(time.monotonic()-start,2),
                               'injected_fault':'null group_key after first genuine response',
                               'stats':client.request_stats,'result':response['result'],'stored_turns':response['session']['revision']},cfg)
    except Exception as error:
        return save('recovery',{'mode':'recovery','real_external_api':True,'success':False,'error':str(error),'stats':client.request_stats},cfg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=('probe', 'classify', 'plans', 'flow', 'borders', 'recovery', 'prepare'))
    args = ap.parse_args()
    if args.mode == 'prepare':
        rows = prepare(); print(json.dumps({'materials': len(rows), 'inputs': str(OUT / 'inputs')}, ensure_ascii=False)); return
    cfg = settings()
    try:
        globals()[args.mode](cfg)
    finally:
        cfg.ai_api_key = ''


if __name__ == '__main__':
    main()
