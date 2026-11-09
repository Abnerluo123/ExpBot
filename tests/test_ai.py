"""Safe local tests for the visual API client; no user files leave loopback."""

from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.settings import Settings
from app.core.ai_vision import (
    AIVisionClient,
    MAX_PARTS,
    PART_MAX_SIDE,
    _extract_json,
    _normalize_result,
    _tile_bounds,
)
from app.core.models import ImageCategory, ImageItem


class _ServerState:
    payload: dict | None = None
    path: str = ""
    authorization: str = ""
    status = 200
    body = {"choices": [{"message": {"content": json.dumps({
        "category": "invoice",
        "amount": "￥1,208.00",
        "merchant": "示例商户",
        "quantity": "2件",
        "date": "2026-11-09",
        "order_no": "ABC-123",
        "items": [{"name": "样品", "quantity": "2", "amount": "1208.00"}],
        "regions": [{"category": "invoice", "box": [0.1, 0.2, 0.5, 0.4]}],
        "confidence": 0.92,
    }, ensure_ascii=False)}}]}


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        state = _ServerState
        state.path = self.path
        state.authorization = self.headers.get("Authorization", "")
        length = int(self.headers.get("Content-Length", "0"))
        state.payload = json.loads(self.rfile.read(length).decode("utf-8"))
        encoded = json.dumps(state.body, ensure_ascii=False).encode("utf-8")
        self.send_response(state.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        pass


class AIVisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        _ServerState.payload = None
        _ServerState.path = ""
        _ServerState.authorization = ""
        _ServerState.status = 200
        _ServerState.body = {"choices": [{"message": {"content": json.dumps({
            "category": "invoice",
            "amount": "￥1,208.00",
            "address": "商户地址不应视作收货地址",
            "merchant": "示例商户",
            "quantity": "2件",
            "date": "2026-11-09",
            "order_no": "ABC-123",
            "items": [{"name": "样品", "quantity": "2", "amount": "1208.00"}],
            "regions": [{"category": "invoice", "box": [0.1, 0.2, 0.5, 0.4]}],
            "confidence": 0.92,
        }, ensure_ascii=False)}}]}

    def _client(self, base: str | None = None, key="dummy-test-key"):
        return AIVisionClient(Settings(
            ai_api_base=base or self.base,
            ai_api_key=key,
            ai_model="mock-vision",
            ai_vision_model="mock-vision",
        ))

    def _image(self, folder: Path, size=(600, 900)) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "local-test.png"
        Image.new("RGB", size, "white").save(path)
        return path

    def test_endpoint_resolution(self):
        cases = {
            "https://example.test": "https://example.test/v1/chat/completions",
            "https://example.test/v1": "https://example.test/v1/chat/completions",
            "https://example.test/compatibility-mode/v1": "https://example.test/compatibility-mode/v1/chat/completions",
            "https://example.test/api/v4": "https://example.test/api/v4/chat/completions",
            "https://example.test/custom/chat/completions": "https://example.test/custom/chat/completions",
        }
        for base, expected in cases.items():
            with self.subTest(base=base):
                self.assertEqual(self._client(base)._chat_url(), expected)

    def test_rejects_unsafe_url_forms(self):
        invalid = [
            "ftp://example.test",
            "http://example.test/api",
            "https://user:password@example.test/v1",
            "https://example.test/v1?token=secret",
        ]
        for base in invalid:
            with self.subTest(base=base), self.assertRaises(RuntimeError):
                self._client(base)._chat_url()

    def test_http_loopback_posts_visual_content_and_preserves_amount(self):
        path = self._image(self.test_dir)
        data = self._client().analyze_image(path)
        self.assertEqual(_ServerState.path, "/v1/chat/completions")
        self.assertEqual(_ServerState.authorization, "Bearer dummy-test-key")
        content = _ServerState.payload["messages"][1]["content"]
        images = [part for part in content if part["type"] == "image_url"]
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0]["image_url"]["url"].startswith(("data:image/png;base64,", "data:image/jpeg;base64,")))
        self.assertEqual(data["amount"], "￥1,208.00")
        self.assertEqual(data["address"], "")
        self.assertEqual(data["date"], "2026-11-09")
        self.assertEqual(data["order_id"], "ABC-123")
        self.assertEqual(data["regions"][0]["box"], [0.1, 0.2, 0.5, 0.4])

    def test_long_screenshot_uses_readable_bounded_tiles(self):
        tiles = _tile_bounds(1400, 12000)
        self.assertLessEqual(len(tiles), MAX_PARTS)
        self.assertTrue(all(max(tile.box[2] - tile.box[0], tile.box[3] - tile.box[1]) <= PART_MAX_SIDE for tile in tiles))
        xs = sorted({tile.box[0] for tile in tiles})
        ys = sorted({tile.box[1] for tile in tiles})
        self.assertGreaterEqual(len(ys), 2)

    def test_oversized_long_screenshot_is_rejected_not_downscaled(self):
        with self.assertRaisesRegex(RuntimeError, "最多 6 块"):
            _tile_bounds(7000, 7000)

    def test_normalizes_combined_regions_items_and_unknown_fields(self):
        result = _normalize_result({
            "category": "combined",
            "amount": 0,
            "invented_compliance_status": "approved",
            "items": [{"name": "商品", "quantity": "3", "amount": "0.00", "ignored": "x"}],
            "regions": [
                {"category": "invoice", "part": 1, "box": [0.2, 0.1, 0.4, 0.5]},
                {"category": "payment", "part": 2, "box": [0.1, 0.2, 0.5, 0.4]},
                {"category": "other", "box": [-1, 0, 1, 1]},
            ],
        }, [(100, 0, 100, 100), (0, 100, 200, 100)], (200, 200))
        self.assertEqual(result["category"], "other")
        self.assertEqual(result["amount"], "0")
        self.assertNotIn("invented_compliance_status", result)
        self.assertEqual(result["items"], [{"name": "商品", "quantity": "3", "amount": "0.00"}])
        self.assertEqual(result["regions"][0]["box"], [0.6, 0.05, 0.2, 0.25])
        self.assertEqual(result["regions"][0]["category"], "invoice")
        self.assertEqual(result["regions"][1]["box"], [0.1, 0.6, 0.5, 0.2])
        self.assertEqual(result["regions"][1]["category"], "payment")

    def test_payment_amount_preserves_sign_and_never_turns_refunds_into_expense(self):
        bounds = [(0, 0, 100, 100)]
        expense = _normalize_result({
            "category": "payment",
            "amount": "626.16",
            "raw_amount": "-626.16",
            "payment_direction": "expense",
            "transaction_status": "付款成功",
        }, bounds, (100, 100))
        self.assertEqual(expense["amount"], "626.16")
        self.assertEqual(expense["raw_amount"], "-626.16")
        self.assertEqual(expense["extra"]["payment_direction"], "expense")
        self.assertEqual(expense["extra"]["transaction_status"], "success")

        for direction, status in (("refund", "退款成功"), ("expense", "pending"), ("expense", "failed"), ("unknown", "unknown")):
            with self.subTest(direction=direction, status=status):
                result = _normalize_result({
                    "category": "payment",
                    "amount": "626.16",
                    "raw_amount": "626.16",
                    "payment_direction": direction,
                    "transaction_status": status,
                }, bounds, (100, 100))
                self.assertIsNone(result["amount"])
                self.assertEqual(result["raw_amount"], "626.16")

    def test_extracts_strict_json_object_only(self):
        self.assertEqual(_extract_json('{"amount":"10.00"}'), {"amount": "10.00"})
        with self.assertRaises(ValueError):
            _extract_json("Here is JSON: {\"amount\": 10}")
        with self.assertRaises(ValueError):
            _extract_json("[1, 2]")
        with self.assertRaises(ValueError):
            _extract_json('{"amount": NaN}')

    def test_http_errors_never_echo_body_or_api_key(self):
        secret = "dummy-secret-value"
        _ServerState.status = 401
        _ServerState.body = {"error": {"message": f"invalid key {secret}"}}
        with self.assertRaises(RuntimeError) as caught:
            self._client(key=secret).analyze_image(self._image(self.test_dir))
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn("invalid key", str(caught.exception))

    def test_api_key_in_model_response_is_redacted(self):
        secret = "dummy-secret-value"
        _ServerState.body = {"choices": [{"message": {"content": json.dumps({"summary": secret})}}]}
        result = self._client(key=secret).analyze_image(self._image(self.test_dir))
        self.assertNotIn(secret, result["_raw"])
        self.assertEqual(result["summary"], "[已隐藏]")

    def test_apply_to_item_keeps_string_amount_and_recommended_regions(self):
        item = ImageItem(path=self._image(self.test_dir))
        result = self._client().apply_to_item(item)
        self.assertIs(result, item)
        self.assertEqual(item.category, ImageCategory.INVOICE)
        self.assertEqual(item.extracted.amount, "￥1,208.00")
        self.assertEqual(item.extracted.extra["items"][0]["amount"], "1208.00")
        self.assertEqual(item.extracted.extra["regions"][0]["category"], "invoice")

    def test_pdf_render_failure_is_explicit_without_ocr_fallback(self):
        pdf = self.test_dir / "local-only.pdf"
        pdf.write_bytes(b"fake pdf")
        pdf.touch()
        client = self._client()
        with patch("app.core.pdf_utils.ensure_image_for_ai", side_effect=RuntimeError("private renderer detail")):
            with self.assertRaisesRegex(RuntimeError, "未使用 OCR") as caught:
                client.analyze_image(pdf)
        self.assertNotIn("private renderer detail", str(caught.exception))
        self.assertIsNone(_ServerState.payload)

    @property
    def test_dir(self):
        directory = ROOT / "output" / "_ai_test_tmp"
        directory.mkdir(parents=True, exist_ok=True)
        return directory


if __name__ == "__main__":
    unittest.main()
