"""Conservative, reviewable grouping suggestions. Never silently mixes purchases."""
from __future__ import annotations

import re
from decimal import Decimal
from .validator import parse_amount


def _id(value) -> str:
    return re.sub(r'[^a-z0-9]', '', str(value or '').lower())


def _merchant(value) -> str:
    return re.sub(r'[\s\W_]+', '', str(value or '').lower())


def propose_groups(records: list[dict]) -> dict:
    rows = [m for m in records if m.get('selected', True)]
    anchors = [m for m in rows if m.get('category') == 'order' and _id(m.get('extracted', {}).get('order_no'))]
    by_order: dict[str, dict] = {}
    for m in anchors:
        key = _id(m['extracted']['order_no'])
        if len(key) < 5:
            continue
        if key not in by_order:
            info=m.get('extracted', {})
            by_order[key]={'key':key, 'title':(info.get('product_name') or '采购')[:30]+' · '+str(info['order_no'])[-6:],
                           'ids':[], 'reason':'订单截图中的清晰订单号', 'amount':None, 'merchant':_merchant(info.get('merchant'))}
            try:by_order[key]['amount']=parse_amount(info.get('amount'))
            except ValueError:pass
        by_order[key]['ids'].append(m['id'])
    groups=list(by_order.values())
    if len(groups)<2:
        return {'groups':[], 'unassigned':[m['id'] for m in rows],
                'message':'暂未识别到至少两个不同的订单号。为避免混单，请先识别材料，或手动新建采购并移动材料。'}
    claimed={mid for g in groups for mid in g['ids']}
    for row in rows:
        if row['id'] in claimed:continue
        info=row.get('extracted',{})
        oid=_id(info.get('order_no'))
        direct=[g for g in groups if oid and oid==g['key']]
        if len(direct)==1:
            direct[0]['ids'].append(row['id']);claimed.add(row['id']);continue
        # A different visible order number is not ignored just to force an amount match.
        if oid:continue
        try:amount=parse_amount(info.get('amount'))
        except ValueError:continue
        merchant=_merchant(info.get('merchant'))
        candidates=[g for g in groups if g['amount']==amount and merchant and g['merchant']==merchant]
        if len(candidates)==1:
            candidates[0]['ids'].append(row['id']);claimed.add(row['id'])
            candidates[0]['reason']+='；无订单号材料按唯一商户+金额辅助关联，需核对原图'
    for g in groups:
        g.pop('amount',None);g.pop('merchant',None)
    return {'groups':groups,'unassigned':[m['id'] for m in rows if m['id'] not in claimed],
            'message':'以下仅为分单建议，导出前仍需核对原始订单、发票与支付关系。无法可靠关联的材料留在原采购，不强行归类。'}
