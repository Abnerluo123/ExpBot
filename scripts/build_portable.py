"""Build an offline x64 Windows portable app from an existing CPython runtime."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '1.0.0'
APP_NAME = 'ExpBot'
DEFAULT_FOLDER = f'{APP_NAME}-v1.0-Windows'


def copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runtime', type=Path, required=True)
    ap.add_argument('--name', default=DEFAULT_FOLDER)
    args = ap.parse_args()
    src = args.runtime.resolve()
    required = [
        src / 'python.exe', src / 'pythonw.exe', src / 'python312.dll', src / 'python3.dll',
        src / 'vcruntime140.dll', src / 'vcruntime140_1.dll',
        src / 'DLLs/_ctypes.pyd', src / 'DLLs/_ssl.pyd', src / 'DLLs/_hashlib.pyd',
        src / 'DLLs/libcrypto-3-x64.dll', src / 'DLLs/libssl-3-x64.dll',
        next((src / 'Lib/site-packages/PIL').glob('_imaging*.pyd')),
    ]
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        raise SystemExit('Incomplete CPython/Pillow runtime: ' + ', '.join(missing))

    out = ROOT / 'dist' / args.name
    if out.exists():
        shutil.rmtree(out)
    runtime = out / 'runtime'
    site = runtime / 'Lib' / 'site-packages'
    site.mkdir(parents=True, exist_ok=True)

    # App sources (no caches / UI scratch).
    for p in (ROOT / 'app').rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc' and 'ui' not in p.relative_to(ROOT).parts:
            copy(p, out / p.relative_to(ROOT))

    for f in (ROOT / 'desktop').glob('*'):
        if f.is_file() and (
            f.suffix.lower() == '.dll'
            or f.name in {
                'SCNU.Window.exe', 'SCNU.Window.exe.config',
                'WebView2-LICENSE.txt', 'webview2-sdk.json',
            }
        ):
            copy(f, out / 'desktop' / f.name)

    for name in (
        'run.py', 'ExpBot.exe', '启动ExpBot.cmd',
        '使用说明.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md',
    ):
        f = ROOT / name
        if f.is_file():
            copy(f, out / name)

    for name in ('python.exe', 'pythonw.exe', 'python3.dll', 'python312.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'):
        copy(src / name, runtime / name)

    (out / 'licenses').mkdir(exist_ok=True)
    copy(src / 'LICENSE.txt', out / 'licenses' / 'Python-LICENSE.txt')

    skip = ('tk', 'tcl', 'sqlite3', '_sqlite3', '_tkinter', '_test', '_ctypes_test', '_msi', 'winsound')
    for f in (src / 'DLLs').iterdir():
        if f.is_file() and f.suffix.lower() in ('.dll', '.pyd') and not f.name.lower().startswith(skip):
            copy(f, runtime / f.name)

    excluded = {'site-packages', '__pycache__', 'test', 'tests', 'tkinter', 'turtledemo', 'idlelib', 'ensurepip', 'venv', 'distutils'}
    with zipfile.ZipFile(runtime / 'python312.zip', 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in (src / 'Lib').rglob('*.py'):
            rel = f.relative_to(src / 'Lib')
            if any(part in excluded for part in rel.parts):
                continue
            z.write(f, rel.as_posix())

    (runtime / 'python312._pth').write_text(
        'python312.zip\n.\nLib/site-packages\n..\nimport site\n', encoding='utf-8'
    )
    (runtime / 'Lib' / 'site-packages' / 'sitecustomize.py').write_text(
        'from pathlib import Path\n'
        'import os\n'
        '_root = Path(__file__).resolve().parents[2]\n'
        '_cert = _root.parent / "certs" / "cacert.pem"\n'
        'if _cert.is_file():\n'
        '    os.environ.setdefault("SSL_CERT_FILE", str(_cert))\n'
        '    os.environ.setdefault("REQUESTS_CA_BUNDLE", str(_cert))\n',
        encoding='utf-8',
    )

    pil = src / 'Lib' / 'site-packages' / 'PIL'
    for f in pil.rglob('*'):
        if (
            f.is_file()
            and '__pycache__' not in f.parts
            and f.suffix != '.pyc'
            and not f.name.startswith('_avif.')
            and f.name != 'AvifImagePlugin.py'
            and not f.name.startswith('_imagingtk')
        ):
            copy(f, site / 'PIL' / f.relative_to(pil))

    dist = src / 'Lib' / 'site-packages' / 'pillow-12.3.0.dist-info'
    if dist.exists():
        copy(dist / 'METADATA', site / 'pillow-12.3.0.dist-info' / 'METADATA')
        for f in (dist / 'licenses').rglob('*'):
            if f.is_file():
                copy(f, out / 'licenses' / 'Pillow' / f.relative_to(dist / 'licenses'))

    cert = src / 'Lib' / 'site-packages' / 'pip' / '_vendor' / 'certifi' / 'cacert.pem'
    if cert.exists():
        copy(cert, out / 'certs' / 'cacert.pem')
        cert_license = cert.with_name('LICENSE')
        if cert_license.exists():
            copy(cert_license, out / 'licenses' / 'certifi-LICENSE.txt')

    manifest = {
        'application': APP_NAME,
        'version': VERSION,
        'platform': 'Windows 10/11 x64',
        'edition': 'portable',
        'python': sys.version.split()[0],
        'dependencies': ['Python standard library', 'Pillow', 'pypdf 5.9.0 (bundled)'],
        'no_user_data_included': True,
        'no_api_key_included': True,
    }
    (out / 'build-info.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    zip_path = out.parent / (out.name + '.zip')
    if zip_path.exists():
        zip_path.unlink()

    def deliverable(f: Path) -> bool:
        rel = f.relative_to(out)
        return (
            f.is_file()
            and not any(p.startswith('.') or p in {'data', 'output', '__pycache__'} for p in rel.parts)
            and f.name != 'startup-error.txt'
        )

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=7) as z:
        for f in out.rglob('*'):
            if deliverable(f):
                z.write(f, Path(out.name) / f.relative_to(out))

    size = sum(f.stat().st_size for f in out.rglob('*') if deliverable(f))
    print(json.dumps({
        'folder': str(out),
        'zip': str(zip_path),
        'unpacked_mb': round(size / 2 ** 20, 2),
        'download_mb': round(zip_path.stat().st_size / 2 ** 20, 2),
        'version': VERSION,
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
