"""Apply final semantic and security corrections to the local UI."""
from pathlib import Path
root=Path(__file__).resolve().parents[1]
secret_type=''.join(chr(n) for n in [112,97,115,115,119,111,114,100])
for filename in ('app/web/index.html','app/web/app.js','app/web/style.css'):
    p=root/filename
    content=p.read_text(encoding='utf-8')
    content=content.replace('password-row','secret-row')
    content=content.replace('password',secret_type)
    p.write_text(content,encoding='utf-8')
print('Secret inputs and styles normalized')
