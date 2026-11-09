"""Local preferences. API keys are encrypted with Windows DPAPI at rest."""
from __future__ import annotations

import base64
import ctypes
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = APP_DIR / "data"
CONFIG_PATH = DATA_DIR / "settings.json"
OUTPUT_DIR = APP_DIR / "output"


@dataclass
class Settings:
    name: str = ""
    advisor: str = ""
    project_name: str = ""
    phone: str = ""
    declare_date: str = ""
    order_index: int = 1
    # Organization invoice / shipping profile (user-filled; not shipped with secrets).
    buyer_name: str = ""
    taxpayer_id: str = ""
    org_address: str = ""
    org_phone: str = ""
    bank_account: str = ""
    bank_name: str = ""
    required_address: str = ""
    address_keywords: str = ""  # optional, comma/space separated soft-match tokens
    ai_api_base: str = "https://api.deepseek.com"
    ai_api_key: str = ""
    ai_model: str = "deepseek-flash"
    ai_vision_model: str = "deepseek-flash"
    ai_enabled: bool = True
    output_dir: str = str(OUTPUT_DIR)
    left_margin_ratio: float = 0.26
    jpeg_quality: int = 95
    pdf_dpi: int = 300
    theme_mode: str = "light"
    window_width: int = 1440
    window_height: int = 960
    extra: dict[str, Any] = field(default_factory=dict)

    def org_invoice_info(self) -> dict[str, str]:
        return {
            "taxpayer_name": str(self.buyer_name or "").strip(),
            "taxpayer_id": str(self.taxpayer_id or "").strip(),
            "address": str(self.org_address or "").strip(),
            "phone": str(self.org_phone or "").strip(),
            "bank_account": str(self.bank_account or "").strip(),
            "bank_name": str(self.bank_name or "").strip(),
        }

    def org_address_keywords(self) -> list[str]:
        raw = str(self.address_keywords or "").replace("，", ",")
        parts = []
        for chunk in raw.replace(";", ",").split(","):
            token = chunk.strip()
            if token and token not in parts:
                parts.append(token)
        if parts:
            return parts
        # Fall back to the full required address as a single token when set.
        ref = str(self.required_address or "").strip()
        return [ref] if ref else []

    def display_prefix(self) -> tuple[str, str]:
        from ..core.naming import build_output_names
        n = build_output_names(self)
        return n.file_prefix, n.folder_name

    def is_profile_complete(self) -> bool:
        return all(str(x).strip() for x in (self.name, self.advisor, self.project_name, self.declare_date))

    def is_ai_ready(self) -> bool:
        return bool(self.ai_api_key and self.ai_api_base and (self.ai_vision_model or self.ai_model))


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise RuntimeError("此系统不支持 Windows 密钥加密；密钥仅保留在本次会话。")
    source = ctypes.create_string_buffer(data)
    src = _Blob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    dst = _Blob()
    # CRYPTPROTECT_UI_FORBIDDEN; encrypted for this Windows account only.
    if decrypt:
        ok = ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst))
    else:
        ok = ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), "Reimburse API key", None, None, None, 1, ctypes.byref(dst))
    if not ok:
        raise OSError("无法使用 Windows 账户加密 API Key")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_settings(config_path: Path | None = None) -> Settings:
    _ensure_dirs()
    config_path = Path(config_path or CONFIG_PATH)
    default = Settings(declare_date=datetime.now().strftime("%Y%m%d"))
    if not config_path.exists():
        return default
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        fields = Settings.__dataclass_fields__
        payload = {k: v for k, v in raw.items() if k in fields and k != "ai_api_key"}
        if raw.get("api_key_dpapi"):
            try:
                payload["ai_api_key"] = _crypt(base64.b64decode(raw["api_key_dpapi"]), decrypt=True).decode("utf-8")
            except Exception:
                payload["ai_api_key"] = ""
        elif raw.get("ai_api_key"):
            # Read legacy key once. The next save migrates it to DPAPI.
            payload["ai_api_key"] = raw["ai_api_key"]
        result = Settings(**payload)
        if not result.declare_date:
            result.declare_date = default.declare_date
        apply_org_profile(result)
        return result
    except (ValueError, TypeError, OSError):
        apply_org_profile(default)
        return default


def save_settings(settings: Settings, config_path: Path | None = None) -> None:
    _ensure_dirs()
    config_path = Path(config_path or CONFIG_PATH)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(settings)
    key = payload.pop("ai_api_key", "")
    if key:
        payload["api_key_dpapi"] = base64.b64encode(_crypt(key.encode("utf-8"))).decode("ascii")
    temporary = config_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(config_path)
    apply_org_profile(settings)


def apply_org_profile(settings: Settings | None = None) -> None:
    """Push user org profile into runtime defaults used by validators / AI profile."""
    from . import defaults
    s = settings or get_settings()
    info = s.org_invoice_info()
    defaults.SCHOOL_INVOICE_INFO.clear()
    defaults.SCHOOL_INVOICE_INFO.update(info)
    defaults.REQUIRED_ADDRESS = str(s.required_address or "").strip()
    defaults.ADDRESS_KEYWORDS.clear()
    defaults.ADDRESS_KEYWORDS.extend(s.org_address_keywords())
