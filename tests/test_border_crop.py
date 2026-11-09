"""Pure outer-band calibration, content preservation and immutable-source tests."""
from __future__ import annotations

import hashlib
import random
import shutil
import unittest
import uuid
from pathlib import Path

from PIL import Image, ImageDraw

from app.core.border_crop import crop_black_borders


class BorderCropTests(unittest.TestCase):
    def framed(self, size=(600, 800), trim=(30, 40, 50, 60), color=(0, 0, 0)):
        im = Image.new("RGB", size, color)
        l, t, r, b = trim
        ImageDraw.Draw(im).rectangle((l, t, size[0] - r - 1, size[1] - b - 1), fill="white")
        ImageDraw.Draw(im).text((l + 20, t + 30), "Invoice 10.01 / original facts", fill="black")
        return im

    def crop(self, source, proposal=None):
        result = crop_black_borders(source, proposal)
        self.addCleanup(result["image"].close)
        return result

    def test_all_four_sides_exact_source_box_and_trim(self):
        with self.framed() as source:
            before = source.tobytes()
            result = self.crop(source)
            self.assertEqual(result["box"], [30, 40, 550, 740])
            self.assertEqual(result["trim"], {"left": 30, "top": 40, "right": 50, "bottom": 60})
            self.assertEqual(result["image"].size, (520, 700))
            self.assertEqual(source.tobytes(), before)
            self.assertEqual(result["image"].tobytes(), source.crop(result["box"]).tobytes())

    def test_model_box_is_only_side_nomination_not_content_crop_authority(self):
        with self.framed() as source:
            result = self.crop(source, {"box": [.3, .25, .4, .5], "has_border": True})
            self.assertEqual(result["box"], [30, 40, 550, 740])
            self.assertEqual(result["image"].getpixel((20, 30)), source.getpixel((50, 70)))

    def test_model_missed_border_does_not_veto_physically_proven_pure_frame(self):
        with self.framed() as source:
            for proposal in ([0, 0, 1, 1], {"box": [.1, .1, .8, .8], "has_border": False}):
                result = self.crop(source, proposal)
                self.assertEqual(result["box"], [30, 40, 550, 740])
                self.assertEqual(result["image"].tobytes(), source.crop(result["box"]).tobytes())

    def test_dark_sidebar_containing_text_rejects_entire_side(self):
        with Image.new("RGB", (600, 800), "white") as source:
            draw = ImageDraw.Draw(source)
            draw.rectangle((0, 0, 89, 799), fill=(12, 12, 12))
            draw.text((32, 380), "10.01", fill="white")
            result = self.crop(source, [.15, 0, .85, 1])
            self.assertEqual(result["box"], [0, 0, 600, 800])
            self.assertTrue(any("整侧保留" in text for text in result["warnings"]))

    def test_full_height_line_inside_dark_panel_does_not_authorize_shaving_its_blank_prefix(self):
        with Image.new("RGB", (600, 800), "white") as source:
            draw = ImageDraw.Draw(source)
            draw.rectangle((0, 0, 89, 799), fill=(12, 12, 12))
            draw.line((40, 0, 40, 799), fill="white", width=3)
            result = self.crop(source, [.15, 0, .85, 1])
            self.assertEqual(result["trim"]["left"], 0)
            self.assertTrue(result["warnings"])

    def test_subtle_connected_mark_on_dark_sidebar_is_content(self):
        with Image.new("RGB", (600, 800), "white") as source:
            draw = ImageDraw.Draw(source)
            draw.rectangle((0, 0, 89, 799), fill=(5, 5, 5))
            draw.line((20, 300, 22, 302), fill=(24, 24, 24), width=1)
            result = self.crop(source)
            self.assertEqual(result["trim"]["left"], 0)

    def test_rule_inside_border_is_preserved_even_when_tiny_in_total_area(self):
        with self.framed(size=(1000, 1400), trim=(80, 100, 80, 100)) as source:
            ImageDraw.Draw(source).line((10, 300, 10, 310), fill="white", width=1)
            result = self.crop(source)
            self.assertEqual(result["trim"]["left"], 0)
            self.assertEqual(result["trim"]["right"], 80)

    def test_near_black_compression_noise_and_isolated_outlier_allowed(self):
        rng = random.Random(19)
        with self.framed(color=(10, 10, 10)) as source:
            for _ in range(3000):
                x, y = rng.randrange(600), rng.randrange(800)
                if x < 30 or x >= 550 or y < 40 or y >= 740:
                    level = rng.randrange(5, 16)
                    source.putpixel((x, y), (level, level, level))
            source.putpixel((10, 300), (44, 44, 44))
            result = self.crop(source)
            self.assertEqual(result["box"], [30, 40, 550, 740])

    def test_thin_invoice_frame_not_treated_as_black_padding(self):
        with Image.new("RGB", (600, 800), "white") as source:
            ImageDraw.Draw(source).rectangle((0, 0, 599, 799), outline="black", width=2)
            result = self.crop(source, [.01, .01, .98, .98])
            self.assertFalse(result["changed"])
            self.assertTrue(result["warnings"])

    def ui_rail(self, size=(1081, 2193)):
        """Recorded geometry of the live stitched browser rail, not black padding."""
        width, height = size
        source = Image.new("RGB", size, "white")
        draw = ImageDraw.Draw(source)
        draw.text((30, 35), "Order / amount 626.16 / original content", fill="black")
        draw.rectangle((28, 250, width - 38, height - 130), outline=(220, 220, 220), width=1)
        draw.line((width // 2, 250, width // 2, height - 130), fill=(220, 220, 220))
        draw.rectangle((width - 8, 77, width - 3, height - 3), fill=(245, 245, 245))
        for start, end in ((77, 553), (1060, 1435), (1711, 2182)):
            draw.rectangle((width - 8, start, width - 3, min(end, height - 3)), fill=(171, 171, 171))
        draw.line((width - 1, 1479, width - 1, height - 1), fill=(111, 111, 111))
        draw.rectangle((width - 2, 1713, width - 2, height - 12), fill=(171, 171, 171))
        # A rounded thumb meets its outline after a short white end gap.
        draw.rectangle((width - 2, height - 4, width - 2, height - 3), fill=(190, 190, 190))
        draw.rectangle((2, height - 2, width - 1, height - 1), fill=(105, 105, 105))
        # The real 2px bottom frame has a small tinted compressed section.
        draw.rectangle((685, height - 2, 724, height - 1), fill=(70, 90, 120))
        return source

    def test_discontinuous_gray_scrollbar_and_two_pixel_frame_exact_trim(self):
        with self.ui_rail() as source:
            before = source.tobytes()
            result = self.crop(source, {"has_border": False})
            self.assertEqual(result["source_box"], [0, 0, 1073, 2191])
            self.assertEqual(result["trim_px"], {"left": 0, "top": 0, "right": 8, "bottom": 2})
            self.assertEqual(result["source_sha256"], hashlib.sha256(
                source.mode.encode() + str(source.size).encode() + before).hexdigest())
            self.assertEqual(result["coordinate_space"], "exif-oriented-pixels")
            self.assertEqual(source.tobytes(), before)
            with source.crop(result["source_box"]) as exact:
                self.assertEqual(result["image"].tobytes(), exact.tobytes())
            self.assertFalse(self.crop(result["image"])["changed"])

    def test_thin_frame_and_rail_detection_is_orientation_independent(self):
        with self.ui_rail() as source:
            expected = ([0, 0, 1073, 2191], [0, 8, 2191, 1081],
                        [8, 2, 1081, 2193], [2, 0, 2193, 1073])
            for turns, box in enumerate(expected):
                with source.rotate(90 * turns, expand=True) as rotated:
                    self.assertEqual(self.crop(rotated)["box"], box)
            with source.resize((2162, 4386), Image.Resampling.NEAREST) as scaled:
                self.assertEqual(self.crop(scaled)["box"], [0, 0, 2146, 4382])

    def test_one_pixel_black_app_frame_requires_white_separation(self):
        with Image.new("RGB", (600, 800), "white") as source:
            draw = ImageDraw.Draw(source)
            draw.text((30, 30), "Receipt facts preserved", fill="black")
            draw.rectangle((30, 300, 560, 740), outline="black")
            draw.line((599, 0, 599, 799), fill="black")
            draw.line((0, 799, 599, 799), fill="black")
            result = self.crop(source)
            self.assertEqual(result["box"], [0, 0, 599, 799])
            self.assertEqual(result["warnings"], [])
            self.assertEqual(result["image"].getpixel((30, 740)), (0, 0, 0))

    def test_edge_table_rules_with_internal_junctions_are_preserved(self):
        with Image.new("RGB", (600, 800), "white") as source:
            draw = ImageDraw.Draw(source)
            for y in (600, 700, 799):
                draw.line((0, y, 599, y), fill="black")
            for x in (0, 200, 400, 599):
                draw.line((x, 600, x, 799), fill="black")
            draw.text((20, 720), "Original ticket table", fill="black")
            self.assertFalse(self.crop(source)["changed"])

    def test_table_junction_at_corner_cannot_hide_in_search_margin(self):
        with Image.new("RGB", (600, 800), "white") as source:
            draw = ImageDraw.Draw(source)
            draw.line((0, 799, 599, 799), fill=(105, 105, 105))
            draw.line((0, 760, 0, 799), fill="black")
            self.assertFalse(self.crop(source)["changed"])

    def test_text_adjacent_to_exterior_rule_vetoes_cut(self):
        with Image.new("RGB", (600, 800), "white") as source:
            draw = ImageDraw.Draw(source)
            draw.line((0, 799, 599, 799), fill=(105, 105, 105))
            draw.text((200, 790), "Order total 626.16", fill="black")
            self.assertFalse(self.crop(source)["changed"])

    def test_narrow_gray_text_panel_is_not_a_scrollbar(self):
        for ink in ("black", "white", (150, 150, 150)):
            with Image.new("RGB", (600, 800), "white") as source:
                draw = ImageDraw.Draw(source)
                draw.rectangle((588, 0, 599, 799), fill=(171, 171, 171))
                draw.text((590, 300), "X", fill=ink)
                self.assertFalse(self.crop(source)["changed"], ink)

    def test_letter_near_gray_panel_end_is_not_ignored_as_a_corner(self):
        with Image.new("RGB", (600, 800), "white") as source:
            draw = ImageDraw.Draw(source)
            draw.rectangle((588, 0, 599, 799), fill=(171, 171, 171))
            draw.text((590, 1), "X", fill="white")
            self.assertFalse(self.crop(source)["changed"])

    def test_colored_rail_and_wide_gray_panel_are_preserved(self):
        for width, color in ((8, (90, 110, 150)), (80, (171, 171, 171))):
            with Image.new("RGB", (600, 800), "white") as source:
                ImageDraw.Draw(source).rectangle((600 - width, 0, 599, 799), fill=color)
                self.assertFalse(self.crop(source)["changed"])

    def test_plain_black_padding_then_gray_rail_keeps_source_coordinates(self):
        with self.ui_rail() as content:
            with Image.new("RGB", (1141, 2273), "black") as source:
                source.paste(content, (30, 40))
                result = self.crop(source)
                self.assertEqual(result["source_size"], [1141, 2273])
                self.assertEqual(result["source_box"], [30, 40, 1103, 2231])
                self.assertEqual(result["trim"], {"left": 30, "top": 40, "right": 38, "bottom": 42})

    def test_dark_color_or_full_dark_document_not_cropped(self):
        for color in ((0, 0, 0), (20, 20, 20), (20, 20, 44)):
            with Image.new("RGB", (400, 500), color) as source:
                result = self.crop(source)
                self.assertFalse(result["changed"])
        with self.framed(color=(10, 10, 35)) as source:
            self.assertFalse(self.crop(source)["changed"])

    def test_scaled_sources_have_scaled_boundaries_not_fixed_pixel_crop(self):
        with self.framed() as source:
            with source.resize((1200, 1600), Image.Resampling.NEAREST) as larger:
                self.assertEqual(self.crop(larger)["box"], [60, 80, 1100, 1480])

    def test_transparency_composited_white_not_interpreted_as_black_padding(self):
        with Image.new("RGBA", (600, 800), (0, 0, 0, 0)) as source:
            ImageDraw.Draw(source).text((200, 300), "visible", fill=(0, 0, 0, 255))
            result = self.crop(source)
            self.assertFalse(result["changed"])
            self.assertEqual(result["image"].getpixel((0, 0)), (255, 255, 255))

    def test_invalid_or_nan_proposal_preserves_complete_source(self):
        with self.framed() as source:
            for proposal in ([0, 0, float("nan"), 1], [0, 0, 2, 1], [0, 1], "crop", [True, 0, .5, 1]):
                result = self.crop(source, proposal)
                self.assertFalse(result["changed"])
                self.assertTrue(result["warnings"])

    def test_path_original_bytes_hash_remain_identical(self):
        folder = Path(__file__).resolve().parents[1] / "output" / ("border-test-" + uuid.uuid4().hex)
        folder.mkdir(parents=True, exist_ok=False)  # Inherit workspace permissions on Windows.
        try:
            path = folder / "original.png"
            with self.framed() as source:
                source.save(path)
            before = path.read_bytes()
            result = self.crop(path)
            result["image"].save(folder / "derived.png")
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(result["source_sha256"], hashlib.sha256(before).hexdigest())
            self.assertEqual(result["coordinate_space"], "exif-oriented-pixels")
        finally:
            folder.resolve().relative_to((Path(__file__).resolve().parents[1] / "output").resolve())
            shutil.rmtree(folder)

    def test_pdf_path_never_rasterized_or_edited(self):
        with self.assertRaisesRegex(ValueError, "PDF"):
            crop_black_borders("source.pdf")


if __name__ == "__main__":
    unittest.main(verbosity=2)
