"""PDF 生成与整包输出（每笔采购恰好 2 个 PDF，原子写入）。"""

from __future__ import annotations

import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from PIL import Image

from .image_processor import (
    DEFAULT_LAYOUT,
    prepare_invoice_image,
    process_voucher_layout,
    resolve_layout,
    save_preview,
)
from .models import ImageCategory, ImageItem, ProcessResult, ValidationIssue
from .naming import NamingError, build_output_names
from .validator import (
    has_blocking_errors,
    needs_manual_confirmation,
    unknown_core_warnings,
    validate_batch,
)
from ..config.settings import Settings


def _image_to_pdf(im: Image.Image, pdf_path: Path, dpi: int = 300) -> Path:
    """单页图片写入 PDF（嵌入位图，保持横向/纵向）。"""
    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    rgb = im.convert("RGB")
    rgb.save(
        str(pdf_path),
        "PDF",
        resolution=float(dpi),
        quality=95,
    )
    return pdf_path


def _layout_from_settings(
    settings: Settings,
    *,
    order_on_left: Optional[bool] = None,
    extra_layout: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """从 Settings.extra / pdf_dpi 解析打印版式（毫米）。"""
    extra = dict(settings.extra or {})
    # settings.pdf_dpi 作为 dpi 回退
    if "dpi" not in extra and getattr(settings, "pdf_dpi", None):
        extra.setdefault("dpi", int(settings.pdf_dpi))
    # 兼容旧 left_margin_ratio → mm
    if "layout_left_mm" not in extra and "left_mm" not in extra:
        ratio = getattr(settings, "left_margin_ratio", None)
        if ratio is not None and "layout_left_mm" not in (extra_layout or {}):
            # 仅当 extra 完全没布局时不强制覆盖 DEFAULT；resolve_layout 会处理
            pass
    merged = dict(extra)
    if extra_layout:
        merged.update(dict(extra_layout))
    ov: dict[str, Any] = {}
    if order_on_left is not None:
        ov["layout_order_on_left"] = order_on_left
    return resolve_layout(merged, **ov)


def generate_voucher_pdf(
    order_paths: Sequence[str | Path],
    payment_paths: Sequence[str | Path],
    pdf_path: str | Path,
    *,
    settings: Settings,
    preview_path: Optional[str | Path] = None,
    order_on_left: Optional[bool] = None,
    crop_whitespace: bool = True,
    layout: Optional[Mapping[str, Any]] = None,
    **_compat: Any,
) -> tuple[Path, Optional[Path], Image.Image]:
    """生成购买及支付凭证 PDF。preview_path 仅可选，正式打包不使用。"""
    cfg = _layout_from_settings(settings, order_on_left=order_on_left, extra_layout=layout)
    im = process_voucher_layout(
        order_paths,
        payment_paths,
        layout=cfg,
        crop_whitespace=crop_whitespace,
        draw_binding_guide=False,
    )
    dpi = int(cfg.get("dpi") or 300)
    pdf = _image_to_pdf(im, Path(pdf_path), dpi=dpi)
    prev = None
    if preview_path:
        quality = int(getattr(settings, "jpeg_quality", 92) or 92)
        prev = save_preview(im, preview_path, quality=quality)
    return pdf, prev, im


def generate_invoice_pdf(
    invoice_path: str | Path,
    pdf_path: str | Path,
    *,
    settings: Settings,
    preview_path: Optional[str | Path] = None,
    source_is_pdf: bool = False,
    crop_whitespace: bool = True,
    **_compat: Any,
) -> tuple[Path, Optional[Path], list[ValidationIssue]]:
    """生成有手写留白的发票打印版，原件由材料库独立保存。

    PDF 原件通过页面变换等比放入 A4，不栅格化票面文字；打印副本不保留数字签名效力。
    显式 invoice_layout_mode='original' 才原样归档，默认 'print'。
    图片原件仍提示不能替代商家开具的电子发票。
    """
    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    extra_issues: list[ValidationIssue] = []

    src = Path(invoice_path)
    is_pdf = source_is_pdf or src.suffix.lower() == ".pdf"

    if is_pdf and (settings.extra or {}).get("invoice_layout_mode", "print") == "original":
        # Explicit archival mode only. Normal submissions use a handwriting-margin print copy.
        shutil.copy2(str(src), str(pdf_path))
        return pdf_path, None, extra_issues

    if is_pdf:
        from .invoice_layout import make_invoice_print_copy
        make_invoice_print_copy(src, pdf_path, settings)
        extra_issues.append(ValidationIssue(
            "info", "invoice_print_layout",
            "原发票已等比放入带上下手写留白的 A4 打印版，票面文字/矢量保留；原始电子发票仍在本机原件中。打印版不沿用原件数字签名的有效性。",
            [src.name],
        ))
        if preview_path:
            from .pdf_utils import render_pdf_first_page
            rendered = Path(preview_path).with_suffix('.png')
            render_pdf_first_page(pdf_path, rendered, 4.0)
            return pdf_path, rendered, extra_issues
        return pdf_path, None, extra_issues

    extra_issues.append(
        ValidationIssue(
            "warning",
            "invoice_from_image",
            "当前电子发票由图片生成 PDF，不能视为税务系统新开具的电子发票；"
            "建议向商家索取原版 PDF 发票；软件会另制留白打印版，并独立保留原件。", 
            [src.name],
        )
    )

    extra = dict(settings.extra or {})
    dpi = int(extra.get("dpi") or getattr(settings, "pdf_dpi", None) or 300)
    from .invoice_layout import invoice_image_page
    im = invoice_image_page(src, settings, dpi=dpi)
    pdf = _image_to_pdf(im, pdf_path, dpi=dpi)
    prev = None
    if preview_path:
        quality = int(getattr(settings, "jpeg_quality", 92) or 92)
        prev = save_preview(im, preview_path, quality=quality)
    return pdf, prev, extra_issues


def _rm_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def build_output_package(
    items: list[ImageItem],
    settings: Settings,
    *,
    force: bool = False,
    order_on_left: Optional[bool] = None,
    manual_confirmed: bool = False,
    crop_whitespace: bool = True,
    layout: Optional[Mapping[str, Any]] = None,
    **_compat: Any,
) -> ProcessResult:
    """校验并输出：普通文件夹 + 恰好 2 个 PDF（电子发票、购买及支付凭证）。

    规则：
    - 存在 error 时拒绝导出；force 保留但不绕过 error（不可宣称合规）
    - 未知核心字段为 warning：无 error 时需 manual_confirmed=True 才允许导出
    - 同名目录已存在 → 绝不覆盖，不留半成品
    - 先写临时目录，成功后再 rename；失败清理临时目录
    - 不写 _preview、不写校验报告
    """
    issues = validate_batch(items)

    # force 不得绕过事实错误
    if has_blocking_errors(issues):
        msg = "存在阻断性错误，已停止导出。请修正后重试。"
        if force:
            msg = (
                "存在阻断性错误，已停止导出。"
                "force 不会绕过事实错误，不能强制宣称合规。"
            )
        return ProcessResult(success=False, issues=issues, message=msg)

    if needs_manual_confirmation(issues) and not manual_confirmed:
        codes = sorted({i.code for i in unknown_core_warnings(issues)})
        return ProcessResult(
            success=False,
            issues=issues
            + [
                ValidationIssue(
                    "error",
                    "need_manual_confirm",
                    "存在未知核心字段（"
                    + ", ".join(codes)
                    + "），请人工核对后以 manual_confirmed=True 导出。",
                )
            ],
            message="需要人工确认未知核心字段后才能导出",
        )

    if not settings.is_profile_complete():
        issues.append(
            ValidationIssue(
                "error",
                "profile",
                "请先完善个人资料：姓名、导师、项目名称、申报日期。",
            )
        )
        return ProcessResult(success=False, issues=issues, message="个人资料不完整")

    try:
        names = build_output_names(settings)
    except NamingError as e:
        issues.append(ValidationIssue("error", "bad_naming", str(e)))
        return ProcessResult(success=False, issues=issues, message=str(e))

    out_root = Path(settings.output_dir)
    try:
        out_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return ProcessResult(
            success=False,
            issues=issues + [ValidationIssue("error", "output_dir", f"无法创建输出目录：{e}")],
            message=str(e),
        )

    final_folder = out_root / names.folder_name
    if final_folder.exists():
        return ProcessResult(
            success=False,
            issues=issues
            + [
                ValidationIssue(
                    "error",
                    "folder_exists",
                    f"目标文件夹已存在，拒绝覆盖：{final_folder.name}。"
                    f"请更换申报日期/笔序或先移走旧目录。",
                )
            ],
            message=f"目录已存在，拒绝覆盖：{final_folder}",
        )

    selected = [i for i in items if i.selected]
    invoices = [i for i in selected if i.category == ImageCategory.INVOICE]
    payments = [i for i in selected if i.category == ImageCategory.PAYMENT]
    orders = [i for i in selected if i.category == ImageCategory.ORDER]

    if len(invoices) != 1:
        # validate_batch 已报错；双保险
        return ProcessResult(
            success=False,
            issues=issues
            + [
                ValidationIssue(
                    "error",
                    "invoice_count",
                    f"导出需要恰好 1 张发票，当前 {len(invoices)} 张。",
                )
            ],
            message="发票数量不正确",
        )
    if not orders or not payments:
        return ProcessResult(
            success=False,
            issues=issues
            + [
                ValidationIssue(
                    "error",
                    "no_voucher_export",
                    "订单或支付材料不足，无法生成购买及支付凭证",
                )
            ],
            message="订单或支付材料不足",
        )

    # 临时目录：同盘优先，完成后 rename，失败不留半成品
    tmp_dir: Optional[Path] = None
    invoice_pdf: Optional[Path] = None
    voucher_pdf: Optional[Path] = None

    try:
        # 手建临时目录（避免 tempfile 在部分沙箱中创建不可写目录）
        # 目录名勿以 "." 开头
        tmp_dir = None
        for _ in range(8):
            cand = out_root / f"tmp_export_{uuid.uuid4().hex[:10]}_{int(time.time() * 1000) % 100000}"
            try:
                cand.mkdir(parents=False, exist_ok=False)
                tmp_dir = cand
                break
            except FileExistsError:
                continue
        if tmp_dir is None:
            raise RuntimeError("无法创建导出临时目录")
        invoice_pdf_path = tmp_dir / names.invoice_pdf
        voucher_pdf_path = tmp_dir / names.voucher_pdf

        inv = invoices[0]
        is_pdf = inv.path.suffix.lower() == ".pdf"
        invoice_pdf, _prev_inv, inv_issues = generate_invoice_pdf(
            inv.path,
            invoice_pdf_path,
            settings=settings,
            preview_path=None,
            source_is_pdf=is_pdf,
            crop_whitespace=crop_whitespace,
        )
        issues.extend(inv_issues)

        # 图片发票的 warning 属于未知/建议类；若此时 manual_confirmed 仍不足
        # （validate 阶段未必有 invoice_from_image），再次门禁
        if (
            any(i.code == "invoice_from_image" for i in inv_issues)
            and not manual_confirmed
            and needs_manual_confirmation(issues)
        ):
            _rm_tree(tmp_dir)
            return ProcessResult(
                success=False,
                issues=issues
                + [
                    ValidationIssue(
                        "error",
                        "need_manual_confirm",
                        "图片生成的发票 PDF 需人工确认（建议改用原 PDF），"
                        "请以 manual_confirmed=True 导出。",
                    )
                ],
                message="需要人工确认后才能导出图片发票",
            )

        cfg = _layout_from_settings(
            settings, order_on_left=order_on_left, extra_layout=layout
        )
        # 兼容旧 left_margin_ratio
        if (
            "layout_left_mm" not in (settings.extra or {})
            and "left_mm" not in (settings.extra or {})
            and getattr(settings, "left_margin_ratio", None) is not None
            and layout is None
        ):
            # 若用户只设了 ratio，转换为 mm 传入
            ratio = float(settings.left_margin_ratio)
            cfg = resolve_layout(cfg, layout_left_mm=297.0 * ratio)

        im = process_voucher_layout(
            [o.path for o in orders],
            [p.path for p in payments],
            layout=cfg,
            crop_whitespace=crop_whitespace,
            draw_binding_guide=False,
        )
        dpi = int(cfg.get("dpi") or 300)
        voucher_pdf = _image_to_pdf(im, voucher_pdf_path, dpi=dpi)

        # 只允许两个 PDF，禁止其它产物
        produced = sorted(p.name for p in tmp_dir.iterdir() if p.is_file())
        expected = sorted([names.invoice_pdf, names.voucher_pdf])
        if produced != expected:
            raise RuntimeError(f"临时目录文件异常：{produced}，期望 {expected}")

        # 再次确认目标不存在（防竞态）
        if final_folder.exists():
            raise FileExistsError(f"目标文件夹已存在：{final_folder}")

        # 原子就位：同卷 rename
        os.rename(str(tmp_dir), str(final_folder))
        tmp_dir = None  # 已成功改名，勿删

        invoice_final = final_folder / names.invoice_pdf
        voucher_final = final_folder / names.voucher_pdf
        if not invoice_final.is_file() or not voucher_final.is_file():
            # 极端情况：尽量清理
            _rm_tree(final_folder)
            raise RuntimeError("导出后文件缺失，已回滚目录")

        # 确保没有 _preview / 报告
        for banned in ("_preview", "校验报告.txt"):
            p = final_folder / banned
            if p.exists():
                if p.is_dir():
                    _rm_tree(p)
                else:
                    p.unlink()

        return ProcessResult(
            success=True,
            folder_path=final_folder,
            invoice_pdf=invoice_final,
            voucher_pdf=voucher_final,
            issues=issues,
            message="导出成功",
            preview_invoice=None,
            preview_voucher=None,
        )
    except FileExistsError as e:
        if tmp_dir is not None:
            _rm_tree(tmp_dir)
        return ProcessResult(
            success=False,
            issues=issues
            + [ValidationIssue("error", "folder_exists", f"目标文件夹已存在，拒绝覆盖：{e}")],
            message=str(e),
        )
    except Exception as e:
        if tmp_dir is not None:
            _rm_tree(tmp_dir)
        return ProcessResult(
            success=False,
            issues=issues + [ValidationIssue("error", "export_fail", f"导出失败：{e}")],
            message=str(e),
        )


# 公开版式默认值，便于 Web/桌面端读取
LAYOUT_DEFAULTS = dict(DEFAULT_LAYOUT)
