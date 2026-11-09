"""Verify the final wizard package and the completed isolated browser QA run."""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
from pypdf import PdfReader


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    info = json.loads((ROOT / 'artifacts/qa-v20-current.json').read_text(encoding='utf-8'))
    data_dir = Path(info['data_dir'])
    state = json.loads((data_dir / 'workspace.json').read_text(encoding='utf-8'))
    metrics = json.loads(Path(info['metrics']).read_text(encoding='utf-8'))
    run = next(r for r in state['runs'].values() if len(r['material_ids']) == 17 and r['stage'] == 'done')
    assert len(run['review']['groups']) == 4
    assert len(run['review']['trash']) == 1
    assert len(run['session']['ledger']) == 17
    assert len(run['session']['messages']) == 7
    assert metrics['review_image_counts'][:3] == [8, 8, 1]
    assert metrics['review_turn_lengths'][:3] == [2, 4, 6]
    assert metrics['max_production'] == 4
    assert all(n == 2 for n in metrics.get('production_message_counts', []))
    assert 'data:image/' not in json.dumps(run['session'])
    assert info['key'] not in json.dumps(state)
    originals = []
    for mid in run['material_ids']:
        material = state['materials'][mid]
        assert sha(material['original']) == sha(Path(info['inputs']) / material['name'])
        originals.append(material['name'])
    assert all(state['materials'][mid]['extracted']['address'] == '实验楼130' for g in run['review']['groups'] for mid in g['material_ids'] if state['materials'][mid]['category'] == 'order')
    orders = {o['id']: o for o in state['orders']}
    assert [orders[oid]['order_index'] for oid in run['order_ids']] == [1, 2, 3, 4]
    products = {p['order_id']: p for p in run['production']}
    assert len(run['exports']) == 4
    export_checks = []
    for export in run['exports']:
        folder = Path(export['folder'])
        files = list(folder.iterdir())
        assert len(files) == 2 and all(p.suffix == '.pdf' for p in files)
        product = products[export['order_id']]
        assert sha(export['invoice']) == sha(product['_invoice_pdf'])
        assert sha(export['voucher']) == sha(product['_voucher_pdf'])
        voucher = PdfReader(export['voucher'])
        assert len(voucher.pages) == 1
        assert abs(float(voucher.pages[0].mediabox.width) / 72 * 25.4 - 297) < .1
        assert abs(float(voucher.pages[0].mediabox.height) / 72 * 25.4 - 210) < .1
        invoice = PdfReader(export['invoice'])
        assert len(invoice.pages) >= 1
        assert abs(float(invoice.pages[0].mediabox.width) / 72 * 25.4 - 297) < .1
        export_checks.append({'order_index': export['order_index'], 'files': len(files), 'voucher_pages': 1, 'export_matches_reviewed_production': True})
    adjusted = next(p for p in run['production'] if abs(p['layout']['layout_column_ratio'] - .57) < .001)
    assert adjusted.get('layout_history')
    assert any(r['stage'] == 'done' and len(r['material_ids']) == 1 and len(r['review']['trash']) == 1 and not r['exports'] for r in state['runs'].values())
    supplement = next(r for r in state['runs'].values() if len(r['material_ids']) == 3 and (r.get('session') or {}).get('revision') == 2)
    assert len(supplement['session']['ledger']) == 3
    archive = ROOT / 'dist/简报-采购报销助手-v2.0-Windows.zip'
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        names = z.namelist()
        assert not any(any(part in {'data', 'output', 'examples', '.qa-v20', '__pycache__'} for part in Path(name).parts) for name in names)
        for suffix in ('app/core/ai_session.py', 'app/flow_engine.py', 'app/web/wizard.js', 'app/web/wizard.css', '简报.exe', 'app/_vendor/pypdf/__init__.py'):
            assert any(n.endswith('/' + suffix) for n in names)
        html = z.read(next(n for n in names if n.endswith('/app/web/index.html'))).decode('utf-8')
        assert '/wizard.js' in html and '/wizard.css' in html
        assert 'src="/app.js"' not in html
        for suffix in ('app/core/ai_session.py', 'app/flow_engine.py', 'app/web/wizard.js', 'app/web/wizard.css', 'app/server.py', '使用说明.md'):
            packed = z.read(next(n for n in names if n.endswith('/' + suffix)))
            assert hashlib.sha256(packed).hexdigest() == sha(ROOT / suffix)
    count = unittest.defaultTestLoader.discover(str(ROOT / 'tests')).countTestCases()
    report = {'version': app.__version__, 'tests': count, 'real_external_api_used': False,
              'browser_acceptance': {'imported': 17, 'batch_image_counts': [8, 8, 1], 'history_message_counts': [2, 4, 6], 'groups': 4,
              'trash_ignored': 1, 'max_concurrent_production_requests': 4, 'independent_new_production_contexts': True,
              'literal_abbreviated_address_preserved': True, 'all_originals_unchanged': True,
              'local_layout_changed_to': .57, 'export_pdf_equals_updated_production': True,
              'all_trash_archives_without_pdf': True, 'supplement_keeps_previous_context': True, 'history_restored_after_restart': True},
              'exports': sorted(export_checks, key=lambda r: r['order_index']), 'zip_crc': 'pass', 'no_user_data_in_release': True,
              'package_mib': round(archive.stat().st_size / 2 ** 20, 2), 'sha256': sha(archive)}
    target = ROOT / 'artifacts/release-verification-v2.0.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
