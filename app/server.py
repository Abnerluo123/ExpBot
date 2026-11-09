"""Small loopback-only application server (stdlib + Pillow).

Original documents are immutable; all crops and joins produce new local files.
Only an explicit 'AI identify' action sends selected materials to the configured API.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import mimetypes
import os
import re
import secrets
import threading
import time
import traceback
import urllib.parse
import uuid
from dataclasses import asdict
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from .config import Settings, get_settings, save_settings
from .config import defaults
from .config.defaults import RULES_TEXT
from .config.settings import APP_DIR, DATA_DIR
from .core.ai_vision import AIVisionClient
from .core.classifier import load_image_item
from .core.image_processor import open_rgb, process_voucher_layout, smart_crop_whitespace, stitch_horizontal, stitch_vertical
from .core.models import ExtractedInfo, ImageCategory, ImageItem
from .core.naming import build_output_names
from .core.pdf_generator import build_output_package
from .core.pdf_utils import PDF_PREVIEW_SCALE, PDF_VIEW_VERSION, render_pdf_page
from .core.validator import validate_batch

WEB_DIR = APP_DIR / "app/web"
STATE_FILE = DATA_DIR / "workspace.json"
MAX_UPLOAD = 40 * 1024 * 1024


def _windows_app_dark() -> bool:
    """True when Windows AppsUseLightTheme is off. Used to stamp dark UI into HTML."""
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value) == 0
    except OSError:
        return False
MAX_PIXELS = 45_000_000
CATEGORIES = {x.value for x in ImageCategory}
EXTRACT_FIELDS = set(ExtractedInfo.__dataclass_fields__)


def uid() -> str:
    return uuid.uuid4().hex[:20]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def public_error(exc: Exception) -> str:
    if isinstance(exc, (ValueError, RuntimeError, FileExistsError)):
        return str(exc)[:700]
    if isinstance(exc, PermissionError):
        return "无权写入所选位置，请选择有写入权限的普通文件夹。"
    if isinstance(exc, FileNotFoundError):
        return "源文件不存在，请重新导入材料。"
    if isinstance(exc, UnidentifiedImageError):
        return "无法读取图片，请使用 PNG、JPEG、WebP、BMP 或 PDF。"
    return "操作未完成，原始文件未改变。请检查文件及磁盘空间后重试。"


class Workspace:
    def __init__(self, data_dir: Path | None = None):
        self.root = Path(data_dir or DATA_DIR)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_file = self.root / "workspace.json"
        self.lock = threading.RLock()
        self.settings = get_settings(self.root / "settings.json")
        self.jobs: dict[str, dict] = {}
        self._pdf_page_locks: dict[tuple[str, int], threading.Lock] = {}
        self._pdf_render_slots = threading.BoundedSemaphore(2)
        self.state: dict[str, Any] = {"version": 3, "orders": [], "materials": {}, "exports": [], "tools": {}, "plans": {}, "runs": {}, "active_run_id": None}
        if self.state_file.exists():
            try:
                saved = json.loads(self.state_file.read_text(encoding="utf-8"))
                for k in self.state:
                    if k in saved:
                        self.state[k] = saved[k]
            except (ValueError, OSError):
                # Keep the corrupt original available for recovery rather than delete it.
                backup = self.state_file.with_name(f"workspace-unreadable-{int(time.time())}.json")
                self.state_file.rename(backup)
        if not self.state["orders"]:
            self.add_order({})
        from .flow_engine import ensure_flow_state
        ensure_flow_state(self)

    def save(self):
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        tmp.replace(self.state_file)

    def order(self, order_id: str) -> dict:
        for row in self.state["orders"]:
            if row["id"] == order_id:
                return row
        raise ValueError("未找到这笔采购")

    def material(self, material_id: str) -> dict:
        try:
            return self.state["materials"][material_id]
        except KeyError:
            raise ValueError("未找到这份材料") from None

    def guard_order(self, order_id: str):
        self.order(order_id)
        active = [j for j in self.jobs.values() if j["status"] in ("pending", "running")]
        if any(j.get("order_id") == order_id or j.get("kind") == "smart-orders" for j in active):
            raise ValueError("这笔采购正在处理中，请等待本次操作完成。")
        for run in self.state.get("runs", {}).values():
            owned_orders = [run.get("inbox_order_id"), *run.get("order_ids", [])]
            if order_id in owned_orders and (run.get("status") == "running" or any(j.get("run_id") == run.get("id") for j in active)):
                raise ValueError("这批材料正在处理中，请等待完成后再调整。")

    def add_order(self, data: dict) -> dict:
        with self.lock:
            import re
            date = str(data.get("declare_date") or self.settings.declare_date or datetime.now().strftime("%Y%m%d"))
            if not re.fullmatch(r"\d{8}", date):
                raise ValueError("采购申报日期须为 YYYYMMDD")
            try:
                datetime.strptime(date, "%Y%m%d")
            except ValueError:
                raise ValueError("采购申报日期不是有效日历日") from None
            from .core.naming import validate_declare_date
            date = validate_declare_date(date)
            same = [o.get("order_index", 1) for o in self.state["orders"] if o.get("declare_date") == date]
            index = int(data.get("order_index") or (max(same, default=0) + 1))
            if not 1 <= index <= 999:
                raise ValueError("当日订单序号应为1–999")
            if any(o.get("declare_date") == date and int(o.get("order_index", 1)) == index for o in self.state["orders"]):
                raise ValueError("同一天的笔序已被另一笔采购使用；请选择不同序号。")
            row = {"id": uid(), "title": str(data.get("title") or "未命名采购")[:80],
                   "declare_date": date, "order_index": index,
                   "project_name": str(data.get("project_name") or self.settings.project_name),
                   "created": now(), "manual_confirmed": False}
            self.state["orders"].append(row)
            self.save()
            return row

    def order_settings(self, order_id: str, overrides: dict | None = None) -> Settings:
        s = copy.deepcopy(self.settings)
        row = self.order(order_id)
        for key in ("name", "advisor", "project_name", "declare_date", "order_index"):
            if key in row and row[key] not in (None, ""):
                setattr(s, key, row[key])
        s.extra.update(copy.deepcopy(row.get("layout", {})))
        if "dpi" in s.extra:
            s.pdf_dpi = int(s.extra["dpi"])
        if overrides:
            s.extra.update(overrides)
        return s

    def records(self, order_id: str) -> list[dict]:
        return [m for m in self.state["materials"].values() if m["order_id"] == order_id]

    def to_item(self, m: dict, for_export: bool = True) -> ImageItem:
        original = Path(m["original"])
        if for_export and m["category"] == "invoice" and original.suffix.lower() == ".pdf":
            path = original
        else:
            path = Path(m["working"])
        fields = {k: v for k, v in m.get("extracted", {}).items() if k in EXTRACT_FIELDS}
        return ImageItem(path=path, category=ImageCategory(m["category"]), extracted=ExtractedInfo(**fields),
                         notes=m.get("notes", []), selected=m.get("selected", True),
                         width=m.get("width", 0), height=m.get("height", 0), file_size=m.get("file_size", 0))

    def items(self, order_id: str) -> list[ImageItem]:
        return [self.to_item(m) for m in self.records(order_id)]

    def invalidate(self, order_id: str):
        self.order(order_id)["manual_confirmed"] = False
        self.order(order_id).pop("last_export", None)

    def public_material(self, m: dict) -> dict:
        return {k: copy.deepcopy(v) for k, v in m.items() if k not in ("original", "working", "history") } | {
            "url": f"/media/{m['id']}/image?v={m.get('revision', 0)}",
            "thumb_url": f"/media/{m['id']}/thumb?v={m.get('revision', 0)}",
            "can_undo": bool(m.get("history")), "is_pdf": Path(m["original"]).suffix.lower() == ".pdf"}

    def public_state(self) -> dict:
        from .flow_engine import public_flow_state
        with self.lock:
            s = asdict(self.settings)
            s["ai_api_key"] = ""
            s["has_api_key"] = bool(self.settings.ai_api_key)
            orders = []
            for raw in self.state["orders"]:
                row = copy.deepcopy(raw)
                rows = self.records(row["id"])
                row["material_ids"] = [m["id"] for m in rows]
                row["issues"] = [asdict(i) for i in validate_batch(self.items(row["id"]))] if rows else []
                row["counts"] = {k: sum(1 for m in rows if m["category"] == k and m.get("selected", True)) for k in ("invoice", "payment", "order", "unknown")}
                try:
                    row["output_names"] = asdict(build_output_names(self.order_settings(row["id"])))
                except (ValueError, TypeError):
                    row["output_names"] = None
                orders.append(row)
            return {"settings": s, "orders": orders,
                    "materials": {k: self.public_material(v) for k, v in self.state["materials"].items()},
                    "jobs": list(self.jobs.values())[-20:], "exports": self.state["exports"][-30:],
                    "invoice_info": self.settings.org_invoice_info(),
                    "required_address": str(self.settings.required_address or "").strip(),
                    "rules": RULES_TEXT,
                    "app_version": "1.0.0", "data_dir": str(self.root), "tools": list(self.state["tools"].values())[-10:],
                    "flow": public_flow_state(self),
                    "plans": [{"id":p["id"],"kind":p["kind"],"order_id":p["order_id"],"applied":p.get("applied",False),"created":p["created"]} for p in list(self.state.get("plans",{}).values())[-20:]]}

    def _inspect_image(self, path: Path) -> tuple[int, int]:
        with Image.open(path) as im:
            if im.width * im.height > MAX_PIXELS:
                raise ValueError("图片超过4500万像素，请拆分长图后导入，避免占用过多内存。")
            im.verify()
        with Image.open(path) as im:
            return ImageOps.exif_transpose(im).size

    def import_bytes(self, content: bytes, name: str, order_id: str) -> dict:
        with self.lock:
            self.guard_order(order_id)
            flow_inbox = any(r.get("inbox_order_id") == order_id for r in self.state.get("runs", {}).values())
            limit = 240 if flow_inbox else 100
            if len(self.records(order_id)) >= limit:
                raise ValueError(f"当前材料库最多保留{limit}份材料，请新开一批继续。")
            if not content or len(content) > MAX_UPLOAD:
                raise ValueError("单个文件需介于1字节和40MB之间")
            name = Path(name.replace("\\", "/")).name[:150]
            suffix = Path(name).suffix.lower()
            if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".pdf"):
                raise ValueError("支持 PNG、JPG、WebP、BMP、TIFF 和 PDF")
            material_id = uid()
            folder = self.root / "materials" / material_id
            folder.mkdir(parents=True)
            original = folder / f"original{suffix}"
            original.write_bytes(content)
            pages = 1
            notes = []
            if suffix == ".pdf":
                if not content.startswith(b"%PDF-"):
                    raise ValueError("这不是有效的 PDF 文件")
                try:
                    working, pages = render_pdf_page(original, folder / f"source-page-1-hq{PDF_VIEW_VERSION}.png", scale=PDF_PREVIEW_SCALE)
                    w, h = self._inspect_image(working)
                except RuntimeError as exc:
                    # Preserve the official original even when the OS cannot render it.
                    working, w, h = original, 0, 0
                    notes.append(str(exc))
                category = "invoice" if "发票" in name or "invoice" in name.lower() else "unknown"
            else:
                w, h = self._inspect_image(original)
                working = original
                category = "unknown"
                if not flow_inbox:
                    classified = load_image_item(original)
                    from .core.classifier import guess_from_filename
                    category = guess_from_filename(name).value
                    if category == "unknown":
                        category = classified.category.value
                    notes.append("本地分类仅供参考；请用视觉模型识别或手动确认。")
            if flow_inbox:
                category = "unknown"
                notes = [n for n in notes if "分类" not in n]
            # Keep the basename supplied by the user; do not expose internal original.png in validation errors.
            record = {"id": material_id, "name": name, "order_id": order_id, "original": str(original),
                      "working": str(working), "category": category, "selected": True,
                      "width": w, "height": h, "pages": pages, "file_size": len(content),
                      "extracted": {}, "notes": notes, "created": now(), "revision": 0, "history": []}
            self.state["materials"][material_id] = record
            self.invalidate(order_id)
            self.save()
            return self.public_material(record)

    def _image(self, m: dict) -> Image.Image:
        path = Path(m["working"])
        if path.suffix.lower() == ".pdf":
            path, pages = render_pdf_page(Path(m["original"]), path.parent / f"source-page-1-hq{PDF_VIEW_VERSION}.png", PDF_PREVIEW_SCALE)
            m["working"], m["pages"] = str(path), pages
        return open_rgb(path)

    def _derived(self, m: dict, image: Image.Image, label: str, clear: bool = True):
        if image.width * image.height > MAX_PIXELS:
            raise ValueError("处理结果过大，请减少拼接数量。")
        target = Path(m["original"]).parent / f"edit-{uid()}.png"
        image.save(target, "PNG", optimize=True)
        m.setdefault("history", []).append({"working": m["working"], "extracted": copy.deepcopy(m.get("extracted", {})),
                                             "width": m["width"], "height": m["height"]})
        m["working"], m["width"], m["height"] = str(target), image.width, image.height
        m["revision"] += 1
        if clear:
            m["extracted"] = {}
            m["notes"] = [f"已{label}；原图保留。请重新识别或人工复核。"]
        self.invalidate(m["order_id"])

    def edit_material(self, material_id: str, data: dict) -> dict:
        with self.lock:
            m = self.material(material_id)
            self.guard_order(m["order_id"])
            action = data.get("action")
            if action in ("undo", "reset"):
                if action == "reset":
                    m["working"] = m["original"]
                    if Path(m["original"]).suffix.lower() == ".pdf":
                        m["working"] = str(Path(m["original"]).parent / "page-1.png")
                    m["extracted"] = {}
                    m["history"] = []
                    try:
                        m["width"], m["height"] = self._image(m).size
                    except Exception:
                        pass
                elif m.get("history"):
                    m.update(m["history"].pop())
                else:
                    raise ValueError("没有可撤销的编辑")
                m["revision"] += 1
                self.invalidate(m["order_id"])
            else:
                im = self._image(m)
                if action == "extract_pdf_page":
                    if Path(m["original"]).suffix.lower() != ".pdf":
                        raise ValueError("该材料不是 PDF")
                    page = int(data.get("page", 1))
                    path, pages = render_pdf_page(m["original"], Path(m["original"]).parent / f"extract-{page}.png", 2.5, page - 1)
                    new = self.import_bytes(path.read_bytes(), f"{Path(m['name']).stem}-第{page}页.png", m["order_id"])
                    child = self.material(new["id"])
                    child["category"] = m["category"] if m["category"] in ("order", "payment") else "unknown"
                    child["source_id"] = m["id"]
                    m["selected"] = False
                    self.save()
                    return self.public_material(child)
                if action == "rotate":
                    im = im.rotate(-90, expand=True, fillcolor="white")
                    self._derived(m, im, "旋转", clear=False)
                elif action in ("crop", "split"):
                    box = data.get("box", [])
                    if not isinstance(box, list) or len(box) != 4:
                        raise ValueError("请选择裁切区域")
                    x, y, w, h = map(float, box)
                    if not all(0 <= v <= 1 for v in (x, y, w, h)) or min(w, h) < 0.01 or x + w > 1.002 or y + h > 1.002:
                        raise ValueError("裁切区域超出图片")
                    crop = im.crop((round(x * im.width), round(y * im.height), min(im.width, round((x + w) * im.width)), min(im.height, round((y + h) * im.height))))
                    if action == "split":
                        category = data.get("category", "unknown")
                        if category not in CATEGORIES:
                            raise ValueError("材料类别无效")
                        b = io.BytesIO()
                        crop.save(b, "PNG")
                        new = self.import_bytes(b.getvalue(), f"{Path(m['name']).stem}-拆分-{category}.png", m["order_id"])
                        child = self.material(new["id"])
                        child["category"], child["source_id"] = category, material_id
                        child["notes"] = ["由原图裁切拆分，未改写任何票据文字。"]
                        m["selected"] = False
                        self.save()
                        return self.public_material(child)
                    self._derived(m, crop, "裁切")
                elif action == "trim":
                    self._derived(m, smart_crop_whitespace(im), "裁去纯白外边")
                elif action == "pdf_page":
                    if Path(m["original"]).suffix.lower() != ".pdf":
                        raise ValueError("该材料不是 PDF")
                    page = int(data.get("page", 1))
                    out = Path(m["original"]).parent / f"page-{page}.png"
                    path, pages = render_pdf_page(m["original"], out, 2.5, page - 1)
                    m["working"], m["pages"] = str(path), pages
                    m["width"], m["height"] = self._inspect_image(path)
                    m["revision"] += 1
                else:
                    raise ValueError("未知图片编辑操作")
            self.save()
            return self.public_material(m)

    def update_material(self, material_id: str, data: dict):
        with self.lock:
            m = self.material(material_id)
            self.guard_order(m["order_id"])
            if "order_id" in data:
                self.guard_order(data["order_id"])
                self.invalidate(m["order_id"])
                m["order_id"] = data["order_id"]
            if "category" in data:
                if data["category"] not in CATEGORIES:
                    raise ValueError("材料类别无效")
                m["category"] = data["category"]
            if "selected" in data:
                m["selected"] = bool(data["selected"])
            if "extracted" in data:
                incoming = data["extracted"]
                for k in EXTRACT_FIELDS - {"extra", "confidence"}:
                    if k in incoming:
                        m.setdefault("extracted", {})[k] = str(incoming[k] or "")[:1000] if k != "amount" else (str(incoming[k]).strip() if incoming[k] is not None else None)
            if "position" in data:
                rows = self.records(m["order_id"])
                ids = [r["id"] for r in rows]
                ids.remove(material_id)
                ids.insert(max(0, min(len(ids), int(data["position"]))), material_id)
                positions = {k: i for i, k in enumerate(ids)}
                self.state["materials"] = dict(sorted(self.state["materials"].items(), key=lambda kv: positions.get(kv[0], 10000)))
            m["revision"] += 1
            self.invalidate(m["order_id"])
            self.save()
            return self.public_material(m)

    def stitch(self, data: dict):
        with self.lock:
            ids = data.get("ids", [])
            if not 2 <= len(ids) <= 12 or len(set(ids)) != len(ids):
                raise ValueError("请按顺序选择2–12张图片")
            rows = [self.material(x) for x in ids]
            order_id = rows[0]["order_id"]
            self.guard_order(order_id)
            if any(r["order_id"] != order_id for r in rows):
                raise ValueError("不同采购不得混拼")
            images = [self._image(r) for r in rows]
            overlap = max(0, min(1500, int(data.get("overlap", 0))))
            if overlap:
                images = [images[0]] + [im.crop((0, min(overlap, im.height - 1), im.width, im.height)) for im in images[1:]]
            if sum(im.width * im.height for im in images) > MAX_PIXELS:
                raise ValueError("拼接总像素过大，请分组拼接")
            im = stitch_horizontal(images) if data.get("direction") == "horizontal" else stitch_vertical(images)
            if im.width * im.height > MAX_PIXELS:
                raise ValueError("拼接总像素过大，请分组拼接")
            buff = io.BytesIO()
            im.save(buff, "PNG")
            new = self.import_bytes(buff.getvalue(), f"拼接-{len(rows)}张.png", order_id)
            m = self.material(new["id"])
            m["category"] = data.get("category") if data.get("category") in CATEGORIES else rows[0]["category"]
            m["notes"] = ["按所选顺序拼接；原始材料保留且已取消参与导出。请核对衔接处是否完整。"]
            m["source_ids"] = ids
            for r in rows:
                r["selected"] = False
            self.save()
            return self.public_material(m)

    def start_job(self, kind: str, order_id: str, task):
        with self.lock:
            self.guard_order(order_id)
            job = {"id": uid(), "kind": kind, "order_id": order_id, "status": "pending", "progress": 0,
                   "message": "准备处理", "created": now()}
            self.jobs[job["id"]] = job
        def work():
            try:
                with self.lock:
                    job["status"] = "running"
                result = task(job)
                with self.lock:
                    job.update(status="done", progress=100, result=result, message="处理完成")
                    self.save()
            except Exception as exc:
                with self.lock:
                    job.update(status="error", message=public_error(exc))
        threading.Thread(target=work, name=f"reimburse-{kind}-{job['id']}", daemon=True).start()
        return copy.deepcopy(job)

    def test_ai(self, data: dict):
        """Test an unsaved configuration using only a generated local test image."""
        from .core.ai_session import ConversationVisionClient
        with self.lock:
            if any(j["status"] in ("pending", "running") for j in self.jobs.values()):
                raise ValueError("仍有任务正在进行，请完成后再测试连接。")
            settings = copy.deepcopy(self.settings)
            for key in ("ai_api_base", "ai_model", "ai_vision_model"):
                if key in data:
                    setattr(settings, key, str(data[key]).strip()[:1000])
            if data.get("ai_api_key"):
                settings.ai_api_key = str(data["ai_api_key"]).strip()
            client = ConversationVisionClient(settings)
            client._validate_config()
            order_id = self.state["orders"][0]["id"]
        def work(job):
            job.update(progress=15, message="发送生成的测试图，检查连接与模型看图能力")
            result = client.test_connection()
            with self.lock:
                # Store only the public outcome. Configuration and credentials are
                # kept separate, and a test never sends any reimbursement material.
                self.settings.extra["last_api_test"] = {
                    "connected": bool(result.get("connected")),
                    "vision_supported": bool(result.get("vision_supported")),
                    "model": client.model,
                    "base": client.base,
                    "message": str(result.get("message", ""))[:800],
                    "tested_at": now(),
                }
            return result
        return self.start_job("api-test", order_id, work)

    def analyze(self, data: dict):
        order_id = data["order_id"]
        if not data.get("consent"):
            raise ValueError("请先确认将选中图片发送到你配置的 AI 服务商")
        with self.lock:
            if not self.settings.is_ai_ready():
                raise ValueError("请先配置支持看图的模型、API 地址和 API Key")
            ids = data.get("ids") or [m["id"] for m in self.records(order_id) if m.get("selected", True)]
            rows = [copy.deepcopy(self.material(k)) for k in ids]
            if not rows:
                raise ValueError("请先导入材料")
            if any(m["order_id"] != order_id for m in rows):
                raise ValueError("只能识别当前采购中的材料")
            settings = copy.deepcopy(self.settings)
        def work(job):
            client = AIVisionClient(settings)
            failures, success = [], 0
            for index, row in enumerate(rows):
                with self.lock:
                    job.update(message=f"识别 {index + 1}/{len(rows)} · {row['name']}", progress=round(index / len(rows) * 100))
                try:
                    item = self.to_item(row, for_export=False)
                    client.apply_to_item(item)
                    with self.lock:
                        target = self.material(row["id"])
                        target["category"] = item.category.value
                        target["extracted"] = asdict(item.extracted)
                        target["notes"] = ["视觉模型识别结果，提交前仍需人工核对。"] + item.notes
                        target["revision"] += 1
                        success += 1
                        self.invalidate(order_id)
                        self.save()
                except Exception as exc:
                    message = public_error(exc)
                    failures.append({"name": row["name"], "message": message})
                    with self.lock:
                        self.material(row["id"]).setdefault("notes", []).append(f"AI 未识别：{message}")
            return {"identified": success, "failed": failures}
        return self.start_job("analyze", order_id, work)

    def material_pages(self, material_id: str) -> dict:
        """List every original PDF page; rasterize only the page a viewer requests."""
        with self.lock:
            material = copy.deepcopy(self.material(material_id))
        original = Path(material["original"])
        original_url = f"/media/{material_id}/original"
        if original.suffix.lower() != ".pdf":
            return {"pages": [{"url": f"/media/{material_id}/image", "thumb_url": f"/media/{material_id}/thumb", "page": 1, "total_pages": 1}],
                    "total_pages": 1, "original_url": original_url}
        from pypdf import PdfReader
        try:
            with original.open("rb") as stream:
                count = len(PdfReader(stream).pages)
        except Exception:
            raise ValueError("无法读取 PDF 页数，请下载原件检查文件是否损坏或加密。") from None
        if not 1 <= count <= 500:
            raise ValueError("PDF 页数超出预览范围，请下载完整原件查看。")
        return {"pages": [{"url": f"/media/{material_id}/page/{n}", "thumb_url": f"/media/{material_id}/page-thumb/{n}",
                           "page": n, "total_pages": count, "kind": "original"} for n in range(1, count + 1)],
                "total_pages": count, "original_url": original_url}

    def material_pdf_page(self, material_id: str, page_number: int) -> Path:
        with self.lock:
            material = copy.deepcopy(self.material(material_id))
            if Path(material["original"]).suffix.lower() != ".pdf":
                raise ValueError("当前材料不是 PDF")
            if not 1 <= page_number <= 500:
                raise ValueError("PDF 页码超出范围")
            target = Path(material["original"]).parent / f"source-page-{page_number}-hq{PDF_VIEW_VERSION}.png"
            page_lock = self._pdf_page_locks.setdefault((material_id, page_number), threading.Lock())
        with page_lock:
            if not target.is_file():
                with self._pdf_render_slots:
                    render_pdf_page(material["original"], target, PDF_PREVIEW_SCALE, page_number - 1)
        return target

    def production_pdf_page(self, plan_id: str, kind: str, page_number: int) -> Path:
        """Upgrade a saved legacy preview lazily from its actual PDF bytes."""
        if kind not in {"invoice", "voucher"} or not 1 <= page_number <= 100:
            raise ValueError("成品预览页无效")
        with self.lock:
            target = self.root / "plans" / plan_id / f"{kind}-hq-page-{page_number}.png"
            page_lock = self._pdf_page_locks.setdefault((plan_id, kind, page_number), threading.Lock())
        with page_lock:
            if not target.is_file():
                with self._pdf_render_slots:
                    render_pdf_page(target.parent / f"{kind}.pdf", target, PDF_PREVIEW_SCALE, page_number - 1)
        return target

    def invoice_preview(self, order_id: str) -> dict:
        """Create a local print-layout preview for the selected invoice.

        This is a derived copy only: the source invoice remains untouched and is
        not replaced by the printable PDF.
        """
        from .core.invoice_layout import make_invoice_print_copy
        from .core.pdf_utils import render_pdf_first_page
        with self.lock:
            rows = [r for r in self.records(order_id) if r.get("selected", True) and r.get("category") == "invoice"]
            if not rows:
                raise ValueError("当前采购还没有选中的电子发票")
            source = Path(rows[0]["original"])
            settings = self.order_settings(order_id)
            preview_dir = self.root / "previews"
            preview_dir.mkdir(parents=True, exist_ok=True)
            stem = f"invoice-{order_id}-{uid()}"
            pdf = preview_dir / f"{stem}.pdf"
            png = preview_dir / f"{stem}.png"
            meta = make_invoice_print_copy(source, pdf, settings)
            render_pdf_first_page(pdf, png, PDF_PREVIEW_SCALE)
            self.order(order_id)["invoice_preview_file"] = png.name
            self.save()
            return {"url": f"/invoice-preview/{order_id}/{png.name}?v={time.time_ns()}",
                    "pdf_url": f"/invoice-preview-pdf/{order_id}/{pdf.name}?v={time.time_ns()}",
                    "layout": meta.get("layout", {}), "source_untouched": True,
                    "signature_preserved_in_copy": bool(meta.get("signature_preserved_in_copy", False))}

    def preview(self, order_id: str) -> dict:
        with self.lock:
            rows = [r for r in self.records(order_id) if r.get("selected", True)]
            orders = [r for r in rows if r["category"] == "order"]
            pays = [r for r in rows if r["category"] == "payment"]
            if not orders or not pays:
                raise ValueError("请先指定至少一张订单截图和一张支付截图，才能生成双栏预览。")
            s = self.order_settings(order_id)
            # Convert PDF order/payment materials to their current rendered page.
            for m in orders + pays:
                if Path(m["working"]).suffix.lower() == ".pdf":
                    self._image(m)
            if any(Path(m["original"]).suffix.lower() == ".pdf" and m.get("pages", 1) > 1 for m in orders + pays):
                raise ValueError("所选订单/支付 PDF 含多页，当前预览不足以代表完整材料。请先提取需要的页面并按顺序拼接；发票原始 PDF 不受影响。")
            layout = dict(s.extra)
            layout.setdefault("layout_left_mm", round(s.left_margin_ratio * 297, 2))
            im = process_voucher_layout([r["working"] for r in orders], [r["working"] for r in pays],
                                        dpi=min(180, s.pdf_dpi), layout=layout,
                                        order_on_left=bool(layout.get("layout_order_on_left", True)))
            out = self.root / "previews" / f"{order_id}-{uid()}.jpg"
            out.parent.mkdir(parents=True, exist_ok=True)
            im.save(out, "JPEG", quality=94)
            self.order(order_id)["preview_file"] = out.name
            return {"url": f"/preview/{order_id}/{out.name}?v={time.time_ns()}", "width": im.width, "height": im.height,
                    "page": "A4 横向 · 297 × 210 mm", "left_mm": round(s.left_margin_ratio * 297, 1),
                    "layout_metrics": copy.deepcopy(im.info.get("layout_metrics", {}))}

    @staticmethod
    def layout_kwargs(extra: dict) -> dict:
        # Public image-processor interface uses ratios and gap pixels; new engine also accepts mm options.
        return {"top_margin_ratio": float(extra.get("layout_top_mm", 28)) / 210,
                "bottom_margin_ratio": float(extra.get("layout_bottom_mm", 26)) / 210,
                "right_margin_ratio": float(extra.get("layout_right_mm", 65)) / 297,
                "gap": round(float(extra.get("layout_gap_mm", 8)) / 25.4 * 180)}

    def export(self, data: dict):
        order_id = data["order_id"]
        if not data.get("manual_confirmed"):
            raise ValueError("请先勾选已逐项核对原始材料、金额与收货地址")
        with self.lock:
            if self.order(order_id).get('layout_review',{}).get('legibility')=='poor':
                raise ValueError('AI 评估当前版式可能无法清晰阅读。请调整本笔边距/内容比例或更换清晰材料，保存并复核预览后再导出。')
            s = self.order_settings(order_id)
            source_records = self.records(order_id)
            for m in source_records:
                if m.get("selected", True) and m["category"] in ("order", "payment") and Path(m["original"]).suffix.lower() == ".pdf" and m.get("pages", 1) > 1:
                    raise ValueError("多页订单/支付 PDF 不能只用当前预览页导出。请在图片版 PDF 工具中提取所需页面，确认所有必要信息后按顺序拼接为一份材料。原始电子发票 PDF 会完整保留。")
            rows = self.items(order_id)
        def work(job):
            job.update(progress=20, message="核验原始材料与命名")
            result = build_output_package(rows, s, manual_confirmed=True,
                                          order_on_left=bool(s.extra.get("layout_order_on_left", True)))
            if not result.success:
                detail = "；".join(i.message for i in result.issues if i.level == "error")
                raise ValueError(detail or result.message)
            export_id = uid()
            record = {"id": export_id, "order_id": order_id, "folder": str(result.folder_path), "created": now(),
                      "invoice": str(result.invoice_pdf), "voucher": str(result.voucher_pdf),
                      "invoice_name": Path(result.invoice_pdf).name, "voucher_name": Path(result.voucher_pdf).name,
                      "issues": [asdict(i) for i in result.issues]}
            with self.lock:
                self.state["exports"].append(record)
                self.order(order_id)["last_export"] = export_id
                self.order(order_id)["manual_confirmed"] = True
                reports = self.root / "reports"
                reports.mkdir(exist_ok=True)
                (reports / f"{export_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            return record
        return self.start_job("export", order_id, work)

    def pdf_tool(self, data: dict):
        """Clearly-labelled raster PDF workbench, never touches official originals."""
        ids = data.get("ids", [])
        if not 1 <= len(ids) <= 12:
            raise ValueError("请选择1–12个材料")
        rows = [copy.deepcopy(self.material(x)) for x in ids]
        sequence = data.get("pages", "")
        pages = None
        if sequence:
            try:
                pages = [int(x.strip()) for x in sequence.split(",")]
            except ValueError:
                raise ValueError("页码请写成 1,3,2；留空表示全部页") from None
            if not 1 <= len(pages) <= 30 or any(p < 1 or p > 300 for p in pages):
                raise ValueError("页码超出范围，一次最多处理30页")
        images = []
        folder = self.root / "tools" / uid()
        folder.mkdir(parents=True, exist_ok=True)
        for row in rows:
            if Path(row["original"]).suffix.lower() == ".pdf":
                first, count = render_pdf_page(row["original"], folder / f"{row['id']}-first.png", 2.5)
                selected = pages or list(range(1, count + 1))
                if len(images) + len(selected) > 30:
                    raise ValueError("一次最多处理30页")
                for page in selected:
                    target, _ = render_pdf_page(row["original"], folder / f"{row['id']}-{page}.png", 2.5, page - 1)
                    images.append(open_rgb(target))
            else:
                images.append(self._image(row))
        if not images:
            raise ValueError("没有可用页面")
        if sum(im.width * im.height for im in images) > 120_000_000:
            raise ValueError("页数或像素总量过大，请分批处理")
        tool_id = folder.name
        out = folder / "图片版PDF-不替代原始电子发票.pdf"
        images[0].save(out, "PDF", save_all=True, append_images=images[1:], resolution=180, quality=95)
        record = {"id": tool_id, "name": out.name, "path": str(out), "pages": len(images), "created": now()}
        with self.lock:
            self.state["tools"][tool_id] = record
            self.save()
        return record


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def __init__(self, address, workspace=None):
        self.workspace = workspace or Workspace()
        self.token = secrets.token_urlsafe(32)
        self.started = time.time()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "JianBao/2.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Never log request bodies, endpoint keys, or document contents.
        pass

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass

    @property
    def ws(self):
        return self.server.workspace

    def check_origin(self):
        expected = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.headers.get("Host") not in expected:
            raise ValueError("只允许从本地应用访问")
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + h for h in expected}:
            raise ValueError("拒绝外部网页访问本地材料")
        fetch_site = self.headers.get("Sec-Fetch-Site", "")
        if fetch_site in ("cross-site",):
            raise ValueError("拒绝跨站请求")

    def authorize(self, write=False):
        self.check_origin()
        cookies = SimpleCookie()
        cookies.load(self.headers.get("Cookie", ""))
        cookie = cookies.get("qingbao_session")
        if not cookie or not secrets.compare_digest(cookie.value, self.server.token):
            raise PermissionError("请从应用主窗口打开")
        if write and not secrets.compare_digest(self.headers.get("X-App-Token", ""), self.server.token):
            raise PermissionError("请求未通过本地会话验证")

    def send_bytes(self, payload: bytes, mime: str, status=200, filename=None, set_cookie=False):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        if filename:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(filename))
        if set_cookie:
            self.send_header("Set-Cookie", f"qingbao_session={self.server.token}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(payload)

    def json(self, value, status=200):
        self.send_bytes(json.dumps(value, ensure_ascii=False, default=str, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def file(self, path: Path, download=False):
        if not path.is_file():
            raise FileNotFoundError(path)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_bytes(path.read_bytes(), mime, filename=path.name if download else None)

    def do_GET(self):
        try:
            self.check_origin()
            u = urllib.parse.urlsplit(self.path)
            path = u.path
            if path in ("/", "/index.html"):
                html = (WEB_DIR / "index.html").read_text(encoding="utf-8").replace("__APP_TOKEN__", self.server.token)
                if _windows_app_dark():
                    # WebView2 often keeps prefers-color-scheme:light; stamp theme in HTML.
                    html = html.replace('<html lang="zh-CN">', '<html lang="zh-CN" data-theme="dark" style="color-scheme:dark">', 1)
                    html = html.replace(
                        '<meta name="theme-color" content="#f5f4f1" media="(prefers-color-scheme: light)">',
                        '<meta name="theme-color" content="#0e0e0d">', 1)
                    html = html.replace(
                        '<head>',
                        '<head><style>html[data-theme="dark"],html[data-theme="dark"] body{background:#0e0e0d!important;color:#f2f1ee}</style>',
                        1)
                self.send_bytes(html.encode("utf-8"), "text/html; charset=utf-8", set_cookie=True)
            elif path in ("/wizard.js", "/wizard.css", "/app.js", "/style.css", "/style-v11.css", "/smart-ui.js", "/icon.svg", "/icon.ico", "/favicon.ico") or re.fullmatch(r"/icon-(?:32|48|64|128|192|256|512|1024)\.png", path):
                self.file(WEB_DIR / ("icon.ico" if path == "/favicon.ico" else path.lstrip("/")))
            elif path == "/health":
                self.json({"ok": True, "app": "qingbao", "pid": os.getpid()})
            else:
                self.authorize()
                if path == "/api/state":
                    self.json(self.ws.public_state())
                elif path == "/api/material-pages":
                    material_id = urllib.parse.parse_qs(u.query).get("id", [""])[0]
                    self.json(self.ws.material_pages(material_id))
                elif path.startswith("/media/"):
                    parts = path.split("/")
                    if len(parts) not in (4, 5) or not re.fullmatch(r"[a-f0-9]{20}", parts[2]):
                        raise FileNotFoundError()
                    material_id, variant = parts[2:4]
                    if variant in ("page", "page-thumb") and len(parts) == 5:
                        if not re.fullmatch(r"[1-9][0-9]{0,2}", parts[4]):
                            raise FileNotFoundError()
                        source = self.ws.material_pdf_page(material_id, int(parts[4]))
                        im = open_rgb(source)
                    elif len(parts) == 4 and variant in ("image", "thumb", "original"):
                        with self.ws.lock:
                            m = self.ws.material(material_id)
                            if variant == "original":
                                self.file(Path(m["original"]), download=True)
                                return
                            im = self.ws._image(m)
                    else:
                        raise FileNotFoundError()
                    try:
                        if variant in ("thumb", "page-thumb"):
                            im.thumbnail((640, 640), Image.Resampling.LANCZOS)
                        buffer = io.BytesIO()
                        if variant in ("image", "page"):
                            im.save(buffer, "PNG")
                            self.send_bytes(buffer.getvalue(), "image/png")
                        else:
                            im.save(buffer, "JPEG", quality=86)
                            self.send_bytes(buffer.getvalue(), "image/jpeg")
                    finally:
                        im.close()
                elif path.startswith("/plan-preview/"):
                    parts=path.split('/')
                    if len(parts)!=4 or not re.fullmatch(r'[a-f0-9]{20}',parts[2]) or (parts[3] not in {'stitch.png','payment-side.png','order-side.png','layout-preview.jpg','voucher-preview.jpg','voucher-preview.png','invoice-preview.png','invoice-print.pdf','invoice.pdf','voucher.pdf'} and not re.fullmatch(r'(?:invoice-page-|invoice-hq-page-|voucher-hq-page-)[1-9][0-9]{0,2}\.png',parts[3])):
                        raise FileNotFoundError()
                    flow_plans = {p.get('plan_id') for r in self.ws.state.get('runs', {}).values() for p in r.get('production', [])}
                    if parts[2] not in self.ws.state.get('plans',{}) and parts[2] not in flow_plans:
                        raise FileNotFoundError()
                    hq = re.fullmatch(r'(invoice|voucher)-hq-page-([1-9][0-9]{0,2})\.png', parts[3])
                    if hq:
                        self.file(self.ws.production_pdf_page(parts[2], hq[1], int(hq[2])))
                    else:
                        self.file(self.ws.root/'plans'/parts[2]/parts[3])
                elif path.startswith("/invoice-preview/") or path.startswith("/invoice-preview-pdf/"):
                    parts = path.split("/")
                    if len(parts) != 4 or not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", parts[3].split("?", 1)[0]):
                        raise FileNotFoundError()
                    filename = parts[3].split("?", 1)[0]
                    if path.startswith("/invoice-preview-pdf/") and not filename.endswith(".pdf"):
                        raise FileNotFoundError()
                    if path.startswith("/invoice-preview/") and not filename.endswith(".png"):
                        raise FileNotFoundError()
                    preview = self.ws.root / "previews" / filename
                    if not preview.is_file() or preview.parent.resolve() != (self.ws.root / "previews").resolve():
                        raise FileNotFoundError()
                    self.ws.order(parts[2])
                    self.file(preview)
                elif path.startswith("/preview/"):
                    parts = path.split("/")
                    order_id = parts[2]
                    if len(parts) != 4 or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}\.jpg", parts[3]):
                        raise FileNotFoundError()
                    preview = self.ws.root / "previews" / parts[3]
                    if preview.parent.resolve() != (self.ws.root / "previews").resolve() or not preview.is_file():
                        raise FileNotFoundError()
                    self.ws.order(order_id)
                    self.file(preview)
                elif path.startswith("/download/"):
                    parts = path.split("/")
                    record = next((x for x in self.ws.state["exports"] if x["id"] == parts[2]), None)
                    if not record or parts[3] not in ("invoice", "voucher"):
                        raise FileNotFoundError()
                    self.file(Path(record[parts[3]]), download=True)
                elif path.startswith("/tool-download/"):
                    record = self.ws.state["tools"].get(path.split("/")[-1])
                    if not record:
                        raise FileNotFoundError()
                    self.file(Path(record["path"]), download=True)
                else:
                    self.json({"error": "页面不存在"}, 404)
        except (ConnectionError, BrokenPipeError):
            pass
        except Exception as exc:
            self.json({"error": public_error(exc)}, 400 if not isinstance(exc, FileNotFoundError) else 404)

    def body(self, limit=2 * 1024 * 1024):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > limit:
            raise ValueError("请求内容过大")
        content = self.rfile.read(length)
        if len(content) != length:
            raise ValueError("上传中断，请重试")
        return content

    def do_POST(self):
        try:
            self.authorize(write=True)
            u = urllib.parse.urlsplit(self.path)
            path = u.path
            if path == "/api/upload":
                query = urllib.parse.parse_qs(u.query)
                name = query.get("name", ["image.png"])[0]
                run_id = query.get("run_id", [""])[0]
                if run_id:
                    from .flow_engine import add_material, public_run
                    content = self.body(MAX_UPLOAD)
                    with self.ws.lock:
                        run = public_run(self.ws, run_id)
                        if run.get("status") == "running":
                            raise ValueError("正在处理本批材料，请完成后再补交。")
                        if run.get("stage") == "done":
                            raise ValueError("这批材料已经归档，请返回主页开始新的一批。")
                        if len(run.get("material_ids", [])) >= 240:
                            raise ValueError("本批最多240份材料，请新开一批继续。")
                        material = self.ws.import_bytes(content, name, run["inbox_order_id"])
                        add_material(self.ws, run_id, material["id"])
                        self.json(material)
                else:
                    order_id = query.get("order_id", [""])[0]
                    self.json(self.ws.import_bytes(self.body(MAX_UPLOAD), name, order_id))
                return
            data = json.loads(self.body().decode("utf-8") or "{}")
            if path.startswith("/api/flow/"):
                from . import flow_engine
                operations = {
                    "new": flow_engine.create_run,
                    "update": flow_engine.update_run,
                    "review": flow_engine.start_review,
                    "make": flow_engine.start_make,
                    "reflow": flow_engine.start_reflow,
                    "export": flow_engine.start_export,
                    "open": flow_engine.open_run,
                    "delete": flow_engine.delete_run,
                    "remove": flow_engine.remove_material,
                    "remove-order": flow_engine.remove_order,
                }
                operation = operations.get(path.rsplit("/", 1)[-1])
                if not operation:
                    raise ValueError("未知的向导操作")
                self.json(operation(self.ws, data))
            elif path == "/api/ai/test":
                self.json(self.ws.test_ai(data))
            elif path == "/api/settings":
                with self.ws.lock:
                    if any(j["status"] in ("pending", "running") for j in self.ws.jobs.values()):
                        raise ValueError("仍有识别或导出任务正在进行，完成后再修改设置。")
                    s = self.ws.order_settings(data["order_id"]) if data.get("order_id") and "layout" in data else copy.deepcopy(self.ws.settings)
                    for key in (
                        "name", "advisor", "project_name", "phone", "declare_date", "output_dir",
                        "buyer_name", "taxpayer_id", "org_address", "org_phone",
                        "bank_account", "bank_name", "required_address", "address_keywords",
                        "ai_api_base", "ai_model", "ai_vision_model",
                    ):
                        if key in data:
                            limit = 2000 if key in {"required_address", "org_address", "address_keywords"} else 1000
                            setattr(s, key, str(data[key]).strip()[:limit])
                    if data.get("clear_api_key"):
                        s.ai_api_key = ""
                    elif data.get("ai_api_key"):
                        s.ai_api_key = str(data["ai_api_key"]).strip()
                    if "pdf_dpi" in data:
                        if int(data["pdf_dpi"]) not in (150, 200, 300):
                            raise ValueError("请选择150、200或300 DPI")
                        s.pdf_dpi = int(data["pdf_dpi"])
                    if "layout" in data:
                        layout = data["layout"]
                        limits = {"layout_left_mm": (20, 110), "layout_right_mm": (8, 80), "layout_top_mm": (8, 55), "layout_bottom_mm": (8, 55), "layout_gap_mm": (3, 25), "invoice_left_mm": (5, 100), "invoice_right_mm": (5, 100), "invoice_top_mm": (5, 100), "invoice_bottom_mm": (5, 100)}
                        for k, (low, high) in limits.items():
                            if k in layout:
                                v = float(layout[k])
                                if not low <= v <= high:
                                    raise ValueError("排版边距超出合理范围")
                                s.extra[k] = v
                        if "layout_order_on_left" in layout:
                            s.extra["layout_order_on_left"] = bool(layout["layout_order_on_left"])
                        if "layout_column_ratio" in layout:
                            ratio=layout["layout_column_ratio"]
                            if ratio is None:
                                s.extra.pop("layout_column_ratio",None)
                            else:
                                ratio=float(ratio)
                                if not .15<=ratio<=.85:
                                    raise ValueError("左右内容宽度分配比例应为15%–85%")
                                s.extra["layout_column_ratio"]=ratio
                        if "layout_adaptive" in layout:
                            s.extra["layout_adaptive"]=bool(layout["layout_adaptive"])
                        if "layout_vertical_align" in layout:
                            if layout["layout_vertical_align"] not in ('top','center'):
                                raise ValueError("垂直对齐方式无效")
                            s.extra["layout_vertical_align"]=layout["layout_vertical_align"]
                        if "invoice_layout_mode" in layout:
                            mode = str(layout["invoice_layout_mode"])
                            if mode not in {"print", "original"}:
                                raise ValueError("发票导出模式无效")
                            s.extra["invoice_layout_mode"] = mode
                        s.left_margin_ratio = float(s.extra.get("layout_left_mm", 77)) / 297
                    if s.ai_api_base:
                        AIVisionClient(s)._chat_url()
                    if data.get("order_id") and "layout" in data:
                        self.ws.guard_order(data["order_id"])
                        current=self.ws.order(data["order_id"])
                        current["layout"]=copy.deepcopy(s.extra)
                        current["layout"]["dpi"]=s.pdf_dpi
                        current.pop("layout_review",None)
                        self.ws.invalidate(current["id"])
                    else:
                        save_settings(s, self.ws.root / "settings.json")
                        self.ws.settings = s
                        for order in self.ws.state["orders"]:
                            # New defaults affect future runs. Completed history
                            # and per-run profile/layout snapshots stay intact.
                            if not order.get("flow_run_id") and not any(e.get("order_id") == order["id"] for e in self.ws.state["exports"]):
                                self.ws.invalidate(order["id"])
                    self.ws.save()
                self.json({"ok": True})
            elif path == "/api/orders":
                self.json(self.ws.add_order(data))
            elif path == "/api/order/update":
                with self.ws.lock:
                    self.ws.guard_order(data["id"])
                    original_order = self.ws.order(data["id"])
                    order = copy.deepcopy(original_order)
                    for k in ("title", "name", "advisor", "project_name", "declare_date"):
                        if k in data:
                            order[k] = str(data[k]).strip()[:120]
                    if "order_index" in data:
                        index = int(data["order_index"])
                        if not 1 <= index <= 999:
                            raise ValueError("当日订单序号应为1–999")
                        order["order_index"] = index
                    from .core.naming import validate_declare_date
                    validate_declare_date(order["declare_date"])
                    duplicate = any(other["id"] != order["id"] and other.get("declare_date") == order.get("declare_date") and int(other.get("order_index", 1)) == int(order.get("order_index", 1)) for other in self.ws.state["orders"])
                    if duplicate:
                        raise ValueError("同一天的笔序已被另一笔采购使用；请选择不同序号。")
                    original_order.update(order)
                    self.ws.invalidate(order["id"])
                    self.ws.save()
                self.json({"ok": True})
            elif path == "/api/order/delete":
                with self.ws.lock:
                    self.ws.guard_order(data["id"])
                    if any(x["order_id"] == data["id"] for x in self.ws.state["exports"]):
                        raise ValueError("该笔采购已有导出记录，为保留审计关系不能删除；可新建下一笔采购。")
                    if self.ws.records(data["id"]):
                        raise ValueError("请先移走或移除该笔采购内的材料")
                    self.ws.state["orders"] = [r for r in self.ws.state["orders"] if r["id"] != data["id"]]
                    if not self.ws.state["orders"]:
                        self.ws.add_order({})
                    self.ws.save()
                self.json({"ok": True})
            elif path == "/api/material/update":
                self.json(self.ws.update_material(data["id"], data))
            elif path == "/api/material/remove":
                with self.ws.lock:
                    m = self.ws.material(data["id"])
                    self.ws.guard_order(m["order_id"])
                    self.ws.invalidate(m["order_id"])
                    del self.ws.state["materials"][m["id"]]
                    # Keep immutable files locally so removal cannot destroy originals.
                    self.ws.save()
                self.json({"ok": True})
            elif path == "/api/material/edit":
                self.json(self.ws.edit_material(data["id"], data))
            elif path == "/api/stitch":
                self.json(self.ws.stitch(data))
            elif path == "/api/smart-plan":
                from .smart_workflows import start_plan
                self.json(start_plan(self.ws,data))
            elif path == "/api/smart-plan/get":
                from .smart_workflows import public_plan
                with self.ws.lock:
                    self.json(public_plan(self.ws,data['plan_id']))
            elif path == "/api/smart-plan/apply":
                from .smart_workflows import apply_plan
                self.json(apply_plan(self.ws,data))
            elif path == "/api/group-proposals":
                from .core.grouping import propose_groups
                with self.ws.lock:
                    self.ws.guard_order(data['order_id'])
                    self.json(propose_groups(self.ws.records(data['order_id'])))
            elif path == "/api/apply-groups":
                from .core.grouping import propose_groups
                with self.ws.lock:
                    origin = self.ws.order(data['order_id'])
                    self.ws.guard_order(origin['id'])
                    proposals = propose_groups(self.ws.records(origin['id']))
                    if not proposals['groups']:
                        raise ValueError(proposals['message'])
                    if not data.get('confirmed'):
                        raise ValueError('请先核对分单建议')
                    new_ids = []
                    for index, group in enumerate(proposals['groups']):
                        if index == 0:
                            # Reuse the source order for the first purchase so same-day indexes
                            # remain 1, 2, 3 rather than leaving an empty phantom order 1.
                            new_order = origin
                            new_order['title'] = group['title'][:80]
                        else:
                            new_order = self.ws.add_order({'title':group['title'], 'project_name':origin.get('project_name',''), 'declare_date':origin['declare_date']})
                        for material_id in group['ids']:
                            self.ws.material(material_id)['order_id'] = new_order['id']
                        new_ids.append(new_order['id'])
                    self.ws.invalidate(origin['id'])
                    self.ws.save()
                    self.json({'order_ids':new_ids,'unassigned':proposals['unassigned']})
            elif path == "/api/apply-regions":
                if not data.get('confirmed'):
                    raise ValueError('请先核对视觉模型的裁切建议')
                with self.ws.lock:
                    self.ws.guard_order(data['order_id'])
                    records = list(self.ws.records(data['order_id']))
                    count = 0
                    for record in records:
                        if not record.get('selected', True):
                            continue
                        regions = record.get('extracted', {}).get('extra', {}).get('regions', [])
                        if len(regions) < 2:
                            continue
                        if Path(record['original']).suffix.lower() == '.pdf' and record['category'] == 'invoice':
                            continue
                        for region in regions[:6]:
                            if region.get('category') not in ('invoice','order','payment'):
                                continue
                            self.ws.edit_material(record['id'], {'action':'split', 'category':region['category'], 'box':region.get('box',region.get('bbox'))})
                            count += 1
                    self.json({'created':count})
            elif path == "/api/analyze":
                self.json(self.ws.analyze(data))
            elif path == "/api/preview":
                self.json(self.ws.preview(data["order_id"]))
            elif path == "/api/invoice-preview":
                self.json(self.ws.invoice_preview(data["order_id"]))
            elif path == "/api/export":
                self.json(self.ws.export(data))
            elif path == "/api/pdf-tool":
                self.json(self.ws.pdf_tool(data))
            elif path == "/api/open-output":
                folder = Path(self.ws.settings.output_dir)
                if data.get("export_id"):
                    record = next((x for x in self.ws.state["exports"] if x["id"] == data["export_id"]), None)
                    if not record:
                        raise ValueError("导出记录不存在")
                    folder = Path(record["folder"])
                folder.mkdir(parents=True, exist_ok=True)
                if os.name == "nt":
                    os.startfile(str(folder.resolve()))
                self.json({"ok": True, "path": str(folder)})
            elif path == "/api/shutdown":
                if any(j["status"] in ("pending", "running") for j in self.ws.jobs.values()):
                    raise ValueError("仍有任务正在进行，完成后再退出")
                self.json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.json({"error": "未知操作"}, 404)
        except (ConnectionError, BrokenPipeError):
            pass
        except Exception as exc:
            self.json({"error": public_error(exc)}, 400)
