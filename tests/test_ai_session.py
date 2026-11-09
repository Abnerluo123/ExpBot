"""Loopback-only session tests. Every image here is synthetic, never a user file."""

from __future__ import annotations

import base64
import copy
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.settings import Settings
from app.core.ai_session import BATCH_SIZE, SESSION_PROMPT, ConversationVisionClient


class _State:
    calls = []
    status = 200
    statuses = []
    finish_reason = "stop"
    responder = None
    source_facts = {}


def _latest(payload):
    content = next(message["content"] for message in reversed(payload["messages"]) if message["role"] == "user")
    return json.loads(next(part["text"] for part in content if part["type"] == "text"))


def _result(payload):
    manifest = _latest(payload)
    for row in manifest["new_materials"]:
        _State.source_facts[row["id"]] = copy.deepcopy(row["original_facts"])
    ids = manifest["known_material_ids"]
    return {"materials": [{"material_id": mid, "category": "unknown", "group_key": "",
                           "facts": copy.deepcopy(_State.source_facts.get(mid, {"amount": None, "extra": {}})),
                           "evidence": ["合成图中的边框与测试字符，未发现足够订单关联信息。"]} for mid in ids],
            "groups": [], "unassigned": list(ids), "trash": [], "warnings": []}


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(size).decode("utf-8"))
        _State.calls.append({"path": self.path, "authorization": self.headers.get("Authorization"), "payload": payload})
        status = _State.statuses.pop(0) if _State.statuses else _State.status
        if status != 200:
            encoded = b'{"error":{"message":"private-body dummy-private-key"}}'
        else:
            result = _State.responder(payload) if _State.responder is not None else _result(payload)
            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            encoded = json.dumps({"choices": [{"finish_reason": _State.finish_reason, "message": {"content": text}}],
                                  "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16}}).encode("utf-8")
        self.send_response(status)
        if status == 302:
            self.send_header("Location", "/must-not-follow")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass


class ConversationVisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}/v1"
        cls.directory = ROOT / "output" / "_ai_session_tests"
        cls.directory.mkdir(parents=True, exist_ok=True)
        cls.image = cls.directory / "synthetic.png"
        image = Image.new("RGB", (180, 240), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 15, 170, 220), outline="#174bdd", width=3)
        draw.text((30, 40), "SYNTHETIC 1234", fill="black")
        image.save(cls.image)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        _State.calls = []
        _State.status = 200
        _State.statuses = []
        _State.finish_reason = "stop"
        _State.responder = None
        _State.source_facts = {}
        self.client = ConversationVisionClient(Settings(ai_api_base=self.base, ai_api_key="dummy-private-key",
                                                       ai_model="local-visual", ai_vision_model="local-visual"))

    def records(self, count, offset=0, facts=None):
        return [{"id": f"m-{index:03d}", "name": f"synthetic-{index}", "path": self.image,
                 "category": "unknown", "width": 180, "height": 240,
                 "extracted": copy.deepcopy(facts or {})} for index in range(offset, offset + count)]

    @staticmethod
    def image_count(payload):
        return sum(part.get("type") == "image_url" for msg in payload["messages"]
                   if isinstance(msg["content"], list) for part in msg["content"])

    def test_eight_images_then_remainder_replays_full_text_with_only_new_images(self):
        first = self.client.analyze_batch(self.records(BATCH_SIZE), profile={"address": "实验楼130实验室"})
        first_snapshot = copy.deepcopy(first["session"])
        self.assertEqual(self.image_count(_State.calls[0]["payload"]), 8)
        self.assertEqual(len(first["result"]["materials"]), 8)
        second = self.client.analyze_batch(self.records(2, 8), first["session"], final=True)
        self.assertEqual(first["session"], first_snapshot)
        self.assertEqual(self.image_count(_State.calls[1]["payload"]), 2)
        wire = _State.calls[1]["payload"]["messages"]
        self.assertEqual(wire[2]["content"], first["session"]["messages"][2]["content"])
        self.assertEqual(wire[1]["content"][0]["text"], first["session"]["messages"][1]["content"][0]["text"])
        self.assertEqual(len(second["result"]["materials"]), 10)
        self.assertEqual(second["session"]["revision"], 2)
        self.assertEqual(len(second["session"]["ledger"]), 10)
        persisted = json.dumps(second["session"], ensure_ascii=False)
        self.assertNotIn("data:image", persisted)
        self.assertIn("image_ref", persisted)
        for msg in _State.calls[0]["payload"]["messages"]:
            if isinstance(msg["content"], list):
                for part in msg["content"]:
                    if part.get("type") == "image_url":
                        self.assertEqual(part["image_url"]["detail"], "high")
        self.assertNotIn("thinking", _State.calls[1]["payload"])

    def test_empty_feedback_round_can_regroup_old_materials_without_resending_images(self):
        first = self.client.analyze_batch(self.records(2))
        def regroup(payload):
            result = _result(payload)
            for material in result["materials"]:
                material["category"] = "order"
                material["group_key"] = "g-new"
            result["unassigned"] = []
            result["groups"] = [{"key": "g-new", "title": "合成关联组", "material_ids": ["m-000", "m-001"],
                                 "checks": [{"code": "missing_invoice", "status": "review", "message": "未见发票。", "material_ids": []}],
                                 "stitch_sets": []}]
            return result
        _State.responder = regroup
        second = self.client.analyze_batch([], first["session"], feedback="这两张为同一订单，请复核。", final=True)
        self.assertEqual(self.image_count(_State.calls[-1]["payload"]), 0)
        self.assertEqual(second["result"]["groups"][0]["material_ids"], ["m-000", "m-001"])
        self.assertIn("这两张为同一订单", _State.calls[-1]["payload"]["messages"][-1]["content"][0]["text"])
        self.assertEqual(first["result"]["unassigned"], ["m-000", "m-001"])

    def test_failure_keeps_original_session_and_retry_can_send_same_new_batch(self):
        first = self.client.analyze_batch(self.records(1))
        before = copy.deepcopy(first["session"])
        _State.status = 401
        with self.assertRaises(RuntimeError) as caught:
            self.client.analyze_batch(self.records(1, 1), first["session"])
        self.assertEqual(first["session"], before)
        self.assertNotIn("dummy-private-key", str(caught.exception))
        self.assertNotIn("private-body", str(caught.exception))
        _State.status = 200
        retry = self.client.analyze_batch(self.records(1, 1), first["session"])
        self.assertEqual(len(retry["session"]["ledger"]), 2)

    def test_limits_duplicate_ids_missing_ids_unknown_ids_and_cross_group_duplication_fail(self):
        with self.assertRaises(RuntimeError):
            self.client.analyze_batch(self.records(9))
        with self.assertRaises(RuntimeError):
            self.client.analyze_batch([])
        first = self.client.analyze_batch(self.records(1))
        with self.assertRaises(RuntimeError):
            self.client.analyze_batch(self.records(1), first["session"])
        snapshot = copy.deepcopy(first["session"])
        def omitted(payload):
            result = _result(payload)
            result["materials"] = result["materials"][1:]
            return result
        _State.responder = omitted
        with self.assertRaises(RuntimeError):
            self.client.analyze_batch(self.records(1, 1), first["session"])
        self.assertEqual(first["session"], snapshot)
        def unknown(payload):
            result = _result(payload)
            result["unassigned"].append("invented")
            return result
        _State.responder = unknown
        with self.assertRaises(RuntimeError):
            self.client.analyze_batch([], first["session"], final=True)
        self.assertEqual(first["session"], snapshot)

    def test_address_semantics_can_pass_abbreviation_without_rewriting_original_fact(self):
        def semantic_address(payload):
            result = _result(payload)
            material = result["materials"][0]
            material["category"] = "order"
            material["group_key"] = "g1"
            result["unassigned"] = []
            result["groups"] = [{"key": "g1", "title": "订单材料", "material_ids": ["m-000"],
                                 "checks": [{"code": "address", "status": "pass", "message": "上下文为同一校区，实验楼130指同栋楼130实验室。", "material_ids": ["m-000"]}],
                                 "stitch_sets": []}]
            return result
        _State.responder = semantic_address
        source = self.records(1, facts={"address": "实验楼130"})
        result = self.client.analyze_batch(source, profile={"address": "示例园区东区实验楼130室"})
        self.assertEqual(result["result"]["materials"][0]["facts"]["address"], "实验楼130")
        self.assertEqual(result["result"]["groups"][0]["checks"][0]["status"], "pass")
        self.assertEqual(source[0]["extracted"]["address"], "实验楼130")

    def test_model_cannot_replace_source_address_to_manufacture_a_pass(self):
        def rewritten(payload):
            result = _result(payload)
            material = result["materials"][0]
            material.update(category="order", group_key="g1")
            material["facts"]["address"] = "标准地址不得冒充图中原文"
            result["unassigned"] = []
            result["groups"] = [{"key": "g1", "title": "订单", "material_ids": ["m-000"],
                                 "checks": [{"code": "address", "status": "pass", "message": "地址通过。", "material_ids": ["m-000"]}], "stitch_sets": []}]
            return result
        _State.responder = rewritten
        result = self.client.analyze_batch(self.records(1, facts={"address": "图中原始地址"}))["result"]
        self.assertEqual(result["materials"][0]["facts"]["address"], "图中原始地址")
        self.assertEqual(result["groups"][0]["checks"][0]["status"], "review")
        self.assertTrue(result["warnings"])

    def test_mixed_material_can_preserve_payment_order_regions_and_group_key(self):
        def mixed(payload):
            result = _result(payload)
            material = result["materials"][0]
            material.update(category="other", group_key="g1")
            material["facts"]["extra"] = {"regions": [{"category": "order", "box": [0, 0, 1, .6]},
                                                         {"category": "payment", "box": [0, .6, 1, .4]}],
                                            "parts": [{"category": "order", "summary": "上方订单"}, {"category": "payment", "summary": "下方付款"}]}
            result["unassigned"] = []
            result["groups"] = [{"key": "g1", "title": "合并材料", "material_ids": ["m-000"], "checks": [], "stitch_sets": []}]
            return result
        _State.responder = mixed
        result = self.client.analyze_batch(self.records(1))["result"]
        material = result["materials"][0]
        self.assertEqual(material["category"], "other")
        self.assertEqual(material["group_key"], "g1")
        self.assertEqual(material["facts"]["extra"]["regions"][1]["box"], [0, .6, 1, .4])

    def test_complete_history_is_not_silently_truncated(self):
        first = self.client.analyze_batch(self.records(1))
        before = copy.deepcopy(first["session"])
        calls_before = len(_State.calls)
        with patch("app.core.ai_session.MAX_TRANSCRIPT_BYTES", 100):
            with self.assertRaises(RuntimeError) as caught:
                self.client.analyze_batch([], first["session"], feedback="继续复核")
        self.assertEqual(first["session"], before)
        self.assertEqual(len(_State.calls), calls_before)
        self.assertIn("未丢弃", str(caught.exception))

    def test_connection_uses_synthetic_visual_challenge_and_cannot_pass_ping_only(self):
        mime, data, expected = self.client._connection_challenge()
        with Image.open(BytesIO(data)) as image:
            self.assertEqual(image.size, (640, 170))
        _State.responder = lambda payload: dict(expected)
        with patch.object(self.client, "_connection_challenge", return_value=(mime, data, expected)):
            result = self.client.test_connection()
        self.assertTrue(result["connected"])
        self.assertTrue(result["vision_supported"])
        payload = _State.calls[-1]["payload"]
        self.assertEqual(self.image_count(payload), 1)
        text = " ".join(part["text"] for message in payload["messages"] if isinstance(message["content"], list)
                        for part in message["content"] if part["type"] == "text")
        self.assertNotIn(expected["digits"], text)
        _State.responder = lambda payload: {"digits": "not-the-visible-digits", "color": "wrong"}
        result = self.client.test_connection()
        self.assertTrue(result["connected"])
        self.assertFalse(result["vision_supported"])

    def test_key_echo_is_removed_from_persisted_full_assistant_and_errors(self):
        def echo(payload):
            result = _result(payload)
            result["warnings"] = ["dummy-private-key"]
            result["extra_explanation"] = "dummy-private-key"
            return result
        _State.responder = echo
        result = self.client.analyze_batch(self.records(1))
        self.assertNotIn("dummy-private-key", json.dumps(result))
        _State.status = 302
        before = copy.deepcopy(result["session"])
        count_before = len(_State.calls)
        with self.assertRaises(RuntimeError):
            self.client.analyze_batch([], result["session"], final=True)
        self.assertEqual(len(_State.calls), count_before + 1)
        self.assertEqual(result["session"], before)

    def test_explicit_revisit_sends_old_image_without_duplicate_ledger_or_materials(self):
        records = self.records(2)
        first = self.client.analyze_batch(records)
        before = copy.deepcopy(first["session"])
        second = self.client.analyze_batch([records[1]], first["session"], revisit=True,
                                           feedback="请重新看m-001确认模糊字段。", final=True)
        self.assertEqual(len(second["session"]["ledger"]), 2)
        self.assertEqual(len(second["result"]["materials"]), 2)
        self.assertEqual(self.image_count(_State.calls[-1]["payload"]), 1)
        manifest = _latest(_State.calls[-1]["payload"])
        self.assertEqual(manifest["new_materials"], [])
        self.assertEqual(manifest["revisited_material_ids"], ["m-001"])
        self.assertEqual(first["session"], before)
        self.assertEqual(second["session"]["messages"][2], first["session"]["messages"][2])
        self.assertNotIn("data:image", json.dumps(second["session"]))

    def test_revisit_requires_unchanged_original_path_and_digest(self):
        original_path = self.directory / "immutable-revisit.png"
        image = Image.new("RGB", (80, 100), "blue")
        image.save(original_path)
        record = self.records(1)[0]
        record["path"] = original_path
        first = self.client.analyze_batch([record])
        snapshot = copy.deepcopy(first["session"])
        alternative_path = self.directory / "other-revisit.png"
        image.save(alternative_path)
        moved = dict(record, path=alternative_path)
        with self.assertRaisesRegex(RuntimeError, "原路径或摘要"):
            self.client.analyze_batch([moved], first["session"], revisit=True)
        Image.new("RGB", (80, 100), "red").save(original_path)
        with self.assertRaisesRegex(RuntimeError, "原路径或摘要"):
            self.client.analyze_batch([record], first["session"], revisit=True)
        self.assertEqual(first["session"], snapshot)

    def test_output_budget_grows_only_for_official_deepseek_and_truncation_does_not_commit(self):
        official = ConversationVisionClient(Settings(ai_api_key="synthetic-dummy"))
        self.assertEqual(official._output_budget(8), 16384)
        self.assertEqual(official._output_budget(80), 56000)
        self.assertEqual(official._output_budget(240), 98304)
        self.assertEqual(self.client._output_budget(240), 16384)
        first = self.client.analyze_batch(self.records(1))
        before = copy.deepcopy(first["session"])
        _State.finish_reason = "length"
        with self.assertRaisesRegex(RuntimeError, "输出被截断"):
            self.client.analyze_batch(self.records(1, 1), first["session"])
        self.assertEqual(first["session"], before)

    def test_eight_distinct_inputs_are_eight_individual_images_not_contact_sheets(self):
        records = self.records(8)
        expected_colors = []
        for index, record in enumerate(records):
            color = (20 * index, 10 + 15 * index, 30 + 12 * index)
            path = self.directory / f"eight-distinct-{index}.png"
            Image.new("RGB", (100 + index, 150 + index), color).save(path)
            record["path"] = path
            expected_colors.append(color)
        self.client.analyze_batch(records)
        payload = _State.calls[-1]["payload"]
        images = [part["image_url"] for part in payload["messages"][-1]["content"] if part["type"] == "image_url"]
        self.assertEqual(len(images), 8)
        for index, image in enumerate(images):
            image_bytes = base64.b64decode(image["url"].split(",", 1)[1])
            with Image.open(BytesIO(image_bytes)) as decoded:
                self.assertEqual(decoded.size, (100 + index, 150 + index))
                self.assertEqual(decoded.getpixel((0, 0)), expected_colors[index])

    def test_timeout_retry_preserves_successful_history_and_retries_same_batch(self):
        first = self.client.analyze_batch(self.records(1))
        before = copy.deepcopy(first["session"])
        next_batch = self.records(1, 1)
        with patch("app.core.ai_session.urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = TimeoutError("private timeout dummy-private-key")
            with self.assertRaisesRegex(RuntimeError, "超时") as caught:
                self.client.analyze_batch(next_batch, first["session"])
            self.assertEqual(opener.return_value.open.call_count, 2)
        self.assertEqual(self.client.request_stats["transient_retries"], 1)
        self.assertEqual(self.client.request_stats["correction_turns"], 0)
        self.assertNotIn("dummy-private-key", str(caught.exception))
        self.assertNotIn("private timeout", str(caught.exception))
        self.assertEqual(first["session"], before)
        second = self.client.analyze_batch(next_batch, first["session"])
        self.assertEqual(len(second["session"]["ledger"]), 2)
        self.assertEqual(second["session"]["messages"][2], first["session"]["messages"][2])
        self.assertEqual(self.image_count(_State.calls[-1]["payload"]), 1)

    def test_one_background_json_repair_keeps_original_images_and_only_commits_final_answer(self):
        def repair(payload):
            return "BROKEN-FIRST-ANSWER dummy-private-key" if len(_State.calls) == 1 else _result(payload)
        _State.responder = repair
        outcome = self.client.analyze_batch(self.records(8))
        self.assertEqual(len(_State.calls), 2)
        first, corrected = [call["payload"] for call in _State.calls]
        self.assertEqual(corrected["messages"][:2], first["messages"])
        self.assertEqual(self.image_count(corrected), 8)
        self.assertEqual(corrected["messages"][-1]["role"], "system")
        self.assertIn("JSON", corrected["messages"][-1]["content"])
        self.assertNotIn("dummy-private-key", corrected["messages"][-2]["content"])
        stored = outcome["session"]
        self.assertEqual(stored["revision"], 1)
        self.assertEqual(len(stored["messages"]), 3)
        self.assertNotIn("BROKEN-FIRST-ANSWER", json.dumps(stored))
        self.assertNotIn("data:image", json.dumps(stored))
        self.assertEqual(len(outcome["result"]["materials"]), 8)
        self.assertEqual(self.client.request_stats["correction_turns"], 1)
        self.assertEqual(self.client.request_stats["total_tokens"], 32)
        self.assertNotIn("dummy-private-key", json.dumps(self.client.request_stats))

    def test_missing_cumulative_id_repairs_once_and_a_second_bad_answer_never_commits(self):
        first = self.client.analyze_batch(self.records(8))
        snapshot = copy.deepcopy(first["session"])
        calls_before = len(_State.calls)
        def omit_once(payload):
            result = _result(payload)
            if len(_State.calls) == calls_before + 1:
                result["materials"] = result["materials"][1:]
            return result
        _State.responder = omit_once
        second = self.client.analyze_batch(self.records(1, 8), first["session"])
        self.assertEqual(len(_State.calls) - calls_before, 2)
        self.assertEqual(len(second["result"]["materials"]), 9)
        self.assertEqual(first["session"], snapshot)
        _State.responder = lambda payload: {"materials": [], "groups": [], "unassigned": [], "trash": []}
        calls_before = len(_State.calls)
        with self.assertRaisesRegex(RuntimeError, "材料覆盖"):
            self.client.analyze_batch(self.records(1, 8), first["session"])
        self.assertEqual(len(_State.calls) - calls_before, 2)
        self.assertEqual(first["session"], snapshot)

    def test_minor_group_key_type_error_gets_one_background_field_repair(self):
        def responder(payload):
            result = _result(payload)
            if len(_State.calls) == 1:
                result["materials"][0]["group_key"] = None
            return result
        _State.responder = responder
        result = self.client.analyze_batch(self.records(1, facts={"extra": {"invoice_no": "原票号001"}}))
        self.assertEqual(len(_State.calls), 2)
        repair = _State.calls[-1]["payload"]["messages"][-1]
        self.assertEqual(repair["role"], "system")
        self.assertIn("group_key", repair["content"])
        self.assertEqual(result["result"]["materials"][0]["group_key"], "")
        self.assertEqual(result["result"]["materials"][0]["facts"]["extra"]["invoice_no"], "原票号001")

    def test_old_system_prompt_is_preserved_and_current_protocol_is_added_on_wire(self):
        first = self.client.analyze_batch(self.records(1))
        first["session"]["messages"][0]["content"] = "旧版系统协议：保留原材料事实。"
        snapshot = copy.deepcopy(first["session"])
        second = self.client.analyze_batch(self.records(1, 1), first["session"])
        wire = _State.calls[-1]["payload"]["messages"]
        self.assertIn("旧版系统协议", wire[0]["content"])
        self.assertIn(SESSION_PROMPT, wire[0]["content"])
        self.assertEqual(wire[2], snapshot["messages"][2])
        self.assertEqual(second["session"]["messages"][:3], snapshot["messages"])
        self.assertEqual(first["session"], snapshot)

    def test_confirmed_expense_sign_is_normalized_with_raw_preserved_but_refunds_are_not(self):
        cases = [("success", "expense", "-626.16", "626.16"),
                 ("退款成功", "退款", "-626.16", None),
                 ("pending", "expense", "-626.16", None),
                 ("unknown", "unknown", "626.16", None)]
        for status, direction, amount, expected in cases:
            with self.subTest(status=status, direction=direction):
                def payment(payload):
                    result = _result(payload)
                    result["materials"][0].update(category="payment", facts={"amount": amount, "extra": {
                        "transaction_status": status, "payment_direction": direction}})
                    return result
                _State.responder = payment
                facts = self.client.analyze_batch(self.records(1))["result"]["materials"][0]["facts"]
                self.assertEqual(facts["amount"], expected)
                self.assertEqual(facts["extra"]["raw_amount"], amount)

    def test_transient_http_errors_retry_once_and_fatal_codes_never_retry(self):
        with patch("app.core.ai_batch.time.sleep"):
            for code in (429, 503):
                with self.subTest(code=code):
                    before = len(_State.calls)
                    _State.statuses = [code, 200]
                    result = self.client.analyze_batch(self.records(1))
                    self.assertEqual(len(result["result"]["materials"]), 1)
                    self.assertEqual(len(_State.calls) - before, 2)
            for code, expected_calls in ((401, 1), (404, 1), (429, 2), (503, 2)):
                with self.subTest(code=code, terminal=True):
                    before = len(_State.calls)
                    _State.status = code
                    with self.assertRaisesRegex(RuntimeError, f"HTTP {code}"):
                        self.client.analyze_batch(self.records(1))
                    self.assertEqual(len(_State.calls) - before, expected_calls)
        self.assertEqual(self.client.request_stats["correction_turns"], 0)

    def test_defaults_use_official_deepseek_model_but_remain_editable(self):
        settings = Settings()
        self.assertEqual(settings.ai_api_base, "https://api.deepseek.com")
        self.assertEqual(settings.ai_model, "deepseek-flash")
        self.assertEqual(settings.ai_vision_model, "deepseek-flash")
        settings.ai_vision_model = "my-custom-visual-model"
        self.assertEqual(ConversationVisionClient(settings).model, "my-custom-visual-model")


if __name__ == "__main__":
    unittest.main()
