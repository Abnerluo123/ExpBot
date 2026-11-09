"""Source-preserving history tombstones; all documents are isolated test fixtures."""
from __future__ import annotations

import copy
import io
import json
import shutil
import unittest
import uuid
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from app import flow_engine as flow
from app.config.settings import Settings
from app.server import Handler, Workspace


class HistoryDeleteTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / "output" / ("history-delete-" + uuid.uuid4().hex[:12])
        self.root.mkdir(parents=True, exist_ok=False)
        self.ws = Workspace(self.root / "data")
        self.ws.settings = Settings(name="TEST", advisor="TEST", project_name="SYNTHETIC_NOT_FOR_CLAIM",
                                    declare_date="20261109", output_dir=str(self.root / "exports"))
        self.public = flow.create_run(self.ws, {"profile": {"declare_date": "20261109"}})
        self.rid = self.public["id"]
        self.run = self.ws.state["runs"][self.rid]

    def tearDown(self):
        assert self.root.resolve().parent == (Path(__file__).resolve().parents[1] / "output").resolve()
        shutil.rmtree(self.root)

    def add_material(self):
        buffer = io.BytesIO()
        Image.new("RGB", (40, 60), "#ccddee").save(buffer, "PNG")
        material = self.ws.import_bytes(buffer.getvalue(), "SYNTHETIC-ONLY.png", self.run["inbox_order_id"])
        flow.add_material(self.ws, self.rid, material["id"])
        return material["id"]

    def add_export(self, eid="synthetic-export", **extra):
        folder = self.root / "exports" / eid
        folder.mkdir(parents=True)
        paths = {}
        for kind in ("invoice", "voucher"):
            path = folder / (kind + ".pdf")
            Image.new("RGB", (40, 60), "white").save(path, "PDF")
            paths[kind] = str(path)
        entry = {"id": eid, "run_id": self.rid, "order_id": self.run["inbox_order_id"],
                 "folder": str(folder), "created": "2026-11-09T00:00:00", **paths,
                 "invoice_name": "invoice.pdf", "voucher_name": "voucher.pdf", **extra}
        self.ws.state["exports"].append(entry)
        self.run["exports"].append(copy.deepcopy(entry))
        return entry

    def delete(self):
        return flow.delete_run(self.ws, {"run_id": self.rid, "revision": self.run["revision"]})

    def http_post(self, operation, data, cookie=True, token=True):
        # Exercise the real HTTP handler/auth/route without sending any requests.
        body = json.dumps(data).encode("utf-8")
        handler = Handler.__new__(Handler)
        handler.server = SimpleNamespace(workspace=self.ws, token="SYNTHETIC-LOCAL-TOKEN", server_port=12345)
        handler.command = "POST"
        handler.path = "/api/flow/" + operation
        handler.request_version = "HTTP/1.1"
        handler.requestline = "POST " + handler.path + " HTTP/1.1"
        handler.close_connection = True
        handler.headers = Message()
        handler.headers["Host"] = "127.0.0.1:12345"
        handler.headers["Content-Type"] = "application/json"
        handler.headers["Content-Length"] = str(len(body))
        if cookie:
            handler.headers["Cookie"] = "qingbao_session=" + handler.server.token
        if token:
            handler.headers["X-App-Token"] = handler.server.token
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.do_POST()
        headers, payload = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
        return int(headers.split(b" ", 2)[1]), json.loads(payload)

    def test_collect_delete_hides_active_run_and_preserves_originals(self):
        mid = self.add_material()
        material = copy.deepcopy(self.ws.material(mid))
        path = Path(material["original"])
        source = path.read_bytes()
        revision = self.run["revision"]
        with patch.object(flow, "ConversationVisionClient", side_effect=AssertionError("no API calls")), \
                patch.object(flow, "BatchVisionClient", side_effect=AssertionError("no API calls")):
            result = self.delete()
        self.assertEqual(result, {"runs": [], "active_run_id": None})
        self.assertTrue(self.run["deleted_at"])
        self.assertEqual(self.run["revision"], revision + 1)
        self.assertEqual(self.run["stage"], "collect")
        self.assertEqual(self.ws.material(mid), material)
        self.assertEqual(path.read_bytes(), source)
        self.assertIn(self.rid, self.ws.state["runs"])
        self.assertEqual(self.ws.public_state()["flow"], result)
        for operation in (flow.open_run, flow.update_run, flow.start_review, flow.start_make, flow.start_export):
            with self.subTest(operation=operation.__name__), self.assertRaisesRegex(ValueError, "已删除"):
                operation(self.ws, {"run_id": self.rid, "confirmed": True, "consent": True})
        with self.assertRaisesRegex(ValueError, "已删除"):
            flow.public_run(self.ws, self.rid)
        restored = Workspace(self.ws.root)
        self.assertEqual(flow.public_flow_state(restored), result)
        self.assertEqual(restored.state["runs"][self.rid]["deleted_at"], self.run["deleted_at"])
        self.assertEqual(Path(restored.material(mid)["original"]).read_bytes(), source)

    def test_done_delete_preserves_exports_and_hides_legacy_entries(self):
        mid = self.add_material()
        entry = self.add_export()
        legacy = self.add_export("legacy-copy")
        legacy.pop("run_id")
        by_id = self.add_export("legacy-id-only", order_id="old-order")
        by_id.pop("run_id")
        excluded_order = self.ws.add_order({"declare_date": "20261109"})
        self.run["_excluded_groups"] = [{"order_id": excluded_order["id"]}]
        excluded = copy.deepcopy(entry)
        excluded.update(id="legacy-excluded", order_id=excluded_order["id"])
        excluded.pop("run_id")
        self.ws.state["exports"].append(excluded)
        historical = copy.deepcopy(entry)
        historical.update(id="old-production-copy", order_id="old-order")
        self.ws.state["exports"].append(historical)
        unrelated = {"id": "unrelated-export", "run_id": "another-run", "order_id": "another-order"}
        self.ws.state["exports"].append(unrelated)
        self.run["stage"] = "done"
        self.run["production"] = [{"order_id": self.run["inbox_order_id"], "status": "ready"}]
        previous = copy.deepcopy(self.run)
        files = {Path(self.ws.material(mid)["original"]): Path(self.ws.material(mid)["original"]).read_bytes()}
        files.update({Path(e[k]): Path(e[k]).read_bytes() for e in (entry, legacy, by_id) for k in ("invoice", "voucher")})
        self.delete()
        self.assertEqual(self.run["exports"], previous["exports"])
        self.assertEqual(self.run["production"], previous["production"])
        self.assertEqual(self.run["stage"], "done")
        for current in (entry, legacy, by_id, excluded, historical):
            self.assertTrue(current["history_hidden"])
        self.assertNotIn("history_hidden", unrelated)
        for path, content in files.items():
            self.assertEqual(path.read_bytes(), content)
        restored = Workspace(self.ws.root)
        self.assertEqual(flow.public_flow_state(restored), {"runs": [], "active_run_id": None})
        self.assertTrue(all(e["history_hidden"] for e in restored.state["exports"] if e["id"] != unrelated["id"]))
        self.assertEqual(len(restored.state["runs"]), 1)

    def test_deleting_inactive_run_preserves_other_active_run(self):
        active = flow.create_run(self.ws, {"title": "Keep this batch"})
        result = self.delete()
        self.assertEqual(result["active_run_id"], active["id"])
        self.assertEqual([r["id"] for r in result["runs"]], [active["id"]])
        self.assertEqual(flow.open_run(self.ws, {"run_id": active["id"]})["id"], active["id"])

    def test_stale_revision_rejects_without_mutating_history(self):
        self.add_material()
        self.add_export()
        before = copy.deepcopy(self.ws.state)
        disk = self.ws.state_file.read_bytes()
        with self.assertRaisesRegex(ValueError, "批次已更新"):
            flow.delete_run(self.ws, {"run_id": self.rid, "revision": self.run["revision"] - 1})
        self.assertEqual(self.ws.state, before)
        self.assertEqual(self.ws.state_file.read_bytes(), disk)

    def test_running_and_pending_jobs_reject_without_mutation(self):
        owned = self.ws.add_order({"declare_date": "20261109"})
        self.run["order_ids"].append(owned["id"])
        archived = self.ws.add_order({"declare_date": "20261109"})
        archived["flow_run_id"] = self.rid
        cases = [
            ("running-run", "running", None),
            ("pending-run-job", "idle", {"run_id": self.rid, "kind": "flow-make", "status": "pending"}),
            ("running-run-job", "idle", {"run_id": self.rid, "kind": "flow-export", "status": "running"}),
            ("inbox-job", "idle", {"order_id": self.run["inbox_order_id"], "kind": "analyze", "status": "pending"}),
            ("owned-order-job", "idle", {"order_id": owned["id"], "kind": "export", "status": "running"}),
            ("archived-order-job", "idle", {"order_id": archived["id"], "kind": "export", "status": "pending"}),
            ("global-job", "idle", {"kind": "smart-orders", "status": "running"}),
        ]
        for name, status, job in cases:
            with self.subTest(name=name):
                self.run["status"] = status
                self.ws.jobs.clear()
                if job:
                    self.ws.jobs[name] = {"id": name, **job}
                before = copy.deepcopy(self.ws.state)
                with self.assertRaisesRegex(ValueError, "正在处理"):
                    self.delete()
                self.assertEqual(self.ws.state, before)
        self.run["status"] = "idle"
        self.ws.jobs = {"finished": {"id": "finished", "run_id": self.rid, "status": "done"},
                        "unrelated": {"id": "unrelated", "run_id": "other", "order_id": "other", "status": "pending"}}
        self.assertEqual(self.delete()["runs"], [])

    def test_public_state_clears_tombstoned_active_id(self):
        self.delete()
        self.ws.state["active_run_id"] = self.rid
        self.assertIsNone(flow.public_flow_state(self.ws)["active_run_id"])
        self.assertIsNone(self.ws.state["active_run_id"])
        self.ws.state["active_run_id"] = self.rid
        self.ws.save()
        restored = Workspace(self.ws.root)
        self.assertIsNone(restored.state["active_run_id"])
        self.assertEqual(flow.public_flow_state(restored)["runs"], [])

    def test_http_delete_uses_existing_session_and_write_auth(self):
        body = {"run_id": self.rid, "revision": self.run["revision"]}
        for cookie, token in ((False, False), (False, True), (True, False)):
            with self.subTest(cookie=cookie, token=token):
                before = copy.deepcopy(self.ws.state)
                status, result = self.http_post("delete", body, cookie=cookie, token=token)
                self.assertEqual(status, 400)
                self.assertIn("error", result)
                self.assertEqual(self.ws.state, before)
        status, result = self.http_post("delete", body)
        self.assertEqual(status, 200)
        self.assertEqual(result, {"runs": [], "active_run_id": None})
        status, result = self.http_post("open", {"run_id": self.rid})
        self.assertEqual(status, 400)
        self.assertIn("已删除", result["error"])


if __name__ == "__main__":
    unittest.main()
