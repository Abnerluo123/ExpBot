"""报销材料校验：金额一致、抬头、地址、材料齐全等。"""

from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional, Union

from ..config import defaults
from .models import ExtractedInfo, ImageCategory, ImageItem, ValidationIssue

AmountLike = Union[int, float, str, Decimal]

# 人工确认才能放行的「未知核心字段」warning 代码
UNKNOWN_CORE_CODES = frozenset(
    {
        "no_buyer",
        "no_tax_id",
        "no_address",
        "no_amount",
        "amount_partial",
        "amount_none",
        "unclassified",
        "invoice_from_image",
    }
)


class AmountParseError(ValueError):
    """金额解析失败。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _norm_text(s: str) -> str:
    """去空白并小写（用于名称/税号）。"""
    return re.sub(r"\s+", "", (s or "")).lower()


def _norm_loose(s: str) -> str:
    """忽略空白与常见标点，保留字母数字与中文。"""
    return re.sub(r"[\s\W_]+", "", (s or ""), flags=re.UNICODE).lower()


# 严格金额字符串：可选币种前缀 + 数字（可含千分位）+ 可选两位小数 + 可选「元」
# 拒绝：指数、字母混杂、负号、多余符号
_AMOUNT_STR_RE = re.compile(
    r"^\s*"
    r"(?:¥|￥|CNY|RMB|cny|rmb)?"
    r"\s*"
    r"(?P<num>"
    r"(?:0|[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d*|0)"
    r"(?:\.\d{1,2})?"
    r")"
    r"\s*"
    r"(?:元)?"
    r"\s*$"
)


def parse_amount(value: AmountLike | None) -> Decimal:
    """将 float/str/int/Decimal 解析为 Decimal，不先四舍五入。

    规则：
    - None / 空串非法
    - 非有限数（NaN/Inf）→ 错误
    - 负金额 → 错误（不自动 abs；退款/冲正须人工处理）
    - 字符串仅允许：可选 ¥/￥/CNY/RMB、千分位逗号、最多两位小数、可选「元」
    - 拒绝指数记法（1e2）、字母混杂（abc626.16xyz）等
    - 超过两位小数 → 错误
    - 不在解析阶段做 round
    """
    if value is None:
        raise AmountParseError("bad_amount", "金额为空")

    if isinstance(value, bool):
        raise AmountParseError("bad_amount", f"不支持的金额类型：bool")

    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise AmountParseError("bad_amount_nonfinite", f"金额非有限数：{value!r}")
        # 用 str 避免二进制浮点噪声放大；不在此处 quantize
        d = Decimal(str(value))
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            raise AmountParseError("bad_amount", "金额为空字符串")
        # 显式拒绝指数 / 正负号科学计数（在宽松清洗前拦截）
        if re.search(r"[eE]", s):
            raise AmountParseError(
                "bad_amount",
                f"不接受指数记法金额：{value!r}（请使用普通十进制，如 100.00）",
            )
        if re.search(r"[+\-]", s):
            # 负号或显式正号均不接受（付款金额应为无符号绝对值）
            if "-" in s:
                raise AmountParseError(
                    "bad_amount_negative",
                    f"不接受负金额：{value!r}（支付截图应输出绝对付款金额，不含负号；"
                    f"退款勿自动取绝对值）",
                )
            raise AmountParseError("bad_amount", f"无法解析金额：{value!r}")
        # 形如 1.001 的三位及以上小数：给出 scale 错误（而非笼统 bad_amount）
        if re.match(
            r"^\s*(?:¥|￥|CNY|RMB|cny|rmb)?\s*"
            r"(?:0|[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d*|0)"
            r"\.\d{3,}\s*(?:元)?\s*$",
            s,
        ):
            raise AmountParseError(
                "bad_amount_scale",
                f"金额「{value}」含超过两位小数，报销金额精确到分",
            )
        m = _AMOUNT_STR_RE.match(s)
        if not m:
            raise AmountParseError(
                "bad_amount",
                f"无法解析金额：{value!r}（仅允许可选币种¥/￥/CNY/RMB、千分位与最多两位小数）",
            )
        num = m.group("num").replace(",", "")
        try:
            d = Decimal(num)
        except InvalidOperation as e:
            raise AmountParseError("bad_amount", f"无法解析金额：{value!r}") from e
    else:
        raise AmountParseError("bad_amount", f"不支持的金额类型：{type(value).__name__}")

    if not d.is_finite():
        raise AmountParseError("bad_amount_nonfinite", f"金额非有限数：{value!r}")

    if d < 0:
        raise AmountParseError(
            "bad_amount_negative",
            f"不接受负金额：{value!r}（应为绝对付款金额，退款勿自动取绝对值）",
        )

    # 超过分（两位小数）视为错误；Decimal('1.10') exponent=-2 OK；1.001 → -3
    exp = d.as_tuple().exponent
    if isinstance(exp, int) and exp < -2:
        raise AmountParseError(
            "bad_amount_scale",
            f"金额「{value}」含超过两位小数，报销金额精确到分",
        )

    return d


def format_amount(d: Decimal) -> str:
    """展示用：量化到分（仅展示，不改变比较逻辑的输入）。"""
    return f"{d.quantize(Decimal('0.01'))}"


def _address_required_parts() -> tuple[str, ...]:
    """Parts that must all appear in a shipping address when configured."""
    parts = [p for p in (defaults.ADDRESS_KEYWORDS or []) if str(p).strip()]
    if parts:
        return tuple(str(p).strip() for p in parts)
    ref = str(defaults.REQUIRED_ADDRESS or "").strip()
    return (ref,) if ref else ()


def address_ok(address: str) -> bool:
    """Match configured address tokens (or full required address) ignoring spaces/punct."""
    if not address:
        return False
    parts = _address_required_parts()
    if not parts:
        # No organization profile configured yet — only require a non-empty address.
        return bool(str(address).strip())
    t = _norm_loose(address)
    return all(_norm_loose(part) in t for part in parts)


def buyer_ok(name: str) -> bool:
    """Buyer name must match configured taxpayer_name when set (ignore whitespace)."""
    n = _norm_text(name)
    if not n:
        return False
    target = _norm_text(defaults.SCHOOL_INVOICE_INFO.get("taxpayer_name", ""))
    if not target:
        return True
    return n == target


def tax_id_ok(tax_id: str) -> bool:
    """Tax ID must match configured taxpayer_id when set (ignore whitespace/case)."""
    if not tax_id:
        return False
    target = _norm_text(defaults.SCHOOL_INVOICE_INFO.get("taxpayer_id", ""))
    if not target:
        return True
    return _norm_text(tax_id) == target


def _amount_issue_for_value(
    raw: object,
    *,
    prefix: str = "",
) -> tuple[Optional[Decimal], Optional[ValidationIssue]]:
    """解析单条金额；成功返回 (decimal, None)，失败返回 (None, issue)。"""
    if raw is None:
        return None, None
    try:
        return parse_amount(raw), None  # type: ignore[arg-type]
    except AmountParseError as e:
        return None, ValidationIssue(
            "error",
            e.code,
            f"{prefix}{e.message}" if prefix else e.message,
        )


def validate_extracted(info: ExtractedInfo, category: ImageCategory) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    # A known failed, pending, refund, or non-expense transaction can never be
    # accepted as the purchase payment record, even after the manual checkbox.
    if category == ImageCategory.PAYMENT:
        status = str(info.extra.get("transaction_status", "unknown")).strip().lower()
        direction = str(info.extra.get("payment_direction", "unknown")).strip().lower()
        failed_statuses = {"failed", "failure", "closed", "cancelled", "canceled", "refund", "refunded", "退款", "已退款", "支付失败", "交易失败", "交易关闭"}
        pending_statuses = {"pending", "processing", "待支付", "待确认", "处理中", "支付中"}
        successful_statuses = {"success", "succeeded", "paid", "complete", "completed", "付款成功", "支付成功", "交易成功"}
        non_expense_directions = {"refund", "refunded", "income", "credit", "退款", "收入", "入账", "退款入账"}
        if status in failed_statuses or direction in non_expense_directions:
            issues.append(ValidationIssue("error", "payment_not_expense", "这张支付记录显示为退款、收入或未成功交易，不能作为本笔采购的成功付款凭证。请提供成功的支出付款记录。"))
        elif status in pending_statuses:
            issues.append(ValidationIssue("error", "payment_pending", "支付记录仍在处理中或待支付，不是已成功的付款凭证。"))
        elif status in successful_statuses and direction not in {"expense", "debit", "outgoing", "支出", "付款", "消费"}:
            issues.append(ValidationIssue("error", "payment_direction_unknown", "无法确认支付记录是成功的采购支出，请核对原图并提供明确的付款成功记录。"))

    if category == ImageCategory.INVOICE:
        if info.buyer_name:
            if not buyer_ok(info.buyer_name):
                issues.append(
                    ValidationIssue(
                        "error",
                        "bad_buyer",
                        f"发票抬头为「{info.buyer_name}」，必须精确为"
                        f"「{defaults.SCHOOL_INVOICE_INFO['taxpayer_name']}」",
                    )
                )
        else:
            expected = str(defaults.SCHOOL_INVOICE_INFO.get("taxpayer_name") or "").strip()
            hint = f"是否精确为「{expected}」" if expected else "是否与设置中的购买方名称一致"
            issues.append(
                ValidationIssue(
                    "warning",
                    "no_buyer",
                    f"未能识别发票抬头，请人工确认{hint}",
                )
            )

        if info.taxpayer_id:
            if not tax_id_ok(info.taxpayer_id):
                expected_tax = str(defaults.SCHOOL_INVOICE_INFO.get("taxpayer_id") or "").strip()
                suffix = f"（应为 {expected_tax}）" if expected_tax else "（与设置中的税号不符）"
                issues.append(
                    ValidationIssue(
                        "error",
                        "bad_tax_id",
                        f"纳税人识别号「{info.taxpayer_id}」与配置不符{suffix}",
                    )
                )
        else:
            expected_tax = str(defaults.SCHOOL_INVOICE_INFO.get("taxpayer_id") or "").strip()
            hint = f"是否为「{expected_tax}」" if expected_tax else "是否与设置中的税号一致"
            issues.append(
                ValidationIssue(
                    "warning",
                    "no_tax_id",
                    f"未识别到纳税人识别号，请人工确认{hint}",
                )
            )

        if info.amount is None:
            issues.append(ValidationIssue("warning", "no_amount", "未识别到发票金额"))
        else:
            _, amt_err = _amount_issue_for_value(info.amount, prefix="发票")
            if amt_err:
                issues.append(amt_err)

    if category in (ImageCategory.ORDER, ImageCategory.PAYMENT):
        label = "订单" if category == ImageCategory.ORDER else "支付"
        address_review = info.extra.get("address_review", {})
        reviewed = (isinstance(address_review, dict) and address_review.get("source") == "ai"
                    and address_review.get("reference") == defaults.REQUIRED_ADDRESS and bool(address_review.get("message")))
        if reviewed and address_review.get("status") in {"pass", "review", "fail"}:
            if address_review["status"] != "pass":
                issues.append(ValidationIssue(
                    "error" if address_review["status"] == "fail" else "warning",
                    "address_mismatch" if address_review["status"] == "fail" else "address_review",
                    str(address_review["message"]),
                ))
        elif info.address:
            if not address_ok(info.address):
                issues.append(
                    ValidationIssue(
                        "error",
                        "bad_address",
                        f"收货地址「{info.address}」尚未确认与「{defaults.REQUIRED_ADDRESS}」一致；"
                        f"请在整理步骤用视觉模型结合整批材料核对，合理简写可按同一地点判断。",
                    )
                )
        elif category == ImageCategory.ORDER:
            issues.append(
                ValidationIssue(
                    "warning",
                    "no_address",
                    "订单截图中未识别到收货地址，请确认是否为"
                    f"「{defaults.REQUIRED_ADDRESS}」",
                )
            )

        if info.amount is None:
            issues.append(
                ValidationIssue(
                    "warning",
                    "no_amount",
                    f"「{category.value}」未识别到金额，请人工核对",
                )
            )
        else:
            _, amt_err = _amount_issue_for_value(info.amount, prefix=f"{label}")
            if amt_err:
                issues.append(amt_err)

    # 商品名 / 数量 / 时间：缺失仅 warning（不纳入 manual_confirmed 核心码）
    if category in (ImageCategory.INVOICE, ImageCategory.ORDER):
        if not (info.product_name or "").strip():
            issues.append(
                ValidationIssue(
                    "warning",
                    "no_product_name",
                    f"「{category.value}」未识别到商品名称，请人工核对凭证是否清晰完整",
                )
            )
        if not (info.quantity or "").strip():
            issues.append(
                ValidationIssue(
                    "warning",
                    "no_quantity",
                    f"「{category.value}」未识别到数量，请人工核对",
                )
            )
    if category in (ImageCategory.PAYMENT, ImageCategory.ORDER):
        if not (info.pay_time or "").strip():
            issues.append(
                ValidationIssue(
                    "warning",
                    "no_pay_time",
                    f"「{category.value}」未识别到支付/下单时间，请人工核对",
                )
            )

    return issues


def _product_tokens(name: str) -> set[str]:
    """拆出商品名中的有意义片段（中文块 / 字母数字块，长度≥2）。"""
    s = (name or "").strip().lower()
    if not s:
        return set()
    parts = re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9]{2,}", s)
    return {p for p in parts if p}


def _quantity_numbers(q: str) -> set[str]:
    """数量中的主数字集合（normalize 后），忽略单位差异。"""
    s = (q or "").strip().lower().replace(" ", "")
    if not s:
        return set()
    s = s.replace("×", "x").replace("*", "x").replace("Ｘ", "x")
    out: set[str] = set()
    for m in re.findall(r"\d+(?:\.\d+)?", s):
        try:
            out.add(str(Decimal(m).normalize()))
        except InvalidOperation:
            out.add(m)
    return out


def _cross_check_product_quantity(
    invoices: list[ImageItem],
    orders: list[ImageItem],
) -> list[ValidationIssue]:
    """发票 vs 订单：商品名/数量疑似差异 warning（不强行精确匹配）。"""
    issues: list[ValidationIssue] = []
    inv_products = [
        (i, (i.extracted.product_name or "").strip())
        for i in invoices
        if (i.extracted.product_name or "").strip()
    ]
    ord_products = [
        (i, (i.extracted.product_name or "").strip())
        for i in orders
        if (i.extracted.product_name or "").strip()
    ]
    if inv_products and ord_products:
        # 任一侧有名称：token 交集 / 互相包含 / token 出现在对方全名中 → 可能同一商品
        def _related(a: str, b: str) -> bool:
            ta, tb = _product_tokens(a), _product_tokens(b)
            na, nb = _norm_loose(a), _norm_loose(b)
            if not na or not nb:
                return False
            if ta & tb:
                return True
            if na in nb or nb in na or na == nb:
                return True
            # 「螺丝钉」∈「螺丝钉套装」等：token 是对方全名子串
            if any(len(t) >= 2 and t in nb for t in ta):
                return True
            if any(len(t) >= 2 and t in na for t in tb):
                return True
            return False

        suspect = True
        for _, ip in inv_products:
            for _, op in ord_products:
                if _related(ip, op):
                    suspect = False
                    break
            if not suspect:
                break
        if suspect:
            inv_show = " / ".join(p for _, p in inv_products[:3])
            ord_show = " / ".join(p for _, p in ord_products[:3])
            issues.append(
                ValidationIssue(
                    "warning",
                    "product_mismatch_suspect",
                    f"商品名称疑似不一致（发票：「{inv_show}」；订单：「{ord_show}」）。"
                    f"发票税目可能与订单品名不同，请人工核对是否同一笔采购。",
                    [inv_products[0][0].name, ord_products[0][0].name],
                )
            )

    inv_qty = [
        (i, (i.extracted.quantity or "").strip())
        for i in invoices
        if (i.extracted.quantity or "").strip()
    ]
    ord_qty = [
        (i, (i.extracted.quantity or "").strip())
        for i in orders
        if (i.extracted.quantity or "").strip()
    ]
    if inv_qty and ord_qty:
        inv_nums: set[str] = set()
        ord_nums: set[str] = set()
        for _, q in inv_qty:
            inv_nums |= _quantity_numbers(q)
        for _, q in ord_qty:
            ord_nums |= _quantity_numbers(q)
        # 有数字可比较且完全无交集 → 疑似；「10」与「10个」数字同为 10，不算差异
        if inv_nums and ord_nums and inv_nums.isdisjoint(ord_nums):
            issues.append(
                ValidationIssue(
                    "warning",
                    "quantity_mismatch_suspect",
                    f"数量疑似不一致（发票：{' / '.join(q for _, q in inv_qty[:3])}；"
                    f"订单：{' / '.join(q for _, q in ord_qty[:3])}），请人工核对。",
                    [inv_qty[0][0].name, ord_qty[0][0].name],
                )
            )
    return issues


def _iter_category_amounts(
    items: Iterable[ImageItem], cat: ImageCategory
) -> list[tuple[ImageItem, Decimal]]:
    """收集某类材料的全部已识别金额（全部解析，不取第一张了事）。"""
    out: list[tuple[ImageItem, Decimal]] = []
    for it in items:
        if it.category != cat:
            continue
        if it.extracted.amount is None:
            continue
        # 解析错误在 validate_extracted / validate_batch 统一处理；此处跳过非法
        try:
            out.append((it, parse_amount(it.extracted.amount)))  # type: ignore[arg-type]
        except AmountParseError:
            continue
    return out


def _unique_amount(pairs: list[tuple[ImageItem, Decimal]]) -> Optional[Decimal]:
    if not pairs:
        return None
    return pairs[0][1]


def validate_batch(items: list[ImageItem]) -> list[ValidationIssue]:
    """整笔采购材料交叉校验。"""
    issues: list[ValidationIssue] = []
    selected = [i for i in items if i.selected]

    invoices = [i for i in selected if i.category == ImageCategory.INVOICE]
    payments = [i for i in selected if i.category == ImageCategory.PAYMENT]
    orders = [i for i in selected if i.category == ImageCategory.ORDER]
    unknown = [
        i
        for i in selected
        if i.category in (ImageCategory.UNKNOWN, ImageCategory.OTHER)
    ]

    if not invoices:
        issues.append(
            ValidationIssue(
                "error",
                "missing_invoice",
                "缺少电子发票图片/文件。每笔采购必须有与设置中购买方信息一致的电子发票。",
            )
        )
    elif len(invoices) > 1:
        # 多发票硬报错，防止混单
        issues.append(
            ValidationIssue(
                "error",
                "multi_invoice",
                f"检测到 {len(invoices)} 张发票。每笔采购只允许 1 张发票，禁止混单。",
                [i.name for i in invoices],
            )
        )

    if not payments:
        issues.append(
            ValidationIssue(
                "error",
                "missing_payment",
                "缺少支付/账单截图。购买及支付凭证需要「订单截图 + 支付截图」。",
            )
        )
    if not orders:
        issues.append(
            ValidationIssue(
                "error",
                "missing_order",
                "缺少订单/购买详情截图。购买及支付凭证需要「订单截图 + 支付截图」。",
            )
        )

    if unknown:
        issues.append(
            ValidationIssue(
                "warning",
                "unclassified",
                f"有 {len(unknown)} 个文件未明确分类，请手动指定类别后再导出。",
                [i.name for i in unknown],
            )
        )

    # 单图问题
    for it in selected:
        for iss in validate_extracted(it.extracted, it.category):
            issues.append(
                ValidationIssue(
                    iss.level,
                    iss.code,
                    f"[{it.label}/{it.name}] {iss.message}",
                    [it.name],
                )
            )

    # 同类多张金额必须完全一致（不“只用第一张”）
    for cat, label in (
        (ImageCategory.INVOICE, "发票"),
        (ImageCategory.PAYMENT, "支付"),
        (ImageCategory.ORDER, "订单"),
    ):
        pairs = _iter_category_amounts(selected, cat)
        if len(pairs) >= 2:
            base = pairs[0][1]
            for it, amt in pairs[1:]:
                if amt != base:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "amount_conflict_same_category",
                            f"多张{label}金额不一致："
                            f"「{pairs[0][0].name}」=¥{format_amount(base)}，"
                            f"「{it.name}」=¥{format_amount(amt)}。"
                            f"禁止混单，请拆成多笔采购分别处理。",
                            [pairs[0][0].name, it.name],
                        )
                    )
                    break

    # 三方金额完全一致（一分钱都不能差；Decimal 精确比较）
    inv_pairs = _iter_category_amounts(selected, ImageCategory.INVOICE)
    pay_pairs = _iter_category_amounts(selected, ImageCategory.PAYMENT)
    ord_pairs = _iter_category_amounts(selected, ImageCategory.ORDER)

    amounts: dict[str, Decimal] = {}
    if inv_pairs:
        amounts["发票"] = _unique_amount(inv_pairs)  # type: ignore[assignment]
    if pay_pairs:
        amounts["支付"] = _unique_amount(pay_pairs)  # type: ignore[assignment]
    if ord_pairs:
        amounts["订单"] = _unique_amount(ord_pairs)  # type: ignore[assignment]

    present = {k: v for k, v in amounts.items() if v is not None}
    if len(present) >= 2:
        vals = list(present.values())
        if any(v != vals[0] for v in vals[1:]):
            detail = "，".join(f"{k}=¥{format_amount(v)}" for k, v in present.items())
            issues.append(
                ValidationIssue(
                    "error",
                    "amount_mismatch",
                    f"金额不一致（{detail}）。发票金额必须 = 订单实际支付金额 = 支付记录金额，"
                    f"一分钱差异都无法报销。",
                )
            )
        else:
            issues.append(
                ValidationIssue(
                    "info",
                    "amount_ok",
                    f"金额一致：¥{format_amount(vals[0])}（{' / '.join(present.keys())}）",
                )
            )
    elif len(present) == 1:
        k, v = next(iter(present.items()))
        issues.append(
            ValidationIssue(
                "warning",
                "amount_partial",
                f"仅识别到{k}金额 ¥{format_amount(v)}，请人工确认三方金额一致。",
            )
        )
    else:
        issues.append(
            ValidationIssue(
                "warning",
                "amount_none",
                "未能自动识别金额，导出前请人工核验发票=订单=支付金额完全一致。",
            )
        )

    # 商品 / 数量交叉疑似差异（warning，不阻断，不进入 manual 核心码）
    issues.extend(_cross_check_product_quantity(invoices, orders))

    return issues


def has_blocking_errors(issues: list[ValidationIssue]) -> bool:
    return any(i.is_error for i in issues)


def unknown_core_warnings(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    """返回需要人工确认的未知核心字段类 warning。"""
    return [i for i in issues if i.level == "warning" and i.code in UNKNOWN_CORE_CODES]


def needs_manual_confirmation(issues: list[ValidationIssue]) -> bool:
    return bool(unknown_core_warnings(issues))
