"""smart_stitch regression tests; synthetic fixtures are content-bearing and OCR-free."""
from __future__ import annotations

import random
import shutil
import sys
import time
import unittest
import uuid
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.smart_stitch import smart_stitch


class SmartStitchCase(unittest.TestCase):
    def setUp(self):
        self.work = ROOT / "output" / "_smart_stitch_tests" / uuid.uuid4().hex
        self.work.mkdir(parents=True, exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(self.work, ignore_errors=True))

    def save(self, name, im):
        path = self.work / name
        im.save(path, "PNG")
        return path

    def records(self, parts, order=None, **meta):
        order = order or list(parts)
        out = []
        for sid in order:
            path = parts[sid]
            with Image.open(path) as im:
                width, height = im.size
            row = {"id": sid, "path": str(path), "name": path.name,
                   "width": width, "height": height}
            row.update(meta.get(sid, {}))
            out.append(row)
        return out

    def long_document(self, width=320, height=900):
        im = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(im)
        # Deterministic glyph-like texture and variable values, not nearly
        # identical solid bars. No OCR or external fonts are used to match it.
        for y in range(30, height - 20, 47):
            draw.line((18, y, width - 18, y), fill=(235, 235, 235))
            rng = random.Random(y + 1009)
            for x in range(25, width - 35, 12):
                for gy in range(7):
                    bits = rng.randrange(1, 31)
                    for gx in range(5):
                        if bits & (1 << gx):
                            draw.rectangle((x + gx * 2, y + 9 + gy * 2,
                                            x + gx * 2 + 1, y + 10 + gy * 2), fill=(40, 50, 60))
        return im

    def test_three_unordered_overlapping_segments_are_ordered_and_deduped(self):
        doc = self.long_document()
        parts = {
            "top": self.save("top.png", doc.crop((0, 0, 320, 390))),
            "middle": self.save("middle.png", doc.crop((0, 300, 320, 700))),
            "bottom": self.save("bottom.png", doc.crop((0, 610, 320, 900))),
        }
        result = smart_stitch(self.records(parts, ["bottom", "top", "middle"]))
        self.assertEqual(result["ordered_ids"], ["top", "middle", "bottom"])
        self.assertEqual(result["status"], "matched")
        self.assertTrue(result["requires_review"])
        self.assertGreaterEqual(len(result["joins"]), 2)
        self.assertGreater(result["confidence"], 0.69)
        self.assertAlmostEqual(result["image"].height, 900, delta=4)
        self.assertAlmostEqual(result["image"].width, 320, delta=2)
        # The source coordinates must cover the original page without holes.
        origin = {"top": 0, "middle": 300, "bottom": 610}
        for prev, nxt in zip(result["segments"], result["segments"][1:]):
            self.assertAlmostEqual(origin[prev["id"]] + prev["source_box"][3],
                                   origin[nxt["id"]] + nxt["source_box"][1], delta=3)
        self.assertEqual(len(result["segments"]), 3)
        self.assertTrue(all(len(s["source_box"]) == 4 and len(s["dest_box"]) == 4 for s in result["segments"]))

    def test_different_widths_align_to_target_width_and_keep_content(self):
        doc = self.long_document(400, 800)
        top = self.save("top.png", doc.crop((0, 0, 400, 380)))
        # Same document at a mildly different width; resampling is expected.
        middle_im = doc.crop((0, 300, 400, 680)).resize((380, 361), Image.Resampling.LANCZOS)
        middle = self.save("middle.png", middle_im)
        result = smart_stitch(self.records({"t": top, "m": middle}, ["m", "t"]))
        self.assertEqual(result["ordered_ids"], ["t", "m"])
        self.assertAlmostEqual(result["image"].width, 390, delta=3)  # median + small alignment shift
        self.assertGreater(result["image"].height, 560)
        self.assertTrue(all(s["source_box"][2] > s["source_box"][0] for s in result["segments"]))

    def test_source_files_and_unique_fact_pixels_are_preserved(self):
        doc = self.long_document(280, 700)
        # A unique red fact sits only in the bottom segment.
        draw = ImageDraw.Draw(doc)
        draw.rectangle((30, 650, 250, 676), fill=(230, 20, 20))
        before = {}
        parts = {
            "a": self.save("a.png", doc.crop((0, 0, 280, 390))),
            "b": self.save("b.png", doc.crop((0, 280, 280, 700))),
        }
        before = {k: p.read_bytes() for k, p in parts.items()}
        result = smart_stitch(self.records(parts, ["b", "a"]))
        self.assertEqual(result["status"], "matched")
        self.assertEqual({k: p.read_bytes() for k, p in parts.items()}, before)
        red = result["image"]
        self.assertTrue(any(count > 300 and color == (230, 20, 20)
                            for count, color in (red.getcolors(red.width * red.height) or [])))
        self.assertAlmostEqual(red.height, 700, delta=4)
        # Each segment's audit source box is a subset, never an invented box.
        for sid, seg in zip(result["ordered_ids"], result["segments"]):
            self.assertEqual(sid, seg["id"])
            self.assertGreaterEqual(seg["source_box"][0], 0)
            self.assertGreaterEqual(seg["source_box"][1], 0)

    def test_ambiguous_repeated_content_is_not_guessed(self):
        # Three identical panels make both directions visually plausible.
        panel = Image.new("RGB", (260, 300), "white")
        draw = ImageDraw.Draw(panel)
        for y in (30, 90, 150, 210):
            draw.rectangle((30, y, 230, y + 20), fill=(40, 40, 40))
        parts = {
            "a": self.save("a.png", panel.copy()),
            "b": self.save("b.png", panel.copy()),
            "c": self.save("c.png", panel.copy()),
        }
        result = smart_stitch(self.records(parts, ["c", "a", "b"]))
        self.assertEqual(result["status"], "unresolved")
        self.assertTrue(result["requires_review"])
        self.assertTrue(any("相近" in w or "可靠" in w for w in result["warnings"]))
        self.assertEqual(result["ordered_ids"], ["c", "a", "b"])
        self.assertEqual(len(result["segments"]), 3)

    def test_no_overlap_returns_unresolved_contact_sheet_without_crop(self):
        a = Image.new("RGB", (240, 150), (255, 250, 250))
        b = Image.new("RGB", (240, 150), (250, 250, 255))
        ImageDraw.Draw(a).rectangle((20, 40, 220, 100), fill=(210, 30, 30))
        ImageDraw.Draw(b).rectangle((20, 40, 220, 100), fill=(30, 30, 210))
        parts = {"a": self.save("a.png", a), "b": self.save("b.png", b)}
        result = smart_stitch(self.records(parts, ["b", "a"]))
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["ordered_ids"], ["b", "a"])
        self.assertEqual(result["image"].height, 312)  # two 150px panels plus contact gap
        self.assertEqual([s["source_box"] for s in result["segments"]], [[0, 0, 240, 150], [0, 0, 240, 150]])

    def test_conflicting_order_metadata_never_cross_stitches(self):
        doc = self.long_document(240, 500)
        parts = {
            "a": self.save("a.png", doc.crop((0, 0, 240, 300))),
            "b": self.save("b.png", doc.crop((0, 220, 240, 500))),
        }
        result = smart_stitch(self.records(parts, ["b", "a"], a={"order_id": "A"}, b={"order_id": "B"}))
        self.assertEqual(result["status"], "unresolved")
        self.assertTrue(any("不同订单" in w for w in result["warnings"]))
        self.assertEqual(result["ordered_ids"], ["b", "a"])

    def test_ai_plan_order_is_accepted_only_when_local_evidence_supports_it(self):
        doc = self.long_document(240, 500)
        parts = {
            "top": self.save("top.png", doc.crop((0, 0, 240, 290))),
            "bottom": self.save("bottom.png", doc.crop((0, 220, 240, 500))),
        }
        plan = {"ordered_ids": ["top", "bottom"], "joins": [{"above_id": "top", "below_id": "bottom", "overlap_hint": .25, "reason": "AI repeated content candidate"}]}
        result = smart_stitch(self.records(parts, ["bottom", "top"]), plan)
        self.assertEqual(result["ordered_ids"], ["top", "bottom"])
        self.assertEqual(result["status"], "matched")
        self.assertTrue(result["requires_review"])
        self.assertGreater(result["confidence"], .69)

    def test_ai_crop_is_applied_only_when_surrounding_regions_are_blank(self):
        doc = Image.new("RGB", (240, 200), "white")
        ImageDraw.Draw(doc).rectangle((40, 40, 200, 160), fill=(100, 100, 100))
        path = self.save("crop.png", doc)
        good = smart_stitch(self.records({"x": path}), {"crops": [{"id": "x", "box": [39/240, 39/200, 163/240, 123/200]}]})
        self.assertEqual(good["segments"][0]["source_box"], [39, 39, 202, 162])
        bad = smart_stitch(self.records({"x": path}), {"crops": [{"id": "x", "box": [0, 0, 0.5, 1]}]})
        self.assertTrue(any("忽略 x" in w for w in bad["warnings"]))
        self.assertEqual(bad["segments"][0]["source_box"], [0, 0, 240, 200])

    def test_invalid_plan_and_duplicate_ids_are_safe(self):
        path = self.save("one.png", Image.new("RGB", (80, 80), "white"))
        with self.assertRaises(ValueError):
            smart_stitch([{"id": "x", "path": str(path)}, {"id": "x", "path": str(path)}])
        result = smart_stitch([{"id": "x", "path": str(path)}], plan="bad")
        self.assertTrue(any("格式无效" in w for w in result["warnings"]))
        self.assertTrue(result["requires_review"])

    def test_missing_required_record_fields_fail_loudly(self):
        path = self.save("one.png", Image.new("RGB", (80, 80), "white"))
        for record in ({"id": "x"}, {"path": str(path)}, {"id": "x", "path": str(self.work / "missing.png")}):
            with self.subTest(record=record):
                with self.assertRaises((ValueError, FileNotFoundError)):
                    smart_stitch([record])

    def test_single_image_is_reliable_but_still_requires_review(self):
        path = self.save("one.png", Image.new("RGB", (100, 120), (20, 30, 40)))
        result = smart_stitch([{"id": "one", "path": str(path)}])
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["ordered_ids"], ["one"])
        self.assertEqual(result["confidence"], 1.0)
        self.assertTrue(result["requires_review"])
        self.assertEqual(result["image"].size, (100, 120))

    def test_metadata_dimensions_do_not_override_decoded_dimensions(self):
        path = self.save("one.png", Image.new("RGB", (100, 120), (20, 30, 40)))
        result = smart_stitch([{"id": "one", "path": str(path), "width": 9999, "height": 9999}])
        self.assertEqual(result["image"].size, (100, 120))

    def test_fixed_header_is_verified_elsewhere_before_skipping(self):
        doc = self.long_document(480, 1000)
        top = doc.crop((0, 0, 480, 630))
        below = Image.new("RGB", (480, 540), "white")
        below.paste(doc.crop((0, 0, 480, 80)), (0, 0))
        below.paste(doc.crop((0, 440, 480, 900)), (0, 80))
        parts = {"top": self.save("header-top.png", top), "lower": self.save("header-lower.png", below)}
        result = smart_stitch(self.records(parts, ["lower", "top"]))
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["ordered_ids"], ["top", "lower"])
        self.assertGreaterEqual(result["joins"][0]["skip_prefix_px"], 65)
        self.assertAlmostEqual(result["image"].height, 900, delta=5)

    def test_unique_unexplained_prefix_is_not_discarded(self):
        doc = self.long_document(480, 1000)
        unique = self.long_document(480, 540)
        unique.paste(doc.crop((0, 440, 480, 900)), (0, 80))
        # Different deterministic glyphs than the top's first two content lines.
        unique.paste(doc.crop((0, 800, 480, 880)), (0, 0))
        parts = {"top": self.save("unique-top.png", doc.crop((0, 0, 480, 630))),
                 "lower": self.save("unique-prefix.png", unique)}
        result = smart_stitch(self.records(parts, ["lower", "top"]))
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["segments"][0]["source_box"], [0, 0, 480, 540])
        self.assertEqual(result["segments"][1]["source_box"], [0, 0, 480, 630])

    def test_jpeg_resampling_and_small_horizontal_translation(self):
        doc = self.long_document(640, 1000)
        top = self.save("jpeg-top.png", doc.crop((0, 0, 640, 600)))
        lower = Image.new("RGB", (640, 600), "white")
        lower.paste(doc.crop((0, 400, 638, 1000)), (2, 0))
        path = self.work / "lower.jpg"
        lower.save(path, "JPEG", quality=88)
        result = smart_stitch(self.records({"top": top, "lower": path}, ["lower", "top"]))
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["ordered_ids"], ["top", "lower"])
        self.assertAlmostEqual(result["image"].height, 1000, delta=6)
        self.assertLessEqual(abs(result["joins"][0]["x_shift_px"]), 5)

    def test_empty_and_excessive_input_lists_are_rejected(self):
        with self.assertRaises(ValueError):
            smart_stitch([])
        path = self.save("count.png", Image.new("RGB", (80, 80), "white"))
        with self.assertRaises(ValueError):
            smart_stitch([{"id": str(n), "path": str(path)} for n in range(9)])

    def test_ai_order_and_hint_never_force_unrelated_pixels(self):
        a = self.save("blank-a.png", Image.new("RGB", (160, 200), "white"))
        b = self.save("blank-b.png", Image.new("RGB", (160, 200), "white"))
        result = smart_stitch(self.records({"a": a, "b": b}, ["b", "a"]), {
            "ordered_ids": ["a", "b"],
            "joins": [{"above_id": "a", "below_id": "b", "overlap_hint": .9, "reason": "AI assumes order"}],
        })
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["ordered_ids"], ["b", "a"])
        self.assertEqual(result["joins"], [])
        self.assertEqual(result["confidence"], 0)

    def test_ai_reason_is_retained_as_separate_untrusted_audit(self):
        doc = self.long_document(320, 700)
        parts = {"a": self.save("hint-a.png", doc.crop((0, 0, 320, 420))),
                 "b": self.save("hint-b.png", doc.crop((0, 280, 320, 700)))}
        result = smart_stitch(self.records(parts, ["b", "a"]), {
            "ordered_ids": ["a", "b"], "crops": [],
            "joins": [{"above_id": "a", "below_id": "b", "overlap_hint": 1/3,
                       "reason": "Visible repeated row textures"}],
        })
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["joins"][0]["ai_reason"], "Visible repeated row textures")
        self.assertAlmostEqual(result["joins"][0]["ai_overlap_hint"], 1/3)
        self.assertIn("二维匹配", result["joins"][0]["reason"])

    def test_every_output_segment_is_resampled_source_pixels_only(self):
        doc = self.long_document(320, 700)
        parts = {"a": self.save("pixel-a.png", doc.crop((0, 0, 320, 420))),
                 "b": self.save("pixel-b.png", doc.crop((0, 280, 320, 700)))}
        result = smart_stitch(self.records(parts, ["b", "a"]))
        self.assertEqual(result["status"], "matched")
        for segment in result["segments"]:
            left, top, right, bottom = segment["dest_box"]
            with Image.open(parts[segment["id"]]) as source:
                expected = source.convert("RGB").crop(segment["source_box"]).resize(
                    (right - left, bottom - top), Image.Resampling.LANCZOS)
            actual = result["image"].crop(segment["dest_box"])
            self.assertIsNone(ImageChops.difference(expected, actual).getbbox())

    def test_white_background_and_table_rules_alone_are_not_evidence(self):
        panel = Image.new("RGB", (320, 400), "white")
        draw = ImageDraw.Draw(panel)
        for y in range(10, 400, 35):
            draw.line((10, y, 310, y), fill=(170, 170, 170), width=1)
        for x in (10, 100, 170, 310):
            draw.line((x, 10, x, 395), fill=(170, 170, 170), width=1)
        parts = {"a": self.save("grid-a.png", panel), "b": self.save("grid-b.png", panel.copy())}
        result = smart_stitch(self.records(parts, ["b", "a"]))
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["joins"], [])
        self.assertEqual(result["segments"][0]["source_box"], [0, 0, 320, 400])

    def test_malformed_ai_crops_do_not_truncate_or_crash(self):
        path = self.save("bad-crop.png", Image.new("RGB", (100, 120), (20, 30, 40)))
        for bad in ([0, 0, float("nan"), 1], [0, 0, 2, 1], [0, 0, -1, 1], "bad"):
            with self.subTest(bad=bad):
                result = smart_stitch([{"id": "a", "path": str(path)}],
                                      {"ordered_ids": ["missing"], "crops": [{"id": "a", "box": bad}], "joins": "bad"})
                self.assertEqual(result["segments"][0]["source_box"], [0, 0, 100, 120])
                self.assertTrue(result["warnings"])

    def test_actual_three_screenshots_without_reference_or_ai(self):
        root = ROOT / "examples" / "v1_1-input"
        paths = {"top": root / "截图-顶部.jpg", "middle": root / "截图-中段.jpg", "bottom": root / "截图-底部.jpg"}
        if not all(p.is_file() for p in paths.values()):
            self.skipTest("user-provided samples are not distributed")
        original_bytes = {k: p.read_bytes() for k, p in paths.items()}
        start = time.perf_counter()
        result = smart_stitch(self.records(paths, ["bottom", "top", "middle"]))
        elapsed = time.perf_counter() - start
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["ordered_ids"], ["top", "middle", "bottom"])
        self.assertAlmostEqual(result["image"].width, 1079, delta=6)
        self.assertAlmostEqual(result["image"].height, 2160, delta=150)
        self.assertLess(elapsed, 15)
        self.assertGreater(result["joins"][0]["skip_prefix_px"], 70)
        # The bottom's expanded phone/address are sourced from its original
        # pixels, not filled from the reference or synthesized by the engine.
        final_segment = result["segments"][-1]
        self.assertLess(final_segment["source_box"][1], 145)
        self.assertEqual(final_segment["source_box"][3], 850)
        self.assertEqual({k: p.read_bytes() for k, p in paths.items()}, original_bytes)
        self.assertTrue(result["requires_review"])
        self.assertTrue(result["joins"][1]["alternate_view_requires_review"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
