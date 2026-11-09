"""Verify structural repair against saved DeepSeek output; no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from app.core.ai_batch import _model_json_object  # noqa: E402

path = next((root / "dist" / "ExpBot-legacy-v2.2.2-Windows" / "data" / "diagnostics").glob("model-json-*.json"))
record = json.loads(path.read_text(encoding="utf-8"))
text = record["response_text"]
data = _model_json_object(text)
print("OK materials", len(data["materials"]), "groups", len(data["groups"]))
print("evidence_ok", all(isinstance(m.get("evidence"), list) for m in data["materials"]))
print("ids", [m["material_id"] for m in data["materials"]])
print("top", list(data.keys()))
