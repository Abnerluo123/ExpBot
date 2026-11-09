"""The four user-requested review categories, shared by review and export gates."""
from __future__ import annotations

import copy
import re

LABELS = {"personal_info": "个人信息", "address": "收货地址", "missing_materials": "材料缺少", "clarity": "模糊"}
_IGNORED = re.compile(r"amount|price|merchant|order_no|identifier|stitch|border|crop|invoice_from_image|invoice_print|signature|multi_invoice|unknown_check|forced_review|事实冲突|金额|商户|店铺|订单号", re.I)


def category_of(issue):
    if isinstance(issue, str):
        code, message, declared = "", issue, ""
    elif isinstance(issue, dict):
        code = str(issue.get("code") or issue.get("key") or "").lower()
        message = str(issue.get("message") or issue.get("text") or issue.get("detail") or "")
        declared = issue.get("category", "")
    else:
        return None
    if declared in LABELS:
        return declared
    if _IGNORED.search(code):
        return None
    if re.search(r"personal|profile|buyer|tax_id|taxpayer|姓名|导师|抬头|税号|个人信息", code, re.I):
        return "personal_info"
    if re.search(r"address|shipping|收货地址", code, re.I):
        return "address"
    if re.search(r"clarity|readability|legibility|blurr|resolution|模糊|清晰", code, re.I):
        return "clarity"
    if re.search(r"missing|completeness|complete|unassigned|unclassified|no_groups|no_voucher", code, re.I):
        return "missing_materials"
    if _IGNORED.search(message):
        return None
    if re.search(r"抬头|税号|个人信息|姓名.{0,5}(?:缺|错|不符)|导师.{0,5}(?:缺|错)", message):
        return "personal_info"
    if re.search(r"收货地址|收件地址|送货地址", message):
        return "address"
    if re.search(r"模糊|看不清|无法辨认|分辨率|清晰度|字迹不清|文字过小", message):
        return "clarity"
    if re.search(r"(?:未提供|缺少|缺失|待补交|不完整).*(?:材料|发票|订单|支付|付款)|(?:材料|发票|订单|支付|付款).*(?:缺少|缺失|不完整|待补交)", message):
        return "missing_materials"
    return None


def compact_issues(issues):
    """At most one issue per category per purchase; facts themselves stay intact."""
    combined = {}
    for raw in issues or []:
        if isinstance(raw, str):
            raw = {"message": raw, "level": "warning"}
        if not isinstance(raw, dict):
            continue
        category = category_of(raw)
        if not category or str(raw.get("level", raw.get("status", "warning"))).lower() in {"info", "pass", "passed", "ok", "success"}:
            continue
        message = str(raw.get("message") or raw.get("text") or raw.get("detail") or "").strip()
        if not message:
            continue
        group = raw.get("group_key") or raw.get("order_id") or ""
        key = (str(group), category)
        if key not in combined:
            combined[key] = {**copy.deepcopy(raw), "category": category, "label": LABELS[category], "message": message}
        else:
            existing = combined[key]
            parts = existing["message"].split("；")
            if message not in parts and message not in existing["message"]:
                existing["message"] += "；" + message
            if raw.get("level") == "error" or raw.get("status") == "fail":
                existing["level"] = "error"
            existing["material_ids"] = list(dict.fromkeys([*existing.get("material_ids", []), *raw.get("material_ids", [])]))
    return list(combined.values())


def visible_warnings(warnings):
    return [issue["message"] for issue in compact_issues(warnings)]
