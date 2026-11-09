"""Windows' built-in PDF renderer. No OCR or third-party PDF runtime."""
from __future__ import annotations

import hashlib
import math
import os
import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).with_name("pdf_render.ps1")
PDF_PREVIEW_SCALE = 4.0  # WinRT uses 96 px/in: ~384 DPI for inspection and vision.
PDF_VIEW_VERSION = 2


def render_pdf_page(pdf_path: str | Path, out_png: str | Path, scale: float = 2.0, page_index: int = 0) -> tuple[Path, int]:
    pdf_path = Path(pdf_path).resolve()
    out_png = Path(out_png).resolve()
    if not 0.5 <= float(scale) <= 4.0 or not 0 <= int(page_index) < 500:
        raise ValueError("PDF 页面或缩放超出范围")
    if os.name != "nt":
        raise RuntimeError("内置 PDF 转图片需要 Windows 10/11；原始 PDF 仍可保留并导出。")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    status = Path(str(out_png) + ".status.txt")
    if status.exists():
        status.unlink()
    ps = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    cmd = [str(ps), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
           "-InPath", str(pdf_path), "-OutPath", str(out_png), "-Scale", str(scale), "-PageIndex", str(page_index)]
    # Inherit no captured pipes: compatible with the confined Windows runtime too.
    with open(os.devnull, "w") as quiet:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=quiet, stderr=quiet, timeout=75,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    msg = status.read_text(encoding="utf-8-sig", errors="replace") if status.exists() else ""
    if proc.returncode or not msg.startswith("OK") or not out_png.exists():
        raise RuntimeError("PDF 转图片失败，文件可能加密、损坏或 Windows PDF 组件不可用。原始文件未被修改。")
    dimensions = re.search(r"width=([0-9.]+) height=([0-9.]+)", msg)
    if dimensions:
        from PIL import Image
        expected = tuple(math.ceil(float(value) * float(scale)) for value in dimensions.groups())
        if max(expected) > 9000 or expected[0] * expected[1] > 45_000_000:
            raise RuntimeError("PDF高清预览超过安全像素范围")
        # WinRT applies the desktop's display scaling on some Windows systems.
        # Normalize that extra scale so a 200% monitor does not quadruple RAM.
        with Image.open(out_png) as rendered:
            if rendered.size != expected and min(rendered.width / expected[0], rendered.height / expected[1]) > 1.01:
                image = rendered.convert("RGB").resize(expected, Image.Resampling.LANCZOS)
            else:
                image = None
        if image is not None:
            try:
                image.save(out_png, "PNG")
            finally:
                image.close()
    match = re.search(r"pages=(\d+)", msg)
    return out_png, int(match.group(1)) if match else 1


def render_pdf_first_page(pdf_path: str | Path, out_png: str | Path, scale: float = 2.0) -> Path:
    return render_pdf_page(pdf_path, out_png, scale=scale)[0]


def is_pdf(path: str | Path) -> bool:
    return Path(path).suffix.lower() == ".pdf"


def ensure_image_for_ai(path: str | Path, cache_dir: str | Path) -> Path:
    path = Path(path)
    if not is_pdf(path):
        return path
    stat = path.stat()
    key = hashlib.sha256((str(path.resolve()) + str(stat.st_mtime_ns) + str(stat.st_size)).encode()).hexdigest()[:20]
    cache_dir = Path(cache_dir)
    out = cache_dir / f"pdf-{key}-p1-hq{PDF_VIEW_VERSION}.png"
    if out.exists():
        return out
    return render_pdf_first_page(path, out, PDF_PREVIEW_SCALE)
