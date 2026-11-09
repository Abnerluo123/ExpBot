"""Conservative, non-destructive removal of proven exterior bands and UI rails.

A model's normalized ``[x, y, width, height]`` box records its visual suggestion.
Full resolution pixels alone decide whether any side is safe to remove. A
text-bearing panel is retained in its entirety, including the blank part before
its first glyph. Narrow neutral framing also requires a white gap separating
its geometry from content. This module does not interpret invoice facts.
The caller owns the returned image; the input image/file is never modified.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageChops, ImageFilter, ImageOps

MAX_PIXELS = 70_000_000
# Dimensionless limits deliberately scale with the source, not a fixed px crop.
_MAX_BAND_FRACTION = .30
_MIN_BAND_FRACTION = .006
_DARK = 48
_MAJORITY_DARK = .60
_NOISE_DELTA = 12
_MAX_ISOLATED_NOISE = .0005


def _rgb(source: Image.Image | str | Path) -> tuple[Image.Image, str]:
    if isinstance(source, Image.Image):
        if source.width * source.height > MAX_PIXELS:
            raise ValueError("裁边图片像素过大；原件保留，请分组处理")
        original = source
        digest = hashlib.sha256(source.mode.encode() + str(source.size).encode() + source.tobytes()).hexdigest()
        close = False
    else:
        path = Path(source)
        if path.suffix.lower() == ".pdf":
            raise ValueError("原始 PDF 不允许栅格裁边；仅处理图片派生副本")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        original = Image.open(path)
        close = True
    try:
        if original.width * original.height > MAX_PIXELS:
            raise ValueError("裁边图片像素过大；原件保留，请分组处理")
        oriented = ImageOps.exif_transpose(original)
        if "A" in oriented.getbands() or "transparency" in oriented.info:
            rgba = oriented.convert("RGBA")
            image = Image.new("RGB", rgba.size, "white")
            alpha = rgba.getchannel("A")
            image.paste(rgba, mask=alpha)
            alpha.close(); rgba.close()
        else:
            image = oriented.convert("RGB")
        if oriented is not original:
            oriented.close()
        image.load()
        return image, digest
    finally:
        if close:
            original.close()


def _proposal(value: Any) -> tuple[list[float] | None, bool]:
    if value is None:
        return None, True
    if isinstance(value, Mapping):
        if value.get("has_border") is False:
            return [0., 0., 1., 1.], True
        value = value.get("box")
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        return None, False
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
        return None, False
    try:
        x, y, w, h = map(float, value)
    except (ValueError, OverflowError):
        return None, False
    if not all(math.isfinite(v) for v in (x, y, w, h)) or min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.000001 or y + h > 1.000001:
        return None, False
    return [x, y, w, h], True


def _median(histogram: list[int], count: int) -> int:
    cumulative = 0
    for value, frequency in enumerate(histogram):
        cumulative += frequency
        if cumulative > count // 2:
            return value
    return 255


def _plain_dark_band(band: Image.Image) -> bool:
    """Allow small near-black compression errors, never connected glyphs/rules."""
    count = band.width * band.height
    medians = tuple(_median(channel.histogram(), count) for channel in band.split())
    if max(medians) > _DARK or max(medians) - min(medians) > 8:
        return False
    reference = Image.new("RGB", band.size, medians)
    difference = ImageChops.difference(band, reference)
    channels = difference.split()
    deviation = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    marked = deviation.point(lambda p: 1 if p > _NOISE_DELTA else 0)
    amount = marked.histogram()[1]
    try:
        if amount / count > _MAX_ISOLATED_NOISE:
            return False
        if not amount:
            return True
        # An isolated compression outlier is tolerated. Adjacent contrast is
        # evidence of a mark, even when its total coverage is very small.
        expanded = ImageOps.expand(marked, 1, fill=0)
        neighbours = expanded.filter(ImageFilter.Kernel((3, 3), [1] * 9, scale=1))
        try:
            return neighbours.getextrema()[1] < 2
        finally:
            neighbours.close(); expanded.close()
    finally:
        marked.close(); deviation.close(); difference.close(); reference.close()
        for channel in channels:
            channel.close()


def _edge_strip(image: Image.Image, side: str, depth: int) -> Image.Image:
    """Put inward distance on x and distance along the edge on y."""
    width, height = image.size
    if side == "left":
        return image.crop((0, 0, depth, height))
    if side == "right":
        band = image.crop((width - depth, 0, width, height))
        operation = Image.Transpose.FLIP_LEFT_RIGHT
    else:
        box = (0, 0, width, depth) if side == "top" else (0, height - depth, width, height)
        band = image.crop(box)
        operation = Image.Transpose.TRANSPOSE
    oriented = band.transpose(operation)
    band.close()
    if side == "bottom":
        flipped = oriented.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        oriented.close()
        return flipped
    return oriented


def _runs(values: list[bool]) -> list[tuple[int, int, bool]]:
    runs: list[tuple[int, int, bool]] = []
    start = 0
    for index in range(1, len(values) + 1):
        if index == len(values) or values[index] != values[start]:
            runs.append((start, index, values[start]))
            start = index
    return runs


def _plain_ui_rail(columns: list[list[tuple[int, int, int]]], extent: int,
                   allow_start_tip: bool, allow_end_tip: bool) -> bool:
    """A scrollbar has long, flat neutral track/thumb runs, never glyphs.

    Check at full resolution: averaging a narrow text panel would conceal its
    letters. Each depth may have its own level (e.g. a dark window outline next
    to a gray thumb). Rounded ends and JPEG ringing get a bounded tip allowance.
    """
    span = len(columns[0])
    minimum_run = max(8 * extent, math.ceil(span * .08))
    tip = max(2, extent * 2)
    for depth, column in enumerate(columns[:extent]):
        if any(max(pixel) - min(pixel) > 12 or min(pixel) < 60 for pixel in column):
            return False
        levels = [max(pixel) for pixel in column]
        runs = _runs([level < 230 for level in levels])
        if len(runs) > 9:
            return False
        for start, end, active in runs:
            # A short interior gap or mark is lettering, not a long UI rail.
            # The bounded tips can meet a window outline at the image end.
            at_tip = (allow_start_tip and start < tip) or (allow_end_tip and end > span - tip)
            if start and end < span and not at_tip and end - start < minimum_run:
                return False
            if active:
                core = levels[start + tip:end - tip] if end - start > 2 * tip else []
                if not core:
                    # A clipped outline junction can touch only the outer
                    # part of the rail; interior glyphs are never tips. Scale
                    # this allowance with the rail, including resized sources.
                    if not at_tip or depth >= max(2, math.ceil(extent * .375)):
                        return False
                else:
                    histogram = [0] * 256
                    for level in core:
                        histogram[level] += 1
                    baseline = _median(histogram, len(core))
                    if any(abs(level - baseline) > _NOISE_DELTA for level in core):
                        return False
    return True


def _ui_edge_candidate(strip: Image.Image, skip_start: int, skip_end: int,
                       limit: int, gap: int, thin: int) -> tuple[int, bool]:
    span = strip.height
    if span - skip_start - skip_end < 32:
        return 0, False
    pixels = strip.load()
    columns = [[pixels[x, y] for y in range(skip_start, span - skip_end)] for x in range(strip.width)]
    coverage = [sum(max(p) < 230 and max(p) - min(p) <= 12 for p in col) / len(col)
                for col in columns]
    extent = 0
    while extent < len(coverage) and coverage[extent] >= .20:
        extent += 1
    # Do not trim an initial slice of a wider panel, even if pure gray.
    if not extent or extent > limit:
        return 0, False
    separator = columns[extent:extent + gap]
    if len(separator) != gap or any(min(pixel) < 232 for col in separator for pixel in col):
        return 0, False
    line = extent <= thin and all(fraction >= .90 for fraction in coverage[:extent])
    if not line and not _plain_ui_rail(columns, extent, skip_start > 0, skip_end > 0):
        return 0, False
    return extent, line


def _exterior_ui_trims(image: Image.Image) -> dict[str, int]:
    """Prove narrow exterior geometry *and* a white moat before content.

    This separate path deliberately has no minimum band width or near-black
    requirement. It cannot shave a broad/detailed panel: a complete white gap
    must start immediately after the entire narrow rail. Perpendicular corners
    are excluded only within the same bounded exterior search area. Joined
    table rules or text reaching that gap veto the cut.
    """
    width, height = image.size
    shortest = min(width, height)
    limit = max(2, math.ceil(shortest * .02))
    gap = max(3, math.ceil(shortest * .006))
    thin = max(2, math.ceil(shortest * .003))
    trims = {side: 0 for side in ("left", "top", "right", "bottom")}
    continuous: set[str] = set()
    strips: dict[str, Image.Image] = {}
    try:
        for side in trims:
            perpendicular = width if side in ("left", "right") else height
            span = height if side in ("left", "right") else width
            cap = min(limit, span // 10)
            depth = min(limit + gap + 1, perpendicular)
            if depth < limit + gap + 1:
                continue
            strip = strips[side] = _edge_strip(image, side, depth)
            extent, line = _ui_edge_candidate(strip, cap, cap, limit, gap, thin)
            trims[side] = extent
            if line:
                continuous.add(side)
        # A closed thin document outline alone is ambiguous. Do not infer that
        # a receipt perimeter is an app frame merely from its white interior.
        if len(continuous) == 4:
            return {side: 0 for side in trims}
        # Recheck the complete edge. Ignore only corners that another proven
        # cut will actually remove, never an arbitrary margin that could hide
        # a table-rule junction or a glyph near the end of a side panel.
        neighbours = {"left": ("top", "bottom"), "right": ("top", "bottom"),
                      "top": ("left", "right"), "bottom": ("left", "right")}
        changed = True
        while changed:
            changed = False
            for side, extent in trims.items():
                if not extent:
                    continue
                start, end = neighbours[side]
                proven, _ = _ui_edge_candidate(strips[side], trims[start], trims[end], limit, gap, thin)
                if proven != extent:
                    trims[side] = 0
                    changed = True
        return trims
    finally:
        for strip in strips.values():
            strip.close()


def crop_black_borders(source: Image.Image | str | Path, model_proposal: Any = None) -> dict[str, Any]:
    """Return ``image/box/source_box/trim/warnings`` and provenance metadata.

    ``box`` and ``source_box`` are exclusive pixel ``[left, top, right, bottom]``
    coordinates in the EXIF-oriented source. ``trim``/``trim_px`` are dicts with
    all four side lengths. ``model_proposal`` may be a normalized xywh list or a
    mapping with ``box`` and optional ``has_border``. Full/no-border model boxes
    still permit physically proven exterior-frame removal; they do not authorize
    content crops. Invalid proposals preserve the entire source.

    Without a proposal the same conservative pixel checks inspect all sides.
    Solid dark images, joined document rules, ambiguous closed outlines and
    text-bearing panels are preserved. Thin neutral exterior lines/UI rails
    additionally require a complete white separator from content. Nothing is
    written to disk by this API.
    """
    image, digest = _rgb(source)
    width, height = image.size
    warnings: list[str] = []
    proposed, valid = _proposal(model_proposal)
    candidates = {side: 0 for side in ("left", "top", "right", "bottom")}
    trims = dict(candidates)
    if not valid:
        warnings.append("模型裁边建议格式无效，完整图片保留。")
    else:
        # Models see scaled pictures and can miss a real outer black strip.
        # Inspect every side even for a full model box; only actual pixels can
        # authorize removal. Model boxes never authorize additional content.
        nominated = {side: True for side in candidates}
        channels = image.split()
        maximum = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
        dark = maximum.point(lambda p: 255 if p <= _DARK else 0)
        horizontal = list(dark.resize((1, height), Image.Resampling.BOX).tobytes())
        vertical = list(dark.resize((width, 1), Image.Resampling.BOX).tobytes())
        minimum_band = max(1, math.ceil(min(width, height) * _MIN_BAND_FRACTION))
        profiles = {"left": vertical, "right": vertical[::-1],
                    "top": horizontal, "bottom": horizontal[::-1]}
        try:
            for side, profile in profiles.items():
                if not nominated[side]:
                    continue
                extent = 0
                for fraction in profile:
                    if fraction < 255 * _MAJORITY_DARK:
                        break
                    extent += 1
                if extent and extent <= len(profile) * _MAX_BAND_FRACTION:
                    # A tall white glyph/rule can interrupt the dark profile.
                    # Inspect the rest of this bounded outer panel as well,
                    # rather than shaving blank black pixels up to its first
                    # stroke. Any later dark run makes the whole panel subject
                    # to the strict connected-contrast check below.
                    outer_limit = min(len(profile), math.ceil(len(profile) * _MAX_BAND_FRACTION))
                    extent = max((i + 1 for i in range(extent, outer_limit) if profile[i] >= 255 * _MAJORITY_DARK), default=extent)
                candidates[side] = extent
                if not extent:
                    continue
                if extent < minimum_band or extent > len(profile) * _MAX_BAND_FRACTION:
                    warnings.append(f"{side}侧深色区可能为票据框线或深色内容，未裁切。")
                    continue
                boxes = {"left": (0, 0, extent, height), "right": (width - extent, 0, width, height),
                         "top": (0, 0, width, extent), "bottom": (0, height - extent, width, height)}
                band = image.crop(boxes[side])
                try:
                    if _plain_dark_band(band):
                        trims[side] = extent
                    else:
                        warnings.append(f"{side}侧深色区含文字、线条或非纯色细节，整侧保留，未硬切。")
                finally:
                    band.close()
        finally:
            maximum.close(); dark.close()
            for channel in channels:
                channel.close()
        # Inspect the retained source pixels, so a proven black padding band
        # cannot hide a gray rail immediately inside it. Add offsets rather
        # than replacing source coordinates; provenance remains source-based.
        retained = image.crop((trims["left"], trims["top"], width - trims["right"], height - trims["bottom"]))
        try:
            ui_trims = _exterior_ui_trims(retained)
            for side, extent in ui_trims.items():
                if extent:
                    # The older dark-run candidate may include a table rule
                    # farther inward. This stronger proof removes only the
                    # separate exterior rail, so do not claim the whole side
                    # was preserved after a narrower cut has been authorized.
                    warnings = [message for message in warnings if not message.startswith(f"{side}侧")]
                trims[side] += extent
                candidates[side] = max(candidates[side], trims[side])
        finally:
            retained.close()
    box = [trims["left"], trims["top"], width - trims["right"], height - trims["bottom"]]
    # The independent side checks cannot authorize deleting the retained image.
    if box[2] <= box[0] or box[3] <= box[1]:
        box = [0, 0, width, height]
        trims = {side: 0 for side in trims}
        warnings.append("四边建议没有留下可靠内容，完整图片保留。")
    changed = any(trims.values())
    result = image.crop(box) if changed else image
    if changed:
        image.close()
    return {"image": result, "box": box, "source_box": list(box), "source_size": [width, height],
            "trim": trims, "trim_px": dict(trims), "candidates_px": candidates,
            "warnings": warnings, "status": "cropped" if changed else "preserved", "changed": changed,
            "source_sha256": digest, "model_box": proposed, "coordinate_space": "exif-oriented-pixels"}


# Descriptive aliases for callers that treat this as a calibration operation.
conservative_border_crop = crop_black_borders
crop_borders = crop_black_borders
