"""Read-only inspection; no API calls or workflow execution."""
import difflib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
release = root / 'dist' / 'ExpBot-legacy-v2.2.2-Windows'
settings = json.loads((release / 'data' / 'settings.json').read_text(encoding='utf-8'))
print(json.dumps({k: settings.get(k) for k in ('ai_api_base', 'ai_model', 'ai_vision_model')}, ensure_ascii=True))
state = json.loads((release / 'data' / 'workspace.json').read_text(encoding='utf-8'))
active_id = state.get('active_run_id') or state.get('active_run')
print('active_id:', active_id)
runs = state.get('runs', {})
for run in (runs.values() if isinstance(runs, dict) else runs):
    if run.get('error'):
        session = run.get('session') or {}
        print(json.dumps({'id': run.get('id'), 'stage': run.get('stage'), 'error': run.get('error'), 'review_keys': list((run.get('review') or {}).keys()), 'session_revision': session.get('revision'), 'session_keys': list(session.keys())}, ensure_ascii=True))
for filename in ('ai_session.py', 'ai_vision.py'):
    before = root / 'dist' / '简报-采购报销助手-v2.1-Windows' / 'app' / 'core' / filename
    after = root / 'app' / 'core' / filename
    print('DIFF:', filename)
    print(''.join(difflib.unified_diff(before.read_text(encoding='utf-8').splitlines(True), after.read_text(encoding='utf-8').splitlines(True), n=1)))
