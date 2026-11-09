/* Joint visual planning + local pixel-verified smart operations, review before apply. */
'use strict';
let currentSmartPlan=null;
function smartWarnings(values){return (values||[]).map(w=>{let message=String(w);for(const id of currentSmartPlan?.material_ids||[]){message=message.replaceAll(id,materialName(id));}return `<div class="callout warning">${icon('warn')}<span>${escapeHTML(message)}</span></div>`;}).join('');}
function materialName(id){return state.materials[id]?.name||id;}
async function smartConsent(kind,count){
  if(!state.settings.has_api_key){openSettings('ai');toast('请先配置可看图的模型。');return false;}
  return confirmAction({orders:'AI 跨订单联合整理',stitch:'AI 理解乱序与重叠截图',layout:'AI 分析不同尺寸的最佳版式'}[kind],
    `<p>将向 <strong>${escapeHTML(state.settings.ai_api_base)}</strong> 的 <strong>${escapeHTML(state.settings.ai_vision_model)}</strong> 发送 ${count} 份选中材料（可能多次请求）。</p><p>模型会跨图分析${kind==='orders'?'订单归属、商户、商品与支付关联':kind==='stitch'?'真实上下顺序、固定页头与重复内容':'有效内容、缩放清晰度与左右空间分配'}，不再仅做单图分类。所有结论均先预览，由你确认后才应用。</p><div class="callout">原图不变。不使用 OCR，不重写任何票据文字。金额相同不等于同一订单；看不清或关联不确定的材料保留待确认。</div>`,'确认发送并规划');
}
async function startSmartOrders(){
  const yes=await confirmAction('选择整批分析范围',`<p>可把很多订单的材料一起导入后交给 AI 关联。每组会展示材料名和关联证据，无法确定的不强行归单。</p><label class="field">分析范围<select id="smartScope"><option value="current">当前采购里的所有选中材料（可含多单）</option><option value="workspace">整个工作区的选中材料</option></select></label><p class="tiny muted">分组不会自动完成合规审核；分组后仍需识别和核对字段。</p>`,'下一步');
  if(!yes)return;
  const scope=$('#smartScope').value;
  const count=(scope==='workspace'?Object.values(state.materials):materials()).filter(m=>m.selected).length;
  if(!count){toast('请先导入材料。');return;}
  if(!await smartConsent('orders',count))return;
  await api('/api/smart-plan',{kind:'orders',scope,order_id:currentId,use_ai:true,consent:true});await refresh();
}
async function startSmartStitch(useAI){
  if(stitchIds.length<2||stitchIds.length>8){toast('请选择2–8张同一订单页面的截图，顺序可以打乱。','error');return;}
  if(useAI&&!await smartConsent('stitch',stitchIds.length))return;
  closeDialog('stitchDialog');
  await api('/api/smart-plan',{kind:'stitch',order_id:currentId,ids:[...stitchIds],category:$('#stitchCategory').value,use_ai:useAI,consent:useAI});await refresh();
}
async function startSmartLayout(){
  const rows=activeMaterials().filter(m=>['order','payment'].includes(m.category));
  if(!rows.some(m=>m.category==='order')||!rows.some(m=>m.category==='payment')){toast('请先准备并分类订单与支付两侧材料。','error');return;}
  if(!await smartConsent('layout',rows.length))return;
  // Persist hard margin constraints first. It is allowed to reserve space, not shrink it away.
  await saveLayout();
  await api('/api/smart-plan',{kind:'layout',order_id:currentId,ids:rows.map(m=>m.id),use_ai:true,consent:true});await refresh();
}
async function showSmartPlan(planId){
  currentSmartPlan=await api('/api/smart-plan/get',{plan_id:planId});
  const p=currentSmartPlan,r=p.result;let html='';
  $('#smartPlanTitle').textContent={orders:'跨订单关联方案',stitch:'智能去重拼接预览',layout:'本笔动态排版建议'}[p.kind];
  $('#applySmartPlan').disabled=!!p.applied;
  if(p.kind==='orders'){
    html=`<p class="smart-intro">已联合考虑 ${p.material_ids.length} 份材料。请对照可见事实决定是否应用每一组；不要只凭金额判断归属。</p>${smartWarnings(r.warnings)}`;
    html+=(r.groups||[]).map(g=>`<section class="smart-order-group"><div class="smart-group-title"><label><input type="checkbox" data-smart-group="${escapeHTML(g.key)}" checked> ${escapeHTML(g.title)}</label><span class="pill neutral">关联可信度 ${Math.round(g.confidence*100)}%</span></div><div class="smart-material-strip">${g.material_ids.map(id=>`<div><img src="${state.materials[id]?.thumb_url||''}" alt=""><span>${escapeHTML(materialName(id))}</span><small>${labels[(g.roles||[]).find(x=>x.material_id===id)?.category]||'待核对'}</small></div>`).join('')}</div><details><summary>查看逐份关联证据</summary><ul>${(g.evidence||[]).map(e=>`<li><strong>${escapeHTML(materialName(e.material_id))}</strong>：${escapeHTML(e.visible_fact)}</li>`).join('')}</ul></details>${(g.stitch_sets||[]).length?`<p class="tiny muted">其中 ${(g.stitch_sets||[]).length} 组可能为同页连续截图，可在归组后使用「AI 乱序拼接」。</p>`:''}</section>`).join('');
    if(r.unassigned?.length)html+=`<section class="smart-unassigned"><h3>归属待确认 · ${r.unassigned.length} 份</h3><ul>${r.unassigned.map(id=>`<li>${escapeHTML(materialName(id))}</li>`).join('')}</ul><p>这些材料不会被强行放进已确认订单。</p></section>`;
    if(!r.groups?.length)$('#applySmartPlan').disabled=true;
  }else if(p.kind==='stitch'){
    const matched=r.status==='matched';
    html=`<div class="smart-summary ${matched?'':'uncertain'}"><strong>${matched?'已找到可核验的连续重叠链':'没有找到可靠接缝，尚未拼接成功'}</strong><p>${matched?`输出 ${r.width} × ${r.height}，依据 ${r.source_mode==='ai+pixels'?'视觉模型规划 + 本地真实像素校准':'本地二维内容匹配'}。请检查全部接缝与地址/金额。`:'下图只是完整原图联系表，不会作为订单长图导出；请补充相邻截图或手动调整。'}</p></div>${smartWarnings(r.warnings)}<div class="smart-stitch-layout"><div class="smart-stitch-preview"><img src="${r.preview_url}" alt="智能排序与重叠去重预览"></div><div class="smart-join-audit"><h3>建议顺序</h3><ol>${r.ordered_ids.map(id=>`<li>${escapeHTML(materialName(id))}</li>`).join('')}</ol><h3>接缝核验</h3>${(r.joins||[]).map(j=>`<div><strong>${escapeHTML(materialName(j.above_id))} → ${escapeHTML(materialName(j.below_id))}</strong><p>重复区 ${j.overlap_px??0}px · 跳过固定前缀 ${j.skip_prefix_px??0}px</p><p>${escapeHTML(j.reason||j.ai_reason||'二维内容匹配，详见本机审计记录')}</p></div>`).join('')}</div></div>`;
    if(!matched)$('#applySmartPlan').disabled=true;
  }else{
    const x=r.layout||{};
    html=`<div class="smart-summary ${r.legibility==='poor'?'uncertain':''}"><strong>可读性评估：${{good:'预计可读，仍需打印核验',review:'需要人工放大核验',poor:'当前约束下可能太小，不应直接提交'}[r.legibility]||'待确认'}</strong><p>${escapeHTML(r.reason||'根据两侧图片有效内容与字号调整空间分配。')}</p></div>${smartWarnings(r.warnings)}<div class="smart-layout-values"><div><b>${Math.round((x.layout_column_ratio??.5)*100)}%</b><span>左侧内容宽度</span></div><div><b>${Math.round((1-(x.layout_column_ratio??.5))*100)}%</b><span>右侧内容宽度</span></div><div><b>${x.layout_vertical_align==='center'?'居中':'顶对齐'}</b><span>垂直对齐</span></div></div><p class="tiny muted">只改变本笔的空间分配，不改图中文字；AI建议裁切区域不会自动应用，需在图片编辑器另行确认。</p>`;
    if(r.preview_url)html+=`<div class="smart-layout-preview"><img src="${r.preview_url}" alt="AI建议的本笔动态双栏排版预览"></div>`;
    if(r.crops?.length)html+=`<details><summary>查看 ${r.crops.length} 项可选裁切建议（未应用）</summary><pre>${escapeHTML(JSON.stringify(r.crops,null,2))}</pre></details>`;
  }
  $('#smartPlanBody').innerHTML=html;openDialog('smartPlanDialog');
}
async function applySmartPlan(){
  if(!currentSmartPlan)return;
  const data={plan_id:currentSmartPlan.id,confirmed:true};
  if(currentSmartPlan.kind==='orders')data.selected_groups=$$('[data-smart-group]:checked').map(e=>e.dataset.smartGroup);
  await busy($('#applySmartPlan'),async()=>{
    const applied=await api('/api/smart-plan/apply',data);closeDialog('smartPlanDialog');await refresh();previewUrl=null;
    if(applied.order_ids?.length){currentId=applied.order_ids[0];render();toast(`已整理为 ${applied.order_ids.length} 笔采购；请逐笔核对。`,'success');}
    else if(applied.material_id){openEditor(applied.material_id);toast('已生成去重长图，原始截图仍在。','success');}
    else{fillLayout();await loadPreview();toast('AI建议已应用到本笔预览，留白约束保留。','success');}
  });
}
$('#aiBatchButton').addEventListener('click',protect(startSmartOrders));
$('#localSmartStitch').addEventListener('click',protect(()=>startSmartStitch(false)));
$('#aiSmartStitch').addEventListener('click',protect(()=>startSmartStitch(true)));
$('#aiLayoutButton').addEventListener('click',protect(startSmartLayout));
$('#applySmartPlan').addEventListener('click',protect(applySmartPlan));
$('[data-smart-close]').addEventListener('click',()=>closeDialog('smartPlanDialog'));
$('#columnRatio').addEventListener('input',()=>{$('#ratioValue').textContent=$('#columnRatio').value+'%';$('#layoutMode').value='custom';});
window.addEventListener('smartjob',protect(async event=>{if(event.detail.result?.plan_id)await showSmartPlan(event.detail.result.plan_id);}));
