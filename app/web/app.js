/* Local-first UI: no external assets, analytics, OCR or API calls from the browser. */
'use strict';
const $ = (selector, root=document) => root.querySelector(selector);
const $$ = (selector, root=document) => [...root.querySelectorAll(selector)];
const token = $('meta[name="app-token"]').content;
const labels = {invoice:'电子发票',order:'订单截图',payment:'支付凭证',unknown:'未分类',other:'其他',receipt:'收据',long_screenshot:'长图碎片'};
const icon = (name, cls='') => `<svg aria-hidden="true" class="${cls}"><use href="#i-${name}"/></svg>`;
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtSize = bytes => bytes >= 1048576 ? (bytes/1048576).toFixed(1)+' MB' : Math.max(1,Math.round(bytes/1024))+' KB';
let state = null, currentId = null, view = 'workspace', filter = 'all', editorId = null;
let crop = null, cropActive = false, cropStart = null, stitchIds = [], toolIds = [], pollTimer = null, seenJobs = new Set();
let previewUrl = null, confirmResolver = null, uploadBusy = false;

async function api(url, data, options={}) {
  const response = await fetch(url, data===undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json','X-App-Token':token},body:JSON.stringify(data),...options});
  let result;
  try { result = await response.json(); } catch { throw new Error('本地服务暂时不可用，请重新启动软件。'); }
  if(!response.ok || result.error) throw new Error(result.error || '操作未完成');
  return result;
}
function toast(message, type='info', duration=5500) {
  const el=document.createElement('div');el.className='toast '+type;el.textContent=message;
  $('#toastStack').append(el);setTimeout(()=>el.remove(),duration);
}
function protect(fn) {return async (...args)=>{try{return await fn(...args);}catch(e){toast(e.message || '操作未完成','error',8000);console.warn('Local operation failed');}};}
function order() {return state?.orders.find(o=>o.id===currentId);}
function materials() {return (order()?.material_ids || []).map(id=>state.materials[id]).filter(Boolean);}
function activeMaterials() {return materials().filter(m=>m.selected);}
function openDialog(id) {const d=$('#'+id);if(!d.open)d.showModal();}
function closeDialog(id) {$('#'+id).close();}
function confirmAction(title, html, okText='确认') {
  if(confirmResolver)confirmResolver(false);
  $('#confirmTitle').textContent=title;$('#confirmBody').innerHTML=html;$('#confirmOk').textContent=okText;
  openDialog('confirmDialog');
  return new Promise(resolve=>{confirmResolver=resolve;});
}
function resolveConfirm(yes) {closeDialog('confirmDialog');if(confirmResolver){confirmResolver(yes);confirmResolver=null;}}
function busy(button, work, text='处理中…') {
  if(button.disabled)return;
  const old=button.innerHTML;button.disabled=true;button.textContent=text;
  return Promise.resolve().then(work).finally(()=>{button.disabled=false;button.innerHTML=old;});
}
async function refresh() {
  state=await api('/api/state');
  if(!state.orders.some(o=>o.id===currentId)) currentId=state.orders[0]?.id;
  render();updateJobs();return state;
}
function render() {
  renderSidebar();renderCurrentOrder();renderMaterials();renderAudit();renderOutputNames();renderArchive();renderTools();
  const any=materials().length>0, recognized=materials().some(m=>m.extracted?.confidence || m.extracted?.amount);
  $('#step2').classList.toggle('active',any);$('#step3').classList.toggle('active',recognized);$('#step4').classList.toggle('done',!!order()?.last_export);
  $('#profileName').textContent=state.settings.name || '完善个人资料';
  $('#profileSub').textContent=state.settings.name ? (state.settings.project_name || '点击编辑默认项目') : '开始前，只需设置一次';
  $('#avatar').textContent=(state.settings.name || '我').slice(-1);
  $('#archiveCount').textContent=state.exports.length;
}
function renderSidebar() {
  $('#orderList').innerHTML=state.orders.map((o,i)=>`<button class="order-row ${o.id===currentId?'active':''}" data-order="${o.id}" title="${escapeHTML(o.title)}"><span class="order-symbol">${String(i+1).padStart(2,'0')}</span><span><strong>${escapeHTML(o.title)}</strong><small>${escapeHTML(o.declare_date)} · ${o.material_ids.length} 份材料</small></span>${o.last_export?'<i class="order-dot"></i>':''}</button>`).join('');
}
function renderCurrentOrder() {
  const o=order();if(!o)return;
  $('#currentOrderName').textContent=o.title;
  $('#orderNumber').textContent=String(state.orders.indexOf(o)+1).padStart(2,'0');
  $('#orderMeta').textContent=`${o.declare_date || '未填日期'} · 当日第${o.order_index}笔 · ${o.project_name || state.settings.project_name || '填写项目名称'}`;
  $('#materialCount').textContent=materials().length+' 份材料';
  for(const [role,id] of [['invoice','invoiceCount'],['order','orderCount'],['payment','paymentCount']]) $('#'+id).textContent=materials().filter(m=>m.category===role).length;
  $('#allCount').textContent=materials().length;
}
function renderMaterials() {
  const rows=materials().filter(m=>filter==='all'||m.category===filter);
  $('#emptyMaterials').classList.toggle('hidden',materials().length>0);
  $('#batchTools').classList.toggle('hidden',materials().length<2);
  $('#materialsGrid').innerHTML=rows.map(m=>`<article class="material-card ${m.selected?'':'excluded'}" data-material="${m.id}"><div class="material-thumb" data-edit="${m.id}" role="button" tabindex="0" aria-label="编辑${escapeHTML(m.name)}"><img src="${m.thumb_url}" alt="${escapeHTML(m.name)}" loading="lazy"><label class="card-check" title="参与本笔导出"><input type="checkbox" data-include="${m.id}" ${m.selected?'checked':''} aria-label="参与本笔导出"></label><span class="material-kind ${m.category}">${labels[m.category]||'未分类'}${m.is_pdf?' · PDF':''}</span></div><div class="material-info"><strong title="${escapeHTML(m.name)}">${escapeHTML(m.name)}</strong><div class="material-sub"><span>${m.width?m.width+' × '+m.height+' · ':''}${fmtSize(m.file_size)}</span><span class="amount">${amountText(m.extracted?.amount)}</span></div></div><div class="material-actions"><select data-category="${m.id}" aria-label="材料类型">${Object.entries(labels).filter(([k])=>k!=='long_screenshot').map(([k,v])=>`<option value="${k}" ${k===m.category?'selected':''}>${v}</option>`).join('')}</select><button class="icon-button" data-edit="${m.id}" title="编辑与移动" aria-label="编辑与移动">${icon('edit')}</button><button class="icon-button" data-remove="${m.id}" title="移除材料" aria-label="移除材料">${icon('trash')}</button></div></article>`).join('');
  if(materials().length && !rows.length) $('#materialsGrid').innerHTML='<div class="empty-page wide"><p>当前分类还没有材料，请手动指定或使用 AI 识别。</p></div>';
}
function amountText(value) {
  if(value===null||value===undefined||value==='')return '待识别';
  const num=Number(value);return Number.isFinite(num)?'¥ '+num.toFixed(2):escapeHTML(value);
}
function auditLine(title, sub, level='pending') {
  return `<div class="audit-line ${level}"><span>${icon(level==='pass'?'check':level==='fail'?'close':'info')}</span><div><strong>${title}</strong><small>${sub}</small></div></div>`;
}
function renderAudit() {
  const o=order(),rows=activeMaterials(),issues=o?.issues||[];
  const errors=issues.filter(x=>x.level==='error'),warnings=issues.filter(x=>x.level==='warning');
  const amounts=['invoice','order','payment'].map(k=>{const m=rows.find(m=>m.category===k&&m.extracted?.amount!==null&&m.extracted?.amount!==undefined&&m.extracted?.amount!=='');return m?.extracted?.amount;});
  const inv=rows.find(m=>m.category==='invoice'),ord=rows.find(m=>m.category==='order');
  const allMaterials=['invoice','order','payment'].every(k=>rows.some(m=>m.category===k));
  const same=amounts.every(x=>x!==undefined)&&new Set(amounts.map(x=>Number(x).toFixed(2))).size===1;
  const hasError=(pattern)=>issues.some(x=>x.level==='error'&&pattern.test(x.code+' '+x.message));
  const invoiceKnown=inv?.extracted?.buyer_name && inv?.extracted?.taxpayer_id;
  const invoiceError=hasError(/buyer|tax|抬头|税号|识别号/);
  const amountError=hasError(/amount|金额/);
  const addressError=hasError(/address|地址/);
  const addressKnown=ord?.extracted?.address;
  const status=$('#auditStatus');
  status.className='audit-status '+(errors.length?'error':warnings.length?'warning':rows.length?'ready':'');
  status.textContent=!rows.length?'待导入':errors.length?errors.length+' 项需补正':warnings.length?'需人工确认':'预检无错误';
  let checks=auditLine('材料完整','电子发票 + 订单详情 + 支付记录',allMaterials?'pass':'pending');
  checks+=auditLine('发票抬头与税号',(state.invoice_info&&(state.invoice_info.taxpayer_name||state.invoice_info.taxpayer_id))?`${state.invoice_info.taxpayer_name||'—'} · ${state.invoice_info.taxpayer_id||'—'}`:'请在设置中填写购买方信息',invoiceError?'fail':invoiceKnown?'pass':'pending');
  checks+=auditLine('三方金额严格一致','精确到分，一分钱的差异也需补正',amountError?'fail':same?'pass':'pending');
  checks+=auditLine('统一收货地址',state.required_address||'请在设置中填写统一收货地址',addressError?'fail':addressKnown?'pass':'pending');
  $('#auditContent').innerHTML=`<div class="audit-checks">${checks}</div><div class="amount-card"><div class="amount-title">${icon('file')}金额交叉核对</div>${['发票金额','订单实付','支付记录'].map((v,i)=>`<div class="amount-row"><span>${v}</span><strong>${amountText(amounts[i])}</strong></div>`).join('')}<div class="amount-note">${same&&!amountError?'三方金额已一致，仍请对照原图复核':'发票金额 = 订单实付 = 支付记录'}</div></div>${rows.length?`<div class="issue-list">${issues.slice(0,5).map(i=>`<div class="issue ${i.level}">${icon(i.level==='error'?'warn':'info')}<span>${escapeHTML(i.message)}</span></div>`).join('')}${issues.length>5?`<div class="tiny muted">另有 ${issues.length-5} 项提醒，请在材料编辑器核对完整信息。</div>`:''}</div>`:''}`;
}
function renderOutputNames() {
  const names=order()?.output_names;
  if(!names||!state.settings.name||!state.settings.advisor){$('#outputName').textContent='请先在设置中填写姓名、指导人和项目名称。';return;}
  $('#outputName').innerHTML=`<strong>${icon('folder')} ${escapeHTML(names.folder_name)}</strong><span>① …-电子发票.pdf<br>② …-购买及支付凭证.pdf</span>`;
}
function renderArchive() {
  $('#archiveList').innerHTML=state.exports.length?state.exports.slice().reverse().map(r=>`<article class="archive-card"><span class="icon-tile">${icon('folder')}</span><div class="archive-content"><h3>${escapeHTML(r.folder.split(/[\\/]/).pop())}</h3><p>${escapeHTML(r.created.replace('T',' '))} · 普通文件夹 · 两份 PDF<br>${escapeHTML(r.folder)}</p><div class="archive-files"><a href="/download/${r.id}/invoice" download>${icon('download')}电子发票 PDF</a><a href="/download/${r.id}/voucher" download>${icon('download')}购买及支付凭证 PDF</a></div></div><button class="button secondary small" data-open-export="${r.id}">${icon('folder')}打开文件夹</button></article>`).join(''):`<div class="empty-page">${icon('folder')}<h3>你的报销材料档案</h3><p>导出完成后，每笔采购会单独列在这里。<br>可直接打开普通文件夹或另存 PDF。</p></div>`;
}
function renderTools() {
  const all=Object.values(state.materials);
  toolIds=toolIds.filter(id=>state.materials[id]);
  $('#toolMaterials').innerHTML=all.length?all.map(m=>selectionRow(m,toolIds,'tool')).join(''):'<div class="empty-page"><p>请先到工作台导入图片或 PDF。</p></div>';
  $('#toolResults').innerHTML=(state.tools||[]).slice().reverse().map(t=>`<div class="tools-result"><a href="/tool-download/${t.id}" download>${icon('download')}<span>${escapeHTML(t.name)} · ${t.pages} 页</span></a></div>`).join('');
}
function selectionRow(m, selected, kind) {
  const index=selected.indexOf(m.id);
  return `<div class="selection-row"><input type="checkbox" data-${kind}-select="${m.id}" ${index>=0?'checked':''} aria-label="选择${escapeHTML(m.name)}"><img src="${m.thumb_url}" alt=""><div class="selection-info"><strong>${escapeHTML(m.name)}</strong><small>${labels[m.category]} · ${m.is_pdf?(m.pages||1)+'页 PDF':m.width+' × '+m.height}</small></div>${index>=0?`<span class="selection-order"><button data-${kind}-up="${m.id}" title="上移">↑</button><span>${index+1}</span><button data-${kind}-down="${m.id}" title="下移">↓</button></span>`:''}</div>`;
}
function changeView(next) {
  view=next;$$('.view').forEach(v=>v.classList.toggle('active',v.id==='view-'+next));
  $$('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.view===next));
  $('#viewTitle').textContent={workspace:'材料工作台',preview:'排版与导出',archive:'导出记录',tools:'PDF 工具箱'}[next];
  if(next==='preview') {fillLayout();renderOutputNames();if(!previewUrl&&activeMaterials().some(x=>x.category==='order')&&activeMaterials().some(x=>x.category==='payment'))protect(loadPreview)();}
  window.scrollTo({top:0,behavior:'instant'});
}
async function selectOrder(id) {
  if(currentId===id)return;
  currentId=id;previewUrl=null;$('#voucherPreview').classList.add('hidden');$('#previewPlaceholder').classList.remove('hidden');$('#manualConfirmed').checked=false;$('#exportResult').innerHTML='';filter='all';
  $$('#filterTabs button').forEach(b=>b.classList.toggle('active',b.dataset.filter==='all'));
  render();if(view==='preview')await protect(loadPreview)();
}
async function newOrder() {
  const result=await api('/api/orders',{});currentId=result.id;previewUrl=null;await refresh();changeView('workspace');openOrder();
}
function openSettings(tab='profile') {
  const s=state.settings;
  $('#settingName').value=s.name;$('#settingAdvisor').value=s.advisor;$('#settingProject').value=s.project_name;
  $('#settingOutput').value=s.output_dir;$('#settingApiBase').value=s.ai_api_base;$('#settingModel').value=s.ai_vision_model||s.ai_model;
  $('#settingKey').value='';$('#settingKey').type='password';$('#clearKey').checked=false;
  $('#settingKey').placeholder=s.has_api_key?'已加密保存；留空不更改':'填写 API Key';
  $('#keyStatus').textContent=s.has_api_key?'已加密保存。为安全起见不回显，留空将保留现有密钥。':'密钥使用当前 Windows 账户加密，永不加入导出文件。';
  const keyLabels={taxpayer_name:'纳税人名称',taxpayer_id:'纳税人识别号',address:'地址',phone:'电话',bank_account:'开户银行账号',bank_name:'开户行名称'};
  $('#invoiceDetails').innerHTML=Object.entries(keyLabels).map(([key,label])=>`<dl class="invoice-line"><dt>${label}</dt><dd>${escapeHTML(state.invoice_info[key])}</dd></dl>`).join('');
  setSettingsTab(tab);openDialog('settingsDialog');
}
function setSettingsTab(tab) {
  $$('[data-settings-tab]').forEach(b=>b.classList.toggle('active',b.dataset.settingsTab===tab));
  $$('[data-settings-page]').forEach(p=>p.classList.toggle('active',p.dataset.settingsPage===tab));
}
async function saveSettings(event) {
  event.preventDefault();
  const data={name:$('#settingName').value,advisor:$('#settingAdvisor').value,project_name:$('#settingProject').value,
    output_dir:$('#settingOutput').value,ai_api_base:$('#settingApiBase').value,ai_vision_model:$('#settingModel').value,
    ai_model:$('#settingModel').value,ai_api_key:$('#settingKey').value,clear_api_key:$('#clearKey').checked};
  await busy($('#settingsForm button[type=submit]'),async()=>{await api('/api/settings',data);await refresh();closeDialog('settingsDialog');toast('设置已保存到本机。','success');});
}
function openOrder() {
  const o=order();$('#orderTitleInput').value=o.title==='未命名采购'?'':o.title;
  $('#orderProjectInput').value=o.project_name || state.settings.project_name;
  const d=o.declare_date||'';$('#orderDateInput').value=d.length===8?d.slice(0,4)+'-'+d.slice(4,6)+'-'+d.slice(6,8):'';
  $('#orderIndexInput').value=o.order_index;openDialog('orderDialog');
}
async function saveOrder(event) {
  event.preventDefault();
  await api('/api/order/update',{id:currentId,title:$('#orderTitleInput').value||'未命名采购',project_name:$('#orderProjectInput').value,
    declare_date:$('#orderDateInput').value.replaceAll('-',''),order_index:Number($('#orderIndexInput').value)});
  await refresh();closeDialog('orderDialog');previewUrl=null;toast('本笔申报信息已更新。','success');
}
async function importFiles(fileList) {
  if(uploadBusy){toast('上一批材料还在导入中，请稍候。');return;}
  const files=[...fileList].filter(f=>/\.(png|jpe?g|webp|bmp|tiff?|pdf)$/i.test(f.name));
  if(!files.length){toast('没有可导入的图片或 PDF。','error');return;}
  if(files.length>80){toast('单次最多导入80个文件，请分批处理。','error');return;}
  uploadBusy=true;$('#uploadProgress').classList.remove('hidden');let success=0,failed=[];
  const targetOrder=currentId;
  try {
    for(let i=0;i<files.length;i++){
      const f=files[i];$('#uploadMessage').textContent=`正在导入 ${i+1}/${files.length} · ${f.name}`;
      $('#uploadBar').style.width=Math.round(i/files.length*100)+'%';
      if(f.size>40*1024*1024){failed.push(f.name+'：超过40 MB');continue;}
      try{
        const response=await fetch(`/api/upload?order_id=${encodeURIComponent(targetOrder)}&name=${encodeURIComponent(f.name)}`,{method:'POST',headers:{'Content-Type':'application/octet-stream','X-App-Token':token},body:f});
        const result=await response.json();if(!response.ok)throw new Error(result.error || '导入失败');success++;
      }catch(e){failed.push(f.name+'：'+e.message);}
    }
    await refresh();previewUrl=null;$('#manualConfirmed').checked=false;
    if(success)toast(`已导入 ${success} 份材料。点击图片可手动分类与裁切。`,'success');
    if(failed.length)toast(failed.join('；'),'error',12000);
  }finally{uploadBusy=false;$('#uploadProgress').classList.add('hidden');$('#fileInput').value='';$('#folderInput').value='';}
}
async function analyzeMaterials() {
  if(!activeMaterials().length){toast('请先导入并选中材料。');return;}
  if(!state.settings.has_api_key){openSettings('ai');toast('请先配置你能看图的模型。');return;}
  const base=state.settings.ai_api_base;
  const yes=await confirmAction('使用你的视觉模型识别',`<p>将把本笔选中的 <strong>${activeMaterials().length} 份材料</strong>发送到：</p><p><strong>${escapeHTML(base)}</strong><br>模型：${escapeHTML(state.settings.ai_vision_model)}</p><p>图片可能包含姓名、地址和交易信息。请确认你信任该服务商；本工具不另行上传云存储。</p><div class="callout">AI 只提取信息并建议分类，不修改票据内容、不代替财务审核。大图会切片识别，不使用 OCR。</div>`,'确认发送并识别');
  if(!yes)return;
  await api('/api/analyze',{order_id:currentId,consent:true});await refresh();
}
function fillLayout() {
  const x={...(state.settings.extra||{}),...(order()?.layout||{})};
  $('#marginLeft').value=x.layout_left_mm??77;$('#marginRight').value=x.layout_right_mm??66;
  $('#marginTop').value=x.layout_top_mm??30;$('#marginBottom').value=x.layout_bottom_mm??26;$('#marginGap').value=x.layout_gap_mm??4;
  $('#invoiceMarginLeft').value=x.invoice_left_mm??54;$('#invoiceMarginRight').value=x.invoice_right_mm??54;
  $('#invoiceMarginTop').value=x.invoice_top_mm??24;$('#invoiceMarginBottom').value=x.invoice_bottom_mm??24;
  $('#invoiceLayoutMode').value=x.invoice_layout_mode||'print';
  $('#pdfDpi').value=x.dpi||state.settings.pdf_dpi||300;$('#layoutOrder').value=x.layout_order_on_left?'order':'payment';
  $('#layoutMode').value=x.layout_column_ratio!==undefined?'custom':(x.layout_adaptive===false?'equal':'adaptive');
  $('#columnRatio').value=Math.round((x.layout_column_ratio??.5)*100);$('#ratioValue').textContent=$('#columnRatio').value+'%';
  $('#verticalAlign').value=x.layout_vertical_align||'top';
}
async function saveLayout(skipVoucher=false) {
  const layout={layout_left_mm:Number($('#marginLeft').value),layout_right_mm:Number($('#marginRight').value),layout_top_mm:Number($('#marginTop').value),
    layout_bottom_mm:Number($('#marginBottom').value),layout_gap_mm:Number($('#marginGap').value),layout_order_on_left:$('#layoutOrder').value==='order',
    invoice_left_mm:Number($('#invoiceMarginLeft').value),invoice_right_mm:Number($('#invoiceMarginRight').value),invoice_top_mm:Number($('#invoiceMarginTop').value),invoice_bottom_mm:Number($('#invoiceMarginBottom').value),invoice_layout_mode:$('#invoiceLayoutMode').value,
    layout_adaptive:$('#layoutMode').value!=='equal',layout_column_ratio:$('#layoutMode').value==='custom'?Number($('#columnRatio').value)/100:null,layout_vertical_align:$('#verticalAlign').value};
  await busy($('#saveLayout'),async()=>{await api('/api/settings',{order_id:currentId,layout,pdf_dpi:Number($('#pdfDpi').value)});await refresh();if(skipVoucher!==true)await loadPreview();},'正在排版…');
}
async function loadPreview() {
  const response=await api('/api/preview',{order_id:currentId});previewUrl=response.url;
  $('#voucherPreview').src=previewUrl;$('#voucherPreview').classList.remove('hidden');$('#previewPlaceholder').classList.add('hidden');
  const metrics=response.layout_metrics||{};$('#layoutMetrics').textContent=metrics.column_ratio?`本笔左/右内容宽度 ${Math.round(metrics.column_ratio*100)}% / ${Math.round((1-metrics.column_ratio)*100)}% · ${metrics.vertical_align==='top'?'顶部对齐':'居中对齐'}。`+(metrics.warnings||[]).join('；'):'';
}
async function exportMaterials() {
  if(!state.settings.name||!state.settings.advisor||!(order()?.project_name||state.settings.project_name)){openSettings();toast('导出前请完善姓名、导师和项目。');return;}
  if(!$('#manualConfirmed').checked){toast('请先对照原图逐项核对，并勾选人工确认。','error');return;}
  const errors=(order()?.issues||[]).filter(x=>x.level==='error');
  if(errors.length){await confirmAction('请先补齐或更正材料',`<p>有 ${errors.length} 项明确问题，尚不能导出：</p><ul>${errors.slice(0,8).map(x=>`<li>${escapeHTML(x.message)}</li>`).join('')}</ul><p>请更换正确材料或真实修正识别结果。软件不会替你修改发票抬头、金额或地址。</p>`,'我知道了');return;}
  const result=await api('/api/export',{order_id:currentId,manual_confirmed:true});await refresh();
}
function updateJobs() {
  const jobs=state.jobs||[],active=jobs.filter(j=>['pending','running'].includes(j.status));
  if(active.length){
    const j=active[0];$('#jobBar').classList.remove('hidden');$('#jobTitle').textContent=j.kind.startsWith('smart-')?'智能规划与本地内容校准':j.kind==='analyze'?'视觉模型正在整理材料':'正在生成规范 PDF';$('#jobMessage').textContent=j.message;$('#jobProgress').style.width=j.progress+'%';
    if(!pollTimer)pollTimer=setInterval(protect(refresh),1300);
  }else{
    $('#jobBar').classList.add('hidden');if(pollTimer){clearInterval(pollTimer);pollTimer=null;}
  }
  const busyOrder=active.some(j=>j.order_id===currentId);
  $('#analyzeButton').disabled=busyOrder;$('#exportButton').disabled=busyOrder;
  for(const j of jobs){
    if(['done','error'].includes(j.status)&&!seenJobs.has(j.id)){
      seenJobs.add(j.id);
      if(j.status==='error'){toast(j.message,'error',11000);continue;}
      if(j.kind==='analyze'){
        const count=j.result?.identified||0, failed=j.result?.failed||[];
        toast(`识别完成：${count} 份成功${failed.length?'，'+failed.length+' 份需检查接口或文件':''}。` ,failed.length?'error':'success',8500);
        if(failed.length)toast(failed.slice(0,3).map(f=>f.name+'：'+f.message).join('；'),'error',12000);
        if(count)$('#step3').classList.add('active');
      }else if(j.kind.startsWith('smart-')){
        const plan=(state.plans||[]).find(p=>p.id===j.result?.plan_id);
        if(!plan?.applied)window.dispatchEvent(new CustomEvent('smartjob',{detail:j}));
      }else if(j.kind==='export'){
        const r=j.result;toast('已生成普通文件夹，内含两份 PDF。','success');
        if(j.order_id===currentId)$('#exportResult').innerHTML=`<div class="success-box"><strong>${icon('check')} 已生成两个 PDF</strong><span>${escapeHTML(r.folder)}</span><div class="success-links"><a href="/download/${r.id}/invoice" download>电子发票</a><a href="/download/${r.id}/voucher" download>购买及支付凭证</a><button class="text-button" data-open-export="${r.id}">打开文件夹</button></div></div>`;
      }
    }
  }
}
function openEditor(id) {
  editorId=id;crop=null;cropActive=false;
  const m=state.materials[id];if(!m)return;
  $('#editorTitle').textContent=m.name;$('#editorImage').src=m.url;$('#editorSize').textContent=m.width?`${m.width} × ${m.height}`:'PDF';
  $('#downloadOriginal').href=`/media/${id}/original`;
  $('#editorCategory').value=m.category in labels?m.category:'unknown';
  $('#editorOrder').innerHTML=state.orders.map(o=>`<option value="${o.id}" ${o.id===m.order_id?'selected':''}>${escapeHTML(o.title)}</option>`).join('');
  const fields={exAmount:'amount',exBuyer:'buyer_name',exTaxId:'taxpayer_id',exProduct:'product_name',exQuantity:'quantity',exTime:'pay_time',exMerchant:'merchant',exOrderNo:'order_no',exAddress:'address'};
  for(const [id,key] of Object.entries(fields))$('#'+id).value=m.extracted?.[key]??'';
  $('#editorNotes').innerHTML=(m.notes||[]).map(n=>`<p>${escapeHTML(n)}</p>`).join('');
  if(m.is_pdf&&m.category==='invoice')$('#editorNotes').innerHTML+='<p>原始电子发票始终保留。导出默认使用带手写留白的打印副本；此处裁切不替换原件。</p>';
  const regions=m.extracted?.extra?.regions||[];
  $('#editorSuggestions').innerHTML=regions.length?'<p class="tiny muted">AI 建议区域（需手动确认）</p>'+regions.map((r,i)=>`<button type="button" class="suggestion-button" data-region="${i}">${icon('crop')} 框选${labels[r.category]||'材料'}区域 · 请核对边界</button>`).join(''):'';
  $('#pdfPageSelect').classList.toggle('hidden',!m.is_pdf);$('#pdfPageInput').value=1;$('#pdfPageInput').max=m.pages||1;$('#pdfPageCount').textContent=`共 ${m.pages||1} 页`;
  $('#undoImage').disabled=!m.can_undo;setCropActive(false);drawCrop();openDialog('editorDialog');
}
function setCropActive(on) {
  cropActive=on;$('#cropOverlay').classList.toggle('hidden',!on);$('#cropToggle').classList.toggle('selected',on);
  $('#cropHelp').textContent=on?'在图片上拖动框选，确保关键信息不被裁掉。':'所有编辑生成本地副本，不改写原始文件。';
}
function drawCrop() {
  const box=$('#cropSelection');
  if(!crop){box.style.display='none';$('#applyCrop').disabled=true;$('#splitCrop').disabled=true;return;}
  box.style.display='block';box.style.left=crop[0]*100+'%';box.style.top=crop[1]*100+'%';box.style.width=crop[2]*100+'%';box.style.height=crop[3]*100+'%';
  const m=state.materials[editorId];$('span',box).textContent=Math.round(crop[2]*m.width)+' × '+Math.round(crop[3]*m.height);
  const valid=crop[2]>=.01&&crop[3]>=.01;$('#applyCrop').disabled=!valid;$('#splitCrop').disabled=!valid;
}
function pointerPoint(event) {
  const r=$('#cropOverlay').getBoundingClientRect();return [Math.max(0,Math.min(1,(event.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(event.clientY-r.top)/r.height))];
}
async function editImage(action, extra={}) {
  const result=await api('/api/material/edit',{id:editorId,action,...extra});
  await refresh();previewUrl=null;$('#manualConfirmed').checked=false;
  openEditor(result.id);return result;
}
async function saveEditor(event) {
  event.preventDefault();
  const extracted={amount:$('#exAmount').value||null,buyer_name:$('#exBuyer').value,taxpayer_id:$('#exTaxId').value,product_name:$('#exProduct').value,
    quantity:$('#exQuantity').value,pay_time:$('#exTime').value,merchant:$('#exMerchant').value,order_no:$('#exOrderNo').value,address:$('#exAddress').value};
  await busy($('#editorForm button[type=submit]'),async()=>{await api('/api/material/update',{id:editorId,category:$('#editorCategory').value,order_id:$('#editorOrder').value,extracted});await refresh();closeDialog('editorDialog');previewUrl=null;$('#manualConfirmed').checked=false;toast('已更新分类与识别信息；票面内容未修改。','success');});
}
async function splitCrop() {
  if(!crop)return;
  const savedCrop=[...crop];
  const yes=await confirmAction('把选区另存为一份材料',`<p>原图将保留并取消参与导出；选区作为一份新材料，请确保内容完整。</p><label class="field">选区类型<select id="splitCategory"><option value="order">订单截图</option><option value="payment">支付凭证</option><option value="invoice">电子发票图片</option><option value="unknown">待分类</option></select></label>`,'确认拆分');
  if(!yes)return;
  const category=$('#splitCategory').value;
  await editImage('split',{box:savedCrop,category});toast('已拆分。还可回到原图，再框选另一半。','success');
}
async function removeMaterial(id) {
  const m=state.materials[id];
  if(!await confirmAction('从工作台移除这份材料？',`<p>${escapeHTML(m.name)}</p><p>这只移除工作台记录，不删除你电脑上的原始文件。已保存的原图副本也会保留在本地数据目录。</p>`,'确认移除'))return;
  await api('/api/material/remove',{id});await refresh();previewUrl=null;
}
function openStitch() {
  stitchIds=[];renderStitch();openDialog('stitchDialog');
}
function renderStitch() {
  const rows=materials().filter(m=>m.category!=='invoice');
  $('#stitchList').innerHTML=rows.length?rows.map(m=>selectionRow(m,stitchIds,'stitch')).join(''):'<p class="muted small-text">请先导入需要拼接的订单或支付截图。</p>';
  $('#stitchCount').textContent=`已选 ${stitchIds.length} 张 · 智能模式会重新判断顺序`;
}
function changeSelection(ids,id,checked) {const index=ids.indexOf(id);if(checked&&index<0)ids.push(id);else if(!checked&&index>=0)ids.splice(index,1);}
function moveSelection(ids,id,dir) {const i=ids.indexOf(id),n=i+dir;if(i>=0&&n>=0&&n<ids.length)[ids[i],ids[n]]=[ids[n],ids[i]];}
async function stitch() {
  if(stitchIds.length<2){toast('请至少选择两张图片，并确认拼接顺序。');return;}
  await busy($('#runStitch'),async()=>{await api('/api/stitch',{ids:stitchIds,direction:$('#stitchDirection').value,category:$('#stitchCategory').value,overlap:Number($('#stitchOverlap').value)});await refresh();closeDialog('stitchDialog');previewUrl=null;toast('拼接副本已生成。请打开检查衔接处，原始材料仍保留。','success');},'正在拼接…');
}
async function runPdfTool() {
  if(!toolIds.length){toast('先选择需要转换的材料。');return;}
  if(!await confirmAction('生成图片版 PDF',`<p>这会把所选内容转为图片页面${$('#toolPages').value?'，按指定页码排列':''}，不会改动原文件。</p><div class="callout warning">不保留文字层、矢量或数字签名，请勿替代原始电子发票。</div>`,'开始本地转换'))return;
  await busy($('#runPdfTool'),async()=>{await api('/api/pdf-tool',{ids:toolIds,pages:$('#toolPages').value});await refresh();toast('图片版 PDF 已生成，可点击下方文件下载。','success');},'正在本机转换…');
}
async function copyInvoice() {
  const x=state.invoice_info;const text=`纳税人名称：${x.taxpayer_name}\n纳税人识别号：${x.taxpayer_id}\n地址：${x.address}\n电话：${x.phone}\n开户银行账号：${x.bank_account}\n开户行名称：${x.bank_name}\n收货地址：${state.required_address}`;
  await navigator.clipboard.writeText(text);toast('已复制开票信息和统一收货地址。','success');
}
function attachEvents() {
  $$('[data-view]').forEach(b=>b.addEventListener('click',()=>changeView(b.dataset.view)));
  $$('[data-close]').forEach(b=>b.addEventListener('click',()=>closeDialog(b.dataset.close)));
  $$('[data-settings-tab]').forEach(b=>b.addEventListener('click',()=>setSettingsTab(b.dataset.settingsTab)));
  $('#confirmOk').addEventListener('click',()=>resolveConfirm(true));$('#confirmCancel').addEventListener('click',()=>resolveConfirm(false));$('#cancelConfirm').addEventListener('click',()=>resolveConfirm(false));
  $('#confirmDialog').addEventListener('cancel',()=>resolveConfirm(false));
  $('#settingsButton').addEventListener('click',()=>openSettings());$('#profileButton').addEventListener('click',()=>openSettings());
  $('#settingsForm').addEventListener('submit',protect(saveSettings));
  $('#toggleKey').addEventListener('click',()=>$('#settingKey').type=$('#settingKey').type==='password'?'text':'password');
  $('#copyInvoiceInfo').addEventListener('click',protect(copyInvoice));
  $('#newOrder').addEventListener('click',protect(newOrder));$('#newOrderSmall').addEventListener('click',protect(newOrder));
  $('#editOrder').addEventListener('click',openOrder);$('#editOrderPreview').addEventListener('click',openOrder);
  $('#orderForm').addEventListener('submit',protect(saveOrder));
  $('#deleteOrder').addEventListener('click',protect(async()=>{if(materials().length)throw new Error('请先移走或移除材料，避免误删采购。');await api('/api/order/delete',{id:currentId});await refresh();closeDialog('orderDialog');}));
  $('#orderList').addEventListener('click',protect(async e=>{const b=e.target.closest('[data-order]');if(b)await selectOrder(b.dataset.order);}));
  $('#pickFiles').addEventListener('click',e=>{e.stopPropagation();$('#fileInput').click();});
  $('#pickFolder').addEventListener('click',e=>{e.stopPropagation();$('#folderInput').click();});
  $('#dropzone').addEventListener('click',e=>{if(!e.target.closest('button'))$('#fileInput').click();});
  $('#dropzone').addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('#fileInput').click();}});
  $('#fileInput').addEventListener('change',protect(e=>importFiles(e.target.files)));$('#folderInput').addEventListener('change',protect(e=>importFiles(e.target.files)));
  const drop=$('#dropzone');
  ['dragenter','dragover'].forEach(event=>drop.addEventListener(event,e=>{e.preventDefault();e.stopPropagation();drop.classList.add('drag-over');}));
  ['dragleave','drop'].forEach(event=>drop.addEventListener(event,e=>{e.preventDefault();e.stopPropagation();drop.classList.remove('drag-over');}));
  drop.addEventListener('drop',protect(e=>importFiles(e.dataTransfer.files)));
  document.addEventListener('dragover',e=>e.preventDefault());document.addEventListener('drop',e=>e.preventDefault());
  $('#filterTabs').addEventListener('click',e=>{const b=e.target.closest('[data-filter]');if(!b)return;filter=b.dataset.filter;$$('#filterTabs button').forEach(b=>b.classList.toggle('active',b.dataset.filter===filter));renderMaterials();});
  $('#materialsGrid').addEventListener('click',protect(async e=>{if(e.target.closest('.card-check'))return;const edit=e.target.closest('[data-edit]');if(edit){openEditor(edit.dataset.edit);return;}const remove=e.target.closest('[data-remove]');if(remove)await removeMaterial(remove.dataset.remove);}));
  $('#materialsGrid').addEventListener('keydown',e=>{if(e.key==='Enter'){const card=e.target.closest('[data-edit]');if(card)openEditor(card.dataset.edit);}});
  $('#materialsGrid').addEventListener('change',protect(async e=>{if(e.target.matches('[data-category]')){await api('/api/material/update',{id:e.target.dataset.category,category:e.target.value});previewUrl=null;await refresh();}if(e.target.matches('[data-include]')){await api('/api/material/update',{id:e.target.dataset.include,selected:e.target.checked});previewUrl=null;await refresh();}}));
  $('#analyzeButton').addEventListener('click',protect(analyzeMaterials));$('#toPreview').addEventListener('click',()=>changeView('preview'));
  $('#groupButton').addEventListener('click',protect(async()=>{const p=await api('/api/group-proposals',{order_id:currentId}); if(!p.groups.length){await confirmAction('暂无可靠分单建议',`<p>${escapeHTML(p.message)}</p>`,'我知道了');return;} const yes=await confirmAction('确认按订单信息分单？',`<p>${escapeHTML(p.message)}</p><ul>${p.groups.map(g=>`<li>${escapeHTML(g.title)} · ${g.ids.length} 份 · ${escapeHTML(g.reason)}</li>`).join('')}</ul><p>未关联材料：${p.unassigned.length} 份，仍留在当前采购。</p>`,'确认创建新采购'); if(!yes)return; const result=await api('/api/apply-groups',{order_id:currentId,confirmed:true}); await refresh(); currentId=result.order_ids[0]||currentId; render(); toast(`已创建 ${result.order_ids.length} 笔独立采购，请逐笔核对。`,'success');}));
  $('#applyRegionsButton').addEventListener('click',protect(async()=>{if(!await confirmAction('应用 AI 裁切建议', '<p>将把每张合并截图的建议区域拆成独立材料；原图保留并取消导出。请确保区域边界正确。</p>','确认拆分'))return; const r=await api('/api/apply-regions',{order_id:currentId,confirmed:true}); await refresh(); toast(`已创建 ${r.created} 份材料，请核对分类。`,'success');}));
  $('#showRules').addEventListener('click',protect(async()=>{await confirmAction('报销材料规范',`<div style="white-space:pre-line;font-size:11px">${escapeHTML(state.rules)}</div>`,'已了解');}));
  $('#refreshPreview').addEventListener('click',protect(()=>busy($('#refreshPreview'),loadPreview,'正在排版…')));
  $('#saveLayout').addEventListener('click',protect(saveLayout));$('#exportButton').addEventListener('click',protect(exportMaterials));
  $('#previewInvoicePrint').addEventListener('click',protect(async()=>{await saveLayout(true);const r=await api('/api/invoice-preview',{order_id:currentId});$('#invoicePrintPreview').src=r.url;$('#invoicePrintPreview').classList.remove('hidden');toast('发票打印留白预览已更新，原件不会被修改。','success');}));
  const zoom=()=>{if(previewUrl){$('#largePreview').src=previewUrl;openDialog('imageDialog');}else toast('请先生成排版预览。');};
  $('#zoomPreview').addEventListener('click',zoom);$('#voucherPreview').addEventListener('click',zoom);
  $('#openOutput').addEventListener('click',protect(()=>api('/api/open-output',{})));
  document.addEventListener('click',protect(async e=>{const b=e.target.closest('[data-open-export]');if(b)await api('/api/open-output',{export_id:b.dataset.openExport});}));
  $('#editorForm').addEventListener('submit',protect(saveEditor));$('#cropToggle').addEventListener('click',()=>setCropActive(!cropActive));
  $('#rotateImage').addEventListener('click',protect(()=>editImage('rotate')));$('#trimImage').addEventListener('click',protect(()=>editImage('trim')));
  $('#undoImage').addEventListener('click',protect(()=>editImage('undo')));$('#resetImage').addEventListener('click',protect(()=>editImage('reset')));
  $('#applyCrop').addEventListener('click',protect(()=>crop&&editImage('crop',{box:crop})));$('#splitCrop').addEventListener('click',protect(splitCrop));
  $('#renderPdfPage').addEventListener('click',protect(()=>editImage('pdf_page',{page:Number($('#pdfPageInput').value)})));
  $('#extractPdfPage').addEventListener('click',protect(()=>editImage('extract_pdf_page',{page:Number($('#pdfPageInput').value)})));
  $('#editorSuggestions').addEventListener('click',e=>{const b=e.target.closest('[data-region]');if(!b)return;const r=state.materials[editorId].extracted.extra.regions[Number(b.dataset.region)];crop=r.box||r.bbox;setCropActive(true);drawCrop();toast('已框选模型建议区域。请调整、核对边界后再确认拆分。');});
  $('#cropOverlay').addEventListener('pointerdown',e=>{if(!cropActive)return;e.preventDefault();cropStart=pointerPoint(e);e.currentTarget.setPointerCapture(e.pointerId);crop=[...cropStart,0,0];drawCrop();});
  $('#cropOverlay').addEventListener('pointermove',e=>{if(!cropStart)return;const p=pointerPoint(e);crop=[Math.min(p[0],cropStart[0]),Math.min(p[1],cropStart[1]),Math.abs(p[0]-cropStart[0]),Math.abs(p[1]-cropStart[1])];drawCrop();});
  $('#cropOverlay').addEventListener('pointerup',()=>{cropStart=null;drawCrop();});$('#cropOverlay').addEventListener('pointercancel',()=>cropStart=null);
  $('#stitchButton').addEventListener('click',openStitch);$('#runStitch').addEventListener('click',protect(stitch));
  for(const kind of ['stitch','tool']){
    const root=$(kind==='stitch'?'#stitchList':'#toolMaterials');
    root.addEventListener('change',e=>{if(e.target.matches(`[data-${kind}-select]`)){const ids=kind==='stitch'?stitchIds:toolIds;changeSelection(ids,e.target.dataset[kind+'Select'],e.target.checked);kind==='stitch'?renderStitch():renderTools();}});
    root.addEventListener('click',e=>{for(const dir of ['up','down']){const b=e.target.closest(`[data-${kind}-${dir}]`);if(b){moveSelection(kind==='stitch'?stitchIds:toolIds,b.dataset[kind+(dir==='up'?'Up':'Down')],dir==='up'?-1:1);kind==='stitch'?renderStitch():renderTools();}}});
  }
  $('#runPdfTool').addEventListener('click',protect(runPdfTool));
  $('#exitApp').addEventListener('click',protect(async()=>{if(await confirmAction('保存并退出 ExpBot', '<p>所有材料与设置已保存在本机，下一次启动会继续保留。</p><p>确认后将停止本地服务，可关闭当前窗口。</p>', '退出应用')){await api('/api/shutdown',{});document.body.innerHTML='<div class="empty-page" style="margin:15vh auto;max-width:550px"><h2>工作空间已保存</h2><p>本地服务已停止，可以关闭此窗口。<br>下次双击启动 ExpBot 即可继续。</p></div>';}}));
}
async function main() {
  attachEvents();await refresh();fillLayout();
  if(!state.settings.name&&!Object.keys(state.materials).length)toast('欢迎使用 ExpBot。先填写个人资料与视觉模型，再导入材料。','info',8000);
  window.appReady=true;
}
protect(main)();
