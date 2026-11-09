"""文件夹与 PDF 命名规范。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from ..config.settings import Settings

# Windows 保留设备名
_WIN_RESERVED = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

# 单路径分量建议上限（给目录与两个 PDF 文件名留余量）
_MAX_COMPONENT_LEN = 120
_MAX_FOLDER_LEN = 150


class NamingError(ValueError):
    """命名/日期校验失败。"""


@dataclass
class OutputNames:
    folder_name: str
    file_prefix: str
    invoice_pdf: str
    voucher_pdf: str


def _safe(s: str, *, max_len: int = _MAX_COMPONENT_LEN) -> str:
    """清洗 Windows 非法文件名字符，去掉首尾空格与点。"""
    s = (s or "").strip()
    # 非法字符 \ / : * ? " < > | 以及控制字符
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", s)
    s = s.strip(" .")
    if not s:
        s = "未命名"
    # 保留名
    core = s.split(".")[0].upper()
    if core in _WIN_RESERVED:
        s = f"_{s}"
    if len(s) > max_len:
        s = s[:max_len].rstrip(" .")
    return s or "未命名"


def validate_declare_date(date: str) -> str:
    """校验申报日期必须为真实日历日 YYYYMMDD。"""
    d = (date or "").strip()
    if not re.fullmatch(r"\d{8}", d):
        raise NamingError(
            f"申报日期「{date}」无效，必须为 8 位数字 YYYYMMDD（例如 20260927）"
        )
    try:
        datetime.strptime(d, "%Y%m%d")
    except ValueError as e:
        raise NamingError(f"申报日期「{date}」不是有效日历日期") from e
    return d


def validate_order_index(idx: int) -> int:
    try:
        n = int(idx)
    except (TypeError, ValueError) as e:
        raise NamingError(f"当日笔序无效：{idx!r}") from e
    if n < 1:
        raise NamingError(f"当日笔序必须 ≥ 1，当前为 {n}")
    if n > 999:
        raise NamingError(f"当日笔序过大：{n}")
    return n


def build_output_names(settings: Settings, **_compat) -> OutputNames:
    """
    文件夹：采购申报日期+本人姓名+指导博士生/导师姓名+项目名称
      当日第 n 笔（n>1）：...（当日第n笔订单）
    PDF：
      日期 或 日期(n) -姓名-导师-项目-电子发票.pdf
      日期 或 日期(n) -姓名-导师-项目-购买及支付凭证.pdf

    第 1 笔不加 (1)；第 2 笔起文件名前缀用 日期(2)。
    """
    date = validate_declare_date(settings.declare_date or "")
    name = _safe(settings.name or "未命名")
    advisor = _safe(settings.advisor or "未填导师")
    project = _safe(settings.project_name or "未填项目")
    idx = validate_order_index(settings.order_index or 1)

    base = f"{date}-{name}-{advisor}-{project}"
    if idx > 1:
        folder_name = f"{base}（当日第{idx}笔订单）"
        file_prefix = f"{date}({idx})-{name}-{advisor}-{project}"
    else:
        folder_name = base
        file_prefix = base

    # 长度保护
    if len(folder_name) > _MAX_FOLDER_LEN:
        # 优先截断项目名
        overflow = len(folder_name) - _MAX_FOLDER_LEN
        project = _safe(project, max_len=max(8, len(project) - overflow))
        base = f"{date}-{name}-{advisor}-{project}"
        if idx > 1:
            folder_name = f"{base}（当日第{idx}笔订单）"
            file_prefix = f"{date}({idx})-{name}-{advisor}-{project}"
        else:
            folder_name = base
            file_prefix = base

    invoice_pdf = f"{file_prefix}-电子发票.pdf"
    voucher_pdf = f"{file_prefix}-购买及支付凭证.pdf"

    for label, fn in (("文件夹", folder_name), ("发票PDF", invoice_pdf), ("凭证PDF", voucher_pdf)):
        if re.search(r'[\\/:*?"<>|]', fn):
            raise NamingError(f"{label}名含非法字符：{fn}")
        if len(fn) > 200:
            raise NamingError(f"{label}名过长（{len(fn)}）：{fn[:40]}…")

    return OutputNames(
        folder_name=folder_name,
        file_prefix=file_prefix,
        invoice_pdf=invoice_pdf,
        voucher_pdf=voucher_pdf,
    )
