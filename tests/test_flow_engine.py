"""Flow state, API mocks, concurrency and atomic export regression tests."""
from __future__ import annotations

import copy
import io
import json
import shutil
import threading
import time
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from app import flow_engine as flow
from app.config.settings import Settings
from app.server import Workspace

ROOT = Path(__file__).resolve().parents[1]


def facts(category="order"):
    result = {"amount": "10.00", "product_name": "Synthetic bolt", "quantity": "1", "pay_time": "2026-09-27"}
    if category == "invoice":
        result.update(buyer_name="示例单位", taxpayer_id="91110000MA01234567")
    if category == "order":
        result["address"] = "示例园区东区实验楼130室"
    if category == "payment":
        result["extra"] = {"transaction_status": "success", "payment_direction": "expense"}
    return result


def checks():
    return [{"code": name, "status": "pass", "message": "原图可见依据"}
            for name in ("address", "amount", "buyer", "tax_id", "payment", "completeness")]


class SessionMock:
    calls = []
    fail_call = None
    trash = False

    def __init__(self, settings):
        self.settings = settings

    def analyze_batch(self, records, session=None, **kwargs):
        type(self).calls.append((copy.deepcopy(records), copy.deepcopy(session), copy.deepcopy(kwargs)))
        if type(self).fail_call == len(type(self).calls):
            raise RuntimeError("模拟服务失败")
        new = copy.deepcopy(session or {"ledger": {}, "messages": [], "revision": 0})
        for record in records:
            category = record.get("extracted", {}).get("_role", "order")
            new["ledger"][record["id"]] = {"path": record["path"], "category": category}
        new["revision"] += 1
        new["messages"].append({"role": "assistant", "content": "SECRET TRANSCRIPT"})
        ids = list(new["ledger"])
        materials = [{"material_id": mid, "category": "trash" if self.trash else new["ledger"][mid]["category"],
                      "group_key": None if self.trash else "g1", "facts": facts(new["ledger"][mid]["category"]), "evidence": ["visible"]}
                     for mid in ids]
        result = {"materials": materials,
                  "groups": [] if self.trash else [{"key": "g1", "title": "采购A", "material_ids": ids, "checks": checks(), "stitch_sets": []}],
                  "unassigned": [], "trash": ids if self.trash else [], "warnings": []}
        return {"session": new, "result": result}


class BatchMock:
    instances = []
    def __init__(self, settings):
        self.calls = []
        type(self).instances.append(self)
    def plan_borders(self, records):
        self.calls.append(("borders", copy.deepcopy(records)))
        return {"materials": [{"material_id": r["id"], "box": [0, 0, 1, 1], "has_border": False, "reason": "本地校准仍检查纯外黑边"} for r in records], "warnings": []}
    def plan_regions(self, records):
        self.calls.append(("regions", copy.deepcopy(records)))
        return {"materials": [{"material_id": r["id"], "regions": [
            {"category": "order", "box": [0, 0, .5, 1], "reason": "左侧订单"},
            {"category": "payment", "box": [.5, 0, .5, 1], "reason": "右侧付款"}]} for r in records], "warnings": []}
    def plan_stitch(self, records):
        self.calls.append(("stitch", copy.deepcopy(records)))
        return {"ordered_ids": [r["id"] for r in records], "crops": [], "joins": []}
    def plan_layout(self, records, **kwargs):
        self.calls.append(("layout", copy.deepcopy(records)))
        return {"layout": {"layout_column_ratio": .4}, "legibility": "good", "reason": "可见资料版面", "warnings": []}
    def _prepare(self, records, *args):
        return records
    def _content(self, prepared, **kwargs):
        return prepared
    def _request_plan(self, prompt, content):
        self.calls.append(("regions", content))
        return {"regions": [{"category": "order", "box": [0, 0, .5, 1], "reason": "左侧订单"},
                            {"category": "payment", "box": [.5, 0, .5, 1], "reason": "右侧付款"}], "warnings": []}


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "output" / ("flow_test_" + uuid.uuid4().hex[:12])
        self.root.mkdir(parents=True, exist_ok=False)
        self.ws = Workspace(self.root / "data")
        self.ws.settings = Settings(name="张三", advisor="李四", project_name="测试采购", declare_date="20260101",
                                    output_dir=str(self.root / "exports"), pdf_dpi=72,
                                    ai_api_key="LOCAL-MOCK-KEY", ai_api_base="http://127.0.0.1:12345", ai_vision_model="mock",
                                    extra={"dpi": 72, "invoice_layout_mode": "original"})
        SessionMock.calls, SessionMock.fail_call, SessionMock.trash = [], None, False
        BatchMock.instances = []
        self.session_patch = patch.object(flow, "ConversationVisionClient", SessionMock)
        self.batch_patch = patch.object(flow, "BatchVisionClient", BatchMock)
        self.session_patch.start(); self.batch_patch.start()
        self.addCleanup(self.session_patch.stop); self.addCleanup(self.batch_patch.stop)
        self.addCleanup(self.cleanup)
        self.run = flow.create_run(self.ws, {"profile": {"declare_date": "20260927"}})
        self.rid = self.run["id"]

    def cleanup(self):
        for thread in list(getattr(self.ws, "_flow_workers", {}).values()):
            thread.join(10)
        self.root.resolve().relative_to((ROOT / "output").resolve())
        shutil.rmtree(self.root)

    def wait(self, job):
        thread = self.ws._flow_workers.get(job["id"])
        if thread:
            thread.join(15)
            self.assertFalse(thread.is_alive(), "worker deadlocked")
        return self.ws.jobs.get(job["id"], job)

    def add(self, role="order", run_id=None):
        rid = run_id or self.rid
        material_id = uuid.uuid4().hex[:20]
        folder = self.root / "data" / "materials" / material_id
        folder.mkdir(parents=True)
        path = folder / "original.png"
        with Image.new("RGB", (240, 300), (40, 70, 110)) as im:
            im.save(path)
        self.ws.state["materials"][material_id] = {"id": material_id, "name": role + ".png", "original": str(path), "working": str(path),
            "order_id": self.ws.state["runs"][rid]["inbox_order_id"], "category": "unknown", "selected": True,
            "extracted": {"_role": role}, "width": 240, "height": 300, "revision": 0, "history": []}
        flow.add_material(self.ws, rid, material_id)
        return material_id

    def reviewed(self, roles=("invoice", "order", "payment")):
        mids = [self.add(role) for role in roles]
        job = self.wait(flow.start_review(self.ws, {"run_id": self.rid, "consent": True}))
        self.assertEqual(job["status"], "done", job)
        return mids

    def ready(self):
        self.reviewed()
        job = self.wait(flow.start_make(self.ws, {"run_id": self.rid, "confirmed": True}))
        self.assertEqual(job["status"], "done", job)
        run = flow.public_run(self.ws, self.rid)
        self.assertEqual(run["production"][0]["status"], "ready", run)
        return run

    def grouped_review(self, count=2):
        """Create independently reviewed complete purchases without remote calls."""
        roles = ("invoice", "order", "payment")
        members = [[self.add(role) for role in roles] for _ in range(count)]
        payload = {"materials": [{"material_id": mid, "category": role, "facts": facts(role)}
                    for group in members for mid, role in zip(group, roles)],
                   "groups": [{"key": f"purchase-{index}", "title": f"采购{index + 1}", "material_ids": ids,
                               "checks": checks(), "stitch_sets": []} for index, ids in enumerate(members)],
                   "unassigned": [], "trash": [], "warnings": []}
        run = self.ws.state["runs"][self.rid]
        flow._apply_review(self.ws, run, payload)
        run.update(stage="review", _review_source_version=run["_source_version"])
        self.ws.save()
        return members, payload

    def assert_review_partition(self, run):
        review = run["review"]
        partition = [mid for group in review["groups"] for mid in group["material_ids"]]
        partition += review["unassigned"] + review["trash"]
        self.assertEqual(len(partition), len(set(partition)))
        self.assertEqual(set(partition), set(run["material_ids"]))

    def test_remove_order_preserves_sources_and_remaining_group_can_make(self):
        members, _ = self.grouped_review()
        run = self.ws.state["runs"][self.rid]
        removed_oid, remaining_oid = run["order_ids"]
        before_materials = {mid: copy.deepcopy(self.ws.material(mid)) for mid in run["material_ids"]}
        before_sources = {mid: Path(row["original"]).read_bytes() for mid, row in before_materials.items()}
        session = {"ledger": {mid: {"category": "order"} for mid in run["material_ids"]}, "messages": []}
        run["session"] = copy.deepcopy(session)
        run["review"]["issues"].append({"code": "wrong_purchase", "level": "error", "message": "不报销这笔",
                                       "group_key": "purchase-0", "material_ids": members[0]})
        source_version, revision = run["_source_version"], run["revision"]
        public = flow.remove_order(self.ws, {"run_id": self.rid, "order_id": removed_oid, "revision": revision})
        self.assertEqual(public["order_ids"], [remaining_oid])
        self.assertEqual(public["stage"], "review")
        self.assertEqual(public["revision"], revision + 1)
        self.assertEqual(run["_source_version"], source_version)
        self.assertEqual(run["session"], session)
        self.assertEqual(run["review"]["issues"], [])
        self.assertEqual(run["review"]["trash"], members[0])
        self.assertEqual(run["_excluded_material_ids"], members[0])
        self.assertIn("原始材料仍保留在本地", run["_excluded_groups"][-1]["message"])
        self.assertTrue(self.ws.order(removed_oid)["flow_archived"])
        self.assert_review_partition(run)
        for mid in members[0]:
            material = self.ws.material(mid)
            self.assertFalse(material["selected"])
            self.assertEqual(material["order_id"], run["inbox_order_id"])
            self.assertEqual(material["original"], before_materials[mid]["original"])
            self.assertEqual(material["working"], before_materials[mid]["working"])
            self.assertEqual(material["extracted"], before_materials[mid]["extracted"])
        with patch.object(flow, "_make_one", wraps=flow._make_one) as maker:
            job = self.wait(flow.start_make(self.ws, {"run_id": self.rid, "confirmed": True}))
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(maker.call_count, 1)
        self.assertEqual(maker.call_args.args[2]["order_id"], remaining_oid)
        self.assertEqual([p["order_id"] for p in run["production"]], [remaining_oid])
        self.assertEqual(len(list((self.ws.root / "plans").glob("*/voucher.pdf"))), 1)
        self.assertEqual(before_sources, {mid: Path(self.ws.material(mid)["original"]).read_bytes() for mid in before_sources})
        self.assertEqual(set(self.ws.state["materials"]), set(before_sources))

    def test_remove_all_orders_archives_without_profile_ai_or_empty_pdfs(self):
        members, _ = self.grouped_review(count=1)
        run = self.ws.state["runs"][self.rid]
        run["profile"]["name"] = ""
        oid = run["order_ids"][0]
        with patch.object(flow, "_make_one", side_effect=AssertionError("excluded purchases must not make PDFs")), \
                patch.object(flow, "BatchVisionClient", side_effect=AssertionError("no AI for excluded purchases")):
            result = flow.remove_order(self.ws, {"run_id": self.rid, "order_id": oid, "revision": run["revision"]})
            job = flow.start_make(self.ws, {"run_id": self.rid, "confirmed": True})
        self.assertEqual(result["stage"], "done")
        self.assertTrue(job["result"]["archived"])
        self.assertEqual(run["order_ids"], [])
        self.assertEqual(run["production"], [])
        self.assertEqual(run["exports"], [])
        self.assertEqual(run["review"]["trash"], members[0])
        self.assertFalse((self.ws.root / "plans").exists())
        self.assertFalse((self.root / "exports").exists())
        self.assert_review_partition(run)
        with self.assertRaises(ValueError):
            flow.start_export(self.ws, {"run_id": self.rid, "confirmed": True})
        self.assertTrue(all(Path(self.ws.material(mid)["original"]).is_file() for mid in members[0]))
        recovered = Workspace(self.ws.root)
        self.assertEqual(recovered.state["runs"][self.rid]["_excluded_material_ids"], members[0])
        self.assertEqual(flow.public_run(recovered, self.rid)["order_ids"], [])

    def test_removed_order_is_not_resurrected_by_old_model_result_or_forced_review(self):
        members, payload = self.grouped_review()
        run = self.ws.state["runs"][self.rid]
        removed_oid = run["order_ids"][0]
        flow.remove_order(self.ws, {"run_id": self.rid, "order_id": removed_oid})
        # A saved transcript/provider can still mention excluded source IDs.
        # Model output may not undo the user's explicit exclusion.
        flow._apply_review(self.ws, run, copy.deepcopy(payload))
        self.assertEqual(len(run["review"]["groups"]), 1)
        self.assertEqual(run["review"]["groups"][0]["material_ids"], members[1])
        self.assertEqual(set(run["review"]["trash"]), set(members[0]))
        self.assertNotIn(removed_oid, run["order_ids"])
        self.assert_review_partition(run)
        job = self.wait(flow.start_review(self.ws, {"run_id": self.rid, "force": True, "confirmed": True}))
        self.assertEqual(job["status"], "done", job)
        self.assertEqual([g["material_ids"] for g in run["review"]["groups"]], [members[1]])
        self.assertEqual(set(run["review"]["trash"]), set(members[0]))
        self.assert_review_partition(run)
        self.assertTrue(all(not self.ws.material(mid)["selected"] for mid in members[0]))

    def test_remove_order_invalidates_only_its_exports_and_preserves_external_files(self):
        members, _ = self.grouped_review()
        run = self.ws.state["runs"][self.rid]
        self.assertEqual(self.wait(flow.start_make(self.ws, {"run_id": self.rid, "confirmed": True}))["status"], "done")
        exported = self.wait(flow.start_export(self.ws, {"run_id": self.rid, "confirmed": True, "request_id": "before-exclusion"}))
        self.assertEqual(exported["status"], "done", exported)
        removed_oid, remaining_oid = run["order_ids"]
        previous = {e["order_id"]: copy.deepcopy(e) for e in run["exports"]}
        remaining_plan = next(p["plan_id"] for p in run["production"] if p["order_id"] == remaining_oid)
        files = {path: Path(path).read_bytes() for entry in previous.values() for path in (entry["invoice"], entry["voucher"])}
        result = flow.remove_order(self.ws, {"run_id": self.rid, "order_id": removed_oid, "revision": run["revision"]})
        self.assertEqual(result["stage"], "done")
        self.assertEqual([e["order_id"] for e in run["exports"]], [remaining_oid])
        self.assertEqual(run["exports"][0], previous[remaining_oid])
        self.assertEqual([p["plan_id"] for p in run["production"]], [remaining_plan])
        historical = next(e for e in self.ws.state["exports"] if e["order_id"] == removed_oid)
        self.assertTrue(historical["invalidated"])
        self.assertEqual(historical["invalidated_reason"], "purchase_excluded")
        self.assertNotIn("last_export", self.ws.order(removed_oid))
        self.assertTrue(self.ws.order(remaining_oid).get("last_export"))
        self.assertEqual(files, {path: Path(path).read_bytes() for path in files})
        repeated = flow.start_export(self.ws, {"run_id": self.rid, "confirmed": True, "request_id": "before-exclusion"})
        self.assertTrue(repeated["result"]["already_exported"])
        self.assertNotEqual(repeated["id"], exported["id"])
        with patch.object(flow, "_export_one", wraps=flow._export_one) as exporter:
            copy_job = self.wait(flow.start_export(self.ws, {"run_id": self.rid, "confirmed": True, "new_copy": True}))
        self.assertEqual(copy_job["status"], "done", copy_job)
        self.assertEqual(exporter.call_count, 1)
        self.assertEqual(exporter.call_args.args[2]["order_id"], remaining_oid)
        self.assertFalse(any(e["order_id"] == removed_oid for e in run["exports"]))
        self.assertTrue(all(Path(self.ws.material(mid)["original"]).is_file() for mid in members[0]))

    def test_remove_order_rebases_only_excluded_material_fingerprints(self):
        members, _ = self.grouped_review()
        run = self.ws.state["runs"][self.rid]
        self.assertEqual(self.wait(flow.start_make(self.ws, {"run_id": self.rid, "confirmed": True}))["status"], "done")
        removed_oid, remaining_oid = run["order_ids"]
        remaining_before = copy.deepcopy(next(p for p in run["production"] if p["order_id"] == remaining_oid))
        self.ws.material(members[0][0])["revision"] += 1
        self.ws.material(members[1][0])["revision"] += 1
        flow.remove_order(self.ws, {"run_id": self.rid, "order_id": removed_oid})
        remaining = run["production"][0]
        self.assertEqual(remaining["plan_id"], remaining_before["plan_id"])
        self.assertEqual(remaining["_source_version"], remaining_before["_source_version"])
        fingerprints = flow._fingerprints(self.ws, run)
        for mid in members[0]:
            self.assertEqual(remaining["_material_fingerprints"][mid], fingerprints[mid])
        # A separate pre-existing changed source must not be blessed by removal.
        for mid in members[1]:
            self.assertEqual(remaining["_material_fingerprints"][mid], remaining_before["_material_fingerprints"][mid])
        with self.assertRaisesRegex(ValueError, "过期"):
            flow.start_export(self.ws, {"run_id": self.rid, "confirmed": True})
        self.ws.material(members[1][0])["revision"] -= 1
        job = self.wait(flow.start_export(self.ws, {"run_id": self.rid, "confirmed": True}))
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(len(job["result"]["exports"]), 1)
        self.assertEqual(job["result"]["exports"][0]["order_id"], remaining_oid)

    def test_remove_order_rejects_stale_revision_foreign_or_already_removed_order(self):
        self.grouped_review()
        run = self.ws.state["runs"][self.rid]
        oid = run["order_ids"][0]
        before = copy.deepcopy(self.ws.state)
        with self.assertRaisesRegex(ValueError, "批次已更新"):
            flow.remove_order(self.ws, {"run_id": self.rid, "order_id": oid, "revision": run["revision"] - 1})
        self.assertEqual(self.ws.state, before)
        for invalid in (run["inbox_order_id"], "missing-order"):
            with self.assertRaisesRegex(ValueError, "不属于本批"):
                flow.remove_order(self.ws, {"run_id": self.rid, "order_id": invalid})
            self.assertEqual(self.ws.state, before)
        another = flow.create_run(self.ws, {})
        with self.assertRaisesRegex(ValueError, "不属于本批"):
            flow.remove_order(self.ws, {"run_id": another["id"], "order_id": oid})
        flow.remove_order(self.ws, {"run_id": self.rid, "order_id": oid})
        after = copy.deepcopy(self.ws.state)
        with self.assertRaisesRegex(ValueError, "不属于本批"):
            flow.remove_order(self.ws, {"run_id": self.rid, "order_id": oid})
        self.assertEqual(self.ws.state, after)

    def test_remove_order_rejects_running_and_pending_jobs_without_mutation(self):
        self.grouped_review()
        run = self.ws.state["runs"][self.rid]
        oid = run["order_ids"][0]
        entered, release = threading.Event(), threading.Event()
        def blocked(ws, rid, group, *args):
            entered.set()
            release.wait(10)
            return {"order_id": group["order_id"], "status": "ready", "warnings": []}
        with patch.object(flow, "_make_one", side_effect=blocked):
            job = flow.start_make(self.ws, {"run_id": self.rid, "confirmed": True})
            try:
                self.assertTrue(entered.wait(3))
                before = copy.deepcopy(self.ws.state)
                with self.assertRaisesRegex(ValueError, "正在处理"):
                    flow.remove_order(self.ws, {"run_id": self.rid, "order_id": oid, "revision": run["revision"]})
                self.assertEqual(self.ws.state, before)
            finally:
                release.set()
                self.wait(job)
        self.ws.jobs["pending-exclusion-test"] = {"id": "pending-exclusion-test", "kind": "flow-export",
            "run_id": self.rid, "order_id": run["inbox_order_id"], "status": "pending"}
        before = copy.deepcopy(self.ws.state)
        with self.assertRaisesRegex(ValueError, "正在处理"):
            flow.remove_order(self.ws, {"run_id": self.rid, "order_id": oid})
        self.assertEqual(self.ws.state, before)

    def test_create_run_uses_today_not_old_settings_date(self):
        run = flow.create_run(self.ws, {})
        self.assertEqual(run["profile"]["declare_date"], datetime.now().strftime("%Y%m%d"))
        self.assertEqual(run["stage"], "collect")
        self.assertEqual(run["session_summary"]["processed"], 0)

    def test_upload_is_unknown_and_original_immutable(self):
        mid = self.add("payment")
        row = self.ws.material(mid)
        before = Path(row["original"]).read_bytes()
        self.assertEqual(row["category"], "unknown")
        self.assertEqual(row["order_id"], self.ws.state["runs"][self.rid]["inbox_order_id"])
        flow.remove_material(self.ws, {"run_id": self.rid, "material_id": mid})
        self.assertEqual(Path(row["original"]).read_bytes(), before)
        self.assertIn(mid, self.ws.state["materials"])

    def test_public_state_hides_transcript_paths_and_api_key(self):
        self.reviewed()
        run = flow.public_run(self.ws, self.rid)
        text = json.dumps(run, ensure_ascii=False)
        self.assertNotIn("SECRET TRANSCRIPT", text)
        self.assertNotIn("LOCAL-MOCK-KEY", text)
        self.assertNotIn(str(self.root), text)
        self.assertNotIn('"ledger"', text)
        self.assertNotIn('"session"', text)
        self.assertEqual(run["session_summary"]["processed"], 3)

    def test_first_actual_purchase_uses_order_index_one(self):
        self.reviewed()
        run = self.ws.state["runs"][self.rid]
        self.assertEqual(self.ws.order(run["order_ids"][0])["order_index"], 1)

    def test_review_batches_eight_and_persists_after_each_success(self):
        for _ in range(17):
            self.add()
        job = self.wait(flow.start_review(self.ws, {"run_id": self.rid, "consent": True}))
        self.assertEqual(job["status"], "done", job)
        self.assertEqual([len(c[0]) for c in SessionMock.calls], [8, 8, 1])
        self.assertEqual([c[2]["final"] for c in SessionMock.calls], [False, False, True])
        saved = json.loads(self.ws.state_file.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["runs"][self.rid]["session"]["ledger"]), 17)

    def test_failed_second_batch_can_resume_without_resending_first_batch(self):
        for _ in range(10):
            self.add()
        SessionMock.fail_call = 2
        job = self.wait(flow.start_review(self.ws, {"run_id": self.rid, "consent": True}))
        self.assertEqual(job["status"], "error")
        self.assertEqual(len(self.ws.state["runs"][self.rid]["session"]["ledger"]), 8)
        SessionMock.fail_call = None
        retry = self.wait(flow.start_review(self.ws, {"run_id": self.rid, "consent": True, "feedback": "继续"}))
        self.assertEqual(retry["status"], "done", retry)
        self.assertEqual([len(c[0]) for c in SessionMock.calls], [8, 2, 2])

    def test_supplement_only_sends_new_materials(self):
        self.reviewed()
        mid = self.add("order")
        self.assertEqual(flow.public_run(self.ws, self.rid)["stage"], "collect")
        job = self.wait(flow.start_review(self.ws, {"run_id": self.rid, "consent": True}))
        self.assertEqual(job["status"], "done", job)
        self.assertEqual([r["id"] for r in SessionMock.calls[-1][0]], [mid])

    def test_feedback_only_uses_empty_records_and_retry_revisits_old_images(self):
        self.reviewed()
        self.wait(flow.start_review(self.ws, {"run_id": self.rid, "consent": True, "feedback": "检查地址"}))
        self.assertEqual(SessionMock.calls[-1][0], [])
        self.wait(flow.start_review(self.ws, {"run_id": self.rid, "consent": True, "feedback": "重新看图", "retry": True}))
        self.assertEqual(len(SessionMock.calls[-1][0]), 3)
        self.assertTrue(SessionMock.calls[-1][2]["revisit"])

    def test_same_run_job_dedup_and_busy_mutations_rejected(self):
        self.add()
        entered, release = threading.Event(), threading.Event()
        original = SessionMock.analyze_batch
        def blocked(client, *args, **kwargs):
            entered.set(); release.wait(10)
            return original(client, *args, **kwargs)
        with patch.object(SessionMock, "analyze_batch", blocked):
            job = flow.start_review(self.ws, {"run_id": self.rid, "consent": True})
            self.assertTrue(entered.wait(3))
            duplicate = flow.start_review(self.ws, {"run_id": self.rid, "consent": True})
            self.assertEqual(job["id"], duplicate["id"])
            with self.assertRaises(ValueError):
                flow.update_run(self.ws, {"run_id": self.rid, "feedback": "edit"})
            with self.assertRaises(ValueError):
                flow.remove_material(self.ws, {"run_id": self.rid, "material_id": self.run["material_ids"]})
            release.set(); self.wait(job)

    def test_stale_revision_rejected(self):
        old = flow.public_run(self.ws, self.rid)["revision"]
        self.add()
        with self.assertRaises(ValueError):
            flow.update_run(self.ws, {"run_id": self.rid, "revision": old, "feedback": "stale"})

    def test_restart_recovers_saved_session_and_marks_running_interrupted(self):
        self.reviewed()
        run = self.ws.state["runs"][self.rid]
        run["status"] = "running"; run["_active_job"] = "lost"; self.ws.save()
        recovered = Workspace(self.ws.root)
        flow.ensure_flow_state(recovered)
        public = flow.public_run(recovered, self.rid)
        self.assertEqual(public["status"], "error")
        self.assertEqual(public["session_summary"]["processed"], 3)
        self.assertIn("中断", public["error"])

    def test_semantic_address_pass_preserves_literal_address(self):
        mids = self.reviewed()
        row = self.ws.material(mids[1])
        self.assertEqual(row["extracted"]["address"], "示例园区东区实验楼130室")
        semantic = row["extracted"]["extra"]["address_review"]
        self.assertEqual(semantic["status"], "pass")
        self.assertEqual(semantic["source"], "ai")
        self.assertFalse(any(i["code"] == "bad_address" for i in flow.public_run(self.ws, self.rid)["review"]["issues"]))

    def test_legacy_amount_warning_no_longer_blocks_four_category_review(self):
        self.reviewed()
        run = self.ws.state["runs"][self.rid]
        run["review"]["issues"] = [{"code": "amount_mismatch", "level": "error", "message": "差一分"}]
        job = self.wait(flow.start_make(self.ws, {"run_id": self.rid, "confirmed": True}))
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(flow.public_run(self.ws, self.rid)["review"]["issues"], [])
        self.assertEqual(run["review"]["issues"][0]["level"], "error")  # private original audit retained
        self.assertFalse(run["forced_steps"])

    def test_make_requires_all_new_materials_reviewed(self):
        self.reviewed()
        self.add()
        with self.assertRaises(ValueError):
            flow.start_make(self.ws, {"run_id": self.rid, "confirmed": True})

    def test_all_trash_archives_without_empty_purchase(self):
        self.add(); SessionMock.trash = True
        self.wait(flow.start_review(self.ws, {"run_id": self.rid, "consent": True}))
        job = flow.start_make(self.ws, {"run_id": self.rid, "confirmed": True})
        self.assertEqual(job["status"], "done")
        run = flow.public_run(self.ws, self.rid)
        self.assertEqual(run["stage"], "done")
        self.assertEqual(run["order_ids"], [])
        self.assertEqual(run["production"], [])
        self.assertEqual(self.ws.material(run["material_ids"][0])["category"], "other")

    def test_make_uses_new_client_and_keeps_original_library_clean(self):
        mids = self.reviewed()
        before = {mid: Path(self.ws.material(mid)["original"]).read_bytes() for mid in mids}
        self.ws.import_bytes = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("worker must not import"))
        self.ws.start_job = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("worker must not nest jobs"))
        result = self.wait(flow.start_make(self.ws, {"run_id": self.rid, "confirmed": True}))
        self.assertEqual(result["status"], "done", result)
        self.assertEqual(len(BatchMock.instances), 1)
        self.assertEqual([c[0] for c in BatchMock.instances[0].calls], ["borders", "borders", "layout"])
        self.assertEqual([len(c[1]) for c in BatchMock.instances[0].calls[:2]], [3, 2])
        self.assertEqual(len(self.ws.state["materials"]), 3)
        self.assertEqual(before, {mid: Path(self.ws.material(mid)["original"]).read_bytes() for mid in mids})

    def test_missing_materials_have_visible_placeholders_without_pass_claim(self):
        self.reviewed(("order",))
        job = self.wait(flow.start_make(self.ws, {"run_id": self.rid, "force": True, "confirmed": True}))
        self.assertEqual(job["status"], "done", job)
        product = self.ws.state["runs"][self.rid]["production"][0]
        self.assertEqual(product["status"], "ready", product)
        self.assertTrue(any("未提供发票" in w for w in product["warnings"]))
        self.assertTrue(any("支付" in w and "占位" in w for w in product["warnings"]))
        self.assertTrue(Path(product["_invoice_pdf"]).is_file())
        self.assertTrue((self.ws.root / "plans" / product["plan_id"] / "missing-invoice.png").is_file())

    def test_export_exactly_two_pdfs_and_external_history_report(self):
        self.ready()
        job = self.wait(flow.start_export(self.ws, {"run_id": self.rid, "force": True, "confirmed": True}))
        self.assertEqual(job["status"], "done", job)
        run = self.ws.state["runs"][self.rid]
        entry = run["exports"][0]
        folder = Path(entry["folder"])
        self.assertEqual(len(list(folder.iterdir())), 2)
        self.assertTrue(all(p.suffix == ".pdf" for p in folder.iterdir()))
        self.assertTrue((self.ws.root / "reports" / ("flow-" + entry["id"] + ".json")).is_file())
        self.assertEqual(run["stage"], "done")
        repeated = flow.start_export(self.ws, {"run_id": self.rid, "force": True, "confirmed": True})
        self.assertTrue(repeated["result"]["already_exported"])
        self.assertEqual(len(run["exports"]), 1)

    def test_export_failure_cleans_partial_and_preserves_retry(self):
        self.ready()
        with patch.object(flow.shutil, "copy2", side_effect=OSError("disk full")):
            job = self.wait(flow.start_export(self.ws, {"run_id": self.rid, "force": True, "confirmed": True}))
        self.assertEqual(job["status"], "done")  # per-purchase failures are isolated
        self.assertEqual(len(job["result"]["failures"]), 1)
        self.assertFalse(list((self.root / "exports").glob("tmp_flow_*")))
        retry = self.wait(flow.start_export(self.ws, {"run_id": self.rid, "force": True, "confirmed": True}))
        self.assertEqual(len(retry["result"]["exports"]), 1)

    def test_reflow_updates_production_pdf_not_old_preview_and_calls_no_model(self):
        self.ready()
        run = self.ws.state["runs"][self.rid]
        before = copy.deepcopy(run["production"][0])
        oid = before["order_id"]
        with patch.object(flow, "BatchVisionClient", side_effect=AssertionError("no AI reflow")):
            job = self.wait(flow.start_reflow(self.ws, {"run_id": self.rid, "order_id": oid,
                "layout": {"layout_column_ratio": .62, "layout_left_mm": 80}}))
        self.assertEqual(job["status"], "done", job)
        after = run["production"][0]
        self.assertNotEqual(after["plan_id"], before["plan_id"])
        self.assertNotEqual(after["_voucher_pdf"], before["_voucher_pdf"])
        self.assertEqual(after["layout"]["layout_column_ratio"], .62)
        self.assertEqual(after["_source_version"], before["_source_version"])
        self.assertTrue(Path(before["_voucher_pdf"]).is_file())

    def test_date_change_resynchronizes_order_and_requires_review(self):
        self.reviewed()
        old = self.ws.state["runs"][self.rid]
        oid = old["order_ids"][0]
        run = flow.update_run(self.ws, {"run_id": self.rid, "profile": {"declare_date": "20261001"}})
        self.assertEqual(run["stage"], "collect")
        self.assertEqual(self.ws.order(oid)["declare_date"], "20261001")
        self.assertEqual(self.ws.order(oid)["order_index"], 1)

    def test_open_history_does_not_clear_older_exports(self):
        first = self.ready()
        self.wait(flow.start_export(self.ws, {"run_id": self.rid, "force": True, "confirmed": True}))
        new = flow.create_run(self.ws, {"profile": {"declare_date": "20260927"}})
        self.assertNotEqual(new["id"], self.rid)
        old = flow.open_run(self.ws, {"run_id": self.rid})
        self.assertEqual(len(old["exports"]), 1)
        self.assertEqual(flow.public_flow_state(self.ws)["active_run_id"], self.rid)

    def test_completed_request_id_is_idempotent(self):
        self.add()
        job = self.wait(flow.start_review(self.ws, {"run_id": self.rid, "consent": True, "request_id": "same"}))
        duplicate = flow.start_review(self.ws, {"run_id": self.rid, "consent": True, "request_id": "same"})
        self.assertEqual(job["id"], duplicate["id"])
        self.assertEqual(len(SessionMock.calls), 1)

    def test_mixed_screenshot_has_new_context_verified_regions_and_no_original_mutation(self):
        mids = self.reviewed()
        mixed = self.ws.material(mids[1])
        payment = self.ws.material(mids[2])
        run = self.ws.state["runs"][self.rid]
        payload = {"materials": [{"material_id": mids[0], "category": "invoice", "facts": facts("invoice")},
                                 {"material_id": mids[1], "category": "other", "facts": {"amount": "10.00", "extra": {
                                     "regions": [{"category": "order", "box": [0, 0, .5, 1]}, {"category": "payment", "box": [.5, 0, .5, 1]}]}}},
                                 {"material_id": mids[2], "category": "trash", "facts": {}}],
                   "groups": [{"key": "g1", "title": "混合截图采购", "material_ids": mids[:2], "checks": checks()}],
                   "unassigned": [], "trash": [mids[2]], "warnings": []}
        flow._apply_review(self.ws, run, payload)
        before = Path(mixed["original"]).read_bytes()
        self.assertFalse(any(i["code"] in {"missing_order", "missing_payment"} for i in run["review"]["issues"]))
        job = self.wait(flow.start_make(self.ws, {"run_id": self.rid, "force": True, "confirmed": True}))
        self.assertEqual(job["status"], "done", job)
        product = run["production"][0]
        self.assertEqual(product["status"], "ready", product)
        self.assertTrue(any(call[0] == "regions" for call in BatchMock.instances[-1].calls))
        self.assertFalse(any("未提供订单" in w or "未提供支付" in w for w in product["warnings"]))
        self.assertEqual(len(self.ws.state["materials"]), 3)
        self.assertEqual(Path(mixed["original"]).read_bytes(), before)
        self.assertTrue(any(a.get("kind") == "mixed-regions" for a in product["stitch_audits"]))

    def test_missing_core_model_checks_are_unknown_not_pass(self):
        mid = self.add()
        run = self.ws.state["runs"][self.rid]
        flow._apply_review(self.ws, run, {"materials": [{"material_id": mid, "category": "order", "facts": facts()}],
            "groups": [{"key": "one", "title": "未核验", "material_ids": [mid], "checks": []}], "unassigned": [], "trash": [], "warnings": []})
        issue_codes = {i["code"] for i in run["review"]["issues"]}
        self.assertIn("address_unknown", issue_codes)
        self.assertTrue(issue_codes <= {"address_unknown", "missing_materials"})
        self.assertFalse(any("amount" in code or "payment_unknown" == code for code in issue_codes))

    def test_review_group_key_change_without_export_does_not_consume_extra_index(self):
        mids = self.reviewed()
        run = self.ws.state["runs"][self.rid]
        old_oid = run["order_ids"][0]
        flow._apply_review(self.ws, run, {"materials": [{"material_id": mid, "category": cat, "facts": facts(cat)} for mid, cat in zip(mids, ("invoice", "order", "payment"))],
            "groups": [{"key": "renamed-group", "title": "重新分组", "material_ids": mids, "checks": checks()}], "unassigned": [], "trash": [], "warnings": []})
        self.assertNotEqual(run["order_ids"][0], old_oid)
        self.assertEqual(self.ws.order(run["order_ids"][0])["order_index"], 1)

    def test_date_change_requires_fresh_review_even_when_all_old_ids_seen(self):
        self.reviewed()
        flow.update_run(self.ws, {"run_id": self.rid, "profile": {"declare_date": "20261001"}})
        with self.assertRaises(ValueError):
            flow.start_make(self.ws, {"run_id": self.rid, "confirmed": True})

    def test_force_make_keeps_known_groups_and_separates_new_unreviewed_files(self):
        self.reviewed()
        run = self.ws.state["runs"][self.rid]
        old_ids = list(run["review"]["groups"][0]["material_ids"])
        new_mid = self.add()
        result = flow._manual_result(self.ws, run)
        self.assertEqual(result["groups"][0]["material_ids"], old_ids)
        self.assertEqual(result["groups"][1]["material_ids"], [new_mid])
        self.assertEqual(result["groups"][1]["checks"][0]["status"], "review")

    def test_forced_review_without_consent_does_not_send_any_images(self):
        self.add()
        job = self.wait(flow.start_review(self.ws, {"run_id": self.rid, "force": True, "confirmed": True}))
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(SessionMock.calls, [])
        self.assertTrue(self.ws.state["runs"][self.rid]["review"]["issues"])

    def test_make_single_failed_order_can_retry_without_replacing_successful_ones(self):
        mids = [self.add() for _ in range(2)]
        run = self.ws.state["runs"][self.rid]
        flow._apply_review(self.ws, run, {"materials": [{"material_id": m, "category": "order", "facts": facts()} for m in mids],
             "groups": [{"key": str(n), "title": str(n), "material_ids": [mid], "checks": checks()} for n, mid in enumerate(mids)],
             "unassigned": [], "trash": [], "warnings": []})
        failed_oid = run["order_ids"][0]
        def first(ws, rid, group, *rest):
            if group["order_id"] == failed_oid:
                raise RuntimeError("one failed")
            return {"order_id": group["order_id"], "title": "ok", "status": "ready", "warnings": [], "marker": "preserve"}
        with patch.object(flow, "_make_one", side_effect=first):
            self.wait(flow.start_make(self.ws, {"run_id": self.rid, "force": True, "confirmed": True}))
        with patch.object(flow, "_make_one", return_value={"order_id": failed_oid, "status": "ready", "warnings": [], "title": "retried"}) as worker:
            job = self.wait(flow.start_make(self.ws, {"run_id": self.rid, "force": True, "confirmed": True, "only_order_ids": [failed_oid]}))
        self.assertEqual(job["status"], "done")
        self.assertEqual(worker.call_count, 1)
        self.assertTrue(any(p.get("marker") == "preserve" for p in run["production"]))

    def test_export_external_material_change_rejects_stale_production(self):
        self.ready()
        mid = self.ws.state["runs"][self.rid]["material_ids"][0]
        self.ws.material(mid)["revision"] += 1
        with self.assertRaises(ValueError):
            flow.start_export(self.ws, {"run_id": self.rid, "force": True, "confirmed": True})

    def test_export_partially_failed_retry_skips_prior_successful_production(self):
        self.ready()
        run = self.ws.state["runs"][self.rid]
        original = run["production"][0]
        new_order = copy.deepcopy(self.ws.order(original["order_id"]))
        new_order.update(id=uuid.uuid4().hex[:20], order_index=2, title="second")
        self.ws.state["orders"].append(new_order)
        run["order_ids"].append(new_order["id"])
        second = {**copy.deepcopy(original), "order_id": new_order["id"], "plan_id": "second-production"}
        run["production"].append(second)
        export = flow._export_one
        def fail_second(ws, current, product, forced):
            if product["order_id"] == new_order["id"]:
                raise OSError("fail second")
            return export(ws, current, product, forced)
        with patch.object(flow, "_export_one", side_effect=fail_second):
            first_job = self.wait(flow.start_export(self.ws, {"run_id": self.rid, "force": True, "confirmed": True}))
        self.assertEqual(len(first_job["result"]["exports"]), 1)
        self.assertEqual(len(run["exports"]), 1)
        with patch.object(flow, "_export_one", wraps=export) as worker:
            retry = self.wait(flow.start_export(self.ws, {"run_id": self.rid, "force": True, "confirmed": True}))
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(len(retry["result"]["exports"]), 1)
        self.assertEqual(len(run["exports"]), 2)

    def test_multiple_invoices_are_preserved_as_all_pdf_pages(self):
        mids = self.reviewed(("invoice", "invoice", "order", "payment"))
        job = self.wait(flow.start_make(self.ws, {"run_id": self.rid, "force": True, "confirmed": True}))
        self.assertEqual(job["status"], "done", job)
        product = self.ws.state["runs"][self.rid]["production"][0]
        self.assertEqual(product["status"], "ready", product)
        from pypdf import PdfReader
        self.assertEqual(len(PdfReader(product["_invoice_pdf"]).pages), 2)
        self.assertTrue(any("多份发票" in w for w in product["_diagnostic_warnings"]))
        self.assertEqual(len(self.ws.state["materials"]), len(mids))

    def test_session_summary_and_resume_accept_real_provider_list_ledger(self):
        class ListLedgerMock(SessionMock):
            def analyze_batch(client, records, session=None, **kwargs):
                converted = copy.deepcopy(session)
                if converted and isinstance(converted.get("ledger"), list):
                    converted["ledger"] = {r["id"]: r for r in converted["ledger"]}
                result = super(ListLedgerMock, client).analyze_batch(records, converted, **kwargs)
                result["session"]["ledger"] = [{"id": key, **row} for key, row in result["session"]["ledger"].items()]
                return result
        for _ in range(9):
            self.add()
        with patch.object(flow, "ConversationVisionClient", ListLedgerMock):
            job = self.wait(flow.start_review(self.ws, {"run_id": self.rid, "consent": True}))
            self.assertEqual(job["status"], "done", job)
            self.assertEqual(flow.public_run(self.ws, self.rid)["session_summary"]["processed"], 9)
            new_id = self.add()
            job = self.wait(flow.start_review(self.ws, {"run_id": self.rid, "consent": True}))
            self.assertEqual(job["status"], "done", job)
            self.assertEqual([r["id"] for r in SessionMock.calls[-1][0]], [new_id])

    def test_completed_request_id_survives_process_restart(self):
        self.add()
        job = self.wait(flow.start_review(self.ws, {"run_id": self.rid, "consent": True, "request_id": "durable"}))
        recovered = Workspace(self.ws.root)
        recovered.settings = copy.deepcopy(self.ws.settings)
        duplicate = flow.start_review(recovered, {"run_id": self.rid, "consent": True, "request_id": "durable"})
        self.assertEqual(job["id"], duplicate["id"])
        self.assertEqual(duplicate["status"], "done")
        self.assertEqual(len(SessionMock.calls), 1)

    def test_all_trash_archive_does_not_require_purchase_profile(self):
        self.add(); SessionMock.trash = True
        self.wait(flow.start_review(self.ws, {"run_id": self.rid, "consent": True}))
        self.ws.state["runs"][self.rid]["profile"]["name"] = ""
        job = flow.start_make(self.ws, {"run_id": self.rid, "confirmed": True})
        self.assertTrue(job["result"]["archived"])
        self.assertIn(job["id"], self.ws.jobs)

    def test_add_material_limit_240_and_same_id_is_idempotent(self):
        mid = self.add()
        run = self.ws.state["runs"][self.rid]
        flow.add_material(self.ws, self.rid, mid)
        self.assertEqual(run["material_ids"], [mid])
        # Fill metadata using existing immutable bytes; no 240 actual images
        # or remote calls are needed to exercise the capacity invariant.
        source = copy.deepcopy(self.ws.material(mid))
        for n in range(239):
            sid = f"limit-{n}"
            self.ws.state["materials"][sid] = {**copy.deepcopy(source), "id": sid}
            run["material_ids"].append(sid)
        new = "one-too-many"
        self.ws.state["materials"][new] = {**source, "id": new}
        with self.assertRaises(ValueError):
            flow.add_material(self.ws, self.rid, new)
        self.assertEqual(len(run["material_ids"]), 240)

    def test_malformed_review_response_rejected_before_any_order_mutation(self):
        mid = self.add()
        run = self.ws.state["runs"][self.rid]
        before = copy.deepcopy(self.ws.state["orders"])
        with self.assertRaises(ValueError):
            flow._apply_review(self.ws, run, {"materials": [{"material_id": mid, "category": "order", "facts": {}}],
                "groups": [{"key": "bad", "title": "bad", "material_ids": [mid], "checks": ["bad"]}],
                "unassigned": [], "trash": [], "warnings": []})
        self.assertEqual(self.ws.state["orders"], before)
        self.assertEqual(self.ws.material(mid)["category"], "unknown")

    def test_repeated_export_requires_explicit_new_copy_and_uses_new_index(self):
        self.ready()
        first = self.wait(flow.start_export(self.ws, {"run_id": self.rid, "force": True, "confirmed": True}))
        second = self.wait(flow.start_export(self.ws, {"run_id": self.rid, "force": True, "confirmed": True, "new_copy": True}))
        self.assertEqual(first["status"], "done"); self.assertEqual(second["status"], "done")
        exports = self.ws.state["runs"][self.rid]["exports"]
        self.assertEqual([e["order_index"] for e in exports], [1, 2])
        self.assertNotEqual(exports[0]["folder"], exports[1]["folder"])
        self.assertTrue(Path(exports[0]["folder"]).is_dir())
        self.assertTrue(Path(exports[1]["folder"]).is_dir())

    def test_export_name_conflict_never_overwrites_existing_folder(self):
        self.ready()
        run = self.ws.state["runs"][self.rid]
        order = self.ws.order(run["order_ids"][0])
        cfg = flow._settings(self.ws, run, order)
        final = Path(cfg.output_dir) / flow.build_output_names(cfg).folder_name
        final.mkdir(parents=True)
        sentinel = final / "keep.txt"; sentinel.write_text("untouched", encoding="utf-8")
        job = self.wait(flow.start_export(self.ws, {"run_id": self.rid, "force": True, "confirmed": True}))
        self.assertEqual(len(job["result"]["failures"]), 1)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "untouched")
        self.assertEqual(run["exports"], [])
        self.assertFalse(list(Path(cfg.output_dir).glob("tmp_flow_*")))

    def test_public_production_and_export_order_are_stable_after_out_of_order_completion(self):
        self.ready()
        run = self.ws.state["runs"][self.rid]
        original = run["production"][0]
        other_order = copy.deepcopy(self.ws.order(original["order_id"]))
        other_order.update(id=uuid.uuid4().hex[:20], order_index=2, title="second")
        self.ws.state["orders"].append(other_order)
        run["order_ids"].append(other_order["id"])
        second = {**copy.deepcopy(original), "order_id": other_order["id"], "plan_id": "finished-first"}
        run["production"] = [second, original]
        self.assertEqual([p["order_id"] for p in flow.public_run(self.ws, self.rid)["production"]], run["order_ids"])
        export = flow._export_one
        sequence = []
        def record_order(ws, current, product, forced):
            sequence.append(product["order_id"])
            return export(ws, current, product, forced)
        with patch.object(flow, "_export_one", side_effect=record_order):
            job = self.wait(flow.start_export(self.ws, {"run_id": self.rid, "force": True, "confirmed": True}))
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(sequence, run["order_ids"])

    def test_scroll_fragment_uses_role_total_without_inventing_each_fragment_amount(self):
        group = {"key": "scroll", "material_ids": ["invoice", "o1", "o2", "payment"], "checks": checks()}
        rows = [{"material_id": "invoice", "category": "invoice", "facts": facts("invoice")},
                {"material_id": "o1", "category": "order", "facts": {"amount": None, "address": "原地址片段"}},
                {"material_id": "o2", "category": "order", "facts": facts("order")},
                {"material_id": "payment", "category": "payment", "facts": facts("payment")}]
        self.assertFalse(any(i["code"] == "amount_unknown" for i in flow._local_issues(group, rows)))
        rows[2]["facts"]["amount"] = None
        self.assertFalse(any(i["code"] == "amount_unknown" for i in flow._local_issues(group, rows)))
        self.assertIsNone(rows[2]["facts"]["amount"])

    def test_border_plans_batch_eight_complete_ids_and_audit_original_source_boxes(self):
        mids = [self.add() for _ in range(17)]
        for mid in mids:
            path = Path(self.ws.material(mid)["original"])
            with Image.new("RGB", (240, 300), "black") as image:
                ImageDraw.Draw(image).rectangle((10, 12, 225, 283), fill="white")
                ImageDraw.Draw(image).text((30, 50), "10.01 original", fill="black")
                image.save(path)
        folder = self.root / "border-batches"; folder.mkdir()
        records = flow._production_records(self.ws, mids, folder)
        original = {r["id"]: Path(r["path"]).read_bytes() for r in records}
        client, warnings, audits = BatchMock(self.ws.settings), [], []
        checked = flow._border_records(records, folder, client, warnings, audits)
        self.assertEqual([len(c[1]) for c in client.calls], [8, 8, 1])
        self.assertEqual([r["id"] for r in checked], mids)
        self.assertEqual([a["source_box"] for a in audits], [[10, 12, 226, 284]] * 17)
        self.assertTrue(all(a["model_disagreed"] for a in audits))
        self.assertEqual(client.calls[0][1][0]["border_candidate"]["margins_px"], [10, 12, 14, 16])
        self.assertEqual({r["id"]: Path(r["path"]).read_bytes() for r in records}, original)
        self.assertTrue(all(Path(r["path"]).parent == folder for r in checked))
        self.assertFalse(warnings)

    def test_failed_or_incomplete_border_plan_preserves_every_input(self):
        mids = [self.add() for _ in range(3)]
        folder = self.root / "border-failure"; folder.mkdir()
        records = flow._production_records(self.ws, mids, folder)
        client = BatchMock(self.ws.settings)
        for side_effect in (RuntimeError("model failure"), {"materials": [{"material_id": mids[0]}], "warnings": []}):
            warnings, audits = [], []
            kwargs = {"side_effect": side_effect} if isinstance(side_effect, Exception) else {"return_value": side_effect}
            with patch.object(client, "plan_borders", **kwargs):
                result = flow._border_records(records, folder, client, warnings, audits)
            self.assertEqual(result, records)
            self.assertTrue(warnings)
            self.assertEqual(audits[0]["status"], "model_failed")
            self.assertEqual(audits[0]["source_ids"], mids)

    def test_mixed_model_tight_boxes_expand_to_preserve_all_source_pixels(self):
        mid = self.add()
        folder = self.root / "mixed-coverage"; folder.mkdir()
        record = flow._production_records(self.ws, [mid], folder)[0]
        record["source_id"] = "original-parent"
        record["source_box"] = [20, 30, 260, 330]
        record["extracted"] = {"extra": {"regions": [{"category": "order"}, {"category": "payment"}]}}
        client = BatchMock(self.ws.settings)
        response = {"materials": [{"material_id": mid, "regions": [
            {"category": "order", "box": [.1, .1, .35, .7], "reason": "left"},
            {"category": "payment", "box": [.55, .2, .35, .6], "reason": "right"}]}], "warnings": []}
        warnings, audits = [], []
        with patch.object(client, "plan_regions", return_value=response):
            pieces = flow._mixed_records([record], folder, client, warnings, audits)
        self.assertEqual(len(pieces), 2)
        self.assertEqual(pieces[0]["source_box"], [20, 30, 140, 330])
        self.assertEqual(pieces[1]["source_box"], [140, 30, 260, 330])
        with Image.open(record["path"]) as source:
            joined = Image.new("RGB", source.size, "white")
            try:
                for x, piece in zip((0, 120), pieces):
                    with Image.open(piece["path"]) as image:
                        joined.paste(image, (x, 0))
                self.assertEqual(joined.tobytes(), source.tobytes())
            finally:
                joined.close()
        self.assertEqual(audits[0]["coverage"], "complete-source")
        self.assertFalse(warnings)

    def _side_records(self, folder, count=3):
        records = []
        for n in range(count):
            path = folder / f"side-{n}.png"
            with Image.new("RGB", (120, 160), (n * 60, 100, 180)) as image:
                image.save(path)
            records.append({"id": f"s{n}", "name": path.name, "category": "order", "path": str(path), "width": 120, "height": 160})
        return records

    def test_independent_pages_never_forced_into_seamless_stitch(self):
        folder = self.root / "independent"; folder.mkdir()
        records = self._side_records(folder)
        warnings, audits = [], []
        with patch.object(flow, "smart_stitch", side_effect=AssertionError("independent pages must remain whole")):
            result = flow._save_side(records, folder, "order", BatchMock(self.ws.settings), warnings, audits, [])
        with Image.open(result) as image:
            self.assertEqual(image.size, (120, 484))
            self.assertEqual(image.getpixel((10, 10)), (0, 100, 180))
            self.assertEqual(image.getpixel((10, 160)), (255, 255, 255))
            self.assertEqual(image.getpixel((10, 170)), (60, 100, 180))
            self.assertEqual(image.getpixel((10, 340)), (120, 100, 180))
        self.assertEqual(len([a for a in audits if a["kind"] == "complete-fragment"]), 3)
        self.assertFalse(warnings)

    def test_stitch_sets_only_stitch_named_scroll_fragments_and_generic_notice_is_info(self):
        folder = self.root / "named-stitch"; folder.mkdir()
        records = self._side_records(folder)
        warnings, audits = [], []
        def stitched(subset, plan=None):
            self.assertEqual([r["id"] for r in subset], ["s1", "s0"])
            return {"image": Image.new("RGB", (120, 200), "white"), "status": "matched", "joins": [], "segments": [],
                    "warnings": ["拼接仅证明局部视觉连续，不能证明同一订单；请核对原图。"]}
        with patch.object(flow, "smart_stitch", side_effect=stitched) as operation:
            result = flow._save_side(records, folder, "order", BatchMock(self.ws.settings), warnings, audits,
                                     [{"category": "order", "material_ids": ["s1", "s0"]}])
        self.assertEqual(operation.call_count, 1)
        self.assertFalse(warnings)
        audit = next(a for a in audits if a["kind"] == "stitch")
        self.assertTrue(audit["applied"])
        self.assertTrue(audit["warnings"])
        self.assertEqual(next(a for a in audits if a["kind"] == "complete-fragment")["source_ids"], ["s2"])
        self.assertTrue(Path(result).is_file())

    def test_exact_duplicates_audited_and_changed_overlap_keeps_both_full_views(self):
        folder = self.root / "different-view"; folder.mkdir()
        records = self._side_records(folder, 2)
        records[0]["extracted"] = {"amount": "10.00"}
        records[1]["extracted"] = {"amount": "10.01"}
        duplicate = {**records[0], "id": "duplicate"}
        warnings, audits = [], []
        result = {"image": Image.new("RGB", (120, 200), "white"), "status": "matched", "segments": [],
                  "joins": [{"alternate_view_requires_review": True}], "warnings": ["重叠区域存在不同视图"]}
        with patch.object(flow, "smart_stitch", return_value=result):
            path = flow._save_side(records + [duplicate], folder, "order", BatchMock(self.ws.settings), warnings, audits,
                                   [{"category": "order", "material_ids": ["s0", "s1"]}])
        with Image.open(path) as image:
            self.assertEqual(image.size, (120, 322))
            self.assertEqual(image.getpixel((10, 10)), (0, 100, 180))
            self.assertEqual(image.getpixel((10, 200)), (60, 100, 180))
        self.assertTrue(any(a["kind"] == "exact-duplicate" and a["id"] == "duplicate" for a in audits))
        self.assertTrue(any(a["kind"] == "stitch" and not a["applied"] for a in audits))
        self.assertTrue(warnings)

    def test_matched_masked_to_visible_view_uses_real_stitch_without_force_warning(self):
        folder = self.root / "masked-view"; folder.mkdir()
        records = self._side_records(folder, 2)
        records[0]["extracted"] = {"order_no": "Y2-PCB-001", "amount": "626.16"}
        records[1]["extracted"] = {"order_no": "SMT-SERVICE-001", "amount": "626.16"}
        warnings, audits = [], []
        client = BatchMock(self.ws.settings)
        plan = {"ordered_ids": ["s0", "s1"], "joins": [{"above_id": "s0", "below_id": "s1", "view_change": "masked_to_visible",
                                                       "view_reason": "同一收货栏手机号展开显示，未见金额冲突", "reason": "连续正文重叠"}]}
        result = {"image": Image.new("RGB", (120, 200), (10, 20, 30)), "status": "matched", "segments": [],
                  "joins": [{"above_id": "s0", "below_id": "s1", "alternate_view_requires_review": True}],
                  "warnings": ["交叠区存在不同原图像素，接缝采用下图原始视图"]}
        with patch.object(client, "plan_stitch", return_value=plan), patch.object(flow, "smart_stitch", return_value=result):
            path = flow._save_side(records, folder, "order", client, warnings, audits,
                                   [{"category": "order", "material_ids": ["s0", "s1"]}])
        with Image.open(path) as image:
            self.assertEqual(image.size, (120, 200))
            self.assertEqual(image.getpixel((0, 0)), (10, 20, 30))
        audit = next(a for a in audits if a["kind"] == "stitch")
        self.assertTrue(audit["applied"])
        self.assertFalse(audit["blocking"])
        self.assertEqual(audit["view_changes"][0]["view_change"], "masked_to_visible")
        self.assertEqual(audit["order_identifiers"], ["Y2-PCB-001", "SMT-SERVICE-001"])
        self.assertFalse(flow._stitch_fact_conflict(records))
        self.assertTrue(any("主单/子单/服务单" in w for w in warnings))
        self.assertTrue(warnings)  # The actual difference remains visible.
        self.assertTrue(all(Path(r["path"]).is_file() for r in records))

    def test_distinct_ids_are_audited_but_different_amounts_block_stitch(self):
        folder = self.root / "identifier-conflict"; folder.mkdir()
        records = self._side_records(folder, 2)
        records[0]["extracted"] = {"order_no": "master-1", "amount": "626.16"}
        records[1]["extracted"] = {"order_no": "child-1", "amount": "626.17"}
        self.assertTrue(flow._stitch_fact_conflict(records))
        records[1]["extracted"]["amount"] = None
        self.assertFalse(flow._stitch_fact_conflict(records))
        records[1]["extracted"]["amount"] = "626.17"
        warnings, audits = [], []
        plan = {"ordered_ids": ["s0", "s1"], "joins": [{"above_id": "s0", "below_id": "s1", "view_change": "masked_to_visible",
                                                       "view_reason": "屏幕信息展开", "reason": "连续片段"}]}
        result = {"image": Image.new("RGB", (120, 200), "white"), "status": "matched", "segments": [],
                  "joins": [{"above_id": "s0", "below_id": "s1", "alternate_view_requires_review": True}], "warnings": ["重叠区不同视图"]}
        client = BatchMock(self.ws.settings)
        with patch.object(client, "plan_stitch", return_value=plan), patch.object(flow, "smart_stitch", return_value=result):
            path = flow._save_side(records, folder, "order", client, warnings, audits,
                                   [{"category": "order", "material_ids": ["s0", "s1"]}])
        with Image.open(path) as image:
            self.assertEqual(image.size, (120, 322))
        self.assertTrue(any("金额/订单号等内容冲突" in w for w in warnings))
        self.assertTrue(next(a for a in audits if a["kind"] == "stitch")["blocking"])

    def test_layout_defaults_are_available_to_ai_but_explicit_right_margin_is_respected(self):
        mids = self.reviewed()
        job = self.wait(flow.start_make(self.ws, {"run_id": self.rid, "confirmed": True}))
        self.assertEqual(job["status"], "done", job)
        run = self.ws.state["runs"][self.rid]
        product = run["production"][0]
        self.assertEqual(product["layout"]["layout_left_mm"], 24)
        self.assertEqual(product["layout"]["layout_right_mm"], 24)
        self.assertTrue(product["layout"]["layout_order_on_left"])
        self.assertEqual(product["layout"]["layout_vertical_align"], "center")
        self.assertEqual(product["layout"]["layout_top_mm"], 24)
        self.assertEqual(product["layout"]["layout_bottom_mm"], 24)
        self.ws.order(product["order_id"])["layout"] = {"layout_right_mm": 30}
        job = self.wait(flow.start_make(self.ws, {"run_id": self.rid, "confirmed": True}))
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(run["production"][0]["layout"]["layout_right_mm"], 30)

    def _pdf_source(self, material_id, pages=2):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, NameObject
        material = self.ws.material(material_id)
        path = Path(material["original"]).with_suffix(".pdf")
        writer = PdfWriter()
        for n in range(pages):
            page = writer.add_blank_page(width=595, height=842)
            stream = DecodedStreamObject(); stream.set_data(f"{n + 1} 0 0 RG 20 20 40 40 re S".encode())
            page[NameObject("/Contents")] = writer._add_object(stream)
        with path.open("wb") as output:
            writer.write(output)
        writer.close()
        material["original"] = str(path)
        return path

    def test_multi_page_pdf_invoice_keeps_vector_pages_and_all_preview_pages_on_make_and_reflow(self):
        mids = self.reviewed()
        source = self._pdf_source(mids[0], 3)
        before = source.read_bytes()
        self.ws.settings.extra["invoice_layout_mode"] = "print"
        def render(pdf, target, scale=1.5, page_index=0):
            self.assertFalse(self.ws.lock._is_owned(), "PDF rendering must not block workspace lock")
            with Image.new("RGB", (300, 200), (page_index * 60, 140, 200)) as image:
                image.save(target)
            return Path(target), 3
        with patch.object(flow, "render_pdf_page", side_effect=render) as renderer:
            job = self.wait(flow.start_make(self.ws, {"run_id": self.rid, "confirmed": True}))
            self.assertEqual(job["status"], "done", job)
            product = self.ws.state["runs"][self.rid]["production"][0]
            self.assertEqual(product["status"], "ready", product)
            self.assertEqual([p["page"] for p in product["invoice_pages"]], [1, 2, 3])
            self.assertEqual([p["source_page"] for p in product["invoice_pages"]], [1, 2, 3])
            self.assertTrue(all(p["total_pages"] == 3 and p["source_id"] == mids[0] for p in product["invoice_pages"]))
            self.assertEqual(product["invoice_preview_pages"], product["invoice_pages"])
            self.assertEqual(len(product["voucher_pages"]), 1)
            self.assertEqual(renderer.call_count, 4)  # fresh high-resolution source view plus all 3 output pages
            self.assertTrue(all(call.kwargs.get("scale") == 4.0 for call in renderer.call_args_list))
            from pypdf import PdfReader
            reader = PdfReader(product["_invoice_pdf"])
            self.assertEqual(len(reader.pages), 3)
            self.assertTrue(all(b"re" in p.get_contents().get_data() for p in reader.pages))
            old_pid = product["plan_id"]
            job = self.wait(flow.start_reflow(self.ws, {"run_id": self.rid, "order_id": product["order_id"], "layout": {"layout_column_ratio": .6}}))
            self.assertEqual(job["status"], "done", job)
            current = self.ws.state["runs"][self.rid]["production"][0]
            self.assertNotEqual(current["plan_id"], old_pid)
            self.assertEqual(len(current["invoice_pages"]), 3)
            self.assertTrue(all(current["plan_id"] in p["url"] for p in current["invoice_pages"] + current["voucher_pages"]))
        self.assertEqual(source.read_bytes(), before)

    def test_order_pdf_every_page_retained_and_never_scroll_stitched(self):
        mid = self.add()
        self.ws.material(mid)["category"] = "order"
        self._pdf_source(mid, 3)
        folder = self.root / "pdf-order"; folder.mkdir()
        def render(pdf, target, scale=1.5, page_index=0):
            self.assertFalse(self.ws.lock._is_owned())
            with Image.new("RGB", (240, 300), (page_index * 60, 120, 200)) as image:
                image.save(target)
            return Path(target), 3
        with patch.object(flow, "render_pdf_page", side_effect=render):
            records = flow._production_records(self.ws, [mid], folder)
        self.assertEqual([r["source_page"] for r in records], [1, 2, 3])
        self.assertEqual(len({r["id"] for r in records}), 3)
        with patch.object(flow, "smart_stitch", side_effect=AssertionError("PDF pages are not scroll pieces")):
            path = flow._save_side(records, folder, "order", BatchMock(self.ws.settings), [], [],
                                   [{"category": "order", "material_ids": [mid]}])
        with Image.open(path) as image:
            self.assertGreater(image.height, 900)

    def test_preview_page_limit_reports_all_pages_without_silently_truncating(self):
        mid = self.add("invoice")
        source = self._pdf_source(mid, 3)
        folder = self.root / "preview-limit"; folder.mkdir()
        with patch.object(flow, "MAX_PREVIEW_PAGES", 2), patch.object(flow, "render_pdf_page") as renderer:
            with self.assertRaisesRegex(ValueError, "3页.*上限2页"):
                flow._invoice_previews(folder, "p", [source], [source], self.ws.settings)
            renderer.assert_not_called()

    def test_system_auto_retry_uses_issues_without_user_chat_and_is_bounded(self):
        self.add()
        original = SessionMock.analyze_batch
        def uncertain(client, records, session=None, **kwargs):
            result = original(client, records, session, **kwargs)
            for check in result["result"]["groups"][0]["checks"]:
                if check["code"] == "address":
                    check.update(status="review", message="需重看图上地址")
            return result
        with patch.object(SessionMock, "analyze_batch", uncertain):
            job = self.wait(flow.start_review(self.ws, {"run_id": self.rid, "consent": True}))
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(len(SessionMock.calls), 2)
        self.assertTrue(SessionMock.calls[-1][2]["revisit"])
        self.assertIn("系统内部审核任务", SessionMock.calls[-1][2]["feedback"])
        self.assertIn("需重看图上地址", SessionMock.calls[-1][2]["feedback"])
        self.assertEqual(self.ws.state["runs"][self.rid]["feedback"], "")
        self.assertTrue(self.ws.state["runs"][self.rid]["review"]["issues"])

    def test_info_and_conservative_retention_warnings_allow_normal_export_but_real_failure_blocks(self):
        self.ready()
        run = self.ws.state["runs"][self.rid]
        product = run["production"][0]
        self.assertTrue(any(i["code"] == "invoice_from_image" for i in product["info"]))
        product["warnings"].append("边框复核不可用，完整图已保留")
        job = self.wait(flow.start_export(self.ws, {"run_id": self.rid, "confirmed": True}))
        self.assertEqual(job["status"], "done", job)
        self.assertFalse(run["exports"][0]["forced"])
        product["blocking_issues"] = [{"code": "clarity", "message": "原件文字模糊，看不清楚"}]
        with self.assertRaises(ValueError):
            flow.start_export(self.ws, {"run_id": self.rid, "confirmed": True, "new_copy": True})

    def test_production_has_maximum_four_workers_and_isolated_failures(self):
        mids = [self.add() for _ in range(6)]
        run = self.ws.state["runs"][self.rid]
        payload = {"materials": [{"material_id": mid, "category": "order", "facts": facts()} for mid in mids],
                   "groups": [{"key": str(n), "title": str(n), "material_ids": [mid], "checks": checks()} for n, mid in enumerate(mids)],
                   "unassigned": [], "trash": [], "warnings": []}
        flow._apply_review(self.ws, run, payload)
        counter, highest, gate = [0], [0], threading.Lock()
        def make(ws, rid, group, cfg, enabled, forced):
            with gate:
                counter[0] += 1; highest[0] = max(highest[0], counter[0])
            time.sleep(.04)
            with gate:
                counter[0] -= 1
            if group["key"] == "1":
                raise RuntimeError("single purchase failed")
            return {"order_id": group["order_id"], "title": group["title"], "status": "ready", "warnings": [], "error": ""}
        with patch.object(flow, "_make_one", side_effect=make):
            job = self.wait(flow.start_make(self.ws, {"run_id": self.rid, "force": True, "confirmed": True}))
        self.assertEqual(job["status"], "done", job)
        self.assertLessEqual(highest[0], 4)
        self.assertGreater(highest[0], 1)
        self.assertEqual(job["result"]["failed"], 1)
        self.assertEqual(len([p for p in run["production"] if p["status"] == "ready"]), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
