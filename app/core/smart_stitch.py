"""Conservative screenshot ordering and overlap removal, without OCR.

All output pixels originate in the supplied images (uniform resampling only).
An AI plan is a suggestion, never evidence that two areas are duplicates.  When
visual continuity is ambiguous, return an *uncut contact sheet*, not a guessed
stitched document.  ``requires_review`` is deliberately always true: matching
page pixels cannot certify that the materials belong to the same transaction.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from PIL import Image, ImageChops, ImageFilter, ImageOps

ANALYSIS_WIDTH = 256
MAX_IMAGES = 8
MAX_CANVAS_PIXELS = 70_000_000


@dataclass
class _Shot:
    id: str
    path: Path
    image: Image.Image
    box: tuple[int, int, int, int]
    ink: Image.Image
    fine: Image.Image
    rows: list[tuple[float, ...]]
    norms: list[float]
    means: list[float]


@dataclass
class _Match:
    above: str
    below: str
    dy: float
    dx: float
    start: float            # below analysis y, beginning of reliable run
    end: float              # below analysis y, end of reliable run
    lines: int
    similarity: float
    merit: float
    confidence: float = 0.0
    alternatives: float = 0.0


def _row_means(im: Image.Image) -> list[float]:
    # BOX performs the expensive summation in Pillow, rather than Python loops.
    return [float(v) for v in im.resize((1, im.height), Image.Resampling.BOX).tobytes()]


def _features(ink: Image.Image):
    small = ink.resize((32, ink.height), Image.Resampling.BOX)
    values = list(small.tobytes())
    rows, norms = [], []
    for y in range(ink.height):
        row = tuple(float(v) for v in values[y * 32:(y + 1) * 32])
        rows.append(row)
        norms.append(math.sqrt(sum(v * v for v in row)))
    return rows, norms, _row_means(ink)


def _is_blank(im: Image.Image) -> bool:
    # Only pure-white margins may be removed by an AI crop suggestion.
    # Even very faint text/grey lines are not permission to crop content.
    return im.convert("RGB").getextrema() == ((255, 255), (255, 255), (255, 255))


def _suggested_box(raw: Any, size: tuple[int, int]):
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x, y, w, h = map(float, raw)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x, y, w, h)):
        return None
    if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.000001 or y + h > 1.000001:
        return None
    iw, ih = size
    return (round(x * iw), round(y * ih), min(iw, round((x + w) * iw)),
            min(ih, round((y + h) * ih)))


def _safe_crop(image: Image.Image, box: tuple[int, int, int, int]) -> bool:
    l, t, r, b = box
    w, h = image.size
    if l >= r or t >= b:
        return False
    borders = [(0, 0, w, t), (0, b, w, h), (0, t, l, b), (r, t, w, b)]
    return all(_is_blank(image.crop(p)) for p in borders if p[2] > p[0] and p[3] > p[1])


def _load(records, plan, warnings):
    crops = {}
    if isinstance(plan, Mapping):
        suggestions = plan.get("crops", []) or []
        if not isinstance(suggestions, (list, tuple)):
            warnings.append("AI crops 格式无效，已忽略。")
            suggestions = []
        for crop in suggestions:
            if isinstance(crop, Mapping) and "id" in crop:
                crops[str(crop["id"])] = crop.get("box")
    shots = []
    for record in records:
        if not isinstance(record, Mapping) or "id" not in record or "path" not in record:
            raise ValueError("每张截图必须提供 id 和 path")
        sid, path = str(record["id"]), Path(record["path"])
        if any(s.id == sid for s in shots):
            raise ValueError(f"截图 id 重复：{sid}")
        with Image.open(path) as original:
            if original.width * original.height > MAX_CANVAS_PIXELS:
                raise ValueError(f"截图像素数过大：{sid}")
            image = ImageOps.exif_transpose(original)
            if image.mode in ("RGBA", "LA", "P"):
                rgba = image.convert("RGBA")
                image = Image.new("RGB", rgba.size, "white")
                image.paste(rgba, mask=rgba.getchannel("A"))
            else:
                image = image.convert("RGB")
            image.load()
        if image.width < 24 or image.height < 24:
            raise ValueError(f"截图过小：{sid}")
        if image.width * image.height > MAX_CANVAS_PIXELS:
            raise ValueError(f"截图像素数过大：{sid}")
        box = (0, 0, image.width, image.height)
        if sid in crops:
            proposed = _suggested_box(crops[sid], image.size)
            if proposed is None:
                warnings.append(f"忽略 {sid} 的无效 AI crop；必须为归一化 [x,y,w,h]。")
            elif _safe_crop(image, proposed):
                box = proposed
            else:
                warnings.append(f"忽略 {sid} 的 AI crop：会删除非白内容，未经重复匹配不得裁除。")
        cropped = image.crop(box)
        ah = max(1, round(cropped.height * ANALYSIS_WIDTH / cropped.width))
        # Remove light table/background fills before resampling. Horizontal rules
        # alone carry little energy and cannot constitute an overlap match.
        original_ink = cropped.convert("L").point(lambda p: max(0, 225 - p))
        fine = original_ink.resize((ANALYSIS_WIDTH * 2, round(cropped.height * ANALYSIS_WIDTH * 2 / cropped.width)), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(.4))
        ink = original_ink.resize((ANALYSIS_WIDTH, ah), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(.8))
        rows, norms, means = _features(ink)
        shots.append(_Shot(sid, path, image, box, ink, fine, rows, norms, means))
    return shots


def _candidate_offsets(a: _Shot, b: _Shot):
    votes: dict[int, float] = {}
    # Suffix of above vs prefix/interior of below. We allow an interior prefix
    # because sticky UI headers may precede the repeated scrolling content.
    ay = [y for y in range(max(0, int(a.ink.height * .25)), a.ink.height)
          if a.means[y] >= 2 and a.norms[y] > 0]
    by = [y for y in range(min(b.ink.height, int(b.ink.height * .66) + 1))
          if b.means[y] >= 2 and b.norms[y] > 0]
    for y in ay:
        va, na = a.rows[y], a.norms[y]
        for z in by:
            dy = y - z
            if dy < 8 or dy >= a.ink.height - 10:
                continue
            nb = b.norms[z]
            ratio = min(na, nb) / max(na, nb)
            if ratio < .48:
                continue
            vb = b.rows[z]
            cosine = sum(x * v for x, v in zip(va, vb)) / (na * nb)
            if cosine > .87:
                votes[dy] = votes.get(dy, 0.0) + cosine ** 6 * ratio
    smoothed = {d: sum(votes.get(d + k, 0) for k in (-1, 0, 1)) for d in votes}
    chosen = []
    for d, strength in sorted(smoothed.items(), key=lambda p: p[1], reverse=True):
        if not any(abs(d - old) < 5 for old in chosen):
            chosen.append(d)
        if len(chosen) == 10:
            break
    return chosen


def _line_groups(energy):
    groups, start, last = [], None, None
    for y, value in enumerate(energy):
        if value >= 2:
            if start is None:
                start = y
            last = y
        elif start is not None and y - last > 1:
            groups.append((max(0, start - 1), min(len(energy), last + 2)))
            start = last = None
    if start is not None:
        groups.append((max(0, start - 1), min(len(energy), last + 2)))
    return groups


def _evaluate_offset(a: _Shot, b: _Shot, dy: int, dx: int):
    ya, yb = max(0, dy), max(0, -dy)
    height = min(a.ink.height - ya, b.ink.height - yb)
    if height < 18:
        return None
    # Exclude outer app border / scrollbar. Search x translation as well as y.
    xa, xb = 6 + max(0, dx), 6 + max(0, -dx)
    width = ANALYSIS_WIDTH - 12 - abs(dx)
    pa = a.ink.crop((xa, ya, xa + width, ya + height))
    pb = b.ink.crop((xb, yb, xb + width, yb + height))
    ea, eb = _row_means(pa), _row_means(pb)
    error = _row_means(ImageChops.difference(pa, pb))
    groups = _line_groups([max(x, y) for x, y in zip(ea, eb)])
    runs, current = [], []
    for start, end in groups:
        total = sum(ea[start:end]) + sum(eb[start:end])
        err = sum(error[start:end])
        similarity = max(0.0, 1 - err / max(1, total))
        # A long solid rule/blank band has no distributed text texture.
        patch = pa.crop((0, start, width, end)).resize((32, 1), Image.Resampling.BOX)
        cells = list(patch.tobytes())
        texture = sum(1 for v in cells if v >= 3)
        if similarity >= .68 and total >= 15 and 3 <= texture <= 32:
            if current and start - current[-1][1] > 30:
                runs.append(current)
                current = []
            current.append((start, end, similarity, total))
        else:
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)
    best = None
    for run in runs:
        if len(run) < 2 or run[-1][1] - run[0][0] < 18:
            continue
        total = sum(g[3] for g in run)
        similarity = sum(g[2] * g[3] for g in run) / total
        start, end = run[0][0], run[-1][1]
        # Rank continuous, distinctive content rather than a single repeated word.
        merit = total * similarity ** 4 * min(2.0, len(run) / 3)
        candidate = _Match(a.id, b.id, dy, dx, yb + start, yb + end,
                           len(run), similarity, merit)
        if best is None or merit > best.merit:
            best = candidate
    return best


def _fine_match(a: _Shot, b: _Shot, coarse: _Match):
    """Confirm coarse votes against actual 2-D glyphs at twice the resolution.

    A 1-pixel neighbourhood tolerates JPEG/subpixel resampling, but a changed
    digit or a merely similar row layout leaves unexplained ink.  Rules alone
    cannot provide support. This stage prevents row histograms deciding facts.
    """
    best = None
    for dy in range(round(coarse.dy * 2) - 2, round(coarse.dy * 2) + 3):
        if dy < 12:
            continue
        for dx in range(round(coarse.dx * 2) - 1, round(coarse.dx * 2) + 2):
            h = min(a.fine.height - dy, b.fine.height)
            if h < 30:
                continue
            width = ANALYSIS_WIDTH * 2 - 24 - abs(dx)
            xa, xb = 12 + max(0, dx), 12 + max(0, -dx)
            pa = a.fine.crop((xa, dy, xa + width, dy + h))
            pb = b.fine.crop((xb, 0, xb + width, h))
            ea, eb = _row_means(pa), _row_means(pb)
            error = _row_means(ImageChops.add(
                ImageChops.subtract(pa, pb.filter(ImageFilter.MaxFilter(3))),
                ImageChops.subtract(pb, pa.filter(ImageFilter.MaxFilter(3))),
            ))
            raw_error = _row_means(ImageChops.difference(pa, pb))
            runs, run = [], []
            for start, end in _line_groups([max(x, y) for x, y in zip(ea, eb)]):
                total = sum(ea[start:end]) + sum(eb[start:end])
                similarity = 1 - sum(error[start:end]) / max(1, total)
                raw_similarity = 1 - sum(raw_error[start:end]) / max(1, total)
                cells = pa.crop((0, start, width, end)).resize((32, 1), Image.Resampling.BOX).tobytes()
                texture = sum(v >= 3 for v in cells)
                # A thin full-width rule or faint margin is neutral, not a
                # matching content line and not a break between real text rows.
                if total < 20 or texture < 3 or (texture > 28 and end - start <= 6):
                    continue
                if similarity >= .978 and raw_similarity >= .68:
                    if run and start - run[-1][1] > 90:
                        runs.append(run)
                        run = []
                    run.append((start, end, similarity, total))
                else:
                    if run:
                        runs.append(run)
                    run = []
            if run:
                runs.append(run)
            for run in runs:
                if len(run) < 2 or run[-1][1] - run[0][0] < 35:
                    continue
                start, end = run[0][0], run[-1][1]
                total = sum(x[3] for x in run)
                similarity = sum(x[2] * x[3] for x in run) / total
                merit = total * similarity ** 8 * min(2.0, len(run) / 2)
                match = _Match(a.id, b.id, dy / 2, dx / 2, start / 2, end / 2,
                               len(run), similarity, merit)
                if best is None or merit > best.merit:
                    best = match
    return best


def _find_match(a: _Shot, b: _Shot, hint: Any = None):
    peaks = []
    tested = set()
    candidates = _candidate_offsets(a, b)
    # A numeric AI hint may nominate one more search location, not authorize a
    # cut. Values <=1 are fractions of below's height; larger values are source
    # pixels of overlap. The same local pixel verification still applies.
    if isinstance(hint, (int, float)) and not isinstance(hint, bool) and math.isfinite(hint) and hint > 0:
        overlap = hint * b.ink.height if hint <= 1 else hint * ANALYSIS_WIDTH / (b.box[2] - b.box[0])
        proposed = round(a.ink.height - overlap)
        if 8 <= proposed < a.ink.height - 10 and proposed not in candidates:
            candidates.append(proposed)
    for coarse in candidates:
        best = None
        for dy in range(max(8, coarse - 2), coarse + 3):
            for dx in (-2, -1, 0, 1, 2):
                key = (dy, dx)
                if key in tested:
                    continue
                tested.add(key)
                found = _evaluate_offset(a, b, dy, dx)
                if found and (best is None or found.merit > best.merit):
                    best = found
        if best:
            verified = _fine_match(a, b, best)
            if verified and _prefix_repeated(a, b, verified.start)[0]:
                peaks.append(verified)
    if not peaks:
        return None
    peaks.sort(key=lambda m: m.merit, reverse=True)
    best = peaks[0]
    distant = [p for p in peaks[1:] if abs(p.dy - best.dy) >= 5]
    runner = max((p.merit for p in distant), default=0)
    margin = 1.0 - runner / max(1, best.merit)
    best.alternatives = margin
    best.confidence = best.similarity * min(1.0, best.lines / 2) * min(1.0, .55 + margin)
    if margin < .13:
        best.confidence = min(best.confidence, .45)
    return best


def _prefix_repeated(a: _Shot, b: _Shot, end: float):
    """Explain fixed header content with independent 2-D patches in above.

    Coarse row votes only nominate locations. A high-resolution neighbourhood
    comparison must explain each substantial prefix text band. Unique prefixes
    are not removed just because their rows have a similar density of ink.
    """
    limit = max(0, math.floor(end * 2) - 2)
    if limit < 4:
        return True, 1.0
    energy = _row_means(b.fine)
    covered, total = 0.0, 0.0
    for start, stop in _line_groups(energy[:limit]):
        weight = sum(energy[start:stop])
        if weight < 13 or stop >= limit - 1:
            continue  # faint UI rule / partial next content line, not a fact band
        patch = b.fine.crop((12, start, ANALYSIS_WIDTH * 2 - 12, stop))
        cells = patch.resize((32, 1), Image.Resampling.BOX).tobytes()
        texture = sum(v >= 3 for v in cells)
        if texture < 3 or (texture > 28 and stop - start <= 6):
            continue
        total += weight
        center = min(len(b.rows) - 1, round((start + stop) / 4))
        vb, nb = b.rows[center], b.norms[center]
        candidates = []
        for z, na in enumerate(a.norms):
            if nb <= 0 or na <= 0 or a.means[z] < 2:
                continue
            cosine = sum(v * w for v, w in zip(vb, a.rows[z])) / (nb * na)
            if cosine > .78:
                candidates.append((cosine, z * 2 - (stop - start) // 2))
        found = False
        for _, nominal in sorted(candidates, reverse=True)[:16]:
            for yy in range(max(0, nominal - 3), min(a.fine.height - patch.height, nominal + 3) + 1):
                for dx in (-1, 0, 1):
                    other = a.fine.crop((12 + dx, yy, ANALYSIS_WIDTH * 2 - 12 + dx, yy + patch.height))
                    err = ImageChops.add(
                        ImageChops.subtract(patch, other.filter(ImageFilter.MaxFilter(3))),
                        ImageChops.subtract(other, patch.filter(ImageFilter.MaxFilter(3))),
                    )
                    mass = sum(_row_means(patch)) + sum(_row_means(other))
                    similarity = 1 - sum(_row_means(err)) / max(1, mass)
                    if similarity >= .975:
                        found = True
                        break
                if found:
                    break
            if found:
                break
        if found:
            covered += weight
    ratio = covered / total if total else 1.0
    return ratio >= .999, ratio


def _safe_source_row(shot: _Shot, y: float):
    """Reject a seam through dark glyphs in the full-resolution source."""
    l, t, r, bottom = shot.box
    source_y = t + round(y * (r - l) / ANALYSIS_WIDTH)
    if source_y < t + 1 or source_y >= bottom - 1:
        return False
    margin = max(1, round((r - l) * .025))
    band = shot.image.crop((l + margin, source_y - 1, r - margin, source_y + 2)).convert("L")
    data = band.tobytes()
    width = band.width
    for row in range(3):
        dark = sum(v < 210 for v in data[row * width:(row + 1) * width])
        # A horizontal table rule is an allowed boundary; glyph-bearing rows
        # with intermediate ink coverage are not. Faint grid fills are ignored.
        if dark > max(1, width * .002) and dark < width * .80:
            return False
    return True


def _seam(a: _Shot, b: _Shot, match: _Match):
    # Prefer a whitespace / horizontal grid boundary near the end of the
    # verified run, never a cut through glyphs. A few quiet rows are sufficient.
    # Search the entire verified run: the last matched glyph may touch the
    # viewport edge, while an earlier inter-line gap remains a safe seam.
    lower = math.ceil(match.start + 3)
    choices = range(lower, math.floor(min(match.end + 3, b.ink.height, a.ink.height - match.dy)))
    ranked = []
    for y in choices:
        quiet = b.means[y] + a.means[round(y + match.dy)]
        if quiet <= 1.5 and _safe_source_row(a, y + match.dy) and _safe_source_row(b, y):
            ranked.append((quiet + .02 * abs(y - match.end), y))
    return min(ranked)[1] if ranked else None


def _changed_overlap(a: _Shot, b: _Shot, match: _Match, seam: float):
    """Flag alternate views (e.g. masked vs expanded fields), never fill them."""
    dy, dx = round(match.dy * 2), round(match.dx * 2)
    start = round(seam * 2)
    end = min(a.fine.height - dy, b.fine.height)
    if end - start < 3:
        return False
    width = ANALYSIS_WIDTH * 2 - 24 - abs(dx)
    xa, xb = 12 + max(0, dx), 12 + max(0, -dx)
    pa = a.fine.crop((xa, start + dy, xa + width, end + dy))
    pb = b.fine.crop((xb, start, xb + width, end))
    residual = ImageChops.add(
        ImageChops.subtract(pa, pb.filter(ImageFilter.MaxFilter(3))),
        ImageChops.subtract(pb, pa.filter(ImageFilter.MaxFilter(3))),
    ).point(lambda p: 255 if p >= 16 else 0)
    count = residual.histogram()[255]
    return count >= max(7, width * (end - start) * .0006)


def _render(shots, order, joins, target_width, resolved, warnings):
    lookup = {s.id: s for s in shots}
    starts = {sid: 0 for sid in order}
    ends = {sid: lookup[sid].ink.height for sid in order}
    offsets = {order[0]: 0}
    join_info = []
    for above, below in zip(order, order[1:]):
        if not resolved:
            offsets[below] = 0
            continue
        match = joins[(above, below)]
        a, b = lookup[above], lookup[below]
        safe, coverage = _prefix_repeated(a, b, match.start)
        if not safe:
            warnings.append(f"{below} 前缀包含未被其他截图解释的内容，拒绝自动裁掉。")
            return None
        seam = _seam(a, b, match)
        if seam is None:
            warnings.append(f"{above}→{below} 没有可靠空白/横格接缝，拒绝切过文字内容。")
            return None
        ends[above] = seam + match.dy
        starts[below] = seam
        offsets[below] = offsets[above] + round(match.dx * target_width / ANALYSIS_WIDTH)
        factor = target_width / ANALYSIS_WIDTH
        different_view = _changed_overlap(a, b, match, seam)
        if different_view:
            warnings.append(f"{above}→{below} 后续交叠区存在不同原图像素（可能是展开/遮掩状态变化，也可能是事实冲突）；"
                            f"接缝采用 {below} 原图视图，必须对照两张源图审核，未合成或填补不同字段。")
        join_info.append({
            "above_id": above, "below_id": below,
            "alternate_view_requires_review": different_view,
            "above_replaced_source_box": [a.box[0], a.box[1] + round((seam + match.dy) * (a.box[2] - a.box[0]) / ANALYSIS_WIDTH), a.box[2], a.box[3]],
            "below_omitted_source_box": [b.box[0], b.box[1], b.box[2], b.box[1] + round(seam * (b.box[2] - b.box[0]) / ANALYSIS_WIDTH)],
            "overlap_px": round((seam - match.start) * factor),
            "skip_prefix_px": round(match.start * factor),
            "score": round(match.confidence, 4),
            "reason": f"连续 {match.lines} 个内容行组二维匹配，纹理相似度 {match.similarity:.3f}；"
                      f"在空白/横格边切换到下图原始像素，固定页头覆盖率 {coverage:.3f}。",
            "x_shift_px": round(match.dx * factor),
            "matched_above_box": [0, round((match.start + match.dy) * factor), target_width,
                                    round((match.end + match.dy) * factor)],
            "matched_below_box": [0, round(match.start * factor), target_width,
                                    round(match.end * factor)],
        })
    pieces, segments, y = [], [], 0
    gap = 0 if resolved else max(12, round(target_width * .02))
    minx, maxx = min(offsets.values()), max(offsets.values()) + target_width
    for sid in order:
        shot = lookup[sid]
        l, t, r, bottom = shot.box
        scale = (r - l) / ANALYSIS_WIDTH
        st = t if starts[sid] == 0 else max(t, min(bottom, t + round(starts[sid] * scale)))
        en = bottom if ends[sid] == shot.ink.height else max(st, min(bottom, t + round(ends[sid] * scale)))
        if en <= st:
            warnings.append(f"{sid} 没有可保留的唯一区域，拒绝生成空切片。")
            return None
        source_box = (l, st, r, en)
        piece = shot.image.crop(source_box)
        out_h = max(1, round(piece.height * target_width / piece.width))
        piece = piece.resize((target_width, out_h), Image.Resampling.LANCZOS)
        x = offsets[sid] - minx
        pieces.append((piece, x, y))
        segments.append({"id": sid, "source_box": list(source_box),
                         "dest_box": [x, y, x + target_width, y + out_h]})
        y += out_h + gap
    height = y - gap
    if (maxx - minx) * height > MAX_CANVAS_PIXELS:
        raise ValueError("预计拼图过大，请减少截图数量或尺寸")
    canvas = Image.new("RGB", (maxx - minx, height), "white")
    for piece, x, yy in pieces:
        canvas.paste(piece, (x, yy))
    return canvas, segments, join_info


def smart_stitch(records: Sequence[Mapping[str, Any]], plan: Mapping[str, Any] | None = None):
    """Return image, provenance segments, candidate order, joins and warnings.

    ``records``: dictionaries with unique ``id`` and ``path``; optional metadata
    dimensions/names are not trusted over decoded image dimensions. ``plan``:
    ``ordered_ids``, ``crops:[{id,box:[x,y,w,h]}]`` (normalized), and
    ``joins:[{above_id,below_id,overlap_hint,reason}]``. Only locally verified
    duplicates can be removed. Unsafe AI crops are ignored with a warning.

    ``status == 'unresolved'`` means an *uncut contact sheet* in input order,
    not a guessed stitch. ``requires_review`` is always True even on success.
    A visual match does not prove common order identity or reimbursement validity.
    """
    records = list(records)
    if not records or len(records) > MAX_IMAGES:
        raise ValueError(f"请提供 1–{MAX_IMAGES} 张截图")
    warnings = []
    if plan is not None and not isinstance(plan, Mapping):
        warnings.append("AI plan 格式无效，忽略并使用本地匹配。")
        plan = None
    shots = _load(records, plan, warnings)
    original = [s.id for s in shots]
    target_width = round(median(s.box[2] - s.box[0] for s in shots))
    # Do not infer common transaction identity from matching prices or names.
    declared = {str(r.get("order_id") or r.get("order_no")) for r in records
                if r.get("order_id") or r.get("order_no")}
    conflict = len(declared) > 1
    if conflict:
        warnings.append("材料声明了不同订单编号，拒绝跨订单拼接；不以金额相同替代订单关联。")
    requested = None
    if plan and plan.get("ordered_ids") is not None:
        ids = [str(x) for x in plan["ordered_ids"]] if isinstance(plan["ordered_ids"], (list, tuple)) else []
        if len(ids) == len(original) and set(ids) == set(original):
            requested = ids
        else:
            warnings.append("AI ordered_ids 不完整、重复或包含未知 id，已忽略。")
    proposals = {}
    if plan:
        raw_joins = plan.get("joins", [])
        if not isinstance(raw_joins, (list, tuple)):
            warnings.append("AI joins 格式无效，已忽略。")
            raw_joins = []
        allowed = set(zip(requested, requested[1:])) if requested else None
        for proposed in raw_joins:
            if not isinstance(proposed, Mapping):
                warnings.append("忽略格式无效的 AI join。")
                continue
            pair = (str(proposed.get("above_id", proposed.get("above", ""))),
                    str(proposed.get("below_id", proposed.get("below", ""))))
            if any(sid not in original for sid in pair) or pair[0] == pair[1] or (allowed is not None and pair not in allowed):
                warnings.append("忽略不属于完整建议顺序中相邻截图的 AI join。")
                continue
            reason = proposed.get("reason", "")
            if not isinstance(reason, str) or not reason.strip():
                warnings.append("AI join 缺少可审阅依据，未采用其 overlap_hint。")
                continue
            hint = proposed.get("overlap_hint")
            if not isinstance(hint, (float, int)) or isinstance(hint, bool) or not math.isfinite(hint) or not 0 <= hint <= 1:
                warnings.append("AI overlap_hint 必须为下图高度的 0..1 比例，未采用无效提示。")
                hint = None
            proposals[pair] = {"hint": hint, "reason": reason.strip()[:500]}
    matches = {}
    if not conflict:
        for a, b in itertools.permutations(shots, 2):
            proposal = proposals.get((a.id, b.id), {})
            match = _find_match(a, b, proposal.get("hint"))
            if match and match.confidence >= .69:
                matches[(a.id, b.id)] = match
    paths = []
    if not conflict:
        def visit(path, remaining, quality):
            if not remaining:
                paths.append((quality, path))
                return
            for sid in remaining:
                edge = matches.get((path[-1], sid))
                if edge:
                    visit(path + [sid], [x for x in remaining if x != sid], quality + edge.confidence)
        for sid in original:
            visit([sid], [x for x in original if x != sid], 0.0)
    paths.sort(key=lambda p: p[0], reverse=True)
    resolved = len(shots) == 1 or bool(paths)
    if len(paths) >= 2 and paths[0][0] - paths[1][0] < .12:
        warnings.append("存在相近的多种截图顺序，拒绝猜测排序或裁切。")
        resolved = False
    order = paths[0][1] if resolved and paths else original
    if requested and order != requested:
        warnings.append("AI 建议顺序未获本地内容匹配支持，未强制采用。")
    result = _render(shots, order, matches, target_width, resolved, warnings)
    if result is None:
        resolved = False
        order = original
        result = _render(shots, order, {}, target_width, False, warnings)
    if not resolved:
        warnings.append("未找到唯一可靠的连续重叠链；仅返回未裁内容的输入顺序联系表，请人工复核。")
    else:
        warnings.append("拼接仅证明局部视觉连续，不能证明同一订单；请核对原图、接缝和事实字段。")
    canvas, segments, joins = result
    for join in joins:
        proposal = proposals.get((join["above_id"], join["below_id"]))
        if proposal:
            join["ai_reason"] = proposal["reason"]
            join["ai_overlap_hint"] = proposal["hint"]
    confidence = min((j["score"] for j in joins), default=1.0 if len(shots) == 1 else 0.0)
    return {"image": canvas, "ordered_ids": order, "joins": joins,
            "segments": segments, "warnings": warnings, "confidence": confidence,
            "requires_review": True, "status": "matched" if resolved else "unresolved"}
