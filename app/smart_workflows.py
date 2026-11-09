"""Reviewable smart plans used by the local server.
Workers only create previews; originals and order assignments change only on apply.
"""
from __future__ import annotations

import copy
import io
import time
from dataclasses import asdict
from pathlib import Path

from PIL import Image

from .core.ai_batch import BatchVisionClient
from .core.image_processor import open_rgb, smart_crop_whitespace, stitch_vertical
from .core.smart_stitch import smart_stitch


def build_records(ws, ids):
    records=[]
    for material_id in ids:
        m=ws.material(material_id)
        image=ws._image(m)
        records.append({'id':m['id'],'name':m['name'],'path':m['working'], 'width':image.width,'height':image.height,
                        'extracted':copy.deepcopy(m.get('extracted',{})),'category':m['category']})
    return records


def start_plan(ws,data):
    order_id=data['order_id'];kind=data.get('kind','orders');use_ai=bool(data.get('use_ai',True))
    if kind not in {'orders','stitch','layout'}:raise ValueError('未知的智能规划类型')
    if use_ai and not data.get('consent'):raise ValueError('请确认将选中材料发送到你配置的视觉模型')
    with ws.lock:
        ws.guard_order(order_id)
        if use_ai and not ws.settings.is_ai_ready():raise ValueError('请先填写视觉模型接口、模型名称与API Key')
        if kind=='orders' and data.get('scope')=='workspace':
            ids=[m['id'] for m in ws.state['materials'].values() if m.get('selected',True)]
        else:
            ids=data.get('ids') or [m['id'] for m in ws.records(order_id) if m.get('selected',True)]
        if not ids or len(ids)!=len(set(ids)):raise ValueError('请选择不重复的材料')
        if kind=='stitch' and not 2<=len(ids)<=8:raise ValueError('智能拼接一次支持2–8张同一页面截图')
        if kind=='orders' and len(ids)>120:raise ValueError('整批分析一次最多120份材料，请按批导入')
        original_rows=[copy.deepcopy(ws.material(mid)) for mid in ids]
        if kind!='orders' and any(r['order_id']!=order_id for r in original_rows):raise ValueError('不能跨采购拼图或排版')
        records=build_records(ws,ids)
        settings=ws.order_settings(order_id)
        snapshot={m['id']:m.get('revision',0) for m in original_rows}
        assignments={m['id']:m['order_id'] for m in original_rows}
        plan_id=__import__('uuid').uuid4().hex[:20]
        folder=ws.root/'plans'/plan_id;folder.mkdir(parents=True,exist_ok=True)
    def work(job):
        def progress(done,total,message):
            with ws.lock:job.update(progress=round(done/max(1,total)*80),message=message)
        ai=BatchVisionClient(settings) if use_ai else None
        if kind=='orders':
            if not ai:raise ValueError('跨订单联合分析需要视觉模型；本地规则分组可使用现有分单工具')
            result=ai.plan_orders(records,on_progress=progress)
        elif kind=='stitch':
            job.update(message='视觉模型判断乱序截图与重叠区域' if ai else '本地比对内容、排序并去掉重复区域',progress=10)
            model_plan=ai.plan_stitch(records,on_progress=progress) if ai else None
            job.update(message='按真实像素校准接缝，生成去重拼接预览',progress=85)
            stitched=smart_stitch(records,plan=model_plan)
            image=stitched.pop('image')
            target=folder/'stitch.png';image.save(target,'PNG',optimize=True)
            result={**stitched,'preview_url':f'/plan-preview/{plan_id}/stitch.png','width':image.width,'height':image.height,
                    'ai_plan':model_plan,'source_mode':'ai+pixels' if ai else 'pixels', 'category':data.get('category','order')}
        else:
            selected=[r for r in original_rows if r.get('selected',True)]
            sides=[]
            for category in ('payment','order'):
                side=[r for r in selected if r['category']==category]
                if not side:raise ValueError('AI排版需要已分类的订单和支付材料')
                images=[smart_crop_whitespace(open_rgb(r['working'])) for r in side]
                im=stitch_vertical(images)
                path=folder/f'{category}-side.png';im.save(path,'PNG')
                sides.append({'id':category+'-side','name':category+' combined side','path':str(path),'width':im.width,'height':im.height,'category':category,'extracted':{}})
            constraints={'min_left_mm':float(settings.extra.get('layout_left_mm',24)), 'min_right_mm':float(settings.extra.get('layout_right_mm',24)),
                         'min_top_mm':float(settings.extra.get('layout_top_mm',24)), 'min_bottom_mm':float(settings.extra.get('layout_bottom_mm',24)),
                         'min_gap_mm':float(settings.extra.get('layout_gap_mm',4)),
                         'page_width_mm':297,'page_height_mm':210,'order_on_left':bool(settings.extra.get('layout_order_on_left',True))}
            result=ai.plan_layout(sides,constraints=constraints,on_progress=progress)
            # AI cannot reduce the user's reserved handwriting margins.
            layout=result.get('layout',{})
            for key,min_key in [('layout_left_mm','min_left_mm'),('layout_right_mm','min_right_mm'),('layout_top_mm','min_top_mm'),('layout_bottom_mm','min_bottom_mm')]:
                layout[key]=max(float(layout.get(key,constraints[min_key])),constraints[min_key])
            result['layout']=layout
            from .core.image_processor import process_voucher_layout
            paths={side['category']:side['path'] for side in sides}
            candidate={**settings.extra,**layout,'layout_adaptive':True,'layout_order_on_left':bool(settings.extra.get('layout_order_on_left',True))}
            preview=process_voucher_layout([paths['order']],[paths['payment']],dpi=180,layout=candidate,crop_whitespace=False)
            preview.save(folder/'layout-preview.jpg','JPEG',quality=94)
            result['preview_url']=f'/plan-preview/{plan_id}/layout-preview.jpg'
            result['layout_metrics']=copy.deepcopy(preview.info.get('layout_metrics',{}))
        entry={'id':plan_id,'kind':kind,'order_id':order_id,'material_ids':ids,'revisions':snapshot,'assignments':assignments,
               'created':__import__('datetime').datetime.now().isoformat(timespec='seconds'),'result':result,'applied':False}
        with ws.lock:
            ws.state.setdefault('plans',{})[plan_id]=entry
            ws.save()
        return {'plan_id':plan_id,'kind':kind}
    return ws.start_job('smart-'+kind,order_id,work)


def public_plan(ws,plan_id):
    try:entry=ws.state.get('plans',{})[plan_id]
    except KeyError:raise ValueError('智能计划不存在，请重新分析') from None
    out=copy.deepcopy(entry)
    for group in out.get('result',{}).get('groups',[]):
        group['materials']=[ws.public_material(ws.material(mid)) for mid in group['material_ids']]
    return out


def apply_plan(ws,data):
    if not data.get('confirmed'):raise ValueError('请先核对智能计划和预览，再确认应用')
    with ws.lock:
        try:entry=ws.state.get('plans',{})[data['plan_id']]
        except KeyError:raise ValueError('智能计划不存在') from None
        if entry.get('applied'):raise ValueError('此计划已经应用，不会重复处理')
        for mid in entry['material_ids']:
            m=ws.material(mid);ws.guard_order(m['order_id'])
            if m.get('revision',0)!=entry['revisions'][mid] or m['order_id']!=entry['assignments'][mid]:
                raise ValueError('分析后材料发生变化，请重新生成计划，避免旧结果覆盖新信息')
        result=entry['result'];kind=entry['kind'];origin=ws.order(entry['order_id'])
        if kind=='stitch':
            if result.get('status')!='matched':raise ValueError('没有找到可靠的重复内容，不能把联系表当成成功拼图。请手动校对或补充连续截图')
            source=ws.root/'plans'/entry['id']/'stitch.png'
            category=result.get('category','order')
            if category not in ('order','payment','other'):category='order'
            new=ws.import_bytes(source.read_bytes(),f'智能去重拼接-{len(entry["material_ids"])}张.png',entry['order_id'])
            target=ws.material(new['id']);target['category']=category;target['source_ids']=entry['material_ids']
            target['stitch_audit']={k:copy.deepcopy(v) for k,v in result.items() if k in ('joins','segments','warnings','ordered_ids','confidence','source_mode')}
            target['notes']=['智能拼接已按真实重叠内容排序、对齐并去重；原件保留。请核对接缝、收货地址和金额。']+result.get('warnings',[])
            for mid in entry['material_ids']:
                m=ws.material(mid);m['selected']=False;m['revision']+=1
            response={'material_id':new['id'],'kind':'stitch'}
        elif kind=='layout':
            layout=result.get('layout',{})
            allowed={'layout_column_ratio','layout_vertical_align','layout_left_mm','layout_right_mm','layout_top_mm','layout_bottom_mm','layout_gap_mm'}
            origin.setdefault('layout',{}).update({k:v for k,v in layout.items() if k in allowed})
            origin['layout']['layout_adaptive']=True
            origin['layout_review']={'legibility':result.get('legibility','review'),'reason':result.get('reason',''),'warnings':result.get('warnings',[])}
            ws.invalidate(origin['id'])
            response={'order_id':origin['id'],'kind':'layout','legibility':result.get('legibility','review')}
        else:
            groups=result.get('groups',[])
            if not groups:raise ValueError('没有可靠分组，材料已保持原样')
            selected_keys=data.get('selected_groups')
            if selected_keys is not None:
                groups=[g for g in groups if g['key'] in selected_keys]
            if not groups:raise ValueError('请至少选择一组应用')
            new_ids=[];claimed=[]
            for index,group in enumerate(groups):
                can_reuse=(index==0 and not any(x['order_id']==origin['id'] for x in ws.state['exports'])
                           and len({entry['assignments'][mid] for mid in entry['material_ids']})==1)
                target=origin if can_reuse else ws.add_order({'title':group['title'],'declare_date':origin['declare_date'],'project_name':origin.get('project_name','')})
                target['title']=group['title'][:80]
                target['ai_association']={'confidence':group['confidence'],'evidence':group['evidence'],'plan_id':entry['id']}
                target['suggested_stitch_sets']=group.get('stitch_sets',[])
                role_map={x['material_id']:x['category'] for x in group.get('roles',[])}
                for mid in group['material_ids']:
                    m=ws.material(mid);ws.invalidate(m['order_id']);m['order_id']=target['id']
                    if role_map.get(mid) in {'invoice','order','payment','other','unknown','receipt'}:m['category']=role_map[mid]
                    m['revision']+=1;claimed.append(mid)
                new_ids.append(target['id'])
            # Unassociated materials do not remain mixed inside the first confirmed purchase.
            leftover=[mid for mid in entry['material_ids'] if mid not in claimed and ws.material(mid)['order_id'] in new_ids]
            if leftover:
                pending=ws.add_order({'title':'待确认归属的材料','declare_date':origin['declare_date'],'project_name':origin.get('project_name','')})
                pending['is_inbox']=True
                for mid in leftover:ws.material(mid)['order_id']=pending['id'];ws.material(mid)['revision']+=1
            response={'order_ids':new_ids,'unassigned':[mid for mid in entry['material_ids'] if mid not in claimed],'kind':'orders'}
        entry['applied']=True;entry['applied_at']=__import__('datetime').datetime.now().isoformat(timespec='seconds');ws.save()
        return response
