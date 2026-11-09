"""Default invoice profile and purchase checklist.

Fill these in Settings for your own organization. The repository ships with
empty placeholders only — no institution-specific names or tax IDs.
"""
SCHOOL_INVOICE_INFO = {
    "taxpayer_name": "",
    "taxpayer_id": "",
    "address": "",
    "phone": "",
    "bank_account": "",
    "bank_name": "",
}
REQUIRED_ADDRESS = ""
# Optional address tokens used only when non-empty (substring soft-match aid).
ADDRESS_KEYWORDS: list[str] = []
RULES_TEXT = """
【采购报销材料整理须知】
1. 下单前确认商家能开具抬头、税号与你在设置中填写的购买方信息一致的电子发票。
2. 收货地址按你在设置中填写的统一地址填写，便于后续核对。
3. 发票、订单实付、支付记录金额必须一致；商品、数量、时间须能与凭证对应。
4. 每笔采购单独一个普通文件夹，内含且仅含 2 个 PDF：电子发票 PDF；购买及支付凭证 PDF。
5. 购买及支付凭证 PDF 为 A4 横向单页，订单与支付材料左右拼接，左侧预留手写与装订空间。
6. 文件夹名：日期-本人姓名-指导老师姓名-项目名称。PDF 以同一名称加 -电子发票 或 -购买及支付凭证。
7. 同一天第 2 笔及以后：PDF 日期后加 (2)、(3)…；每笔仍单独一个文件夹，不可混单。
8. 视觉模型与自动预检仅是辅助，不能替代核对原件和财务审核，也不保证报销通过。
""".strip()
