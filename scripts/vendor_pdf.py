"""Fetch a pinned pure-Python dependency into the project without pip temporary files."""
from pathlib import Path
import hashlib
import io
import json
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
version='5.9.0'
with urllib.request.urlopen('https://pypi.org/pypi/pypdf/'+version+'/json',timeout=15) as r:
    metadata=json.load(r)
wheel=next(x for x in metadata['urls'] if x['filename'].endswith('py3-none-any.whl'))
with urllib.request.urlopen(wheel['url'],timeout=20) as r:
    content=r.read(10*1024*1024)
assert hashlib.sha256(content).hexdigest()==wheel['digests']['sha256']
base=ROOT/'app/_vendor'
base.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(io.BytesIO(content)) as z:
    for entry in z.infolist():
        p=Path(entry.filename)
        if entry.is_dir() or p.is_absolute() or '..' in p.parts:continue
        if not (p.parts[0]=='pypdf' or p.parts[0].startswith('pypdf-')):continue
        target=base/p
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(z.read(entry))
(base/'pypdf-source.json').write_text(json.dumps({'package':'pypdf','version':version,'wheel':wheel['filename'],'sha256':wheel['digests']['sha256'],'source':wheel['url']},indent=2),encoding='utf-8')
print('Vendored pypdf '+version+'; verified wheel SHA256; '+str(len(content))+' download bytes')
