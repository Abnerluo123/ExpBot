"""领域数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class ImageCategory(str, Enum):
    UNKNOWN = "unknown"
    INVOICE = "invoice"  # 电子发票
    PAYMENT = "payment"  # 支付/账单截图
    ORDER = "order"  # 订单/购买详情截图
    RECEIPT = "receipt"  # 其他小票
    LONG_SCREENSHOT = "long_screenshot"  # 待拼接长图碎片
    OTHER = "other"


CATEGORY_LABELS = {
    ImageCategory.UNKNOWN: "未分类",
    ImageCategory.INVOICE: "电子发票",
    ImageCategory.PAYMENT: "支付凭证",
    ImageCategory.ORDER: "订单/购买截图",
    ImageCategory.RECEIPT: "小票/收据",
    ImageCategory.LONG_SCREENSHOT: "长图碎片",
    ImageCategory.OTHER: "其他",
}


@dataclass
class ExtractedInfo:
    """AI / 本地规则从单张图抽取的关键信息。"""

    amount: Optional[float | str] = None
    currency: str = "CNY"
    merchant: str = ""
    product_name: str = ""
    quantity: str = ""
    pay_time: str = ""
    order_no: str = ""
    buyer_name: str = ""  # 发票抬头
    taxpayer_id: str = ""
    address: str = ""
    pay_method: str = ""
    raw_text_summary: str = ""
    confidence: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def amount_str(self) -> str:
        if self.amount is None:
            return ""
        from decimal import Decimal, InvalidOperation
        try:
            return format(Decimal(str(self.amount)), '.2f')
        except (InvalidOperation, ValueError):
            return str(self.amount)


@dataclass
class ImageItem:
    path: Path
    category: ImageCategory = ImageCategory.UNKNOWN
    extracted: ExtractedInfo = field(default_factory=ExtractedInfo)
    notes: list[str] = field(default_factory=list)
    selected: bool = True
    thumb_path: Optional[Path] = None
    width: int = 0
    height: int = 0
    file_size: int = 0
    ai_raw: str = ""

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category.value)


@dataclass
class ValidationIssue:
    level: str  # error | warning | info
    code: str
    message: str
    related_files: list[str] = field(default_factory=list)

    @property
    def is_error(self) -> bool:
        return self.level == "error"


@dataclass
class ProcessResult:
    success: bool
    folder_path: Optional[Path] = None
    invoice_pdf: Optional[Path] = None
    voucher_pdf: Optional[Path] = None
    issues: list[ValidationIssue] = field(default_factory=list)
    message: str = ""
    preview_voucher: Optional[Path] = None
    preview_invoice: Optional[Path] = None
