"""本地启发式分类（无 AI 时兜底）+ 标签映射。"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

from .models import CATEGORY_LABELS, ImageCategory, ImageItem

__all__ = ["CATEGORY_LABELS", "classify_locally", "guess_from_filename", "probe_image"]


INVOICE_NAME_RE = re.compile(
    r"(发票|invoice|fapiao|电子发票|增值税)",
    re.I,
)
PAYMENT_NAME_RE = re.compile(
    r"(支付|付款|账单|交易|payment|alipay|wechat|微信|支付宝|bank|扣款|成功)",
    re.I,
)
ORDER_NAME_RE = re.compile(
    r"(订单|order|购买|详情|购物|商品|pcb|淘宝|京东|拼多多|detail)",
    re.I,
)


def probe_image(path: Path) -> tuple[int, int, int]:
    path = Path(path)
    size = path.stat().st_size if path.exists() else 0
    try:
        with Image.open(path) as im:
            return im.width, im.height, size
    except Exception:
        return 0, 0, size


def guess_from_filename(name: str) -> ImageCategory:
    if INVOICE_NAME_RE.search(name):
        return ImageCategory.INVOICE
    if PAYMENT_NAME_RE.search(name):
        return ImageCategory.PAYMENT
    if ORDER_NAME_RE.search(name):
        return ImageCategory.ORDER
    return ImageCategory.UNKNOWN


def _aspect_hint(w: int, h: int) -> ImageCategory | None:
    if w <= 0 or h <= 0:
        return None
    ratio = h / max(w, 1)
    # 竖长手机截图更可能是支付账单
    if ratio > 1.6 and w < 900:
        return ImageCategory.PAYMENT
    # 很宽的网页截图更可能是订单
    if w / max(h, 1) > 1.3 and w > 1200:
        return ImageCategory.ORDER
    return None


def classify_locally(item: ImageItem) -> ImageItem:
    """基于文件名与尺寸的本地分类。"""
    cat = guess_from_filename(item.path.name)
    if cat == ImageCategory.UNKNOWN:
        hinted = _aspect_hint(item.width, item.height)
        if hinted:
            cat = hinted
            item.notes.append(f"按画面比例推测为「{CATEGORY_LABELS[cat]}」")
    item.category = cat
    if cat == ImageCategory.UNKNOWN:
        item.notes.append("未能自动分类，请手动指定")
    return item


def load_image_item(path: str | Path) -> ImageItem:
    p = Path(path)
    w, h, sz = probe_image(p)
    item = ImageItem(path=p, width=w, height=h, file_size=sz)
    return classify_locally(item)
