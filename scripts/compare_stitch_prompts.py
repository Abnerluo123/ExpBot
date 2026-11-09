"""Compare stitch/layout prompts across release trees; no network."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
paths = [
    root / "app/core/ai_batch.py",
    root / "dist/ExpBot-legacy-v2.2.2-Windows/app/core/ai_batch.py",
    root / "dist/ExpBot-legacy-v2.2.1-Windows/app/core/ai_batch.py",
    root / "dist/简报-采购报销助手-v2.2-Windows/app/core/ai_batch.py",
    root / "dist/简报-采购报销助手-v2.1-Windows/app/core/ai_batch.py",
]


def extract(text: str, name: str) -> str | None:
    match = re.search(rf"{name}\s*=\s*_BASE_PROMPT\s*\+\s*\"\"\"(.*?)\"\"\"", text, re.S)
    return match.group(1).strip() if match else None


def sha(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:16]


for path in paths:
    if not path.exists():
        print("MISSING", path)
        continue
    text = path.read_text(encoding="utf-8")
    stitch = extract(text, "_STITCH_PROMPT")
    layout = extract(text, "_LAYOUT_PROMPT")
    order = extract(text, "_ORDER_PROMPT")
    print("=" * 72)
    print(path.relative_to(root))
    print("STITCH", sha(stitch), "len", len(stitch or 0))
    print("LAYOUT", sha(layout), "len", len(layout or 0))
    print("ORDER ", sha(order), "len", len(order or 0))
    if stitch:
        print("--- FULL STITCH ---")
        print(stitch)
        print("--- END ---")
