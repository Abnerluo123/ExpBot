"""Diff SESSION_PROMPT and related prompts across releases."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
paths = {
    "src": root / "app/core/ai_session.py",
    "v222": root / "dist/ExpBot-legacy-v2.2.2-Windows/app/core/ai_session.py",
    "v221": root / "dist/ExpBot-legacy-v2.2.1-Windows/app/core/ai_session.py",
    "v22": root / "dist/简报-采购报销助手-v2.2-Windows/app/core/ai_session.py",
    "v21": root / "dist/简报-采购报销助手-v2.1-Windows/app/core/ai_session.py",
}


def session_prompt(text: str) -> str | None:
    m = re.search(r'SESSION_PROMPT\s*=\s*"""(.*?)"""', text, re.S)
    return m.group(1) if m else None


def instruction(text: str) -> str | None:
    m = re.search(r'"instruction":\s*"([^"]+)"', text)
    return m.group(1) if m else None


refs = {}
for name, path in paths.items():
    if not path.exists():
        print("missing", name)
        continue
    text = path.read_text(encoding="utf-8")
    sp = session_prompt(text) or ""
    inst = instruction(text) or ""
    refs[name] = (sp, inst)
    print(name, "SESSION", hashlib.sha256(sp.encode()).hexdigest()[:12], "len", len(sp))
    print(name, "INSTR", hashlib.sha256(inst.encode()).hexdigest()[:12], "len", len(inst))

# Show full v221 SESSION if different from src
if "v221" in refs and "src" in refs:
    a, b = refs["src"][0], refs["v221"][0]
    if a != b:
        print("\n=== SRC SESSION ===")
        print(a)
        print("\n=== V221 SESSION ===")
        print(b)
        print("\n=== INSTR SRC ===")
        print(refs["src"][1])
        print("\n=== INSTR V221 ===")
        print(refs["v221"][1])
    else:
        print("SESSION identical src/v221")
