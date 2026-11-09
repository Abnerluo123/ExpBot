"""图片处理：去白边、缩放、左右拼接、装订留白、同侧纵向拼接。"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

from PIL import Image, ImageOps

# A4 横向：297mm × 210mm
MM_TO_INCH = 1 / 25.4
A4_LANDSCAPE_MM = (297.0, 210.0)
A4_PORTRAIT_MM = (210.0, 297.0)

# 默认版式（毫米）：订单在左、支付在右，内容在可打印区域内居中。
DEFAULT_LAYOUT: dict[str, Any] = {
    "layout_left_mm": 24.0,
    "layout_right_mm": 24.0,
    "layout_top_mm": 24.0,
    "layout_bottom_mm": 24.0,
    "layout_gap_mm": 4.0,
    "layout_order_on_left": True,
    "layout_vertical_align": "center",
    "dpi": 300,
}


def a4_landscape_pixels(dpi: int = 300) -> tuple[int, int]:
    w = int(round(A4_LANDSCAPE_MM[0] * MM_TO_INCH * dpi))
    h = int(round(A4_LANDSCAPE_MM[1] * MM_TO_INCH * dpi))
    return w, h


def a4_portrait_pixels(dpi: int = 300) -> tuple[int, int]:
    w = int(round(A4_PORTRAIT_MM[0] * MM_TO_INCH * dpi))
    h = int(round(A4_PORTRAIT_MM[1] * MM_TO_INCH * dpi))
    return w, h


def mm_to_px(mm: float, dpi: int) -> int:
    return max(0, int(round(float(mm) * MM_TO_INCH * dpi)))


def resolve_layout(
    layout: Optional[Mapping[str, Any]] = None,
    **overrides: Any,
) -> dict[str, Any]:
    """合并默认版式、settings.extra 与显式参数。

    识别键（均可选）：
      layout_left_mm, layout_right_mm, layout_top_mm, layout_bottom_mm,
      layout_gap_mm, layout_order_on_left, layout_vertical_align,
      layout_column_ratio, layout_adaptive, dpi
    兼容短键：left_mm/right_mm/top_mm/bottom_mm/gap_mm/order_on_left
    """
    cfg = dict(DEFAULT_LAYOUT)
    if layout:
        cfg.update(_normalize_layout_keys(dict(layout)))
    if overrides:
        # 过滤 None，避免显式 None 覆盖默认
        cleaned = {k: v for k, v in _normalize_layout_keys(overrides).items() if v is not None}
        cfg.update(cleaned)
    # 类型规整
    for k in (
        "layout_left_mm",
        "layout_right_mm",
        "layout_top_mm",
        "layout_bottom_mm",
        "layout_gap_mm",
    ):
        cfg[k] = float(cfg[k])
    cfg["layout_order_on_left"] = bool(cfg["layout_order_on_left"])
    cfg["dpi"] = int(cfg.get("dpi") or 300)
    if cfg["dpi"] <= 0:
        cfg["dpi"] = 300
    return cfg


def _normalize_layout_keys(d: Mapping[str, Any]) -> dict[str, Any]:
    alias = {
        "left_mm": "layout_left_mm",
        "right_mm": "layout_right_mm",
        "top_mm": "layout_top_mm",
        "bottom_mm": "layout_bottom_mm",
        "gap_mm": "layout_gap_mm",
        "order_on_left": "layout_order_on_left",
        "left_margin_mm": "layout_left_mm",
        "right_margin_mm": "layout_right_mm",
        "top_margin_mm": "layout_top_mm",
        "bottom_margin_mm": "layout_bottom_mm",
    }
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = alias.get(k, k)
        out[key] = v
    return out


def open_rgb(path: str | Path) -> Image.Image:
    """打开图片为 RGB，保留内容；应用 EXIF 方向。"""
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        alpha = im.split()[-1]
        bg.paste(im, mask=alpha)
        return bg
    if im.mode == "P":
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        if "A" in im.getbands():
            bg.paste(im, mask=im.split()[-1])
        else:
            bg.paste(im.convert("RGB"))
        return bg
    return im.convert("RGB")


def smart_crop_whitespace(
    im: Image.Image,
    padding: int = 8,
    threshold: int = 245,
) -> Image.Image:
    """裁掉近白边，保留少量 padding。

    仅裁外围白边，不自动裁切有内容的区域；全白图原样返回。
    """
    if im.mode != "RGB":
        im = im.convert("RGB")
    gray = im.convert("L")
    bw = gray.point(lambda p: 255 if p < threshold else 0)
    bbox = bw.getbbox()
    if not bbox:
        return im
    l, t, r, b = bbox
    l = max(0, l - padding)
    t = max(0, t - padding)
    r = min(im.width, r + padding)
    b = min(im.height, b + padding)
    # 若裁后与原图几乎相同，直接返回，避免无意义拷贝
    if l == 0 and t == 0 and r == im.width and b == im.height:
        return im
    return im.crop((l, t, r, b))


def stitch_vertical(
    images: Sequence[Image.Image],
    bg: tuple[int, int, int] = (255, 255, 255),
    gap: int = 0,
) -> Image.Image:
    """按有序序列纵向拼接（宽度对齐到最大宽，等比缩放，保持比例完整）。"""
    if not images:
        raise ValueError("无图片可拼接")
    if len(images) == 1:
        return images[0].copy()
    max_w = max(im.width for im in images)
    estimated_h = sum(max(1, int(round(im.height * (max_w / im.width)))) for im in images) + gap * (len(images) - 1)
    if max_w * estimated_h > 50_000_000:
        raise ValueError("纵向拼接会产生过大的长图，请分组处理或缩小材料。")
    resized: list[Image.Image] = []
    for im in images:
        if im.width != max_w:
            nh = max(1, int(round(im.height * (max_w / im.width))))
            im = im.resize((max_w, nh), Image.Resampling.LANCZOS)
        resized.append(im)
    total_h = sum(im.height for im in resized) + gap * (len(resized) - 1)
    canvas = Image.new("RGB", (max_w, total_h), bg)
    y = 0
    for i, im in enumerate(resized):
        canvas.paste(im, (0, y))
        y += im.height
        if i < len(resized) - 1:
            y += gap
    return canvas


def stitch_horizontal(
    images: Sequence[Image.Image],
    gap: int = 12,
    bg: tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    if not images:
        raise ValueError("无图片可拼接")
    if len(images) == 1:
        return images[0].copy()
    max_h = max(im.height for im in images)
    estimated_w = sum(max(1, int(round(im.width * (max_h / im.height)))) for im in images) + gap * (len(images) - 1)
    if estimated_w * max_h > 50_000_000:
        raise ValueError("横向拼接会产生过大的长图，请分组处理或缩小材料。")
    resized: list[Image.Image] = []
    for im in images:
        if im.height != max_h:
            nw = max(1, int(round(im.width * (max_h / im.height))))
            im = im.resize((nw, max_h), Image.Resampling.LANCZOS)
        resized.append(im)
    total_w = sum(im.width for im in resized) + gap * (len(resized) - 1)
    canvas = Image.new("RGB", (total_w, max_h), bg)
    x = 0
    for im in resized:
        canvas.paste(im, (x, 0))
        x += im.width + gap
    return canvas


def fit_into_box(
    im: Image.Image,
    box_w: int,
    box_h: int,
    bg: tuple[int, int, int] = (255, 255, 255),
    center: bool = True,
) -> Image.Image:
    """等比缩放完整放入 box（不裁切内容），不足部分填白。"""
    if box_w <= 0 or box_h <= 0:
        return Image.new("RGB", (max(1, box_w), max(1, box_h)), bg)
    if im.width <= 0 or im.height <= 0:
        return Image.new("RGB", (box_w, box_h), bg)
    scale = min(box_w / im.width, box_h / im.height)
    nw = max(1, int(round(im.width * scale)))
    nh = max(1, int(round(im.height * scale)))
    # 防止四舍五入越界
    nw = min(nw, box_w)
    nh = min(nh, box_h)
    resized = im.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (box_w, box_h), bg)
    if center:
        x = (box_w - nw) // 2
        y = (box_h - nh) // 2
    else:
        x, y = 0, 0
    canvas.paste(resized, (x, y))
    return canvas


def _fit_prefer_box(im: Image.Image, max_w: int, max_h: int) -> Image.Image:
    """完整保留比例，最大放入 (max_w, max_h)，不放大限制、不裁切。"""
    if max_w <= 0 or max_h <= 0 or im.width <= 0 or im.height <= 0:
        return Image.new("RGB", (max(1, max_w), max(1, max_h)), (255, 255, 255))
    scale = min(max_w / im.width, max_h / im.height)
    nw = max(1, int(round(im.width * scale)))
    nh = max(1, int(round(im.height * scale)))
    nw = min(nw, max_w)
    nh = min(nh, max_h)
    return im.resize((nw, nh), Image.Resampling.LANCZOS)


def _load_side(
    paths: Sequence[Union[str, Path]],
    *,
    crop_whitespace: bool,
) -> Image.Image:
    """按有序路径加载同侧图片；多张纵向拼接。原图不被修改（只读打开）。"""
    if not paths:
        raise ValueError("路径列表为空")
    imgs: list[Image.Image] = []
    source_pixels = 0
    for p in paths:
        im = open_rgb(p)
        source_pixels += im.width * im.height
        if source_pixels > 50_000_000:
            raise ValueError("同侧材料原始总像素过大，请分笔处理。")
        if crop_whitespace:
            im = smart_crop_whitespace(im)
        imgs.append(im)
    if len(imgs) == 1:
        return imgs[0]
    return stitch_vertical(imgs)


def process_voucher_layout(
    order_paths: Sequence[str | Path],
    payment_paths: Sequence[str | Path],
    *,
    dpi: Optional[int] = None,
    # —— 兼容旧比例参数（若提供 mm 则优先 mm）——
    left_margin_ratio: Optional[float] = None,
    right_margin_ratio: Optional[float] = None,
    top_margin_ratio: Optional[float] = None,
    bottom_margin_ratio: Optional[float] = None,
    gap: Optional[int] = None,  # 像素 gap（旧接口）；mm 优先
    outer_pad: Optional[int] = None,  # 保留兼容，忽略
    # —— 毫米版式 ——
    left_mm: Optional[float] = None,
    right_mm: Optional[float] = None,
    top_mm: Optional[float] = None,
    bottom_mm: Optional[float] = None,
    gap_mm: Optional[float] = None,
    crop_whitespace: bool = True,
    order_on_left: Optional[bool] = None,
    layout: Optional[Mapping[str, Any]] = None,
    draw_binding_guide: bool = False,  # 默认不画灰色装订线
    **_compat: Any,
) -> Image.Image:
    """生成「购买及支付凭证」横向 A4 单页大图。

    默认版式：
      - A4 横向 297×210mm，DPI 300
      - 四边各留 24mm 可打印区边距，左右列间距 4mm
      - 订单在左、支付在右；组合在可打印区域内水平和垂直居中
      - 同侧多图按传入顺序纵向拼接
      - 等比完整缩放，不裁内容；可选裁白边
      - 不绘制灰色装订线
    """
    # 合并 layout
    ov: dict[str, Any] = {}
    if left_mm is not None:
        ov["layout_left_mm"] = left_mm
    if right_mm is not None:
        ov["layout_right_mm"] = right_mm
    if top_mm is not None:
        ov["layout_top_mm"] = top_mm
    if bottom_mm is not None:
        ov["layout_bottom_mm"] = bottom_mm
    if gap_mm is not None:
        ov["layout_gap_mm"] = gap_mm
    if order_on_left is not None:
        ov["layout_order_on_left"] = order_on_left
    if dpi is not None:
        ov["dpi"] = dpi

    layout_values = _normalize_layout_keys(layout or {})
    cfg = resolve_layout(layout_values, **ov)
    use_dpi = int(cfg["dpi"])

    # 旧比例参数仅在调用方没有提供对应毫米值时生效（含所有兼容别名）。
    page_w_mm, page_h_mm = A4_LANDSCAPE_MM
    for key, legacy_ratio, page_mm in (
        ("layout_left_mm", left_margin_ratio, page_w_mm),
        ("layout_right_mm", right_margin_ratio, page_w_mm),
        ("layout_top_mm", top_margin_ratio, page_h_mm),
        ("layout_bottom_mm", bottom_margin_ratio, page_h_mm),
    ):
        if legacy_ratio is not None and key not in ov and key not in layout_values:
            cfg[key] = page_mm * float(legacy_ratio)
    for key in ("layout_left_mm", "layout_right_mm", "layout_top_mm",
                "layout_bottom_mm", "layout_gap_mm"):
        if not math.isfinite(cfg[key]) or cfg[key] < 0:
            raise ValueError("页边距和列间距必须为非负有限数")

    page_w, page_h = a4_landscape_pixels(use_dpi)
    left_margin = mm_to_px(cfg["layout_left_mm"], use_dpi)
    right_margin = mm_to_px(cfg["layout_right_mm"], use_dpi)
    top_margin = mm_to_px(cfg["layout_top_mm"], use_dpi)
    bottom_margin = mm_to_px(cfg["layout_bottom_mm"], use_dpi)

    if gap is not None and gap_mm is None and "layout_gap_mm" not in layout_values:
        gap_px = int(gap)
        if gap_px < 0:
            raise ValueError("列间距不能为负数")
    else:
        gap_px = mm_to_px(cfg["layout_gap_mm"], use_dpi)

    content_x0 = left_margin
    content_x1 = page_w - right_margin
    content_y0 = top_margin
    content_y1 = page_h - bottom_margin
    content_w = content_x1 - content_x0
    content_h = content_y1 - content_y0
    if content_w - gap_px < 2 or content_h < 1:
        raise ValueError("页边距或列间距设置后没有足够的可打印区域")

    if not order_paths:
        raise ValueError("缺少订单/购买截图")
    if not payment_paths:
        raise ValueError("缺少支付截图")

    # 有序加载；同侧多张纵向拼接
    order_im = _load_side(list(order_paths), crop_whitespace=crop_whitespace)
    pay_im = _load_side(list(payment_paths), crop_whitespace=crop_whitespace)

    # Adaptive widths: a tall phone bill and a wide order are not forced into
    # equal 50:50 boxes. Allocate the real space by their natural aspect ratios;
    # an explicitly reviewed AI ratio may override that allocation.
    if cfg["layout_order_on_left"]:
        left_source, right_source = order_im, pay_im
    else:
        left_source, right_source = pay_im, order_im
    available_w = content_w - gap_px
    ratio = cfg.get("layout_column_ratio")
    if ratio is None and cfg.get("layout_adaptive", True):
        la = left_source.width / max(1, left_source.height)
        ra = right_source.width / max(1, right_source.height)
        ratio = la / max(0.001, la + ra)
    if ratio is None:
        ratio = 0.5
    try:
        ratio = float(ratio)
    except (TypeError, ValueError):
        ratio = 0.5
    if not math.isfinite(ratio):
        raise ValueError("左右内容比例必须是有限数")
    ratio = min(0.85, max(0.15, ratio))
    left_w = min(available_w - 1, max(1, round(available_w * ratio)))
    right_w = available_w - left_w
    left_im = _fit_prefer_box(left_source, left_w, content_h)
    right_im = _fit_prefer_box(right_source, right_w, content_h)

    # 列宽只约束缩放；去掉列内闲置宽度后，以实际图片组合的边界居中。
    # 不调整页边距，也不把闲置宽度混入用户指定的列间距。
    pair_w = left_im.width + gap_px + right_im.width
    pair_x = content_x0 + (content_w - pair_w) // 2
    canvas = Image.new("RGB", (page_w, page_h), (255, 255, 255))
    align = cfg["layout_vertical_align"]
    if align not in ("top", "center"):
        raise ValueError("垂直对齐方式必须为 top 或 center")
    placements = []

    def _paste_col(im: Image.Image, x: int, source: Image.Image, role: str) -> None:
        y = content_y0 if align == "top" else content_y0 + (content_h - im.height) // 2
        canvas.paste(im, (x, y))
        placements.append({
            "role": role,
            "source_pixels": [source.width, source.height],
            "box_pixels": [x, y, im.width, im.height],
            "printed_mm": [round(im.width / use_dpi * 25.4, 2), round(im.height / use_dpi * 25.4, 2)],
            "source_effective_dpi": round(max(source.width / im.width, source.height / im.height) * use_dpi, 1),
        })

    _paste_col(left_im, pair_x, left_source, "order" if cfg["layout_order_on_left"] else "payment")
    _paste_col(right_im, pair_x + left_im.width + gap_px, right_source,
               "payment" if cfg["layout_order_on_left"] else "order")
    pair_y = min(p["box_pixels"][1] for p in placements)
    canvas.info["layout_metrics"] = {
        # column_ratio remains the left column's allocation, regardless of role.
        "column_ratio": round(ratio, 4),
        "rendered_column_ratio": round(left_im.width / (left_im.width + right_im.width), 4),
        "adaptive": bool(cfg.get("layout_adaptive", True)),
        "vertical_align": align,
        "printable_box_pixels": [content_x0, content_y0, content_w, content_h],
        "pair_box_pixels": [pair_x, pair_y, pair_w, max(left_im.height, right_im.height)],
        "placements": placements,
        "warnings": ["材料缩放较大，请放大检查金额、地址、数量与时间，再核对实际打印清晰度。"]
        if any(p["source_effective_dpi"] > 500 for p in placements) else [],
    }

    # 默认不画灰色装订线；仅当显式要求时绘制极浅参考线
    if draw_binding_guide:
        try:
            from PIL import ImageDraw

            draw = ImageDraw.Draw(canvas)
            guide_x = left_margin - 2
            if guide_x > 8:
                draw.line(
                    [(guide_x, 16), (guide_x, page_h - 16)],
                    fill=(238, 238, 238),
                    width=1,
                )
        except Exception:
            pass

    return canvas


def prepare_invoice_image(
    path: str | Path,
    *,
    dpi: int = 300,
    margin_ratio: float = 0.04,
    crop_whitespace: bool = True,
    portrait: bool = True,
    **_compat: Any,
) -> Image.Image:
    """将发票图放入 A4 页面（默认纵向），四周留白；保持比例完整。"""
    im = open_rgb(path)
    if crop_whitespace:
        im = smart_crop_whitespace(im, padding=4)

    if portrait:
        page_w, page_h = a4_portrait_pixels(dpi)
    else:
        page_w, page_h = a4_landscape_pixels(dpi)

    pad = int(min(page_w, page_h) * margin_ratio)
    box_w = max(1, page_w - pad * 2)
    box_h = max(1, page_h - pad * 2)
    fitted = fit_into_box(im, box_w, box_h, center=True)
    canvas = Image.new("RGB", (page_w, page_h), (255, 255, 255))
    canvas.paste(fitted, (pad, pad))
    return canvas


def save_preview(im: Image.Image, path: str | Path, quality: int = 85) -> Path:
    """保存预览图（供 UI 预览用；正式导出包内不写 preview）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rgb = im.convert("RGB")
    rgb.save(path, format="JPEG", quality=quality, optimize=True)
    return path


def make_thumbnail(path: str | Path, out: str | Path, size: int = 160) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
        im.thumbnail((size, size), Image.Resampling.LANCZOS)
        im.save(out, format="JPEG", quality=80)
    return out
