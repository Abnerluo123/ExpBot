"""Batch planning tests against a loopback mock API and synthetic images only."""

from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.settings import Settings
from app.core.ai_batch import BatchVisionClient, _json_object
from app.core.models import ExtractedInfo


class _MockState:
    requests: list[dict[str, Any]] = []
    status = 200
    content = "{}"
    extraction_content = '{"category":"unknown","facts":{"summary":"合成材料中未见更多可确认字段"}}'
    finish_reason = "stop"
    responses = []


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(size).decode("utf-8"))
        _MockState.requests.append({"payload": payload, "authorization": self.headers.get("Authorization"), "path": self.path})
        system_prompt = payload["messages"][0]["content"]
        response_content = (_MockState.extraction_content
                            if "暂不做跨材料归组" in system_prompt
                            else _MockState.content)
        if _MockState.responses:
            response_content = _MockState.responses.pop(0)
        body = {"choices": [{"finish_reason": _MockState.finish_reason,
                             "message": {"content": response_content}}]}
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(_MockState.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        pass


def _json_response(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


class BatchVisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        _MockState.requests = []
        _MockState.responses = []
        _MockState.status = 200
        _MockState.finish_reason = "stop"
        _MockState.extraction_content = _json_response({"category": "unknown", "facts": {"summary": "合成材料中未见更多可确认字段"}})
        _MockState.content = _json_response({"groups": [], "unassigned": [], "warnings": []})

    def _client(self, key: str = "batch-dummy-key") -> BatchVisionClient:
        return BatchVisionClient(Settings(ai_api_base=self.base, ai_api_key=key,
                                         ai_model="local-vision", ai_vision_model="local-vision"))

    def _image(self, folder: Path, name: str, label: str | None = None) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.png"
        image = Image.new("RGB", (120, 180), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((8, 12, 110, 165), outline="#234567", width=3)
        if label:
            draw.text((15, 25), label, fill="black")
        image.save(path)
        return path

    def _record(self, folder: Path, material_id: str, *, facts=None, category="unknown"):
        return {
            "id": material_id,
            "name": f"本地合成材料-{material_id}",
            "path": self._image(folder, material_id, material_id),
            "width": 120,
            "height": 180,
            "extracted": facts if facts is not None else ExtractedInfo(),
            "category": category,
        }

    @property
    def test_dir(self):
        directory = ROOT / "output" / "_ai_batch_test_tmp"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def test_plan_orders_sends_multiple_images_for_joint_reasoning(self):
        records = [
            self._record(self.test_dir, "inv-1", facts={"amount": "626.16", "merchant": "示例店", "product_name": "USB线", "order_no": "O-7"}, category="invoice"),
            self._record(self.test_dir, "pay-1", facts={"amount": "626.16", "merchant": "示例店", "product_name": "USB线", "order_no": "O-7", "payment_direction": "expense", "transaction_status": "success"}, category="payment"),
            self._record(self.test_dir, "order-1", facts={"amount": "626.16", "merchant": "示例店", "product_name": "USB线", "order_no": "O-7"}, category="order"),
        ]
        _MockState.content = _json_response({"groups": [{
            "key": "order-O7", "title": "示例店 USB线", "material_ids": ["inv-1", "pay-1", "order-1"], "confidence": 0.91,
            "evidence": [
                {"material_id": "inv-1", "visible_fact": "发票可见示例店和USB线，金额626.16，订单号O-7"},
                {"material_id": "pay-1", "visible_fact": "支付成功记录显示示例店和订单号O-7"},
                {"material_id": "order-1", "visible_fact": "订单详情显示USB线及订单号O-7"},
            ],
            "roles": [{"material_id": "inv-1", "category": "invoice"}, {"material_id": "pay-1", "category": "payment"}, {"material_id": "order-1", "category": "order"}],
            "stitch_sets": [],
        }], "unassigned": [], "warnings": []})
        progress = []
        result = self._client().plan_orders(records, lambda done, total, message: progress.append((done, total, message)))
        self.assertEqual(len(_MockState.requests), 1)
        request = _MockState.requests[0]
        self.assertEqual(request["authorization"], "Bearer batch-dummy-key")
        self.assertEqual(request["path"], "/v1/chat/completions")
        user_content = request["payload"]["messages"][1]["content"]
        self.assertEqual(sum(part.get("type") == "image_url" for part in user_content), 3)
        joined = json.dumps(user_content, ensure_ascii=False)
        self.assertIn('\\"material_id\\":\\"inv-1\\"', joined)
        self.assertIn("联合归组", request["payload"]["messages"][0]["content"])
        self.assertEqual(result["groups"][0]["material_ids"], ["inv-1", "pay-1", "order-1"])
        self.assertEqual(result["unassigned"], [])
        self.assertEqual(progress[-1][0], progress[-1][1])

    def test_same_amount_different_order_ids_keep_unlinked_payment_unassigned(self):
        facts_a = {"amount": "626.16", "order_no": "ORDER-A", "merchant": "甲店", "product_name": "USB线"}
        facts_b = {"amount": "626.16", "order_no": "ORDER-B", "merchant": "乙店", "product_name": "鼠标"}
        records = [self._record(self.test_dir, "order-a", facts=facts_a, category="order"),
                   self._record(self.test_dir, "order-b", facts=facts_b, category="order"),
                   self._record(self.test_dir, "pay-ambiguous", facts={"amount": "626.16"}, category="payment")]
        _MockState.content = _json_response({
            "groups": [
                {"key": "a", "title": "甲店订单", "material_ids": ["order-a"], "confidence": .9,
                 "evidence": [{"material_id": "order-a", "visible_fact": "订单号ORDER-A，商户甲店"}], "roles": [], "stitch_sets": []},
                {"key": "b", "title": "乙店订单", "material_ids": ["order-b"], "confidence": .9,
                 "evidence": [{"material_id": "order-b", "visible_fact": "订单号ORDER-B，商户乙店"}], "roles": [], "stitch_sets": []},
                {"key": "pay", "title": "付款626.16", "material_ids": ["pay-ambiguous"], "confidence": .8,
                 "evidence": [{"material_id": "pay-ambiguous", "visible_fact": "支付金额626.16"}], "roles": [], "stitch_sets": []},
            ], "unassigned": [], "warnings": []})
        result = self._client().plan_orders(records)
        self.assertEqual(result["unassigned"], ["pay-ambiguous"])
        self.assertEqual({mid for group in result["groups"] for mid in group["material_ids"]}, {"order-a", "order-b"})
        self.assertTrue(any("同金额" in warning for warning in result["warnings"]))

    def test_amount_only_cross_image_proposal_is_discarded(self):
        records = [self._record(self.test_dir, "a", facts={"amount": "10.00"}),
                   self._record(self.test_dir, "b", facts={"amount": "10.00"})]
        _MockState.content = _json_response({"groups": [{
            "key": "same-amount", "title": "相同金额订单", "material_ids": ["a", "b"], "confidence": .99,
            "evidence": [{"material_id": "a", "visible_fact": "订单金额10.00元"}, {"material_id": "b", "visible_fact": "订单金额10.00元"}],
            "roles": [], "stitch_sets": [],
        }], "unassigned": [], "warnings": []})
        result = self._client().plan_orders(records)
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["unassigned"], ["a", "b"])

    def test_more_than_twelve_inputs_keeps_every_id_and_batches_contact_sheet_images(self):
        records = [self._record(self.test_dir, f"id-{index:02d}", facts={"product_name": f"商品{index}", "merchant": f"店{index}"}) for index in range(13)]
        _MockState.content = _json_response({"groups": [], "unassigned": [record["id"] for record in records], "warnings": []})
        result = self._client().plan_orders(records)
        self.assertEqual(len(_MockState.requests), 1)
        content = _MockState.requests[0]["payload"]["messages"][1]["content"]
        self.assertEqual(sum(part.get("type") == "image_url" for part in content), 2)
        self.assertEqual(set(result["unassigned"]), {record["id"] for record in records})
        text = json.dumps(content, ensure_ascii=False)
        for record in records:
            self.assertIn(record["id"], text)

    def test_missing_facts_over_twelve_are_extracted_then_jointly_sent(self):
        records = [self._record(self.test_dir, f"unknown-{index:02d}") for index in range(13)]
        _MockState.content = _json_response({"groups": [], "unassigned": [record["id"] for record in records], "warnings": []})
        result = self._client().plan_orders(records)
        self.assertEqual(set(result["unassigned"]), {record["id"] for record in records})
        self.assertEqual(len(_MockState.requests), 14)
        extraction_requests = [request for request in _MockState.requests
                               if "暂不做跨材料归组" in request["payload"]["messages"][0]["content"]]
        self.assertEqual(len(extraction_requests), 13)
        self.assertTrue(all(request["authorization"] == "Bearer batch-dummy-key" for request in _MockState.requests))
        final_content = _MockState.requests[-1]["payload"]["messages"][1]["content"]
        for record in records:
            self.assertIn(record["id"], json.dumps(final_content, ensure_ascii=False))

    def test_rejects_unknown_ids_duplicate_assignment_and_incomplete_evidence(self):
        record = self._record(self.test_dir, "real-id", facts={"merchant": "店"})
        invalid_plans = [
            {"groups": [{"key": "g", "title": "x", "material_ids": ["invented"], "confidence": .5,
                         "evidence": [{"material_id": "invented", "visible_fact": "事实"}], "roles": [], "stitch_sets": []}], "unassigned": [], "warnings": []},
            {"groups": [{"key": "g", "title": "x", "material_ids": ["real-id"], "confidence": .5,
                         "evidence": [], "roles": [], "stitch_sets": []}], "unassigned": ["real-id"], "warnings": []},
        ]
        for response in invalid_plans:
            with self.subTest(response=response):
                _MockState.content = _json_response(response)
                with self.assertRaises(RuntimeError):
                    self._client().plan_orders([record])

    def test_stitch_plan_uses_all_input_images_and_validates_order_crop_and_joins(self):
        records = [self._record(self.test_dir, f"scroll-{i}", category="long_screenshot") for i in (2, 3, 1)]
        _MockState.content = _json_response({
            "ordered_ids": ["scroll-1", "scroll-2", "scroll-3"],
            "crops": [{"id": "scroll-2", "box": [0, .05, 1, .9]}],
            "joins": [
                {"above_id": "scroll-1", "below_id": "scroll-2", "overlap_hint": .18, "reason": "下图开头续接上图底部商品列表"},
                {"above_id": "scroll-2", "below_id": "scroll-3", "overlap_hint": .12, "reason": "同一滚动页内容连续"},
            ], "confidence": .87, "warnings": ["固定页头可能遮挡部分内容"]})
        result = self._client().plan_stitch(records)
        payload_content = _MockState.requests[0]["payload"]["messages"][1]["content"]
        self.assertEqual(sum(part.get("type") == "image_url" for part in payload_content), 3)
        self.assertIn("固定页头、标签栏", _MockState.requests[0]["payload"]["messages"][0]["content"])
        self.assertEqual(result["ordered_ids"], ["scroll-1", "scroll-2", "scroll-3"])
        self.assertEqual(result["crops"][0]["box"], [0, .05, 1, .9])
        self.assertEqual(len(result["joins"]), 2)
        self.assertTrue(any("裁切框仅为建议" in warning for warning in result["warnings"]))

    def test_stitch_view_change_preserves_model_evidence_and_old_joins_default_to_unknown(self):
        records = [self._record(self.test_dir, "upper"), self._record(self.test_dir, "lower")]
        for change in (None, "masked_to_visible", "content_conflict"):
            with self.subTest(change=change):
                join = {"above_id": "upper", "below_id": "lower", "overlap_hint": .2, "reason": "相同版式交叠区"}
                if change:
                    join.update(view_change=change, view_reason="下图同位置号码有明确差异，需要对照原图")
                _MockState.content = _json_response({"ordered_ids": ["upper", "lower"], "joins": [join],
                    "crops": [], "confidence": .9, "warnings": []})
                result = self._client().plan_stitch(records)
                self.assertEqual(result["joins"][0]["view_change"], change or "unknown")
                self.assertTrue(result["joins"][0]["view_reason"])
                self.assertEqual(result["confidence"], .5 if change == "content_conflict" else .9)

    def test_stitch_plan_rejects_unknown_or_missing_ids_and_invalid_boxes(self):
        records = [self._record(self.test_dir, "top"), self._record(self.test_dir, "bottom")]
        invalid = [
            {"ordered_ids": ["top", "missing"], "crops": [], "joins": [], "confidence": .5, "warnings": []},
            {"ordered_ids": ["top", "bottom"], "crops": [{"id": "bottom", "box": [0, 0, 2, 1]}],
             "joins": [{"above_id": "top", "below_id": "bottom", "overlap_hint": .5, "reason": "x"}], "confidence": .5, "warnings": []},
        ]
        for plan in invalid:
            with self.subTest(plan=plan):
                _MockState.content = _json_response(plan)
                with self.assertRaises(RuntimeError):
                    self._client().plan_stitch(records)

    def test_errors_are_sanitized_and_no_http_redirects_followed(self):
        records = [self._record(self.test_dir, "one")]
        _MockState.status = 401
        secret = "private-dummy-secret"
        _MockState.content = secret
        with self.assertRaises(RuntimeError) as caught:
            self._client(key=secret).plan_orders(records)
        self.assertNotIn(secret, str(caught.exception))

        _MockState.requests = []
        _MockState.status = 302
        with self.assertRaises(RuntimeError):
            self._client().plan_orders(records)
        self.assertEqual(len(_MockState.requests), 1)

    def test_json_decoder_rejects_non_json_duplicate_keys_and_non_object(self):
        self.assertEqual(_json_object('{"groups":[]}'), {"groups": []})
        for invalid in ('prefix {"x": 1}', '{"x":1,"x":2}', '[1,2]', '{"x":NaN}'):
            with self.subTest(invalid=invalid), self.assertRaises(Exception):
                _json_object(invalid)

    def test_extracts_extra_fields_without_mutating_record(self):
        extracted = ExtractedInfo(amount="626.16", merchant="店A", extra={
            "raw_amount": "-626.16", "payment_direction": "expense",
            "transaction_status": "success", "date": "2026-11-09",
        })
        record = self._record(self.test_dir, "extra-fields", facts=extracted, category="payment")
        _MockState.content = _json_response({"groups": [], "unassigned": ["extra-fields"], "warnings": []})
        result = self._client().plan_orders([record])
        self.assertEqual(result["unassigned"], ["extra-fields"])
        payload_text = json.dumps(_MockState.requests[0]["payload"], ensure_ascii=False)
        self.assertIn("-626.16", payload_text)
        self.assertIn("expense", payload_text)
        self.assertEqual(extracted.amount, "626.16")
        self.assertEqual(extracted.extra["raw_amount"], "-626.16")

    def test_plan_layout_adapts_two_different_side_images_and_preserves_hard_left_margin(self):
        records = [
            self._record(self.test_dir, "order-side", facts={"product_name": "A4纸", "quantity": "2箱"}, category="order"),
            self._record(self.test_dir, "payment-side", facts={"amount": "258.00", "merchant": "纸品店"}, category="payment"),
        ]
        _MockState.content = _json_response({"layout": {
            "layout_column_ratio": .3, "layout_vertical_align": "center",
            "layout_left_mm": 60, "layout_right_mm": 18, "layout_top_mm": 20,
            "layout_bottom_mm": 18, "layout_gap_mm": 5,
        }, "crops": [], "legibility": "good", "warnings": [],
            "reason": "支付侧放在左栏且其内容可用较窄宽度，payment 左侧比例为 0.3，订单在右侧获得更多空间；两图保持原比例。"})
        record_before = dict(records[0]["extracted"])
        result = self._client().plan_layout(records)
        request = _MockState.requests[0]
        content = request["payload"]["messages"][1]["content"]
        self.assertEqual(sum(part.get("type") == "image_url" for part in content), 2)
        self.assertEqual(result["layout"]["layout_column_ratio"], .3)
        self.assertEqual(result["layout"]["layout_left_mm"], 60)
        self.assertGreaterEqual(result["layout"]["layout_right_mm"], 24)
        self.assertEqual(result["layout"]["layout_vertical_align"], "center")
        self.assertTrue(any("硬约束" in warning for warning in result["warnings"]))
        self.assertEqual(records[0]["extracted"], record_before)
        self.assertIn("四边默认各24mm", request["payload"]["messages"][0]["content"])
        content_json = json.dumps(content, ensure_ascii=False)
        self.assertIn("min_left_mm", content_json)
        self.assertIn("order_on_left\\\":true", content_json)
        self.assertIn("左栏的宽度份额", request["payload"]["messages"][0]["content"])
        self.assertIn("payment 左侧比例为 0.3", result["reason"])

    def test_plan_layout_order_on_left_changes_role_mapping_not_ratio_semantics(self):
        records = [
            self._record(self.test_dir, "order-left", facts={"product_name": "电源适配器"}, category="order"),
            self._record(self.test_dir, "payment-right", facts={"amount": "80.00"}, category="payment"),
        ]
        _MockState.content = _json_response({"layout": {
            "layout_column_ratio": .7, "layout_vertical_align": "top",
            "layout_left_mm": 77, "layout_right_mm": 66, "layout_top_mm": 30,
            "layout_bottom_mm": 26, "layout_gap_mm": 4,
        }, "crops": [], "legibility": "good", "warnings": [],
            "reason": "order_on_left启用，订单栏在左且分配可打印宽度70%，比例始终表示左栏宽度。"})
        result = self._client().plan_layout(records, constraints={"order_on_left": True, "min_gap_mm": 4})
        sent = json.dumps(_MockState.requests[0]["payload"]["messages"][1]["content"], ensure_ascii=False)
        self.assertEqual(result["layout"]["layout_column_ratio"], .7)
        self.assertIn("order_on_left\\\":true", sent)
        self.assertIn("min_gap_mm\\\":4", sent)
        self.assertIn("比例始终表示左栏宽度", result["reason"])

    def test_plan_layout_enforces_invoice_top_and_bottom_minimums_and_crop_is_only_advice(self):
        invoice = self._record(self.test_dir, "invoice", facts={"amount": "100.00", "buyer_name": "学校"}, category="invoice")
        _MockState.content = _json_response({"layout": {
            "layout_column_ratio": .5, "layout_vertical_align": "top",
            "layout_left_mm": 80, "layout_right_mm": 10, "layout_top_mm": 10,
            "layout_bottom_mm": 12, "layout_gap_mm": 4,
        }, "crops": [{"id": "invoice", "box": [.02, .02, .96, .96]}],
            "legibility": "review", "warnings": [],
            "reason": "单张发票按原比例放置，保留票面主体并检查纵向边距。"})
        before = dict(invoice["extracted"])
        result = self._client().plan_layout([invoice])
        self.assertEqual(result["layout"]["layout_top_mm"], 24)
        self.assertEqual(result["layout"]["layout_bottom_mm"], 24)
        self.assertEqual(result["crops"][0]["box"], [.02, .02, .96, .96])
        self.assertTrue(any("24 mm" in warning for warning in result["warnings"]))
        self.assertTrue(any("仅是可选建议" in warning for warning in result["warnings"]))
        self.assertEqual(invoice["extracted"], before)

    def test_plan_layout_marks_poor_when_locked_margins_leave_tiny_columns(self):
        records = [self._record(self.test_dir, "narrow-order", category="order"),
                   self._record(self.test_dir, "narrow-payment", category="payment")]
        _MockState.content = _json_response({"layout": {
            "layout_column_ratio": .15, "layout_vertical_align": "top",
            "layout_left_mm": 110, "layout_right_mm": 80, "layout_top_mm": 30,
            "layout_bottom_mm": 26, "layout_gap_mm": 25,
        }, "crops": [], "legibility": "good", "warnings": [],
            "reason": "硬边距留下的可打印宽度不足以清晰容纳两栏。"})
        result = self._client().plan_layout(records, constraints={
            "min_left_mm": 110, "min_right_mm": 80, "min_gap_mm": 25,
        })
        self.assertEqual(result["legibility"], "poor")
        self.assertTrue(any("人工调整/拆分" in warning for warning in result["warnings"]))
        self.assertEqual(result["layout"]["layout_left_mm"], 110)

    def test_plan_layout_rejects_invalid_constraints_or_nonfinite_model_values(self):
        records = [self._record(self.test_dir, "single", category="invoice")]
        for constraints in ({"min_left_mm": float("nan")}, {"min_left_mm": 111}, {"min_gap_mm": True}):
            with self.subTest(constraints=constraints), self.assertRaises(RuntimeError):
                self._client().plan_layout(records, constraints)
        _MockState.content = _json_response({"layout": {
            "layout_column_ratio": float("inf"), "layout_vertical_align": "top",
            "layout_left_mm": 77, "layout_right_mm": 8, "layout_top_mm": 24,
            "layout_bottom_mm": 24, "layout_gap_mm": 3,
        }, "crops": [], "legibility": "good", "warnings": [], "reason": "合成测试。"})
        with self.assertRaises(RuntimeError):
            self._client().plan_layout(records)

    def test_border_planning_sends_all_eight_images_and_only_returns_advice(self):
        records = [self._record(self.test_dir, f"border-{i}") for i in range(8)]
        records[0]["border_candidate"] = {"box": [.05, .1, .9, .8], "margins_px": [6, 18, 6, 18],
                                           "ignored_instruction": "untrusted instructions"}
        before = [Path(record["path"]).read_bytes() for record in records]
        _MockState.content = _json_response({"materials": [{"material_id": record["id"],
            "box": [.05, .1, .9, .8], "has_border": i == 0,
            "reason": "四周连续近黑外沿无字" if i == 0 else "暗色内容/票据线不能确认为外边，保留整图"}
            for i, record in enumerate(records)], "warnings": []})
        client = self._client()
        result = client.plan_borders(records)
        payload = _MockState.requests[0]["payload"]
        content = payload["messages"][1]["content"]
        self.assertEqual(sum(part["type"] == "image_url" for part in content), 8)
        self.assertIn("上、下、左、右", payload["messages"][0]["content"])
        self.assertIn("任何文字", payload["messages"][0]["content"])
        sent_text = " ".join(part["text"] for part in content if part["type"] == "text")
        self.assertIn("border_candidates", sent_text)
        self.assertNotIn("untrusted instructions", sent_text)
        self.assertEqual([row["material_id"] for row in result["materials"]], [row["id"] for row in records])
        self.assertEqual(result["materials"][0]["box"], [.05, .1, .9, .8])
        self.assertEqual(result["materials"][1]["box"], [0, 0, 1, 1])
        self.assertEqual(before, [Path(record["path"]).read_bytes() for record in records])
        with self.assertRaises(RuntimeError):
            client.plan_borders(records + [self._record(self.test_dir, "ninth")])
        self.assertEqual(len(_MockState.requests), 1)

    def test_border_missing_id_repairs_once_using_original_images_or_fails_complete(self):
        records = [self._record(self.test_dir, "frame-a"), self._record(self.test_dir, "frame-b")]
        row = lambda mid: {"material_id": mid, "box": [0, 0, 1, 1], "has_border": False, "reason": "无法确认黑外边"}
        _MockState.responses = [_json_response({"materials": [row("frame-a")], "warnings": []}),
                                _json_response({"materials": [row("frame-a"), row("frame-b")], "warnings": []})]
        result = self._client().plan_borders(records)
        self.assertEqual(len(result["materials"]), 2)
        first, repair = [request["payload"] for request in _MockState.requests]
        self.assertEqual(repair["messages"][:2], first["messages"])
        self.assertEqual(repair["messages"][-1]["role"], "system")
        self.assertIn("frame-b", repair["messages"][-1]["content"])
        _MockState.requests = []
        _MockState.content = _json_response({"materials": [row("frame-a")], "warnings": []})
        with self.assertRaisesRegex(RuntimeError, "缺少输入ID"):
            self._client().plan_borders(records)
        self.assertEqual(len(_MockState.requests), 2)

    def test_mixed_regions_covers_multiple_materials_and_keeps_uncertain_images_whole(self):
        records = [self._record(self.test_dir, "mixed"), self._record(self.test_dir, "unclear")]
        _MockState.content = _json_response({"materials": [
            {"material_id": "mixed", "regions": [{"category": "order", "box": [0, 0, .5, 1], "reason": "左侧完整订单"},
                                                 {"category": "payment", "box": [.5, 0, .5, 1], "reason": "右侧完整付款"}]},
            {"material_id": "unclear", "regions": [], "reason": "边界不清保留原图"}], "warnings": []})
        result = self._client().plan_regions(records)
        self.assertEqual(len(result["materials"][0]["regions"]), 2)
        self.assertEqual(result["materials"][1]["regions"], [])
        content = _MockState.requests[0]["payload"]["messages"][1]["content"]
        self.assertEqual(sum(part["type"] == "image_url" for part in content), 2)

    def test_semantic_aliases_amount_evidence_and_suborder_ids_do_not_reject_visual_groups(self):
        records = [self._record(self.test_dir, "main", facts={"merchant": "深圳嘉立创科技集团股份有限公司", "order_no": "SMT2610291455"}),
                   self._record(self.test_dir, "sub", facts={"merchant": "深圳嘉立创科技集团股份有限公司", "order_no": "Y2"}),
                   self._record(self.test_dir, "single", facts={"amount": "10.00"})]
        _MockState.content = _json_response({"groups": [
            {"key": "pcb", "title": "PCB采购", "material_ids": ["main", "sub"], "confidence": .9,
             "evidence": [{"material_id": "main", "visible_fact": "同一嘉立创PCBA采购页面，主单明细与下面商品段连续"},
                          {"material_id": "sub", "visible_fact": "嘉立创子单，商品段与主单页面连续且时间相符"}]},
            {"key": "amount", "title": "单张材料", "material_ids": ["single"], "confidence": .5,
             "evidence": [{"material_id": "single", "visible_fact": "付款金额10元"}]}],
            "unassigned": [], "warnings": []})
        result = self._client().plan_orders(records)
        self.assertEqual([row["material_ids"] for row in result["groups"]], [["main", "sub"], ["single"]])
        self.assertEqual(result["unassigned"], [])
        self.assertTrue(any("子单" in warning for warning in result["warnings"]))

    def test_layout_geometry_comes_from_program_math_and_uses_all_materials(self):
        records = [self._record(self.test_dir, "payment", category="payment"),
                   self._record(self.test_dir, "order", category="order")]
        _MockState.content = _json_response({"layout": {"layout_column_ratio": .48, "layout_vertical_align": "top",
            "layout_left_mm": 77, "layout_right_mm": 8, "layout_top_mm": 24,
            "layout_bottom_mm": 24, "layout_gap_mm": 4}, "crops": [], "legibility": "good",
            "reason": "两图实际长宽比接近，支付左栏略窄可保持近似可读字号。", "warnings": [],
            "geometry": {"column_space_width_mm": 123}})
        result = self._client().plan_layout(records, {"min_left_mm": 77, "min_right_mm": 8, "min_top_mm": 24, "min_bottom_mm": 24})
        geometry = result["geometry"]
        self.assertEqual(geometry["printable_width_mm"], 212)
        self.assertEqual(geometry["column_space_width_mm"], 208)
        self.assertAlmostEqual(geometry["left_column_width_mm"], 99.84)
        manifest_text = _MockState.requests[0]["payload"]["messages"][1]["content"][0]["text"]
        supplied = json.loads(manifest_text[manifest_text.index("{"):manifest_text.rindex("}") + 1])
        self.assertEqual(supplied["geometry"]["column_space_width_mm"], 208)

    def test_progress_and_limits(self):
        records = [self._record(self.test_dir, "top"), self._record(self.test_dir, "bottom")]
        _MockState.content = _json_response({"ordered_ids": ["top", "bottom"], "crops": [], "joins": [], "confidence": .4, "warnings": []})
        progress = []
        result = self._client().plan_stitch(records, lambda done, total, message: progress.append((done, total, message)))
        self.assertEqual(result["confidence"], .4)
        self.assertEqual(progress[-1][0], progress[-1][1])
        for invalid_records in ([], [records[0]], records * 6):
            with self.subTest(count=len(invalid_records)), self.assertRaises(RuntimeError):
                self._client().plan_stitch(invalid_records)


if __name__ == "__main__":
    unittest.main()
