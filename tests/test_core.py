"""核心回归测试：仅用 unittest、Pillow 和标准库，无网络、OCR 或 PDF 渲染依赖。"""
from __future__ import annotations

import re
import shutil
import sys
import unittest
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.settings import Settings
from app.core import pdf_generator
from app.core.image_processor import (
    DEFAULT_LAYOUT,
    a4_landscape_pixels,
    a4_portrait_pixels,
    fit_into_box,
    mm_to_px,
    open_rgb,
    prepare_invoice_image,
    process_voucher_layout,
    resolve_layout,
    smart_crop_whitespace,
    stitch_vertical,
)
from app.core.models import ExtractedInfo, ImageCategory, ImageItem
from app.core.naming import (
    NamingError,
    build_output_names,
    validate_declare_date,
    validate_order_index,
)
from app.core.pdf_generator import (
    build_output_package,
    generate_invoice_pdf,
    generate_voucher_pdf,
)
from app.core.validator import (
    UNKNOWN_CORE_CODES,
    AmountParseError,
    address_ok,
    buyer_ok,
    has_blocking_errors,
    needs_manual_confirmation,
    parse_amount,
    tax_id_ok,
    validate_batch,
    validate_extracted,
)

# Generic fixtures for unit tests (not any real institution).
ORG_NAME = "示例单位"
ADDRESS = "示例园区东区实验楼130室"
TAX_ID = "91110000MA01234567"
RED = (220, 40, 40)
BLUE = (40, 80, 220)
GREEN = (40, 190, 60)
WHITE = (255, 255, 255)


def codes(issues):
    return {i.code for i in issues}


def item(path, category, *, amount="626.16", **fields):
    return ImageItem(
        path=Path(path),
        category=category,
        extracted=ExtractedInfo(amount=amount, **fields),
    )


def complete_items(invoice="invoice.pdf", order="order.png", payment="payment.png"):
    """一笔金额/抬头/税号/地址/商品/数量/时间齐全的采购。"""
    return [
        item(invoice, ImageCategory.INVOICE, buyer_name=ORG_NAME, taxpayer_id=TAX_ID,
             product_name="螺丝钉 M3", quantity="10个"),
        item(order, ImageCategory.ORDER, address=ADDRESS, product_name="螺丝钉 M3套装",
             quantity="10", pay_time="2026-09-27 10:00:00"),
        item(payment, ImageCategory.PAYMENT, pay_time="2026-09-27 10:01:00",
             extra={"transaction_status": "success", "payment_direction": "expense"}),
    ]


def settings(output_dir="unused", **overrides):
    values = dict(
        name="张三", advisor="李四", project_name="实验采购", declare_date="20260927",
        order_index=1, output_dir=str(output_dir), pdf_dpi=72,
        extra={**DEFAULT_LAYOUT, "dpi": 72},
    )
    values.update(overrides)
    return Settings(**values)


def ink_bbox(im):
    return ImageChops.difference(im.convert("RGB"), Image.new("RGB", im.size, WHITE)).getbbox()


def color_bbox(im, color):
    masks = [channel.point(lambda value, wanted=wanted: 255 if value == wanted else 0)
             for channel, wanted in zip(im.convert("RGB").split(), color)]
    return ImageChops.multiply(ImageChops.multiply(masks[0], masks[1]), masks[2]).getbbox()


def pdf_page_sizes(data):
    return [tuple(map(float, match)) for match in re.findall(
        rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", data
    )]


class WorkspaceCase(unittest.TestCase):
    """每个测试只在工作区内创建自己的目录，避免系统 Temp 的沙箱 ACL。"""

    def setUp(self):
        self.work = ROOT / "output" / "_core_tests" / uuid.uuid4().hex
        self.work.mkdir(parents=True, exist_ok=False)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        # 只清理本测试创建并确认属于工作区的目录。
        self.work.resolve().relative_to((ROOT / "output" / "_core_tests").resolve())
        shutil.rmtree(self.work)

    def image(self, name, color=RED, size=(240, 480)):
        path = self.work / name
        with Image.new("RGB", size, color) as im:
            im.save(path, "PNG")
        return path

    def source_pdf(self):
        path = self.work / "original.pdf"
        with Image.new("RGB", (200, 100), (15, 25, 35)) as im:
            im.save(path, "PDF", resolution=72)
        return path


class AmountParsingTests(unittest.TestCase):
    def test_float_int_and_decimal_are_exact(self):
        for raw, expected in [(12.3, "12.3"), (12, "12"), (Decimal("12.30"), "12.30")]:
            with self.subTest(raw=raw):
                self.assertIsInstance(parse_amount(raw), Decimal)
                self.assertEqual(parse_amount(raw), Decimal(expected))

    def test_plain_string_and_zero(self):
        self.assertEqual(parse_amount("626.16"), Decimal("626.16"))
        self.assertEqual(parse_amount("0.00"), Decimal("0"))
        self.assertEqual(parse_amount(" 12.3 "), Decimal("12.3"))

    def test_currency_prefixes_and_yuan_suffix(self):
        for raw in ("¥626.16", "￥626.16元", "CNY 626.16", "RMB626.16元", "rmb 626.16"):
            with self.subTest(raw=raw):
                self.assertEqual(parse_amount(raw), Decimal("626.16"))

    def test_thousands_grouping(self):
        for raw in ("1,234.56", "¥1,234.56元", "CNY 12,345,678.90"):
            with self.subTest(raw=raw):
                expected = "12345678.90" if "12,345" in raw else "1234.56"
                self.assertEqual(parse_amount(raw), Decimal(expected))

    def test_invalid_thousands_grouping_rejected(self):
        for raw in ("1,23.45", "12,34,567.89", "1,,234", "1234,567.00"):
            with self.subTest(raw=raw), self.assertRaises(AmountParseError):
                parse_amount(raw)

    def test_three_decimal_inputs_not_rounded(self):
        for raw in ("1.001", 1.001, Decimal("1.001"), "¥1,234.567元", "1.000"):
            with self.subTest(raw=raw), self.assertRaises(AmountParseError) as cm:
                parse_amount(raw)
            self.assertEqual(cm.exception.code, "bad_amount_scale")

    def test_nonfinite_values_rejected(self):
        for raw in (float("nan"), float("inf"), float("-inf"), Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(raw=raw), self.assertRaises(AmountParseError) as cm:
                parse_amount(raw)
            self.assertEqual(cm.exception.code, "bad_amount_nonfinite")

    def test_scientific_notation_strings_rejected(self):
        for raw in ("1e2", "1E2", "CNY 6.2616e2", "1e-2"):
            with self.subTest(raw=raw), self.assertRaises(AmountParseError):
                parse_amount(raw)

    def test_mixed_letters_and_explanatory_text_rejected(self):
        for raw in ("abc626.16xyz", "626.16kg", "约100", "实付款626.16", "USD100", "NaN", "Infinity"):
            with self.subTest(raw=raw), self.assertRaises(AmountParseError):
                parse_amount(raw)

    def test_negative_values_never_become_positive(self):
        for raw in ("-626.16", "¥-10.00", -10, -10.01, Decimal("-0.01")):
            with self.subTest(raw=raw), self.assertRaises(AmountParseError) as cm:
                parse_amount(raw)
            self.assertEqual(cm.exception.code, "bad_amount_negative")

    def test_empty_boolean_and_unsupported_types_rejected(self):
        for raw in (None, "", "  ", True, False, [], object()):
            with self.subTest(raw=raw), self.assertRaises(AmountParseError):
                parse_amount(raw)

    def test_one_cent_is_distinct_and_no_quantization(self):
        self.assertNotEqual(parse_amount("10.00"), parse_amount("10.01"))
        self.assertEqual(parse_amount("10.10").as_tuple().exponent, -2)
        with self.assertRaises(AmountParseError):
            parse_amount(0.1 + 0.2)


class SchoolRulesTests(unittest.TestCase):
    def setUp(self):
        from app.config import defaults
        self._prev_info = dict(defaults.SCHOOL_INVOICE_INFO)
        self._prev_address = defaults.REQUIRED_ADDRESS
        self._prev_keywords = list(defaults.ADDRESS_KEYWORDS)
        defaults.SCHOOL_INVOICE_INFO.clear()
        defaults.SCHOOL_INVOICE_INFO.update({
            "taxpayer_name": ORG_NAME,
            "taxpayer_id": TAX_ID,
            "address": "",
            "phone": "",
            "bank_account": "",
            "bank_name": "",
        })
        defaults.REQUIRED_ADDRESS = ADDRESS
        defaults.ADDRESS_KEYWORDS.clear()
        defaults.ADDRESS_KEYWORDS.extend(["示例园区东区", "实验楼", "130室"])

    def tearDown(self):
        from app.config import defaults
        defaults.SCHOOL_INVOICE_INFO.clear()
        defaults.SCHOOL_INVOICE_INFO.update(self._prev_info)
        defaults.REQUIRED_ADDRESS = self._prev_address
        defaults.ADDRESS_KEYWORDS.clear()
        defaults.ADDRESS_KEYWORDS.extend(self._prev_keywords)

    def test_buyer_exact_school(self):
        self.assertTrue(buyer_ok(ORG_NAME))
        self.assertTrue(buyer_ok(f" {ORG_NAME} "))

    def test_buyer_suffix_or_prefix_is_not_school(self):
        for raw in (f"{ORG_NAME}附属机构", f"外地{ORG_NAME}", ORG_NAME[:2], ""):
            with self.subTest(raw=raw):
                self.assertFalse(buyer_ok(raw))

    def test_tax_id_exact(self):
        self.assertTrue(tax_id_ok(TAX_ID))
        self.assertTrue(tax_id_ok(" " + TAX_ID + " "))
        self.assertFalse(tax_id_ok(TAX_ID[:-1] + "8"))
        self.assertFalse(tax_id_ok(""))

    def test_address_tolerates_whitespace_and_punctuation(self):
        self.assertTrue(address_ok(ADDRESS))
        self.assertTrue(address_ok("省内，示例园区 东区 / 实验楼 - 130 室"))

    def test_address_needs_all_three_parts(self):
        for raw in ("示例园区东区实验楼", "实验楼130室",
                    "示例园区东区130室", "示例园区东区实验楼130", ""):
            with self.subTest(raw=raw):
                self.assertFalse(address_ok(raw))

    def test_known_wrong_invoice_fields_are_errors(self):
        issues = validate_extracted(
            ExtractedInfo(amount="10", buyer_name="个人", taxpayer_id="999"), ImageCategory.INVOICE
        )
        self.assertTrue(has_blocking_errors(issues))
        self.assertTrue({"bad_buyer", "bad_tax_id"} <= codes(issues))

    def test_unknown_invoice_core_fields_are_warnings(self):
        issues = validate_extracted(ExtractedInfo(), ImageCategory.INVOICE)
        self.assertFalse(has_blocking_errors(issues))
        self.assertTrue({"no_buyer", "no_tax_id", "no_amount"} <= codes(issues))
        self.assertTrue(needs_manual_confirmation(issues))

    def test_known_wrong_order_address_is_error(self):
        issues = validate_extracted(ExtractedInfo(amount="10", address="其他学校"), ImageCategory.ORDER)
        self.assertIn("bad_address", codes(issues))
        self.assertTrue(has_blocking_errors(issues))


class BatchValidationTests(unittest.TestCase):
    def test_complete_transaction_has_no_blocking_or_unknown_core_fields(self):
        issues = validate_batch(complete_items())
        self.assertFalse(has_blocking_errors(issues))
        self.assertFalse(needs_manual_confirmation(issues))
        self.assertIn("amount_ok", codes(issues))

    def test_mixed_float_and_string_amounts_compare_by_decimal_value(self):
        rows = complete_items()
        rows[0].extracted.amount = 626.16
        rows[1].extracted.amount = "626.16"
        rows[2].extracted.amount = Decimal("626.16")
        self.assertFalse(has_blocking_errors(validate_batch(rows)))

    def test_one_cent_difference_is_blocking(self):
        rows = complete_items()
        rows[2].extracted.amount = "626.17"
        self.assertIn("amount_mismatch", codes(validate_batch(rows)))
        self.assertTrue(has_blocking_errors(validate_batch(rows)))

    def test_multiple_invoices_block_even_equal_amounts(self):
        rows = complete_items()
        rows.append(complete_items(invoice="second.pdf")[0])
        issues = validate_batch(rows)
        self.assertTrue(any(i.code == "multi_invoice" and i.is_error for i in issues))

    def test_later_payment_different_amount_is_not_ignored(self):
        rows = complete_items()
        rows.append(item("payment2.png", ImageCategory.PAYMENT, amount="1.00"))
        issues = validate_batch(rows)
        self.assertIn("amount_conflict_same_category", codes(issues))
        self.assertTrue(has_blocking_errors(issues))

    def test_later_order_different_amount_is_not_ignored(self):
        rows = complete_items()
        rows.append(item("order2.png", ImageCategory.ORDER, amount="626.15", address=ADDRESS))
        self.assertIn("amount_conflict_same_category", codes(validate_batch(rows)))

    def test_same_category_equal_amounts_are_allowed(self):
        rows = complete_items()
        rows.append(item("payment2.png", ImageCategory.PAYMENT, amount=626.16))
        self.assertFalse(has_blocking_errors(validate_batch(rows)))

    def test_later_invalid_amount_is_blocking(self):
        rows = complete_items()
        rows.append(item("payment2.png", ImageCategory.PAYMENT, amount="626.160"))
        self.assertIn("bad_amount_scale", codes(validate_batch(rows)))

    def test_missing_required_material_categories_are_blocking(self):
        for index, code in ((0, "missing_invoice"), (1, "missing_order"), (2, "missing_payment")):
            rows = complete_items()
            del rows[index]
            with self.subTest(code=code):
                self.assertTrue(any(i.code == code and i.is_error for i in validate_batch(rows)))

    def test_deselected_material_is_ignored(self):
        rows = complete_items()
        extra = complete_items(invoice="excluded.pdf")[0]
        extra.extracted.amount = "1.001"
        extra.selected = False
        rows.append(extra)
        self.assertFalse(has_blocking_errors(validate_batch(rows)))

    def test_partial_and_absent_amounts_require_manual_confirmation(self):
        rows = complete_items()
        rows[1].extracted.amount = rows[2].extracted.amount = None
        issues = validate_batch(rows)
        self.assertIn("amount_partial", codes(issues))
        self.assertTrue(needs_manual_confirmation(issues))
        rows[0].extracted.amount = None
        issues = validate_batch(rows)
        self.assertIn("amount_none", codes(issues))
        self.assertFalse(has_blocking_errors(issues))

    def test_unclassified_selected_material_warns(self):
        rows = complete_items() + [item("unknown.png", ImageCategory.UNKNOWN)]
        self.assertIn("unclassified", codes(validate_batch(rows)))
        self.assertTrue(needs_manual_confirmation(validate_batch(rows)))

    def test_missing_product_quantity_and_time_warn_without_core_manual_gate(self):
        rows = complete_items()
        for row in rows:
            row.extracted.product_name = row.extracted.quantity = row.extracted.pay_time = ""
        issues = validate_batch(rows)
        advisory = {"no_product_name", "no_quantity", "no_pay_time"}
        self.assertTrue(advisory <= codes(issues))
        self.assertTrue(advisory.isdisjoint(UNKNOWN_CORE_CODES))
        self.assertFalse(needs_manual_confirmation(issues))

    def test_unrelated_products_and_quantities_are_advisory(self):
        rows = complete_items()
        rows[0].extracted.product_name = "螺丝钉"
        rows[0].extracted.quantity = "100个"
        rows[1].extracted.product_name = "笔记本电脑"
        rows[1].extracted.quantity = "1台"
        issues = validate_batch(rows)
        advisory = {"product_mismatch_suspect", "quantity_mismatch_suspect"}
        self.assertTrue(advisory <= codes(issues))
        self.assertFalse(has_blocking_errors(issues))
        self.assertTrue(all(i.level == "warning" for i in issues if i.code in advisory))

    def test_related_product_and_quantity_unit_variants_not_flagged(self):
        rows = complete_items()
        rows[0].extracted.product_name = "实验耗材-螺丝钉"
        rows[1].extracted.product_name = "螺丝钉套装"
        self.assertNotIn("product_mismatch_suspect", codes(validate_batch(rows)))
        self.assertNotIn("quantity_mismatch_suspect", codes(validate_batch(rows)))


class PaymentDirectionSafetyTests(unittest.TestCase):
    # 保留 lead 原先的三项交易安全回归测试，并补充别名/方向校验。
    def test_known_refund_is_blocking(self):
        info = ExtractedInfo(amount=None, extra={
            "transaction_status": "refunded", "payment_direction": "refund", "raw_amount": "-626.16"
        })
        issues = validate_extracted(info, ImageCategory.PAYMENT)
        self.assertIn("payment_not_expense", codes(issues))
        self.assertTrue(has_blocking_errors(issues))

    def test_known_failed_and_pending_are_blocking(self):
        for status, expected in (("failed", "payment_not_expense"), ("pending", "payment_pending")):
            with self.subTest(status=status):
                issues = validate_extracted(ExtractedInfo(extra={
                    "transaction_status": status, "payment_direction": "expense"
                }), ImageCategory.PAYMENT)
                self.assertIn(expected, codes(issues))
                self.assertTrue(has_blocking_errors(issues))

    def test_successful_debit_not_blocked(self):
        for direction in ("expense", "debit", "outgoing", "支出"):
            with self.subTest(direction=direction):
                issues = validate_extracted(ExtractedInfo(amount="626.16", extra={
                    "transaction_status": "success", "payment_direction": direction
                }), ImageCategory.PAYMENT)
                self.assertFalse(has_blocking_errors(issues))

    def test_refund_income_and_credit_directions_block_with_positive_amount(self):
        for direction in ("refund", "income", "credit", "退款入账"):
            with self.subTest(direction=direction):
                issues = validate_extracted(ExtractedInfo(amount="626.16", extra={
                    "transaction_status": "success", "payment_direction": direction
                }), ImageCategory.PAYMENT)
                self.assertIn("payment_not_expense", codes(issues))
                self.assertTrue(has_blocking_errors(issues))

    def test_success_with_unknown_direction_is_blocking(self):
        issues = validate_extracted(ExtractedInfo(amount="626.16", extra={
            "transaction_status": "paid", "payment_direction": "unknown"
        }), ImageCategory.PAYMENT)
        self.assertIn("payment_direction_unknown", codes(issues))
        self.assertTrue(has_blocking_errors(issues))

    def test_failed_and_pending_status_aliases_block(self):
        for status in ("closed", "cancelled", "已退款", "交易失败", "处理中", "待支付"):
            with self.subTest(status=status):
                issues = validate_extracted(ExtractedInfo(amount="626.16", extra={
                    "transaction_status": status, "payment_direction": "expense"
                }), ImageCategory.PAYMENT)
                self.assertTrue(has_blocking_errors(issues))


class NamingTests(unittest.TestCase):
    def test_first_order_names(self):
        names = build_output_names(settings())
        self.assertEqual(names.folder_name, "20260927-张三-李四-实验采购")
        self.assertEqual(names.file_prefix, names.folder_name)
        self.assertEqual(names.invoice_pdf, names.file_prefix + "-电子发票.pdf")
        self.assertEqual(names.voucher_pdf, names.file_prefix + "-购买及支付凭证.pdf")
        self.assertNotIn("(1)", names.file_prefix)

    def test_second_order_date_numbering(self):
        names = build_output_names(settings(order_index=2))
        self.assertEqual(names.folder_name, "20260927-张三-李四-实验采购（当日第2笔订单）")
        self.assertEqual(names.file_prefix, "20260927(2)-张三-李四-实验采购")
        self.assertTrue(names.invoice_pdf.startswith("20260927(2)-"))
        self.assertTrue(names.voucher_pdf.startswith("20260927(2)-"))

    def test_later_order_numbering(self):
        names = build_output_names(settings(order_index=12))
        self.assertTrue(names.file_prefix.startswith("20260927(12)-"))
        self.assertTrue(names.folder_name.endswith("（当日第12笔订单）"))

    def test_windows_forbidden_characters_and_control_characters_are_removed(self):
        names = build_output_names(settings(name='A/B:C', advisor='D?E*F', project_name='G|H<>"\x00\n'))
        for value in (names.folder_name, names.invoice_pdf, names.voucher_pdf):
            with self.subTest(value=value):
                self.assertIsNone(re.search(r'[\\/:*?"<>|\x00-\x1f]', value))

    def test_windows_reserved_names_and_trailing_dots_are_safe(self):
        names = build_output_names(settings(name=" CON. ", advisor="LPT1", project_name="测试. "))
        self.assertIn("-_CON-", names.file_prefix)
        self.assertIn("-_LPT1-", names.file_prefix)
        self.assertTrue(names.folder_name.endswith("测试"))

    def test_date_format_and_impossible_days_rejected(self):
        for raw in ("", "2026-09-27", "2026092", "20260230", "20261301", "20250229"):
            with self.subTest(raw=raw), self.assertRaises(NamingError):
                validate_declare_date(raw)

    def test_valid_leap_day(self):
        self.assertEqual(validate_declare_date("20240229"), "20240229")

    def test_order_index_limits(self):
        self.assertEqual(validate_order_index(1), 1)
        self.assertEqual(validate_order_index(999), 999)
        for raw in (-1, 0, 1000, "abc", None):
            with self.subTest(raw=raw), self.assertRaises(NamingError):
                validate_order_index(raw)

    def test_long_project_is_bounded_and_extremely_long_profile_rejected(self):
        names = build_output_names(settings(project_name="项目" * 150))
        self.assertLessEqual(len(names.folder_name), 150)
        self.assertLessEqual(len(names.voucher_pdf), 200)
        with self.assertRaises(NamingError):
            build_output_names(settings(name="甲" * 150, advisor="乙" * 150, project_name="丙" * 150))


class LayoutTests(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.order = self.image("order.png", RED, (240, 480))
        self.pay = self.image("payment.png", BLUE, (240, 480))

    def test_default_a4_landscape_pixels_at_300_dpi(self):
        self.assertEqual(a4_landscape_pixels(), (round(297 / 25.4 * 300), round(210 / 25.4 * 300)))
        page = process_voucher_layout([self.order], [self.pay])
        self.assertEqual(page.size, a4_landscape_pixels(300))

    def assert_pair_centered(self, page, *, dpi, left=24, right=24, top=24, bottom=24, gap=4):
        order_box = color_bbox(page, RED)
        payment_box = color_bbox(page, BLUE)
        self.assertIsNotNone(order_box)
        self.assertIsNotNone(payment_box)
        boxes = sorted([order_box, payment_box])
        pair = ink_bbox(page)
        x0, y0 = mm_to_px(left, dpi), mm_to_px(top, dpi)
        x1, y1 = page.width - mm_to_px(right, dpi), page.height - mm_to_px(bottom, dpi)
        self.assertGreaterEqual(pair[0], x0)
        self.assertGreaterEqual(pair[1], y0)
        self.assertLessEqual(pair[2], x1)
        self.assertLessEqual(pair[3], y1)
        # Integer placement may differ by half a pixel from the exact center.
        self.assertLessEqual(abs((pair[0] + pair[2]) - (x0 + x1)), 1)
        self.assertLessEqual(abs((pair[1] + pair[3]) - (y0 + y1)), 1)
        self.assertEqual(boxes[1][0] - boxes[0][2], mm_to_px(gap, dpi))
        metrics = page.info["layout_metrics"]
        self.assertEqual(metrics["printable_box_pixels"], [x0, y0, x1 - x0, y1 - y0])
        self.assertEqual(metrics["pair_box_pixels"], [pair[0], pair[1], pair[2] - pair[0], pair[3] - pair[1]])
        for placement in metrics["placements"]:
            box = order_box if placement["role"] == "order" else payment_box
            self.assertEqual(placement["box_pixels"], [box[0], box[1], box[2] - box[0], box[3] - box[1]])
        return order_box, payment_box

    def test_default_millimetres_and_direction(self):
        cfg = resolve_layout()
        self.assertEqual([cfg[k] for k in ("layout_left_mm", "layout_right_mm", "layout_top_mm",
                                          "layout_bottom_mm", "layout_gap_mm")], [24, 24, 24, 24, 4])
        self.assertEqual(cfg["dpi"], 300)
        self.assertIs(cfg["layout_order_on_left"], True)
        self.assertEqual(cfg["layout_vertical_align"], "center")

    def test_layout_aliases_and_explicit_override_precedence(self):
        cfg = resolve_layout({"left_mm": 70, "dpi": 180, "order_on_left": False}, left_mm=82)
        self.assertEqual(cfg["layout_left_mm"], 82)
        self.assertEqual(cfg["dpi"], 180)
        self.assertFalse(cfg["layout_order_on_left"])
        self.assertEqual(resolve_layout({"dpi": 0})["dpi"], 300)

    def test_default_order_left_payment_right_and_pair_centered(self):
        for dpi in (72, 100, 300):
            with self.subTest(dpi=dpi):
                page = process_voucher_layout([self.order], [self.pay], dpi=dpi, crop_whitespace=False)
                order_box, payment_box = self.assert_pair_centered(page, dpi=dpi)
                self.assertLess(order_box[2], payment_box[0])

    def test_explicit_direction_overrides_default_and_layout(self):
        cases = (({"layout_order_on_left": False}, None, False),
                 ({"order_on_left": False}, None, False),
                 ({"layout_order_on_left": True}, False, False),
                 ({"layout_order_on_left": False}, True, True))
        for layout, override, order_left in cases:
            with self.subTest(layout=layout, override=override):
                page = process_voucher_layout([self.order], [self.pay], dpi=72,
                                              layout=layout, order_on_left=override, crop_whitespace=False)
                order_box, payment_box = self.assert_pair_centered(page, dpi=72)
                self.assertEqual(order_box[0] < payment_box[0], order_left)

    def test_default_binding_margin_is_white_without_gray_guide(self):
        page = process_voucher_layout([self.order], [self.pay], dpi=72)
        margin = page.crop((0, 0, mm_to_px(24, 72), page.height))
        self.assertIsNone(ink_bbox(margin))

    def test_explicit_mm_margins_bound_all_content(self):
        page = process_voucher_layout([self.order], [self.pay], dpi=100, left_mm=81, right_mm=62,
                                      top_mm=32, bottom_mm=29, gap_mm=6, crop_whitespace=False)
        left, top, right, bottom = ink_bbox(page)
        self.assertGreaterEqual(left, mm_to_px(81, 100))
        self.assertGreaterEqual(top, mm_to_px(32, 100))
        self.assertLessEqual(right, page.width - mm_to_px(62, 100))
        self.assertLessEqual(bottom, page.height - mm_to_px(29, 100))

    def test_unequal_sources_and_column_slack_use_actual_pair_center(self):
        cases = (
            ((900, 1200), (300, 1200), {}),
            ((900, 1200), (300, 1200), {"layout_column_ratio": 0.2}),
            ((300, 1200), (900, 1200), {"layout_column_ratio": 0.8}),
            ((1200, 300), (240, 1200), {"layout_adaptive": False}),
        )
        for index, (order_size, payment_size, options) in enumerate(cases):
            with self.subTest(index=index):
                order = self.image(f"unequal-order-{index}.png", RED, order_size)
                payment = self.image(f"unequal-payment-{index}.png", BLUE, payment_size)
                page = process_voucher_layout([order], [payment], dpi=100,
                                              layout=options, crop_whitespace=False)
                order_box, payment_box = self.assert_pair_centered(page, dpi=100)
                self.assertLess(order_box[2], payment_box[0])
                for box, source_size in ((order_box, order_size), (payment_box, payment_size)):
                    w, h = box[2] - box[0], box[3] - box[1]
                    sw, sh = source_size
                    self.assertLessEqual(abs(w * sh - h * sw), (sw + sh) / 2)
                    self.assertLessEqual(abs(box[1] + box[3] - page.height), 1)
                metrics = page.info["layout_metrics"]
                if "layout_column_ratio" in options:
                    self.assertEqual(metrics["column_ratio"], options["layout_column_ratio"])
                if index == 0:
                    self.assertEqual(metrics["column_ratio"], 0.75)
                if index in (1, 2):
                    self.assertNotAlmostEqual(metrics["rendered_column_ratio"], metrics["column_ratio"], places=2)

    def test_pair_centering_preserves_explicit_asymmetric_margin_rectangle(self):
        order = self.image("asymmetric-order.png", RED, (900, 1200))
        payment = self.image("asymmetric-payment.png", BLUE, (300, 1200))
        page = process_voucher_layout(
            [order], [payment], dpi=100, left_mm=52, right_mm=13, top_mm=37,
            bottom_mm=9, gap_mm=7, crop_whitespace=False,
            layout={"layout_column_ratio": 0.2},
        )
        self.assert_pair_centered(page, dpi=100, left=52, right=13, top=37, bottom=9, gap=7)
        # Asymmetric margins intentionally move the printable center away from the page center.
        box = ink_bbox(page)
        self.assertGreater(box[0] + box[2], page.width)
        self.assertGreater(box[1] + box[3], page.height)

    def test_explicit_top_alignment_preserves_horizontal_centering_and_margins(self):
        order = self.image("top-order.png", RED, (900, 1200))
        payment = self.image("top-payment.png", BLUE, (300, 1200))
        page = process_voucher_layout([order], [payment], dpi=100, crop_whitespace=False,
                                      layout={"layout_vertical_align": "top", "layout_column_ratio": 0.2})
        order_box, payment_box = color_bbox(page, RED), color_bbox(page, BLUE)
        self.assertEqual(order_box[1], mm_to_px(24, 100))
        self.assertEqual(payment_box[1], mm_to_px(24, 100))
        self.assertEqual(payment_box[0] - order_box[2], mm_to_px(4, 100))
        pair = ink_bbox(page)
        self.assertLessEqual(abs(pair[0] + pair[2] - page.width), 1)
        self.assertEqual(page.info["layout_metrics"]["vertical_align"], "top")

    def test_ratio_parameters_match_equivalent_millimetres(self):
        dpi = 180
        gap_px = 30
        by_ratio = process_voucher_layout(
            [self.order], [self.pay], dpi=dpi, left_margin_ratio=80 / 297,
            right_margin_ratio=60 / 297, top_margin_ratio=25 / 210,
            bottom_margin_ratio=20 / 210, gap=gap_px, crop_whitespace=False,
        )
        by_mm = process_voucher_layout(
            [self.order], [self.pay], dpi=dpi, left_mm=80, right_mm=60, top_mm=25,
            bottom_mm=20, gap_mm=gap_px / dpi * 25.4, crop_whitespace=False,
        )
        self.assertIsNone(ImageChops.difference(by_ratio, by_mm).getbbox())

    def test_explicit_mm_takes_priority_over_all_legacy_ratios(self):
        margins = {"left_mm": 61, "right_mm": 17, "top_mm": 32, "bottom_mm": 19, "gap_mm": 7}
        legacy = {"left_margin_ratio": 0.1, "right_margin_ratio": 0.1,
                  "top_margin_ratio": 0.1, "bottom_margin_ratio": 0.1, "gap": 40}
        expected = process_voucher_layout([self.order], [self.pay], dpi=72,
                                           crop_whitespace=False, **margins)
        for aliases in (margins, {"layout_" + k: v for k, v in margins.items()},
                        {k.replace("_mm", "_margin_mm") if k != "gap_mm" else k: v
                         for k, v in margins.items()}):
            with self.subTest(aliases=aliases):
                page = process_voucher_layout([self.order], [self.pay], dpi=72,
                                              crop_whitespace=False, layout=aliases, **legacy)
                self.assertIsNone(ImageChops.difference(page, expected).getbbox())
        explicit = process_voucher_layout([self.order], [self.pay], dpi=72,
                                          crop_whitespace=False, **margins, **legacy)
        self.assertIsNone(ImageChops.difference(explicit, expected).getbbox())

    def test_invalid_geometry_rejected_instead_of_clipping_or_overlapping(self):
        invalid = ({"left_mm": 200, "right_mm": 100}, {"top_mm": 120, "bottom_mm": 100},
                   {"gap_mm": 260}, {"gap": -1}, {"left_mm": -1},
                   {"right_mm": float("inf")}, {"top_mm": float("nan")},
                   {"gap_mm": float("nan")})
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                process_voucher_layout([self.order], [self.pay], dpi=72, **values)

    def test_two_pixel_printable_width_keeps_both_sources_inside_bounds(self):
        dpi = 72
        page_w, _ = a4_landscape_pixels(dpi)
        right_mm = (page_w - 2) / dpi * 25.4
        page = process_voucher_layout([self.order], [self.pay], dpi=dpi, crop_whitespace=False,
                                      left_mm=0, right_mm=right_mm, top_mm=0, bottom_mm=0, gap_mm=0,
                                      layout={"layout_column_ratio": 0.85})
        self.assertEqual(ink_bbox(page)[::2], (0, 2))
        self.assertEqual(color_bbox(page, RED)[0::2], (0, 1))
        self.assertEqual(color_bbox(page, BLUE)[0::2], (1, 2))

    def test_adjusting_dpi_changes_pixels_while_preserving_page_shape(self):
        low = process_voucher_layout([self.order], [self.pay], dpi=100)
        high = process_voucher_layout([self.order], [self.pay], dpi=200)
        self.assertLessEqual(abs(high.width - 2 * low.width), 1)
        self.assertLessEqual(abs(high.height - 2 * low.height), 1)
        self.assertAlmostEqual(high.width / high.height, 297 / 210, places=3)

    def test_same_side_multiple_images_follow_input_order(self):
        second = self.image("payment2.png", GREEN, (240, 480))
        page = process_voucher_layout([self.order], [self.pay, second], dpi=100, crop_whitespace=False)
        payment = next(p for p in page.info["layout_metrics"]["placements"] if p["role"] == "payment")
        left, top, w, h = payment["box_pixels"]
        x = left + w // 2
        self.assertEqual(page.getpixel((x, top + h // 4)), BLUE)
        self.assertEqual(page.getpixel((x, top + 3 * h // 4)), GREEN)
        reverse = process_voucher_layout([self.order], [second, self.pay], dpi=100, crop_whitespace=False)
        self.assertEqual(reverse.getpixel((x, top + h // 4)), GREEN)

    def test_crop_removes_only_outer_white_area(self):
        im = Image.new("RGB", (200, 160), WHITE)
        draw = ImageDraw.Draw(im)
        draw.rectangle((60, 40, 139, 119), fill=RED)
        draw.rectangle((80, 60, 119, 99), fill=WHITE)
        cropped = smart_crop_whitespace(im, padding=2)
        self.assertEqual(cropped.size, (84, 84))
        self.assertEqual(cropped.getpixel((2, 2)), RED)
        self.assertEqual(cropped.getpixel((42, 42)), WHITE)  # 内部白区保留
        self.assertEqual(im.size, (200, 160))  # 输入不被原地修改

    def test_white_and_edge_content_not_lost_by_crop(self):
        blank = Image.new("RGB", (80, 90), WHITE)
        self.assertEqual(smart_crop_whitespace(blank).size, blank.size)
        with_edges = Image.new("RGB", (80, 90), WHITE)
        with_edges.putpixel((0, 0), (0, 0, 0))
        with_edges.putpixel((79, 89), (0, 0, 0))
        cropped = smart_crop_whitespace(with_edges)
        self.assertEqual(cropped.size, with_edges.size)
        self.assertEqual(cropped.getpixel((79, 89)), (0, 0, 0))

    def test_fit_keeps_aspect_ratio_and_all_colored_content(self):
        source = Image.new("RGB", (80, 160), RED)
        fitted = fit_into_box(source, 100, 100)
        self.assertEqual(fitted.size, (100, 100))
        self.assertEqual(ink_bbox(fitted), (25, 0, 75, 100))
        self.assertEqual(fitted.getpixel((50, 99)), RED)

    def test_stitch_keeps_order_and_scales_proportionally(self):
        a = Image.new("RGB", (100, 50), RED)
        b = Image.new("RGB", (50, 50), BLUE)
        joined = stitch_vertical([a, b])
        self.assertEqual(joined.size, (100, 150))
        self.assertEqual(joined.getpixel((50, 25)), RED)
        self.assertEqual(joined.getpixel((50, 125)), BLUE)
        self.assertEqual(b.size, (50, 50))

    def test_original_image_bytes_unchanged_after_layout(self):
        before = (self.order.read_bytes(), self.pay.read_bytes())
        process_voucher_layout([self.order], [self.pay], dpi=72, crop_whitespace=True)
        self.assertEqual((self.order.read_bytes(), self.pay.read_bytes()), before)

    def test_alpha_is_composited_on_white(self):
        path = self.work / "alpha.png"
        with Image.new("RGBA", (20, 20), (200, 0, 0, 0)) as im:
            im.putpixel((10, 10), (*BLUE, 255))
            im.save(path, "PNG")
        rgb = open_rgb(path)
        self.assertEqual(rgb.mode, "RGB")
        self.assertEqual(rgb.getpixel((0, 0)), WHITE)
        self.assertEqual(rgb.getpixel((10, 10)), BLUE)

    def test_invoice_image_a4_portrait_and_landscape(self):
        portrait = prepare_invoice_image(self.order, dpi=72)
        landscape = prepare_invoice_image(self.order, dpi=72, portrait=False)
        self.assertEqual(portrait.size, a4_portrait_pixels(72))
        self.assertEqual(landscape.size, a4_landscape_pixels(72))
        self.assertEqual(portrait.getpixel((0, 0)), WHITE)

    def test_missing_either_side_is_rejected(self):
        for orders, pays in (([], [self.pay]), ([self.order], [])):
            with self.subTest(orders=orders, pays=pays), self.assertRaises(ValueError):
                process_voucher_layout(orders, pays, dpi=72)


class ExportTests(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.order = self.image("order.png", RED)
        self.pay = self.image("payment.png", BLUE)
        self.invoice = self.source_pdf()
        self.out = self.work / "export"
        self.out.mkdir()
        self.cfg = settings(self.out)

    def rows(self):
        return complete_items(self.invoice, self.order, self.pay)

    def assert_empty_output(self):
        self.assertEqual(list(self.out.iterdir()), [])

    def test_success_creates_exactly_two_pdfs_and_no_previews_or_report(self):
        result = build_output_package(self.rows(), self.cfg)
        self.assertTrue(result.success, result.message)
        self.assertEqual({p.name for p in result.folder_path.iterdir()},
                         {build_output_names(self.cfg).invoice_pdf, build_output_names(self.cfg).voucher_pdf})
        self.assertEqual(list(self.out.iterdir()), [result.folder_path])
        for pdf in (result.invoice_pdf, result.voucher_pdf):
            self.assertTrue(pdf.is_file())
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF-"))
        self.assertIsNone(result.preview_invoice)
        self.assertIsNone(result.preview_voucher)

    def test_original_pdf_is_byte_copy_without_rendering(self):
        # Explicit archival mode remains byte-identical; normal submissions now default to handwriting print layout.
        self.cfg.extra['invoice_layout_mode'] = 'original'
        before = self.invoice.read_bytes()
        with patch.object(pdf_generator, "prepare_invoice_image", side_effect=AssertionError("不应渲染原PDF")):
            result = build_output_package(self.rows(), self.cfg)
        self.assertTrue(result.success, result.message)
        self.assertEqual(result.invoice_pdf.read_bytes(), before)
        self.assertEqual(self.invoice.read_bytes(), before)
        self.assertNotIn("invoice_from_image", codes(result.issues))

    def test_generated_voucher_is_single_landscape_a4_page(self):
        path, preview, page = generate_voucher_pdf(
            [self.order], [self.pay], self.work / "voucher.pdf", settings=self.cfg
        )
        data = path.read_bytes()
        self.assertIsNone(preview)
        self.assertEqual(len(re.findall(rb"/Type\s*/Page\b", data)), 1)
        self.assertEqual(page.size, a4_landscape_pixels(72))
        sizes = pdf_page_sizes(data)
        self.assertEqual(len(sizes), 1)
        self.assertGreater(sizes[0][0], sizes[0][1])
        self.assertAlmostEqual(sizes[0][0], 297 / 25.4 * 72, delta=1)
        self.assertAlmostEqual(sizes[0][1], 210 / 25.4 * 72, delta=1)

    def test_300_dpi_pdf_has_a4_physical_size(self):
        self.cfg.extra["dpi"] = 300
        path, _, page = generate_voucher_pdf(
            [self.order], [self.pay], self.work / "voucher300.pdf", settings=self.cfg
        )
        self.assertEqual(page.size, a4_landscape_pixels(300))
        width, height = pdf_page_sizes(path.read_bytes())[0]
        self.assertAlmostEqual(width, 297 / 25.4 * 72, delta=0.3)
        self.assertAlmostEqual(height, 210 / 25.4 * 72, delta=0.3)

    def test_image_invoice_warning_recommends_original_pdf(self):
        invoice = self.image("invoice.png", GREEN, (400, 200))
        path, preview, issues = generate_invoice_pdf(invoice, self.work / "image-invoice.pdf", settings=self.cfg)
        self.assertTrue(path.read_bytes().startswith(b"%PDF-"))
        self.assertIsNone(preview)
        warning = next(i for i in issues if i.code == "invoice_from_image")
        self.assertEqual(warning.level, "warning")
        self.assertIn("原版 PDF", warning.message)
        self.assertIn("不能视为", warning.message)

    def test_image_invoice_requires_confirmation_and_cleans_staging(self):
        rows = self.rows()
        rows[0].path = self.image("invoice.png", GREEN)
        result = build_output_package(rows, self.cfg)
        self.assertFalse(result.success)
        self.assertIn("need_manual_confirm", codes(result.issues))
        self.assert_empty_output()
        result = build_output_package(rows, self.cfg, manual_confirmed=True)
        self.assertTrue(result.success, result.message)
        self.assertIn("invoice_from_image", codes(result.issues))
        self.assertEqual(len(list(result.folder_path.iterdir())), 2)

    def test_unknown_core_fields_block_until_manual_confirmation(self):
        rows = self.rows()
        rows[0].extracted.buyer_name = rows[0].extracted.taxpayer_id = ""
        result = build_output_package(rows, self.cfg)
        self.assertFalse(result.success)
        self.assertIn("need_manual_confirm", codes(result.issues))
        self.assert_empty_output()
        confirmed = build_output_package(rows, self.cfg, manual_confirmed=True)
        self.assertTrue(confirmed.success, confirmed.message)
        self.assertTrue({"no_buyer", "no_tax_id"} <= codes(confirmed.issues))

    def test_force_and_manual_never_override_known_error(self):
        rows = self.rows()
        rows[0].extracted.buyer_name = "个人"
        result = build_output_package(rows, self.cfg, force=True, manual_confirmed=True)
        self.assertFalse(result.success)
        self.assertIn("bad_buyer", codes(result.issues))
        self.assert_empty_output()

    def test_refund_cannot_export_even_with_force_manual_and_equal_positive_amounts(self):
        rows = self.rows()
        rows[2].extracted.extra = {"transaction_status": "refunded", "payment_direction": "refund"}
        result = build_output_package(rows, self.cfg, force=True, manual_confirmed=True)
        self.assertFalse(result.success)
        self.assertIn("payment_not_expense", codes(result.issues))
        self.assert_empty_output()

    def test_multiple_invoices_block_before_writing(self):
        rows = self.rows()
        rows.append(complete_items(invoice=self.invoice)[0])
        result = build_output_package(rows, self.cfg, force=True, manual_confirmed=True)
        self.assertFalse(result.success)
        self.assertIn("multi_invoice", codes(result.issues))
        self.assert_empty_output()

    def test_same_name_directory_never_overwritten(self):
        final = self.out / build_output_names(self.cfg).folder_name
        final.mkdir()
        sentinel = final / "existing.txt"
        sentinel.write_text("原有材料", encoding="utf-8")
        result = build_output_package(self.rows(), self.cfg, force=True, manual_confirmed=True)
        self.assertFalse(result.success)
        self.assertIn("folder_exists", codes(result.issues))
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "原有材料")
        self.assertEqual(list(self.out.iterdir()), [final])

    def test_failure_after_invoice_copy_removes_all_partial_files(self):
        with patch.object(pdf_generator, "_image_to_pdf", side_effect=OSError("磁盘写入失败")):
            result = build_output_package(self.rows(), self.cfg)
        self.assertFalse(result.success)
        self.assertIn("export_fail", codes(result.issues))
        self.assert_empty_output()

    def test_missing_input_file_fails_without_partial_package(self):
        rows = self.rows()
        rows[0].path = self.work / "missing.pdf"
        result = build_output_package(rows, self.cfg)
        self.assertFalse(result.success)
        self.assertIn("export_fail", codes(result.issues))
        self.assert_empty_output()

    def test_atomic_rename_happens_only_after_both_pdfs_exist(self):
        rename = pdf_generator.os.rename
        observations = []

        def inspect_then_rename(source, target):
            source, target = Path(source), Path(target)
            observations.append((source.parent, target.parent))
            self.assertFalse(target.exists())
            self.assertEqual(len(list(source.iterdir())), 2)
            self.assertTrue(all(p.suffix == ".pdf" for p in source.iterdir()))
            rename(source, target)

        with patch.object(pdf_generator.os, "rename", side_effect=inspect_then_rename):
            result = build_output_package(self.rows(), self.cfg)
        self.assertTrue(result.success, result.message)
        self.assertEqual(observations, [(self.out, self.out)])
        self.assertEqual(list(self.out.iterdir()), [result.folder_path])

    def test_rename_conflict_preserves_existing_directory_and_cleans_staging(self):
        final = self.out / build_output_names(self.cfg).folder_name

        def race_conflict(source, target):
            Path(target).mkdir()
            (Path(target) / "other.txt").write_text("other export", encoding="utf-8")
            raise FileExistsError("并发导出目标已存在")

        with patch.object(pdf_generator.os, "rename", side_effect=race_conflict):
            result = build_output_package(self.rows(), self.cfg)
        self.assertFalse(result.success)
        self.assertIn("folder_exists", codes(result.issues))
        self.assertEqual(list(self.out.iterdir()), [final])
        self.assertEqual((final / "other.txt").read_text(encoding="utf-8"), "other export")

    def test_settings_mm_layout_and_dpi_reach_export(self):
        self.cfg.extra = {**DEFAULT_LAYOUT, "layout_left_mm": 84, "layout_right_mm": 59,
                          "layout_top_mm": 25, "layout_bottom_mm": 22, "layout_gap_mm": 6,
                          "layout_order_on_left": True, "dpi": 100}
        with patch.object(pdf_generator, "process_voucher_layout", wraps=process_voucher_layout) as render:
            result = build_output_package(self.rows(), self.cfg)
        self.assertTrue(result.success, result.message)
        self.assertEqual(render.call_args.kwargs["layout"], self.cfg.extra)
        self.assertFalse(render.call_args.kwargs["draw_binding_guide"])

    def test_legacy_ratio_and_pdf_dpi_fallback_reach_export(self):
        self.cfg.extra = {}
        self.cfg.left_margin_ratio = 0.30
        self.cfg.pdf_dpi = 100
        with patch.object(pdf_generator, "process_voucher_layout", wraps=process_voucher_layout) as render:
            result = build_output_package(self.rows(), self.cfg)
        self.assertTrue(result.success, result.message)
        layout = render.call_args.kwargs["layout"]
        self.assertAlmostEqual(layout["layout_left_mm"], 297 * 0.30)
        self.assertEqual(layout["dpi"], 100)

    def test_second_order_output_uses_date_parentheses(self):
        self.cfg.order_index = 2
        result = build_output_package(self.rows(), self.cfg)
        self.assertTrue(result.success, result.message)
        self.assertTrue(result.folder_path.name.endswith("（当日第2笔订单）"))
        self.assertTrue(all(p.name.startswith("20260927(2)-") for p in result.folder_path.iterdir()))

    def test_invalid_profile_or_date_never_creates_package(self):
        for changes, expected in (({"name": ""}, "profile"), ({"declare_date": "20260230"}, "bad_naming")):
            cfg = settings(self.out, **changes)
            with self.subTest(changes=changes):
                result = build_output_package(self.rows(), cfg)
                self.assertFalse(result.success)
                self.assertIn(expected, codes(result.issues))
                self.assert_empty_output()


if __name__ == "__main__":
    unittest.main(verbosity=2)
