/* All interface assets and files are local. Model requests require an explicit user action. */
(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const token = $('meta[name="app-token"]')?.content || '';
  const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[ch]));
  const icon = name => `<svg aria-hidden="true"><use href="#w-${name}"/></svg>`;
  const asArray = value => Array.isArray(value) ? value : value && typeof value === 'object' ? Object.values(value).filter(item => item && typeof item === 'object') : [];
  const ids = value => (Array.isArray(value) ? value : []).map(item => typeof item === 'object' ? item?.id || item?.material_id : item).filter(Boolean).map(String);
  const unique = list => [...new Set(list)];
  const textOf = value => typeof value === 'string' ? value : value?.message || value?.text || value?.label || value?.title || '';
  const activeStatuses = new Set(['pending', 'queued', 'running']);
  const roles = {invoice: '电子发票', order: '订单详情', payment: '支付凭证'};
  const stageNames = {collect: '收集', review: '整理', making: '制作', ready: '制作', done: '完成'};
  const defaultModel = 'deepseek-flash';
  const defaultBase = 'https://api.deepseek.com';
  let state = {settings: {}, materials: {}, orders: [], jobs: [], exports: [], flow: {runs: [], active_run_id: null}};
  let initialized = false;
  let fetching = null;
  let pollTimer = null;
  let confirmResolver = null;
  let toastSequence = 0;
  const ui = {
    view: 'home', runId: null, networkError: '', globalError: '',
    localRuns: new Map(), pendingJobs: new Map(), seenJobs: new Set(), trackedJobs: new Map(),
    locks: new Set(), steps: new Map(), force: new Set(), profileDrafts: new Map(),
    dirtyProfiles: new Set(), layoutDrafts: new Map(), batchViews: new Map(),
    previewCache: new Map(), errors: new Map(), exportConfirmed: new Set(),
    upload: null, failedUpload: null, settingsIntent: null, testJobId: null, testResult: null,
    gallery: [], galleryAll: [], galleryIndex: 0, galleryRunId: null, galleryFilter: 'all',
    materialPages: new Map(), pageRequests: new Map(), galleryVersion: 0,
    zoomMode: 'fit', zoom: 1, pan: null, reflowOrders: new Map()
  };

  function normalizeState(raw) {
    const value = raw && typeof raw === 'object' ? raw : {};
    const materialRows = value.materials;
    const materials = Array.isArray(materialRows) ? Object.fromEntries(materialRows.filter(m => m?.id).map(m => [m.id, m])) : materialRows && typeof materialRows === 'object' ? materialRows : {};
    return {...value, settings: value.settings || {}, materials, orders: asArray(value.orders), jobs: asArray(value.jobs), exports: asArray(value.exports), flow: {...(value.flow || {}), runs: asArray(value.flow?.runs)}};
  }

  function localURL(value) {
    if (typeof value !== 'string' || !value.trim()) return '';
    try {
      const url = new URL(value, window.location.href);
      return url.origin === window.location.origin && /^https?:$/.test(url.protocol) ? url.pathname + url.search + url.hash : '';
    } catch { return ''; }
  }

  function errorMessage(error) {
    return error?.message || (typeof error === 'string' ? error : '操作未完成，请重试。');
  }

  async function api(path, data, options = {}) {
    let response;
    try {
      response = await fetch(path, data === undefined ? {cache: 'no-store', credentials: 'same-origin', headers: {'X-App-Token': token}, ...options} : {
        method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-App-Token': token}, body: JSON.stringify(data), ...options
      });
    } catch { throw new Error('无法连接本地服务。请确认应用仍在运行，然后重试。'); }
    let result;
    try { result = await response.json(); } catch { throw new Error('本地服务未返回有效结果，请稍后重试。'); }
    if (!response.ok || result?.error && !result?.id && !result?.run && !result?.job) {
      const message = typeof result?.error === 'string' ? result.error : result?.error?.message || result?.message;
      const error = new Error(message || (response.status === 404 ? '此功能的本地接口尚未就绪，请更新或重新启动应用后重试。' : `操作未完成（${response.status}），请重试。`));
      error.status = response.status;
      throw error;
    }
    return result;
  }

  function allRuns() {
    const map = new Map([...ui.localRuns.values()].map(run => [String(run.id), run]));
    for (const run of state.flow.runs) if (run?.id) map.set(String(run.id), run);
    return [...map.values()];
  }
  function runById(id = ui.runId) { return allRuns().find(run => String(run.id) === String(id)); }
  function rememberRun(result) {
    const run = result?.run || result;
    if (run?.id && (run.stage || Array.isArray(run.material_ids))) ui.localRuns.set(String(run.id), run);
    return run?.id && (run.stage || Array.isArray(run.material_ids)) ? run : null;
  }
  function allJobs() {
    const jobs = new Map(state.jobs.filter(job => job?.id).map(job => [String(job.id), job]));
    for (const [id, entry] of ui.pendingJobs) {
      if (jobs.has(id)) ui.pendingJobs.delete(id);
      else if (Date.now() - entry.added < 30000) jobs.set(id, entry.job);
      else ui.pendingJobs.delete(id);
    }
    return [...jobs.values()];
  }
  function jobForRun(job, run) {
    if (!run) return false;
    return String(job.run_id || '') === String(run.id) || ui.trackedJobs.get(String(job.id))?.runId === String(run.id) || (job.order_id && [run.inbox_order_id, ...ids(run.order_ids)].includes(job.order_id));
  }
  function runJobs(run) { return allJobs().filter(job => jobForRun(job, run)); }
  function runBusy(run) { return run?.status === 'running' || runJobs(run).some(job => activeStatuses.has(job.status)) || ui.locks.has('run:' + run?.id); }
  function hasBusyJobs() { return allJobs().some(job => activeStatuses.has(job.status)) || allRuns().some(run => run.status === 'running'); }
  function trackJob(result, run, kind) {
    const job = result?.job || result;
    if (!job?.id) throw new Error('服务未返回任务编号，请刷新后确认状态再重试。');
    ui.pendingJobs.set(String(job.id), {job: {...job, status: job.status || 'pending', kind: job.kind || kind, run_id: run?.id || job.run_id}, added: Date.now()});
    if (run) ui.trackedJobs.set(String(job.id), {runId: String(run.id), kind});
    schedulePoll();
    return job;
  }
  function schedulePoll() {
    clearTimeout(pollTimer);
    pollTimer = null;
    if (hasBusyJobs()) pollTimer = setTimeout(() => refresh().catch(() => {}), 1000);
  }

  function toast(message, type = 'info') {
    const element = document.createElement('div');
    element.className = 'toast ' + type;
    element.id = 'toast-' + (++toastSequence);
    element.setAttribute('role', type === 'error' ? 'alert' : 'status');
    const content = document.createElement('span');
    content.textContent = message;
    const close = document.createElement('button');
    close.type = 'button'; close.className = 'toast-close'; close.setAttribute('aria-label', '关闭提示'); close.innerHTML = icon('close');
    close.addEventListener('click', () => element.remove());
    element.append(content, close); $('#toastStack').append(element);
    setTimeout(() => element.remove(), type === 'error' ? 14000 : 6500);
  }

  function observeJobs(firstLoad) {
    for (const job of allJobs()) {
      if (!['done', 'error', 'failed', 'cancelled'].includes(job.status)) continue;
      if (ui.testJobId === String(job.id)) {
        ui.testResult = job.status === 'done' ? job.result || {connected: false, message: '连接测试未返回详细结果，请重试。'} : {connected: false, message: job.message || textOf(job.error) || '连接测试失败，请检查配置后重试。'};
      }
      if (ui.seenJobs.has(String(job.id))) continue;
      ui.seenJobs.add(String(job.id));
      if (firstLoad) continue;
      if (job.status === 'error' || job.status === 'failed') {
        const tracked = ui.trackedJobs.get(String(job.id));
        if (tracked) ui.errors.set(tracked.runId, {message: job.message || textOf(job.error) || '任务未完成，请重试。', action: tracked.kind});
        toast(job.message || textOf(job.error) || '任务未完成，请重试。', 'error');
      } else if (job.status === 'done' && ui.trackedJobs.has(String(job.id))) {
        const tracked = ui.trackedJobs.get(String(job.id));
        ui.errors.delete(tracked.runId); ui.steps.delete(tracked.runId);
        const failures = Number(job.result?.failed) || (Array.isArray(job.result?.failures) ? job.result.failures.length : 0);
        if (failures) {
          const message = textOf(runById(tracked.runId)?.error) || `${failures} 笔未完成，其余结果已保留，可以重试失败项。`;
          ui.errors.set(tracked.runId, {message, action: tracked.kind}); toast(message, 'error');
        } else toast(/reflow/.test(tracked.kind) ? '本笔版式、两份 PDF 与预览已同步更新，请重新核对。' : /export/.test(tracked.kind) ? 'PDF 已保存到本机文件夹。' : /make/.test(tracked.kind) ? job.result?.archived ? '无关材料已归档，本次没有生成采购文件。' : '制作完成，请核对每笔预览。' : '整理完成，可以核对分组与检查结果。', 'success');
      }
    }
    renderTestResult();
  }

  async function refresh() {
    if (fetching) return fetching;
    fetching = (async () => {
      try {
        const firstLoad = !initialized;
        state = normalizeState(await api('/api/state'));
        ui.networkError = ''; initialized = true; window.appReady = true;
        if (firstLoad) routeFromHash();
        observeJobs(firstLoad);
        render();
        return state;
      } catch (error) {
        ui.networkError = errorMessage(error);
        render();
        throw error;
      } finally {
        fetching = null;
        schedulePoll();
      }
    })();
    return fetching;
  }

  function routeFromHash() {
    const hash = window.location.hash;
    const match = /^#run\/([^/?#]+)$/.exec(hash);
    if (match) {
      try { ui.runId = decodeURIComponent(match[1]); ui.view = 'run'; } catch { ui.view = 'home'; }
    } else ui.view = hash === '#history' ? 'history' : 'home';
  }
  function navigate(view, runId) {
    ui.view = view;
    if (runId !== undefined) ui.runId = String(runId);
    window.history.replaceState(null, '', view === 'run' ? '#run/' + encodeURIComponent(ui.runId) : view === 'history' ? '#history' : window.location.pathname + window.location.search);
    render();
    window.scrollTo({top: 0, behavior: 'instant'});
    $('#main').focus({preventScroll: true});
  }
  function today() {
    const date = new Date();
    return String(date.getFullYear()) + String(date.getMonth() + 1).padStart(2, '0') + String(date.getDate()).padStart(2, '0');
  }
  function displayDate(value) {
    const text = String(value || '');
    if (/^\d{8}$/.test(text)) return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`;
    return text.replace('T', ' ').replace(/\.\d+(?:Z)?$/, '').slice(0, 16) || '日期未记录';
  }
  function sizeText(bytes) {
    const size = Number(bytes);
    if (!Number.isFinite(size) || size <= 0) return '';
    return size >= 1048576 ? (size / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(size / 1024)) + ' KB';
  }
  function missingSettings(settings = state.settings) {
    const missing = [];
    for (const [key, label] of [
      ['name', '姓名'], ['advisor', '导师'], ['project_name', '项目'], ['output_dir', '输出文件夹'],
      ['buyer_name', '发票抬头'], ['taxpayer_id', '税号'], ['required_address', '统一收货地址'],
      ['ai_api_base', 'API 地址'],
    ]) if (!String(settings[key] || '').trim()) missing.push(label);
    if (!String(settings.ai_vision_model || settings.ai_model || '').trim()) missing.push('图像模型');
    if (!settings.has_api_key) missing.push('API Key');
    return missing;
  }
  function profileFor(run) {
    return {name: state.settings.name || '', advisor: state.settings.advisor || '', project_name: state.settings.project_name || '', declare_date: today(), ...(run.profile || {}), ...(ui.profileDrafts.get(String(run.id)) || {})};
  }
  function materialIds(run) {
    if (!run) return [];
    if (Array.isArray(run.material_ids)) return unique(ids(run.material_ids));
    const inbox = state.orders.find(order => order.id === run.inbox_order_id);
    return unique([...ids(inbox?.material_ids), ...asArray(run.review?.groups).flatMap(group => ids(group.material_ids)), ...ids(run.review?.unassigned), ...ids(run.review?.trash)]);
  }
  function materialsFor(run) { return materialIds(run).map(id => state.materials[id]).filter(Boolean); }
  function stageFor(run) {
    if (run.stage === 'done') return 'done';
    if (runBusy(run)) {
      const job = runJobs(run).filter(job => activeStatuses.has(job.status)).at(-1);
      if (/reflow/.test(job?.kind || '')) return 'ready';
      if (/review/.test(job?.kind || '')) return 'review';
      if (/make|export/.test(job?.kind || '')) return 'making';
      return run.stage === 'collect' ? 'review' : (run.stage || 'review');
    }
    return ui.steps.get(String(run.id)) || (stageNames[run.stage] ? run.stage : 'collect');
  }
  function runNameParts(run) {
    const profile = run.profile || {};
    const date = String(profile.declare_date || run.created?.slice(0, 10) || '').replaceAll('-', '');
    return [date, profile.name || '未填写姓名', profile.advisor || '未填写导师', profile.project_name || run.title || '报销材料'];
  }
  function runTitle(run) { return runNameParts(run).join('-'); }
  function runTitleHTML(run) {
    const [date, ...fields] = runNameParts(run);
    return `<time class="run-date" datetime="${escapeHTML(displayDate(date))}">${escapeHTML(date)}</time><span>-${escapeHTML(fields.join('-'))}</span>`;
  }
  function badge(text, kind = '') { return `<span class="badge ${kind}">${escapeHTML(text)}</span>`; }
  function notice(message, type = 'info', action = '') {
    return `<div class="notice ${type}" ${type === 'error' ? 'role="alert"' : ''}>${icon(type === 'error' || type === 'warning' ? 'warning' : type === 'success' ? 'check' : 'info')}<div>${message}</div>${action}</div>`;
  }
  function networkBanner() {
    return ui.networkError ? notice(escapeHTML(ui.networkError), 'error', '<button class="button secondary small" data-action="refresh">重新连接</button>') : '';
  }
  function captureUI() {
    const main = $('#main');
    const active = document.activeElement;
    const focus = main.contains(active) && active !== main ? {id: active.id, key: active.dataset.focusKey, start: active.selectionStart, end: active.selectionEnd} : null;
    const open = new Map($$('details[id]', main).map(item => [item.id, item.open]));
    return {focus, open};
  }
  function render() {
    const saved = captureUI();
    const identity = $('#batchIdentity');
    const activeBatch = ui.view === 'run' ? runById() : null;
    if (identity) { identity.hidden = !activeBatch; identity.innerHTML = activeBatch ? runTitleHTML(activeBatch) : ''; }
    $('#main').innerHTML = networkBanner() + (ui.globalError ? notice(escapeHTML(ui.globalError), 'error', '<button class="icon-button" aria-label="关闭错误" data-action="clear-error">' + icon('close') + '</button>') : '') + (ui.view === 'history' ? renderHistory() : ui.view === 'run' ? renderRun() : renderHome());
    for (const [id, open] of saved.open) { const detail = document.getElementById(id); if (detail?.tagName === 'DETAILS') detail.open = open; }
    const active = saved.focus?.id ? document.getElementById(saved.focus.id) : saved.focus?.key ? $$('[data-focus-key]').find(element => element.dataset.focusKey === saved.focus.key) : null;
    if (active && !active.disabled) {
      active.focus({preventScroll: true});
      if (typeof saved.focus.start === 'number') { try { active.setSelectionRange(saved.focus.start, saved.focus.end); } catch { /* Date/range inputs have no text selection. */ } }
    }
    renderTestResult();
  }

  function renderHome() {
    const draft = allRuns().filter(run => run.stage !== 'done' && (materialIds(run).length || run.status === 'running')).sort((a, b) => String(b.updated || b.created).localeCompare(String(a.updated || a.created)))[0];
    return `<section class="home-page"><div class="home-art" aria-hidden="true"><span class="art-orbit"></span><span class="art-paper paper-one">${icon('image')}<i></i><i></i></span><span class="art-paper paper-two">${icon('file')}<i></i><i></i></span><span class="art-done">${icon('check')}</span><span class="art-dot dot-one"></span><span class="art-dot dot-two"></span></div>
      <div class="home-copy"><h1>采购报销</h1><p>一次导入多笔材料，分组核对后导出。<br>每笔保留发票与购买及支付凭证两份 PDF。</p></div>
      <div class="home-actions"><button class="button primary start-button" data-action="start" ${!initialized || ui.locks.has('start') ? 'disabled' : ''}><span>${ui.locks.has('start') ? '正在开始…' : '开始'}</span>${ui.locks.has('start') ? '<span class="spinner"></span>' : icon('arrow')}</button><button class="button history-button" data-action="history">${icon('history')}历史</button></div>
      ${draft ? `<button class="resume-link" data-action="open-run" data-run-id="${escapeHTML(draft.id)}"><span class="resume-dot ${draft.status === 'running' ? 'pulse' : ''}"></span>${draft.status === 'running' ? '正在处理上次材料' : '继续上次'}<span class="resume-title">${escapeHTML(runTitle(draft))}</span>${icon('arrow')}</button>` : '<p class="home-footnote">支持图片与 PDF，原始材料始终保留。</p>'}
      ${!initialized && !ui.networkError ? '<p class="tiny muted" role="status">正在连接本地工作空间…</p>' : ''}</section>`;
  }

  function stepper(run, stage) {
    const current = stage === 'collect' ? 0 : stage === 'review' ? 1 : stage === 'done' ? 3 : 2;
    return `<div class="flow-top"><button class="text-button back-link" data-action="home">${icon('back')}主页</button></div><ol class="stepper" aria-label="报销步骤">${['收集', '整理', '制作', '完成'].map((name, index) => `<li class="${index === current ? 'current' : index < current ? 'complete' : ''}" ${index === current ? 'aria-current="step"' : ''}><span class="step-number">${index < current ? icon('check') : index + 1}</span><span>${name}</span></li>`).join('')}</ol>`;
  }
  function heading(title, copy, side = '') { return `<div class="page-heading"><div><h1>${title}</h1><p>${copy}</p></div>${side}</div>`; }
  function renderRun() {
    const run = runById();
    if (!run) return `<section class="flow-page">${heading('暂时没有找到这次材料', '可以返回历史，继续保存在本机的草稿。')}<div class="empty-state">${icon('folder')}<p>${initialized ? '记录可能尚未同步，点击刷新再试一次。' : '正在读取记录…'}</p><div class="button-row"><button class="button secondary" data-action="refresh">刷新记录</button><button class="button primary" data-action="history">查看历史</button></div></div></section>`;
    const stage = stageFor(run);
    return `<section class="flow-page">${stepper(run, stage)}${runErrorHTML(run)}${stage === 'collect' ? renderCollect(run) : stage === 'review' ? renderReview(run) : stage === 'done' ? renderDone(run) : renderMaking(run, stage)}</section>`;
  }

  function runErrorHTML(run) {
    const trackedError = ui.errors.get(String(run.id));
    const latestJob = runJobs(run).at(-1);
    const message = trackedError?.message || textOf(run.error) || (run.status === 'error' ? latestJob?.message || '上次处理没有完成。已保留材料和会话，可以重试。' : '');
    if (!message) return '';
    const action = trackedError?.action || latestJob?.kind || (run.stage === 'collect' || run.stage === 'review' ? 'review' : 'make');
    const retry = /reflow|layout/.test(action) && ui.reflowOrders.has(String(run.id)) ? 'reflow' : /export/.test(action) ? 'export' : /make/.test(action) ? 'make' : /review/.test(action) ? 'review' : action === 'profile' ? 'save-profile' : action === 'upload' ? 'pick' : 'refresh';
    return notice(`<strong>这一步未完成</strong><p>${escapeHTML(message)}</p><span class="tiny">材料与已保存的处理上下文仍在本机。</span>`, 'error', `<button class="button secondary small" data-action="${retry}" data-order-id="${escapeHTML(ui.reflowOrders.get(String(run.id)) || '')}" ${runBusy(run) ? 'disabled' : ''}>${icon('refresh')}重试这一步</button>`);
  }

  function profileHTML(run) {
    const profile = profileFor(run);
    const busy = runBusy(run);
    return `<form id="profileForm" class="profile-card"><div class="section-title"><h2>这次的申报信息</h2><span id="profileSaveState" class="tiny muted">${ui.dirtyProfiles.has(String(run.id)) ? '下一步会保存' : '保存在本机，可修改'}</span></div><div class="profile-grid">
      <label class="field" for="runName">姓名<input id="runName" data-profile-field="name" value="${escapeHTML(profile.name)}" maxlength="30" autocomplete="name" required ${busy ? 'disabled' : ''}></label>
      <label class="field" for="runAdvisor">导师 / 指导人<input id="runAdvisor" data-profile-field="advisor" value="${escapeHTML(profile.advisor)}" maxlength="30" required ${busy ? 'disabled' : ''}></label>
      <label class="field profile-project" for="runProject">项目名称<input id="runProject" data-profile-field="project_name" value="${escapeHTML(profile.project_name)}" maxlength="100" required ${busy ? 'disabled' : ''}></label>
      <label class="field" for="runDate">申报日期<input id="runDate" data-profile-field="declare_date" type="date" value="${escapeHTML(displayDate(profile.declare_date))}" required ${busy ? 'disabled' : ''}></label>
      </div><div class="profile-footer"><span class="tiny muted">只影响本次报销，不会改动票据内容。</span><button class="text-button" type="submit" ${busy ? 'disabled' : ''}>保存信息${icon('check')}</button></div></form>`;
  }

  function dropzoneHTML(run, supplemental = false) {
    const disabled = !!ui.upload || runBusy(run);
    return `<button class="dropzone ${supplemental ? 'compact-dropzone' : ''}" type="button" data-action="pick" data-dropzone="true" aria-label="${supplemental ? '补交图片或PDF' : '导入图片或PDF'}" ${disabled ? 'disabled' : ''}><span class="upload-symbol">${icon('upload')}</span><span class="drop-copy"><strong>${supplemental ? '补交图片或 PDF' : '把图片或 PDF 放到这里'}</strong><span>也可以<span class="inline-link">选择文件</span>，支持多份一起导入</span><small>PNG / JPG / WebP / BMP / TIFF / PDF · 单份不超过 40 MB</small></span>${!supplemental ? '<span class="drop-plus" aria-hidden="true">' + icon('plus') + '</span>' : ''}</button>`;
  }
  function materialCard(material, removable = false) {
    const url = localURL(material.thumb_url || material.url);
    return `<article class="material-card"><button class="material-image" data-action="material-preview" data-material-id="${escapeHTML(material.id)}" aria-label="查看${escapeHTML(material.name || '材料')}原图">${url ? `<img src="${escapeHTML(url)}" alt="${escapeHTML(material.name || '材料')}" loading="lazy" decoding="async">` : '<span class="image-placeholder">' + icon('image') + '<span>等待预览</span></span>'}<span class="image-hover">${icon('zoom')}查看原图</span></button><div class="material-caption"><div><strong title="${escapeHTML(material.name)}">${escapeHTML(material.name || '未命名材料')}</strong><span>${escapeHTML([material.is_pdf ? `PDF${material.pages ? ' · ' + material.pages + ' 页' : ''}` : material.width && material.height ? `${material.width} × ${material.height}` : '图片', sizeText(material.file_size)].filter(Boolean).join(' · '))}</span></div>${removable ? `<button class="icon-button remove-material" data-action="remove" data-material-id="${escapeHTML(material.id)}" aria-label="移除${escapeHTML(material.name || '材料')}" title="移除材料">${icon('close')}</button>` : ''}</div></article>`;
  }
  function uploadHTML(run) {
    const progress = ui.upload?.runId === String(run.id) ? ui.upload : null;
    const failure = ui.failedUpload?.runId === String(run.id) ? ui.failedUpload : null;
    return `${progress ? `<div class="upload-status" role="status"><div><span class="spinner"></span><span>正在导入 ${progress.index}/${progress.total} · ${escapeHTML(progress.name)}</span></div><progress value="${progress.index - 1}" max="${progress.total}"></progress></div>` : ''}${failure ? notice(`<strong>${failure.files.length} 份材料未导入</strong><ul class="compact-list">${failure.messages.map(message => `<li>${escapeHTML(message)}</li>`).join('')}</ul>`, 'error', '<button class="button secondary small" data-action="retry-upload">重试未导入材料</button>') : ''}`;
  }
  function renderCollect(run) {
    const materials = materialsFor(run);
    const busy = runBusy(run) || !!ui.upload;
    const base = state.settings.ai_api_base || defaultBase;
    const model = state.settings.ai_vision_model || state.settings.ai_model || defaultModel;
    return `${heading('先把材料放进来', '不用分类。准备好发票、订单详情和支付截图，剩下的交给下一步。', badge(materials.length + ' 份材料', 'neutral'))}${profileHTML(run)}${dropzoneHTML(run)}${uploadHTML(run)}
      ${materials.length ? `<div class="section-title materials-heading"><h2>已收集 <span class="count">${materials.length}</span></h2><span class="tiny muted">点击放大查看原图</span></div><div class="materials-grid">${materials.map(material => materialCard(material, !busy)).join('')}</div>` : '<div class="collect-empty"><span class="empty-line"></span><p>材料可以乱序、分批添加，原始文件始终保留。</p><span class="empty-line"></span></div>'}
      <div class="flow-bottom"><div class="send-disclosure">${icon('info')}<p>点击下一步，将把本次材料发送至 <strong>${escapeHTML(base)}</strong><br><span>模型 ${escapeHTML(model)}，用于图像识别与整理。请确认你信任该服务商。</span></p></div><button class="button primary large" data-action="review" ${!materials.length || busy ? 'disabled' : ''}>下一步 · 发往 API 整理${icon('arrow')}</button></div>`;
  }

  function checkLevel(check) {
    if (check === true) return 'pass';
    if (check === false) return 'error';
    if (check && typeof check === 'object') {
      if (check.ok === true || check.passed === true || check.valid === true) return 'pass';
      if (check.ok === false || check.passed === false || check.valid === false) return 'error';
      return checkLevel(check.status ?? check.level ?? check.result);
    }
    const value = String(check ?? '').toLowerCase();
    if (/^(pass|passed|ok|success|valid|true|一致|通过|已通过|完整)$/.test(value)) return 'pass';
    if (/error|fail|invalid|missing|mismatch|不一致|缺失|缺少|错误|未通过|不通过/.test(value)) return 'error';
    if (/warning|warn|review|pending|uncertain|待核对|待确认|提醒|不确定/.test(value)) return 'warning';
    return 'neutral';
  }
  function validRegion(region) {
    const box = region?.box || region?.bbox;
    return Array.isArray(box) && box.length === 4 && box.every(Number.isFinite) && box[0] >= 0 && box[1] >= 0 && box[2] > 0 && box[3] > 0 && box[0] + box[2] <= 1.001 && box[1] + box[3] <= 1.001;
  }
  function materialHasRole(material, role) {
    return (material.category || material.role) === role || asArray(material.extracted?.extra?.regions).some(region => region.category === role && validRegion(region));
  }
  function allTrash(run) {
    const materials = materialIds(run), trash = new Set(ids(run.review?.trash));
    return materials.length > 0 && !asArray(run.review?.groups).length && !ids(run.review?.unassigned).length && materials.every(id => trash.has(id));
  }
  const checkLabels = {address: '收货地址',address_match: '收货地址',buyer: '发票抬头',buyer_name: '发票抬头',tax_id: '税号',taxpayer_id: '税号',invoice: '电子发票',order: '订单详情',payment: '支付凭证',complete: '材料完整',completeness: '材料完整',missing_invoice: '发票完整',missing_order: '订单完整',missing_payment: '支付凭证完整',readability: '清晰度',clarity: '清晰度'};
  function issueCategory(issue) {
    const code = String(issue?.code || issue?.key || issue?.category || issue?.field || '').toLowerCase().replace(/[\s-]+/g, '_');
    const message = textOf(issue) || issue?.detail || '';
    // Missing model conclusions, matching heuristics and metadata are not defects
    // in the user's four review categories.
    if (['info', 'neutral'].includes(String(issue?.level || issue?.severity || '').toLowerCase()) || /模型.*(?:尚未提供|未提供|核验结论|理由缺失)|核验结论或理由缺失/.test(message) || /^(?:unknown_check|unassigned|no_groups|production_unclassified|stitch_|metadata|invoice_from_image)/.test(code)) return '';
    const defect = /不符|不一致|错误|有误|不正确|未识别|未知|无法|缺失|缺少|未提供|空白|不完整|模糊|看不清|不清晰|不可读|难以辨认|不清楚|需补交|待补交|mismatch|invalid|incorrect|missing|unknown|unreadable|illegible|blurry/i.test(message);
    if (/(?:^|_)(?:amounts?|price|total|order_no|order_number|order_id)(?:_|$)/.test(code) || /(?:金额|订单号|订单编号|单号).*(?:不一致|不符|差异|不同|冲突)|(?:amount|order\s*(?:number|no\.?)).*(?:mismatch|differ|inconsistent)/i.test(message)) return '';
    if (/(?:^|_)(?:personal(?:_info)?|profile(?:_info)?|buyer(?:_name|_fields)?|tax(?:payer)?_id|taxpayer|name|advisor|project_name|declare_date|phone|contact)(?:_|$)/.test(code) || defect && /个人信息|申报信息|姓名|导师|发票抬头|购买方|购方|纳税人|税号/.test(message)) return 'personal';
    if (/(?:^|_)(?:shipping_address|delivery_address|recipient_address|address)(?:_|$)/.test(code) || defect && /收货地址|收件地址|配送地址|送货地址/.test(message)) return 'address';
    if (/(?:^|_)(?:missing_materials?|missing_(?:invoice|order|payment)|unprovided_(?:invoice|order|payment)|no_(?:invoice|order|payment))(?:_|$)/.test(code) || /(?:未提供|缺少|缺失|缺件|漏交|需补交|待补交|不完整).*(?:材料|发票|订单|支付|付款|凭证)|(?:材料|发票|订单|支付|付款|凭证).*(?:未提供|缺少|缺失|缺件|漏交|需补交|待补交|不完整)/.test(message)) return 'missing';
    const level = checkLevel(issue);
    if (/blurr?y|illegible|unreadable|legibility_poor/.test(code) || /模糊|看不清|不清晰|无法(?:看清|辨认|识别|读)|难以辨认|字迹不清|不可读|文字(?:过小|较小)|(?:blurred|blurry|illegible|unreadable)/i.test(message) || /^(?:readability|clarity|legibility)$/.test(code) && level === 'error') return 'legibility';
    return '';
  }
  function checksFor(group) {
    const checks = group.checks;
    const entries = Array.isArray(checks) ? checks.map((value, index) => [value?.key || value?.code || String(index), value]) : checks && typeof checks === 'object' ? Object.entries(checks) : [];
    return entries.map(([key, value]) => ({key, label: value?.label || value?.title || value?.name || checkLabels[key] || key.replaceAll('_', ' '), message: typeof value === 'string' && checkLevel(value) === 'neutral' ? value : value?.message || value?.detail || '', level: checkLevel(value)})).filter(check => issueCategory({...check, message: check.message || check.label}));
  }
  function reviewSettled(run) {
    return !!run && !runBusy(run) && run.stage !== 'collect' && !allTrash(run);
  }
  function reviewProblems(run) {
    // Only completed, explicit review results can produce warnings. A zero role
    // count is not proof that a document is missing (mixed images are common).
    if (!reviewSettled(run)) return [];
    const review = run.review || {}, trash = new Set(ids(review.trash)), problems = [];
    function add(issue, group = null, fallback = 'warning') {
      if (!issue) return;
      const related = ids(issue.material_ids);
      if (related.length && related.every(id => trash.has(id))) return;
      const rawLevel = String(issue.level || issue.severity || issue.status || fallback).toLowerCase();
      if (['info', 'pass', 'passed', 'ok', 'success'].includes(rawLevel)) return;
      const message = textOf(issue), category = issueCategory(issue);
      if (!message || !category) return;
      problems.push({message, category, level: checkLevel(rawLevel) === 'error' ? 'error' : 'warning', code: String(issue.code || issue.key || ''), groupKey: String(issue.group_key || group?.key || ''), orderId: String(issue.order_id || group?.order_id || ''), materialIds: related});
    }
    for (const issue of asArray(review.issues)) add(issue);
    for (const warning of Array.isArray(review.warnings) ? review.warnings : []) add(warning);
    for (const group of asArray(review.groups)) {
      for (const issue of Array.isArray(group.issues) ? group.issues : []) add(issue, group);
      for (const check of checksFor(group)) if (['error', 'warning'].includes(check.level)) add({message: check.message || check.label, code: check.key, level: check.level}, group);
    }
    return [...new Map(problems.map(problem => [[problem.groupKey, problem.orderId, problem.message].join('|'), problem])).values()];
  }
  function groupKey(group, index = 0) { return String(group.order_id || group.key || 'group-' + index); }
  function groupForOrder(run, orderId) {
    return asArray(run?.review?.groups).find(group => String(group.order_id) === String(orderId));
  }
  function groupProblems(run, group) {
    const members = new Set(ids(group?.material_ids));
    return reviewProblems(run).filter(problem => problem.groupKey && problem.groupKey === String(group?.key) || problem.orderId && problem.orderId === String(group?.order_id) || problem.materialIds.length && problem.materialIds.some(id => members.has(id)));
  }
  function missingRole(run, group, role) {
    return groupProblems(run, group).some(problem => new RegExp('^(missing_|unprovided_|no_)' + role + '$', 'i').test(problem.code) || new RegExp('(?:未提供|缺少|缺失).*(?:' + roles[role] + '|' + (role === 'invoice' ? '发票' : role === 'order' ? '订单' : '支付') + ')').test(problem.message));
  }
  function batchView(run) {
    const key = String(run.id);
    if (!ui.batchViews.has(key)) ui.batchViews.set(key, {query: '', group: 'all', onlyProblems: false, layout: 'grid'});
    return ui.batchViews.get(key);
  }
  function rowProblems(run, row, making) {
    const group = making ? groupForOrder(run, row.order_id) : row;
    const warnings = making ? [...productionBlockers(row), ...productionWarnings(row).map(message => ({message}))] : [];
    return [...new Map([...groupProblems(run, group), ...warnings].map(problem => [problem.message, problem])).values()];
  }
  function visibleBatchRows(run, rows, making = false) {
    const view = batchView(run), query = view.query.trim().toLocaleLowerCase();
    return rows.map((row, index) => ({row, index, key: groupKey(row, index), problems: rowProblems(run, row, making)})).filter(entry => {
      if (view.group !== 'all' && entry.key !== view.group) return false;
      if (view.onlyProblems && !entry.problems.length) return false;
      const group = making ? groupForOrder(run, entry.row.order_id) : entry.row;
      const names = ids(group?.material_ids).map(id => state.materials[id]?.name || '').join(' ');
      return !query || [entry.row.title, entry.index + 1, names, ...entry.problems.map(problem => problem.message)].join(' ').toLocaleLowerCase().includes(query);
    }).sort((a, b) => Number(!!b.problems.length) - Number(!!a.problems.length) || a.index - b.index);
  }
  function batchSummary(run, rows, making = false) {
    const busy = runBusy(run), materials = materialsFor(run), problemGroups = rows.filter(row => rowProblems(run, row, making).length).length;
    const ready = making ? rows.filter(row => ['done', 'ready', 'success'].includes(row.status)).length : 0;
    return `<div class="batch-summary" aria-label="本批数量总览"><div><strong>${rows.length}</strong><span>笔采购</span></div><div><strong>${materials.length}</strong><span>份原始材料</span></div><div><strong>${rows.length * 2}</strong><span>份 PDF${making ? ` · 已制作 ${ready}/${rows.length} 笔` : ' · 每笔 2 份'}</span></div><div class="${problemGroups ? 'has-problems' : ''}"><strong>${busy ? '…' : problemGroups}</strong><span>${busy ? '正在处理' : '组需要核对'}</span></div></div>`;
  }
  function batchToolbar(run, rows, making = false) {
    const view = batchView(run), busy = runBusy(run), visible = visibleBatchRows(run, rows, making);
    return `<div class="batch-toolbar"><label class="batch-search">${icon('search')}<span class="sr-only">搜索采购或材料</span><input id="batchSearch" type="search" data-batch-field="query" value="${escapeHTML(view.query)}" placeholder="搜索采购 / 材料" autocomplete="off"></label><label class="batch-group-label"><span class="sr-only">选择采购组</span><select id="batchGroup" data-batch-field="group"><option value="all">全部 ${rows.length} 笔采购</option>${rows.map((row, index) => `<option value="${escapeHTML(groupKey(row, index))}" ${view.group === groupKey(row, index) ? 'selected' : ''}>${index + 1}. ${escapeHTML(row.title || '采购 ' + (index + 1))}</option>`).join('')}</select></label><label class="checkbox-label batch-problem-filter"><input id="batchProblems" type="checkbox" data-batch-field="onlyProblems" ${view.onlyProblems ? 'checked' : ''}><span>只看有问题</span></label><div class="batch-view-switch" role="group" aria-label="预览排列"><button class="icon-button ${view.layout === 'grid' ? 'selected' : ''}" data-action="batch-layout" data-layout="grid" aria-label="紧凑网格" aria-pressed="${view.layout === 'grid'}" title="紧凑网格">${icon('grid')}</button><button class="icon-button ${view.layout === 'list' ? 'selected' : ''}" data-action="batch-layout" data-layout="list" aria-label="列表" aria-pressed="${view.layout === 'list'}" title="列表">${icon('list')}</button></div><span class="batch-visible tiny muted">显示 ${visible.length}/${rows.length} 笔</span>${making ? `<button class="button secondary small" data-action="make" ${busy ? 'disabled' : ''}>${icon('refresh')}重新制作本批</button>` : `<button class="icon-button batch-review-retry" data-action="review" ${busy || !!ui.upload ? 'disabled' : ''} aria-label="重新审核本批，无需填写文字" title="重新审核本批">${icon('refresh')}</button>`}</div>`;
  }
  function batchEmptyHTML() {
    return '<div class="batch-empty">没有符合筛选的采购。<button class="text-button" data-action="batch-clear">清除筛选</button></div>';
  }
  function progressHTML(run, phase = 'review') {
    const jobs = runJobs(run).filter(job => activeStatuses.has(job.status));
    const job = jobs.at(-1);
    const summary = run.session_summary || run.summary || run.session || {};
    const total = Math.max(0, Number(summary.total) || materialIds(run).length);
    const processed = Math.max(0, Number(summary.processed) || 0);
    const progress = Number.isFinite(Number(job?.progress)) ? Math.max(0, Math.min(100, Number(job.progress))) : total ? Math.min(100, processed / total * 100) : null;
    const busy = runBusy(run);
    if (!busy) return '';
    return `<div class="processing-card is-processing" role="status"><span class="processing-icon"><span class="spinner"></span></span><div class="processing-copy"><strong>${phase === 'review' ? '正在整理材料' : /reflow/.test(job?.kind || '') ? '正在更新版式与 PDF' : /export/.test(job?.kind || '') ? '正在导出 PDF' : '正在逐笔制作'}</strong><p>${escapeHTML(job?.message || '进度自动保存，可以稍后回来继续。')}</p><div class="processing-meta">${phase === 'review' ? `<span>已处理 ${processed} / ${total} 份材料</span>` : `<span>${productionFor(run).filter(row => ['ready', 'done'].includes(row.status)).length} / ${productionFor(run).length} 笔已制作</span>`}<span class="saved-label">${icon('check')}进度自动保存</span></div><div class="progress-track ${progress === null ? 'indeterminate' : ''}" role="progressbar" aria-label="${phase === 'review' ? '整理进度' : '制作进度'}" ${progress === null ? '' : `aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(progress)}"`}><span style="width:${progress ?? 35}%"></span></div></div></div>`;
  }

  function roleHTML(group, role, run, detailed = false) {
    const materials = ids(group.material_ids).map(id => state.materials[id]).filter(material => material && materialHasRole(material, role));
    const missing = !materials.length && missingRole(run, group, role);
    return `<div class="role-column ${missing ? 'confirmed-missing' : ''}"><div class="role-label"><strong>${roles[role]}</strong><span class="role-count ${missing ? 'warning-text' : ''}">${missing ? '需补交' : materials.length + ' 份'}</span></div>${materials.length ? `<div class="role-materials ${detailed ? 'role-materials-detailed' : ''}">${materials.map(material => `<button class="role-material" data-action="material-preview" data-material-id="${escapeHTML(material.id)}" aria-label="查看${escapeHTML(material.name || roles[role])}" title="${escapeHTML(material.name || roles[role])}">${localURL(material.thumb_url || material.url) ? `<img src="${escapeHTML(localURL(material.thumb_url || material.url))}" alt="" loading="lazy" decoding="async">` : icon('file')}${detailed ? `<span>${escapeHTML(material.name || roles[role])}</span>` : ''}${material.is_pdf && material.pages > 1 ? `<small>${Number(material.pages)}页</small>` : ''}</button>`).join('')}</div>` : `<span class="role-empty ${missing ? 'warning-text' : 'muted'}">${missing ? '审核提示需补交' : runBusy(run) ? '整理中' : '—'}</span>`}</div>`;
  }
  function allGroupMaterialsHTML(group) {
    const materials = ids(group.material_ids).map(id => state.materials[id]).filter(Boolean);
    return `<div class="all-group-materials"><h3>全部原始材料 · ${materials.length} 份</h3><div class="group-original-strip">${materials.map(material => `<button class="original-chip" data-action="material-preview" data-material-id="${escapeHTML(material.id)}" title="${escapeHTML(material.name || '材料')}">${localURL(material.thumb_url || material.url) ? `<img src="${escapeHTML(localURL(material.thumb_url || material.url))}" alt="" loading="lazy">` : icon('file')}<span>${escapeHTML(material.name || '材料')}</span>${material.is_pdf ? `<small>PDF${material.pages ? ' · ' + Number(material.pages) + '页' : ''}</small>` : ''}</button>`).join('')}</div></div>`;
  }
  function problemDetailsHTML(problems) {
    return problems.length ? `<ul class="group-problem-list">${problems.map(problem => `<li>${icon('warning')}<span>${escapeHTML(problem.message)}</span></li>`).join('')}</ul>` : '<p class="group-note muted tiny">当前没有待处理提醒。原件始终保留，可点击缩略图核对。</p>';
  }
  function excludeOrderHTML(row, run) {
    return `<div class="purchase-actions"><button class="button secondary small exclude-order" data-action="remove-order" data-order-id="${escapeHTML(row.order_id || '')}" ${!row.order_id || runBusy(run) || !!ui.upload ? 'disabled' : ''} aria-label="排除${escapeHTML(row.title || '本笔采购')}" title="从本次制作与导出中排除整笔采购">${icon('trash')}排除此笔采购</button></div>`;
  }
  function groupHTML(group, index, run) {
    const busy = runBusy(run), checks = checksFor(group), problems = groupProblems(run, group);
    const passed = checks.filter(check => check.level === 'pass').length;
    const otherMaterials = ids(group.material_ids).map(id => state.materials[id]).filter(material => material && !Object.keys(roles).some(role => materialHasRole(material, role)));
    return `<article class="group-card batch-card ${problems.length ? 'has-problems' : ''}" data-group-key="${escapeHTML(groupKey(group, index))}"><header class="group-header"><span class="group-number">${String(index + 1).padStart(2, '0')}</span><div class="group-title"><h2 title="${escapeHTML(group.title || '')}">${escapeHTML(group.title || '采购 ' + (index + 1))}</h2><span class="tiny muted">${ids(group.material_ids).length} 份原始材料 · 2 份 PDF</span></div>${busy ? badge('整理中', 'neutral') : problems.length ? badge(problems.length + ' 项待核对', 'warning') : badge(passed ? '已核对' : '已分组', 'success')}</header><div class="role-grid">${Object.keys(roles).map(role => roleHTML(group, role, run)).join('')}</div>${otherMaterials.length ? `<div class="other-materials"><span class="tiny muted">其他材料 ${otherMaterials.length} 份</span><div class="group-original-strip">${otherMaterials.map(material => `<button class="original-chip" data-action="material-preview" data-material-id="${escapeHTML(material.id)}">${localURL(material.thumb_url || material.url) ? `<img src="${escapeHTML(localURL(material.thumb_url || material.url))}" alt="" loading="lazy">` : icon('file')}<span>${escapeHTML(material.name || '材料')}</span></button>`).join('')}</div></div>` : ''}${problems.length ? `<p class="card-issue-summary">${icon('warning')}<span>${escapeHTML(problems[0].message)}${problems.length > 1 ? ` · 另 ${problems.length - 1} 项` : ''}</span></p>` : ''}${excludeOrderHTML(group, run)}<details class="group-details" id="group-${escapeHTML(groupKey(group, index))}"><summary>查看本组详情<span class="tiny muted">${passed ? `${passed} 项已核对` : '全部原件'}</span>${icon('arrow')}</summary><div class="group-detail-body">${problemDetailsHTML(problems)}${allGroupMaterialsHTML(group)}${group.order_id ? `<button class="button secondary small" data-action="make" data-order-id="${escapeHTML(group.order_id)}" ${busy || !!ui.upload ? 'disabled' : ''}>${icon('refresh')}仅制作本笔</button>` : ''}</div></details></article>`;
  }
  function renderReview(run) {
    const busy = runBusy(run), review = run.review || {}, groups = asArray(review.groups), problems = reviewProblems(run);
    const trash = new Set(ids(review.trash)), settled = reviewSettled(run);
    const unassigned = ids(review.unassigned).filter(id => !trash.has(id)).map(id => state.materials[id]).filter(Boolean);
    const trashMaterials = [...trash].map(id => state.materials[id]).filter(Boolean);
    const visible = visibleBatchRows(run, groups), view = batchView(run);
    return `${heading(busy ? '正在整理本批材料' : '核对采购分组', '所有采购都在下方。点击缩略图放大，展开一组查看原件。')}${batchSummary(run, groups)}${progressHTML(run)}
      ${!busy && problems.length ? notice(`<strong>${problems.length} 项待核对</strong><span>有问题的组已排在前面。${escapeHTML(problems[0].message)}</span>`, 'warning', '<button class="text-button" data-action="batch-show-problems">只看有问题</button>') : !busy && groups.length ? notice('<strong>本批审核完成</strong><span>确认分组与原件后即可制作。</span>', 'success') : ''}
      ${groups.length ? `${batchToolbar(run, groups)}<div class="groups-list batch-grid ${view.layout === 'list' ? 'is-list' : ''}">${visible.map(entry => groupHTML(entry.row, entry.index, run)).join('') || batchEmptyHTML()}</div>` : '<div class="review-placeholder">' + icon('folder') + '<h2>' + (busy ? '正在识别与匹配采购' : allTrash(run) ? '本次材料均已忽略' : '等待整理结果') + '</h2><p>' + (allTrash(run) ? '原件保留，归档后不会生成空 PDF。' : '采购分组会在这里显示。') + '</p></div>'}
      ${settled && unassigned.length ? `<section class="unassigned-card"><div class="section-title"><h2>尚未归组 <span class="count">${unassigned.length}</span></h2>${badge('需核对原件', 'warning')}</div><div class="materials-grid compact-grid">${unassigned.map(material => materialCard(material)).join('')}</div></section>` : ''}
      ${!busy && trash.size ? `<details class="disclosure trash-disclosure" id="trash-${escapeHTML(run.id)}"><summary>${icon('trash')}已忽略 ${trash.size} 份无关材料<span class="tiny">原件保留</span></summary><div class="materials-grid compact-grid">${trashMaterials.map(material => materialCard(material)).join('')}</div></details>` : ''}
      ${settled && problems.length ? `<section class="supplement-card" id="supplement-${escapeHTML(run.id)}"><div class="section-title"><h2>${icon('plus')}补交材料</h2><span class="tiny muted">也可直接重试，无需填写文字</span></div>${dropzoneHTML(run, true)}${uploadHTML(run)}<div class="supplement-footer"><span class="tiny muted">原件和已完成结果保留</span><button class="button secondary small" data-action="review" ${!!ui.upload ? 'disabled' : ''}>${icon('refresh')}重新审核本批</button></div></section>` : ''}
      <div class="flow-bottom"><button class="text-button" data-action="back-collect" ${busy || !!ui.upload ? 'disabled' : ''}>${icon('back')}返回收集</button><button class="button primary large" data-action="continue-review" ${busy || !!ui.upload || !materialIds(run).length ? 'disabled' : ''}>${allTrash(run) ? '归档并完成' : !groups.length ? '确认未归组材料后继续' : problems.length ? '确认提醒并制作' : '制作本批 ' + groups.length + ' 笔'}${icon('arrow')}</button></div>`;
  }

  function productionFor(run) {
    const production = asArray(run.production);
    if (production.length) return production;
    return ids(run.order_ids).map((id, index) => {
      const order = state.orders.find(row => row.id === id);
      return {order_id: id, title: order?.title || '采购 ' + (index + 1), status: 'pending', layout: order?.layout || {}};
    });
  }
  function processingFailure(issue) {
    const code = String(issue?.code || issue?.key || '').toLowerCase(), message = textOf(issue);
    return /(?:process(?:ing)?|network|connection|export|render|pdf|make|write|read|save|service|request)[_-](?:error|fail(?:ed|ure)?|timeout)|(?:error|fail(?:ed|ure)?|timeout)[_-](?:process(?:ing)?|network|export|render|pdf|make|write|read|save)/.test(code) || /(?:网络|连接|服务|请求|制作|生成|处理|导出|写入|保存|文件读取|PDF).*(?:失败|故障|超时|不可用)|(?:无法|未能).*(?:连接|写入|导出|保存|生成|制作)|(?:network|connection|export|render|request|service|PDF).*(?:fail(?:ed|ure)?|error|timed?\s*out)/i.test(message);
  }
  function productionWarnings(row) {
    return (Array.isArray(row.warnings) ? row.warnings : []).filter(issue => issueCategory(issue) || processingFailure(issue)).map(textOf).filter(Boolean);
  }
  function productionBlockers(row) {
    const blockers = (Array.isArray(row.blocking_issues) ? row.blocking_issues : []).filter(issue => issueCategory(issue) || processingFailure(issue)).map(issue => ({message: textOf(issue), category: issueCategory(issue), level: issue?.level || 'warning'})).filter(issue => issue.message);
    // Processing failures remain actionable even though heuristic checks are hidden.
    if (['error', 'failed'].includes(row.status) || row.error) blockers.unshift({message: textOf(row.error) || '这笔制作未完成', level: 'error'});
    if (row.legibility === 'poor' && !blockers.some(issue => issue.category === 'legibility')) blockers.push({message: '预览文字较小或不清晰，请放大核对', category: 'legibility', level: 'warning'});
    return [...new Map(blockers.map(issue => [issue.message, issue])).values()];
  }
  function normalizePages(value, fallback = '') {
    const rows = Array.isArray(value) ? value : [];
    const pages = rows.map((entry, index) => {
      const row = typeof entry === 'string' ? {url: entry} : entry || {};
      const url = localURL(row.url || row.preview_url || row.image_url);
      return url ? {url, thumb: localURL(row.thumb_url || row.thumbnail_url) || url, page: Math.max(1, Number(row.page) || index + 1), totalPages: Math.max(1, Number(row.total_pages) || rows.length), sourceId: String(row.source_id || ''), sourcePage: Number(row.source_page) || null} : null;
    }).filter(Boolean);
    const url = localURL(fallback);
    return pages.length ? pages : url ? [{url, thumb: url, page: 1, totalPages: 1}] : [];
  }
  function previewData(row) {
    const cached = ui.previewCache.get(String(row.order_id)) || {};
    const voucherPages = normalizePages(row.voucher_pages || row.preview_pages, cached.voucher || row.preview_url || row.voucher_url || row.voucher_pdf_url);
    const invoicePages = normalizePages(row.invoice_pages || row.invoice_preview_pages, cached.invoice || row.invoice_url || row.invoice_pdf_url || row.pdf_url);
    return {voucher: voucherPages[0]?.url || '', voucherPages, voucherPDF: localURL(row.voucher_pdf_url), invoice: invoicePages[0]?.url || '', invoicePages, invoicePDF: localURL(cached.invoicePDF || row.invoice_pdf_url || row.pdf_url), warnings: cached.warnings || []};
  }
  function isPDF(url) { return /\.pdf(?:[?#]|$)|\/invoice-preview-pdf\//i.test(url); }
  function previewTile(row, kind, title) {
    const previews = previewData(row), pages = kind === 'voucher' ? previews.voucherPages : previews.invoicePages;
    const url = pages[0]?.url || '', pdf = (kind === 'invoice' ? previews.invoicePDF : previews.voucherPDF) || (isPDF(url) ? url : '');
    return `<div class="preview-tile" data-preview-kind="${kind}"><div class="preview-label"><h3>${title}</h3><span class="tiny muted">${pages.length ? pages.length + ' 页' : '待制作'}</span></div>${url && !isPDF(url) ? `<button class="paper-preview" data-action="production-preview" data-order-id="${escapeHTML(row.order_id)}" data-kind="${kind}" data-page="${pages[0].page}" aria-label="放大${title}${pages.length > 1 ? '，共 ' + pages.length + ' 页' : ''}"><img src="${escapeHTML(url)}" alt="${title}预览" loading="lazy" decoding="async"><span class="paper-zoom">${icon('zoom')}放大</span></button>` : `<button class="paper-placeholder" data-action="production-preview" data-order-id="${escapeHTML(row.order_id)}" data-kind="${kind}" ${!url ? 'disabled' : ''}>${icon('file')}<span>${pdf ? '打开完整 PDF' : row.status === 'error' ? '待重试' : '制作后显示'}</span></button>`}${pages.length > 1 ? `<div class="pdf-page-strip" aria-label="${title}全部 ${pages.length} 页">${pages.map(page => `<button class="pdf-page-thumb" data-action="production-preview" data-order-id="${escapeHTML(row.order_id)}" data-kind="${kind}" data-page="${page.page}" aria-label="查看${title}第${page.page}页">${!isPDF(page.thumb) ? `<img src="${escapeHTML(page.thumb)}" alt="" loading="lazy">` : icon('file')}<span>${page.page}</span></button>`).join('')}</div>` : ''}</div>`;
  }
  function layoutFor(row) {
    const order = state.orders.find(order => order.id === row.order_id);
    return {...(state.settings.extra || {}), ...(order?.layout || {}), ...(row.layout || {}), ...(ui.layoutDrafts.get(String(row.order_id)) || {})};
  }
  function layoutHTML(row, busy) {
    const layout = layoutFor(row);
    const ratio = Math.round(Number(layout.layout_column_ratio ?? 0.5) * 100);
    return `<details class="disclosure layout-disclosure" id="layout-${escapeHTML(row.order_id)}"><summary>${icon('settings')}调整版式<span class="tiny">本机更新预览</span></summary><form class="layout-form" data-layout-order="${escapeHTML(row.order_id)}"><div class="layout-grid"><label class="field" for="ratio-${escapeHTML(row.order_id)}">左栏宽度 <output id="ratio-output-${escapeHTML(row.order_id)}">${ratio}%</output><input id="ratio-${escapeHTML(row.order_id)}" name="layout_column_ratio" type="range" min="15" max="85" value="${ratio}" ${busy ? 'disabled' : ''}><small>左右均保持原始比例，不拉伸材料。</small></label><label class="field" for="align-${escapeHTML(row.order_id)}">垂直对齐<select id="align-${escapeHTML(row.order_id)}" name="layout_vertical_align" ${busy ? 'disabled' : ''}><option value="top" ${layout.layout_vertical_align !== 'center' ? 'selected' : ''}>顶部对齐</option><option value="center" ${layout.layout_vertical_align === 'center' ? 'selected' : ''}>居中对齐</option></select></label><label class="field" for="side-${escapeHTML(row.order_id)}">左栏内容<select id="side-${escapeHTML(row.order_id)}" name="layout_order_on_left" ${busy ? 'disabled' : ''}><option value="false" ${!layout.layout_order_on_left ? 'selected' : ''}>支付凭证</option><option value="true" ${layout.layout_order_on_left ? 'selected' : ''}>订单详情</option></select></label></div><fieldset class="invoice-spacing"><legend>发票留白 <span class="tiny muted">毫米</span></legend><div class="invoice-spacing-grid">${[['left','左',54],['right','右',54],['top','上',24],['bottom','下',24]].map(([key, label, fallback]) => `<label class="field" for="invoice-${key}-${escapeHTML(row.order_id)}">${label}<input id="invoice-${key}-${escapeHTML(row.order_id)}" name="invoice_${key}_mm" type="number" min="5" max="100" step="1" value="${escapeHTML(layout['invoice_' + key + '_mm'] ?? fallback)}" ${busy ? 'disabled' : ''}></label>`).join('')}</div></fieldset><div class="layout-footer"><span class="tiny muted">只调整空间分配，保留原始发票。</span><button class="button secondary small" type="submit" ${busy || ui.locks.has('layout:' + row.order_id) ? 'disabled' : ''}>${ui.locks.has('layout:' + row.order_id) ? '<span class="spinner"></span>正在更新…' : icon('refresh') + '更新预览'}</button></div></form></details>`;
  }
  function productionHTML(row, index, run) {
    const busy = runBusy(run), problems = rowProblems(run, row, true), previews = previewData(row), group = groupForOrder(run, row.order_id);
    const failed = ['error', 'failed'].includes(row.status) || !!row.error;
    const status = failed ? badge('需重试', 'warning') : row.status === 'stale' ? badge('材料已更新', 'warning') : ['done', 'ready', 'success'].includes(row.status) || previews.voucher ? badge('已制作', 'success') : row.status === 'running' ? badge('制作中', 'neutral') : badge('等待制作', 'neutral');
    return `<article class="production-card batch-card ${problems.length ? 'has-problems' : ''}" data-group-key="${escapeHTML(groupKey(row, index))}"><header class="group-header"><span class="group-number">${String(index + 1).padStart(2, '0')}</span><div class="group-title"><h2 title="${escapeHTML(row.title || '')}">${escapeHTML(row.title || '采购 ' + (index + 1))}</h2><span class="tiny muted">${ids(group?.material_ids).length} 份原件 · 两份 PDF</span></div>${status}<button class="icon-button remake-button" data-action="make" data-order-id="${escapeHTML(row.order_id)}" ${busy ? 'disabled' : ''} aria-label="重新制作${escapeHTML(row.title || '本笔采购')}" title="仅重新制作本笔">${icon('refresh')}</button></header><div class="production-previews">${previewTile(row, 'voucher', '购买及支付凭证')}${previewTile(row, 'invoice', '发票打印版')}</div>${problems.length ? `<p class="card-issue-summary">${icon('warning')}<span>${escapeHTML(problems[0].message)}${problems.length > 1 ? ' · 另 ' + (problems.length - 1) + ' 项' : ''}</span></p>` : ''}${excludeOrderHTML(row, run)}<details class="group-details production-details" id="production-${escapeHTML(row.order_id)}"><summary>核对原件与调整版式<span class="tiny muted">${problems.length ? problems.length + ' 项提醒' : '展开详情'}</span>${icon('arrow')}</summary><div class="group-detail-body">${problemDetailsHTML(problems)}${group ? allGroupMaterialsHTML(group) : ''}${layoutHTML(row, busy)}<button class="button secondary small" data-action="make" data-order-id="${escapeHTML(row.order_id)}" ${busy ? 'disabled' : ''}>${icon('refresh')}仅重新制作本笔</button></div></details></article>`;
  }
  function renderMaking(run, stage) {
    const busy = runBusy(run), production = productionFor(run), ready = stage === 'ready' || run.stage === 'ready' && !busy;
    const hadProduction = production.some(row => previewData(row).voucher || ['done', 'ready', 'error', 'running'].includes(row.status));
    const visible = visibleBatchRows(run, production, true), view = batchView(run);
    const problemGroups = production.filter(row => rowProblems(run, row, true).length).length;
    return `${heading(ready ? '核对本批预览' : busy ? '正在制作本批 PDF' : '制作本批材料', '每笔两份 PDF 都在下方。点击任一预览，可跨组核对原件和全部页面。', state.settings.output_dir ? '<button class="button secondary small" data-action="open-output">' + icon('folder') + '打开本机输出文件夹</button>' : '')}${batchSummary(run, production, true)}
      ${busy ? progressHTML(run, 'make') : !ready && !hadProduction ? `<div class="make-intro"><span class="make-symbol">${icon('file')}</span><div><h2>按采购逐笔制作</h2><p>原始材料全部保留，完成后再核对导出。</p></div><button class="button primary" data-action="make">开始制作${icon('arrow')}</button></div>` : ''}
      ${!busy && problemGroups ? notice(`<strong>${problemGroups} 组需要核对</strong><span>问题组优先显示，已完成的结果不会丢失。</span>`, 'warning', '<button class="text-button" data-action="batch-show-problems">只看有问题</button>') : ''}
      ${production.length ? `${batchToolbar(run, production, true)}<div class="production-list batch-grid ${view.layout === 'list' ? 'is-list' : ''}">${visible.map(entry => productionHTML(entry.row, entry.index, run)).join('') || batchEmptyHTML()}</div>` : busy ? '<div class="review-placeholder">' + icon('file') + '<p>各组预览将依次显示在这里。</p></div>' : ''}
      <div class="flow-bottom make-bottom"><button class="text-button" data-action="back-review" ${busy ? 'disabled' : ''}>${icon('back')}返回整理</button>${ready ? `<div class="export-action"><label class="checkbox-label"><input id="exportConfirmed" data-export-confirmed="true" type="checkbox" ${ui.exportConfirmed.has(String(run.id)) ? 'checked' : ''}><span>已核对本批全部 ${production.length} 笔原件与预览</span></label><button id="exportButton" class="button primary large" data-action="export" ${busy || !ui.exportConfirmed.has(String(run.id)) ? 'disabled' : ''}>导出本批 ${production.length * 2} 份 PDF${icon('download')}</button></div>` : ''}</div>`;
  }

  function exportsFor(run) {
    const records = new Map(state.exports.filter(row => row?.id).map(row => [String(row.id), row]));
    const result = [];
    for (const row of Array.isArray(run.exports) ? run.exports : []) {
      if (typeof row === 'string') { const record = records.get(row); if (record) result.push(record); }
      else if (row && typeof row === 'object') result.push({...records.get(String(row.id || row.export_id)), ...row, id: row.id || row.export_id});
    }
    for (const record of state.exports) if (!record.invalidated && !record.history_hidden && String(record.run_id || '') === String(run.id) && !result.some(row => row.id === record.id)) result.push(record);
    return result;
  }
  function exportCard(record, index = 0) {
    const id = record.id || record.export_id;
    const folder = String(record.folder || record.path || '');
    const title = record.title || record.order_title || folder.split(/[\\/]/).filter(Boolean).at(-1) || '采购 ' + (index + 1);
    const invoice = localURL(record.invoice_url) || (id ? `/download/${encodeURIComponent(id)}/invoice` : '');
    const voucher = localURL(record.voucher_url) || (id ? `/download/${encodeURIComponent(id)}/voucher` : '');
    return `<article class="export-card"><span class="export-icon">${icon('folder')}</span><div class="export-info"><h3>${escapeHTML(title)}</h3><p>${escapeHTML(displayDate(record.created))}</p>${folder ? `<p class="folder-path" title="${escapeHTML(folder)}">${escapeHTML(folder)}</p>` : ''}<p class="tiny muted">两份 PDF 已保存到本地文件夹</p></div>${id ? `<button class="button secondary small" data-action="open-output" data-export-id="${escapeHTML(id)}">${icon('folder')}打开文件夹</button>` : ''}</article>`;
  }
  function renderDone(run) {
    const exports = exportsFor(run);
    const forced = Array.isArray(run.forced_steps) ? run.forced_steps : [];
    const archived = allTrash(run) && !exports.length;
    const problems = unique([...reviewProblems(run).map(problem => problem.message), ...productionFor(run).flatMap(productionWarnings)]);
    return `<div class="done-heading"><span class="done-mark">${icon('check')}</span><span class="eyebrow">${archived ? '无关材料已归档' : '本次处理已完成'}</span><h1>材料已保存<span class="accent">。</span></h1><p>${exports.length ? `${exports.length} 笔采购 · ${exports.length * 2} 份 PDF，已保存到本机普通文件夹。` : archived ? '本次材料均已忽略，原件保留，未生成空采购或 PDF。' : '导出记录保存在本机，可随时从历史查看。'}</p></div>${problems.length ? notice(`<strong>仍有已确认的提醒，请保留原始材料复核</strong>${problems.length ? problemsList(problems.map(message => ({message}))) : '<p>部分步骤在你确认后强制继续，原有检查结论没有改为通过。</p>'}<span class="tiny">${forced.length} 次强制步骤已记录在本次报销中。</span>`, 'warning') : ''}<div class="exports-list">${exports.map(exportCard).join('') || (archived ? notice(`已归档 ${materialIds(run).length} 份无关材料，可从历史重新查看。`, 'success') : notice('暂时没有读取到导出记录。请刷新，或从历史查看已保存的文件。', 'info', '<button class="button secondary small" data-action="refresh">刷新</button>'))}</div><div class="done-actions"><button class="button primary start-button" data-action="start" ${ui.locks.has('start') ? 'disabled' : ''}>${ui.locks.has('start') ? '正在开始…' : '新开始'}${icon('plus')}</button><button class="button secondary" data-action="home">返回主页</button></div>`;
  }
  function historyRuns() {
    // Collect-stage drafts are working sessions only; they must not clutter history
    // (each「开始」would otherwise leave an empty 草稿·收集 card).
    return allRuns().filter(run => run.stage && run.stage !== 'collect')
      .sort((a, b) => String(b.updated || b.created).localeCompare(String(a.updated || a.created)));
  }
  function renderHistory() {
    const runs = historyRuns();
    const linkedExports = new Set(runs.flatMap(run => exportsFor(run).map(record => String(record.id))));
    const legacy = state.exports.filter(record => !record.history_hidden && !linkedExports.has(String(record.id))).slice().reverse();
    return `<section class="history-page"><div class="flow-top"><button class="text-button back-link" data-action="home">${icon('back')}主页</button><button class="text-button" data-action="refresh">${icon('refresh')}刷新</button></div>${heading('每次整理，都在这里', '整理中的草稿可以接着做，完成的文件可以随时取用。收集中的空草稿不会出现在这里。', '<button class="button primary" data-action="start" ' + (ui.locks.has('start') ? 'disabled' : '') + '>新开始' + icon('plus') + '</button>')}
      ${runs.length ? `<div class="history-list">${runs.map(run => `<article class="history-card"><span class="history-icon">${icon(run.stage === 'done' ? 'folder' : 'file')}</span><div class="history-info"><div class="history-title"><h2>${runTitleHTML(run)}</h2>${badge(run.status === 'running' ? '处理中' : run.status === 'error' ? '待重试' : run.stage === 'done' ? '已完成' : '草稿 · ' + (stageNames[run.stage] || '整理'), run.stage === 'done' ? 'success' : run.status === 'error' ? 'warning' : 'neutral')}</div><p>${escapeHTML(displayDate(run.updated || run.created))} · ${materialIds(run).length} 份材料${exportsFor(run).length ? ' · ' + exportsFor(run).length + ' 笔已导出' : ''}</p></div><button class="button secondary small" data-action="open-run" data-run-id="${escapeHTML(run.id)}">${run.stage === 'done' ? '查看结果' : '继续'}${icon('arrow')}</button><button class="icon-button history-delete" data-action="delete-run" data-run-id="${escapeHTML(run.id)}" aria-label="删除${escapeHTML(runTitle(run))}的历史记录" title="删除历史记录" ${runBusy(run) ? 'disabled' : ''}>${icon('trash')}</button></article>`).join('')}</div>` : ''}
      ${legacy.length ? `<section class="legacy-history"><div class="section-title"><h2>${runs.length ? '更早的导出记录' : '已导出的文件'}</h2><span class="tiny muted">原有记录完整保留</span></div><div class="exports-list">${legacy.map(exportCard).join('')}</div></section>` : ''}
      ${!runs.length && !legacy.length ? '<div class="empty-state">' + icon('history') + '<h2>开始第一份报销吧</h2><p>整理中的草稿和已完成记录会出现在这里。</p><button class="button primary" data-action="start">开始' + icon('arrow') + '</button></div>' : ''}</section>`;
  }

  async function locked(key, work) {
    if (ui.locks.has(key)) return;
    ui.locks.add(key); render();
    try { return await work(); } finally { ui.locks.delete(key); render(); schedulePoll(); }
  }
  function showError(error, action) {
    const message = errorMessage(error);
    if (ui.view === 'run' && ui.runId) ui.errors.set(String(ui.runId), {message, action});
    else ui.globalError = message;
    render(); toast(message, 'error');
  }
  async function safe(work, action) {
    try { return await work(); } catch (error) { showError(error, action); }
  }
  async function deleteRun(runId) {
    const run = runById(runId);
    if (!run || runBusy(run)) return;
    const revision = run.revision;
    const accepted = await confirmAction('删除这条历史记录？', `<p>${escapeHTML(runTitle(run))}</p><p>记录将从历史中移除，原始材料和已经导出的 PDF 仍保留在本机。</p>`, '删除记录');
    if (!accepted) return;
    await locked('delete:' + runId, async () => {
      await api('/api/flow/delete', {run_id: runId, revision});
      ui.localRuns.delete(String(runId));
      state.flow.runs = state.flow.runs.filter(item => String(item.id) !== String(runId));
      state.exports = state.exports.filter(item => String(item.run_id) !== String(runId));
      for (const map of [ui.steps, ui.profileDrafts, ui.errors, ui.batchViews]) map.delete(String(runId));
      if (ui.runId === String(runId)) ui.runId = null;
      await refresh(); navigate('history'); toast('历史记录已删除。', 'success');
    });
  }
  async function start() {
    if (!initialized) { await refresh(); if (!initialized) return; }
    if (ui.upload) throw new Error('材料还在导入，完成后再开始新的报销。');
    if (missingSettings().length) { openSettings('start'); return; }
    await locked('start', async () => {
      ui.globalError = '';
      const result = await api('/api/flow/new', {});
      const run = rememberRun(result);
      if (!run) throw new Error('未收到新的报销记录，请刷新后重试。');
      await refresh(); ui.steps.delete(String(run.id));
      navigate('run', run.id);
    });
  }
  async function persistProfile(run) {
    const profile = profileFor(run);
    for (const key of ['name', 'advisor', 'project_name']) profile[key] = String(profile[key] || '').trim();
    profile.declare_date = String(profile.declare_date || '').replaceAll('-', '');
    if (!profile.name || !profile.advisor || !profile.project_name || !/^\d{8}$/.test(profile.declare_date)) throw new Error('请填写完整的姓名、导师、项目与申报日期。');
    const result = await api('/api/flow/update', {run_id: run.id, profile});
    rememberRun(result); ui.dirtyProfiles.delete(String(run.id)); ui.profileDrafts.delete(String(run.id));
    if (Object.entries(profile).some(([key, value]) => String(run.profile?.[key] || '') !== String(value))) { ui.force.delete(String(run.id)); ui.exportConfirmed.delete(String(run.id)); }
    return profile;
  }
  async function saveProfile() {
    const form = $('#profileForm');
    if (form && !form.reportValidity()) return;
    const run = runById(); if (!run) return;
    await locked('run:' + run.id, async () => { await persistProfile(run); await refresh(); toast('本次申报信息已保存。', 'success'); });
  }
  async function home() {
    const run = runById();
    if (run && ui.dirtyProfiles.has(String(run.id)) && !runBusy(run)) await persistProfile(run);
    navigate('home');
  }
  async function openRun(id) {
    await locked('open:' + id, async () => {
      rememberRun(await api('/api/flow/open', {run_id: id}));
      await refresh(); ui.steps.delete(String(id));
      navigate('run', id);
    });
  }
  async function review() {
    const run = runById(); if (!run || runBusy(run) || ui.upload) return;
    if (!materialsFor(run).length) throw new Error('先导入图片或 PDF，再开始整理。');
    if (missingSettings().length) { openSettings('review'); return; }
    const form = $('#profileForm');
    if (form && !form.reportValidity()) return;
    await locked('run:' + run.id, async () => {
      await persistProfile(run);
      const result = await api('/api/flow/review', {run_id: run.id, consent: true, retry: true});
      trackJob(result, run, 'flow-review'); ui.errors.delete(String(run.id)); ui.force.delete(String(run.id)); ui.steps.set(String(run.id), 'review');
      await refresh(); render();
    });
  }
  function confirmAction(title, body, label = '确认继续') {
    if (confirmResolver) { confirmResolver(false); confirmResolver = null; }
    $('#confirmTitle').textContent = title; $('#confirmBody').innerHTML = body; $('#confirmYes').textContent = label;
    openDialog('confirmDialog');
    return new Promise(resolve => { confirmResolver = resolve; });
  }
  function resolveConfirm(yes) {
    const resolve = confirmResolver; confirmResolver = null;
    if ($('#confirmDialog').open) $('#confirmDialog').close();
    resolve?.(yes);
  }
  function problemsList(problems) { return `<ul class="compact-list">${problems.slice(0, 8).map(problem => `<li>${escapeHTML(problem.message || problem)}</li>`).join('')}${problems.length > 8 ? `<li>另有 ${problems.length - 8} 项提醒。</li>` : ''}</ul>`; }
  async function acceptProblems(run, problems, context = '制作') {
    if (!problems.length || ui.force.has(String(run.id))) return true;
    const yes = await confirmAction('仍有提醒，确认继续' + context + '？', `<p>以下内容还需要人工核对：</p>${problemsList(problems)}<p>确认后将带着这些问题继续，强制步骤会保存在本次记录中。请以原始材料和实际要求为准。</p>`, '确认带问题继续');
    if (yes) ui.force.add(String(run.id));
    return yes;
  }
  async function continueReview() {
    const run = runById(); if (!run || runBusy(run)) return;
    if (allTrash(run)) { await make(); return; }
    if (!asArray(run.review?.groups).length) {
      if (!materialIds(run).length) throw new Error('还没有材料，请先返回收集并导入文件。');
      const yes = await confirmAction('材料尚未归组，确认强制继续？', '<p>这些材料的归属尚未确认。继续后将保留为未审核组，并显示缺件与检查提示；不会把未知内容标为通过。</p><p>全部原始材料会保留。后续仍可返回补交并重新审核，本次强制步骤会记录。</p>', '确认带未归组材料继续');
      if (!yes) return;
      ui.force.add(String(run.id));
    }
    if (!await acceptProblems(run, reviewProblems(run))) return;
    await make();
    window.scrollTo({top: 0, behavior: 'instant'});
  }
  async function make(orderId) {
    const run = runById(); if (!run || runBusy(run) || ui.upload) return;
    if (orderId && !ids(run.order_ids).includes(String(orderId))) throw new Error('这笔采购已更新，请刷新后重试。');
    const problems = reviewProblems(run);
    if (!await acceptProblems(run, problems)) return;
    await locked('run:' + run.id, async () => {
      const data = {run_id: run.id, force: ui.force.has(String(run.id)), confirmed: true};
      if (orderId && ids(run.order_ids).includes(String(orderId))) data.only_order_ids = [String(orderId)];
      const result = await api('/api/flow/make', data);
      trackJob(result, run, 'flow-make'); ui.steps.set(String(run.id), 'making'); ui.errors.delete(String(run.id)); ui.exportConfirmed.delete(String(run.id));
      for (const row of productionFor(run)) if (!orderId || String(row.order_id) === String(orderId)) ui.previewCache.delete(String(row.order_id));
      await refresh();
    });
  }
  async function exportRun() {
    const run = runById(); if (!run || runBusy(run)) return;
    if (!ui.exportConfirmed.has(String(run.id))) { ui.steps.set(String(run.id), 'ready'); render(); throw new Error('请先对照原始材料核对预览，再勾选确认。'); }
    const problems = reviewProblems(run).concat(productionFor(run).flatMap(productionBlockers));
    if (problems.length) {
      const yes = await confirmAction('仍有提醒，确认导出？', `<p>请最后核对这些提醒：</p>${problemsList(problems)}<p>确认后将生成两份 PDF，并记录本次强制导出。</p>`, '确认并强制导出');
      if (!yes) return;
    }
    await locked('run:' + run.id, async () => {
      const result = await api('/api/flow/export', {run_id: run.id, force: problems.length > 0 || ui.force.has(String(run.id)), confirmed: true});
      trackJob(result, run, 'flow-export'); ui.errors.delete(String(run.id)); ui.steps.delete(String(run.id));
      await refresh();
    });
  }

  async function importFiles(fileList) {
    const run = runById(); if (!run) throw new Error('请先开始一次报销，再导入材料。');
    if (ui.upload || runBusy(run)) throw new Error('当前任务还在进行，完成后再补交材料。');
    const files = [...fileList];
    if (!files.length) return;
    const remaining = Math.max(0, 240 - materialIds(run).length);
    if (files.length > remaining) throw new Error(`本次最多保留 240 份材料，还可导入 ${remaining} 份。请减少文件或先移除不需要的材料。`);
    const runId = String(run.id);
    const failed = [], messages = [];
    let success = 0;
    ui.failedUpload = null;
    ui.upload = {runId, index: 1, total: files.length, name: files[0].name}; render();
    try {
      for (let index = 0; index < files.length; index++) {
        const file = files[index];
        ui.upload = {runId, index: index + 1, total: files.length, name: file.name}; render();
        try {
          if (!/\.(png|jpe?g|webp|bmp|tiff?|pdf)$/i.test(file.name)) throw new Error('支持 PNG、JPG、WebP、BMP、TIFF 或 PDF');
          if (!file.size || file.size > 40 * 1024 * 1024) throw new Error('文件需大于 0 且不超过 40 MB');
          await api(`/api/upload?run_id=${encodeURIComponent(runId)}&name=${encodeURIComponent(file.name)}`, null, {headers: {'Content-Type': 'application/octet-stream', 'X-App-Token': token}, body: file});
          success++;
        } catch (error) { failed.push(file); messages.push(file.name + '：' + errorMessage(error)); }
      }
      await refresh();
      if (success) { ui.force.delete(runId); ui.exportConfirmed.delete(runId); toast(`已导入 ${success} 份材料，原始文件已保存在本机。`, 'success'); }
      if (failed.length) ui.failedUpload = {runId, files: failed, messages};
    } finally {
      ui.upload = null; $('#materialPicker').value = ''; render();
    }
  }
  async function removeOrder(orderId) {
    const run = runById(); if (!run || runBusy(run) || ui.upload) return;
    orderId = String(orderId || '');
    const row = groupForOrder(run, orderId) || productionFor(run).find(row => String(row.order_id) === orderId);
    if (!orderId || !row || !ids(run.order_ids).includes(orderId)) throw new Error('这笔采购已更新，请刷新后重试。');
    const revision = run.revision ?? 0;
    if (!await confirmAction('排除这笔采购？', `<p>${escapeHTML(row.title || '本笔采购')}</p><p>整笔采购将从本次制作与导出中排除。电脑上的原始文件和已经保存的 PDF 不会删除。</p>`, '确认排除此笔采购')) return;
    const current = runById(run.id);
    if (!current || runBusy(current) || ui.upload) throw new Error('本次报销正在处理，请等待完成后再排除采购。');
    if ((current.revision ?? 0) !== revision || !ids(current.order_ids).includes(orderId)) throw new Error('本次报销已更新，请重新核对后再排除采购。');
    await locked('run:' + run.id, async () => {
      let result;
      try { result = await api('/api/flow/remove-order', {run_id: run.id, order_id: orderId, revision}); }
      catch (error) {
        if (error.status === 409 || /revision|版本|已更新|已变化|已变更|发生变化|冲突/i.test(errorMessage(error))) {
          try { await refresh(); }
          catch { throw new Error('本次报销已更新，暂时无法读取最新记录。请重新连接后再核对采购。'); }
          throw new Error('本次报销已更新，已重新读取最新记录。请核对后再次排除采购。');
        }
        throw error;
      }
      const updated = rememberRun(result);
      if (!updated || String(updated.id) !== String(run.id)) throw new Error('未收到更新后的报销记录，请刷新确认后再操作。');
      state.flow.runs = state.flow.runs.map(row => String(row.id) === String(updated.id) ? updated : row);
      ui.force.delete(String(run.id)); ui.exportConfirmed.delete(String(run.id)); ui.errors.delete(String(run.id)); ui.steps.delete(String(run.id));
      ui.previewCache.delete(orderId); ui.layoutDrafts.delete(orderId); ui.reflowOrders.delete(String(run.id));
      if (batchView(updated).group === orderId) batchView(updated).group = 'all';
      if (ui.galleryRunId === String(run.id) && ui.gallery.some(item => String(item.orderId || item.groupId) === orderId)) closeDialog('mediaDialog');
      toast('已从本次制作与导出中排除这笔采购。', 'success');
    });
  }
  async function removeMaterial(id) {
    const run = runById(); if (!run || runBusy(run) || ui.upload) return;
    const material = state.materials[id];
    if (!await confirmAction('移除这份材料？', `<p>${escapeHTML(material?.name || '这份材料')}</p><p>只从本次报销中移除，不会删除你电脑上的原始文件。</p>`, '移除材料')) return;
    await locked('run:' + run.id, async () => { rememberRun(await api('/api/flow/remove', {run_id: run.id, material_id: id})); ui.force.delete(String(run.id)); ui.exportConfirmed.delete(String(run.id)); await refresh(); });
  }

  function openDialog(id) { const dialog = document.getElementById(id); if (dialog && !dialog.open) dialog.showModal(); }
  function closeDialog(id) {
    const dialog = document.getElementById(id); if (dialog?.open) dialog.close();
    if (id === 'settingsDialog') { $('#settingKey').value = ''; ui.settingsIntent = null; }
    if (id === 'mediaDialog') { $('#mediaStage').replaceChildren(); ui.gallery = []; ui.galleryAll = []; ui.galleryVersion++; ui.pan = null; }
  }
  function openSettings(intent = null) {
    const settings = state.settings;
    ui.settingsIntent = intent;
    $('#settingName').value = settings.name || '';
    $('#settingAdvisor').value = settings.advisor || '';
    $('#settingProject').value = settings.project_name || '';
    $('#settingOutput').value = settings.output_dir || '';
    $('#settingBuyer').value = settings.buyer_name || '';
    $('#settingTaxId').value = settings.taxpayer_id || '';
    $('#settingRequiredAddress').value = settings.required_address || '';
    $('#settingAddressKeywords').value = settings.address_keywords || '';
    $('#settingOrgAddress').value = settings.org_address || '';
    $('#settingOrgPhone').value = settings.org_phone || '';
    $('#settingBankName').value = settings.bank_name || '';
    $('#settingBankAccount').value = settings.bank_account || '';
    $('#settingApiBase').value = settings.ai_api_base || defaultBase;
    $('#settingModel').value = settings.ai_vision_model || settings.ai_model || defaultModel;
    $('#settingKey').value = ''; $('#settingKey').required = !settings.has_api_key;
    $('#settingKey').placeholder = settings.has_api_key ? '已保存，留空保留现有密钥' : '填写 API Key';
    $('#keyHelp').textContent = settings.has_api_key ? '密钥已加密保存在本机，不会回显；留空将保留已有密钥。' : '密钥加密保存在本机，不会回显，也不会加入导出文件。';
    const missing = missingSettings();
    $('#settingsIntro').textContent = intent && missing.length ? '开始前还需补充：' + missing.join('、') + '。配置齐全后将直接进入收集。' : '资料、购买方信息和密钥保存在本机。已有密钥不会回显。';
    $('#settingsTitle').textContent = intent ? '准备好，就可以开始' : '设置';
    $('#saveSettingsButton').innerHTML = (intent ? '保存并继续' : '保存设置') + icon('check');
    $('#settingsError').hidden = true; ui.testResult = null; ui.testJobId = null;
    renderTestResult(); openDialog('settingsDialog');
  }
  function settingsData() {
    const model = $('#settingModel').value.trim();
    const data = {
      name: $('#settingName').value.trim(),
      advisor: $('#settingAdvisor').value.trim(),
      project_name: $('#settingProject').value.trim(),
      output_dir: $('#settingOutput').value.trim(),
      buyer_name: $('#settingBuyer').value.trim(),
      taxpayer_id: $('#settingTaxId').value.trim(),
      required_address: $('#settingRequiredAddress').value.trim(),
      address_keywords: $('#settingAddressKeywords').value.trim(),
      org_address: $('#settingOrgAddress').value.trim(),
      org_phone: $('#settingOrgPhone').value.trim(),
      bank_name: $('#settingBankName').value.trim(),
      bank_account: $('#settingBankAccount').value.trim(),
      ai_api_base: $('#settingApiBase').value.trim(),
      ai_vision_model: model,
      ai_model: model,
    };
    const key = $('#settingKey').value.trim();
    if (key) data.ai_api_key = key;
    return data;
  }
  function settingsError(error) { $('#settingsError').hidden = false; $('#settingsError').textContent = errorMessage(error); }
  async function saveSettings(event) {
    event.preventDefault();
    if (!$('#settingsForm').reportValidity() || ui.locks.has('settings')) return;
    const intent = ui.settingsIntent;
    ui.locks.add('settings'); renderTestResult(); $('#settingsError').hidden = true;
    try {
      await api('/api/settings', settingsData());
      $('#settingKey').value = '';
      await refresh();
      closeDialog('settingsDialog'); toast('设置已保存在本机。', 'success');
    } catch (error) { settingsError(error); return; }
    finally { ui.locks.delete('settings'); renderTestResult(); }
    if (intent === 'start') await safe(start, 'start');
    else if (intent === 'review') await safe(review, 'review');
  }
  async function testAPI() {
    if (ui.locks.has('test') || allJobs().some(job => String(job.id) === ui.testJobId && activeStatuses.has(job.status))) return;
    const base = $('#settingApiBase'), model = $('#settingModel'), key = $('#settingKey');
    if (!base.reportValidity() || !model.reportValidity()) return;
    if (!key.value.trim() && !state.settings.has_api_key) { key.reportValidity(); key.focus(); return; }
    ui.locks.add('test'); ui.testResult = null; renderTestResult();
    try {
      const data = {ai_api_base: base.value.trim(), ai_vision_model: model.value.trim()};
      if (key.value.trim()) data.ai_api_key = key.value.trim();
      const result = await api('/api/ai/test', data);
      if (typeof result?.connected === 'boolean') ui.testResult = result;
      else { const job = trackJob(result, null, 'api-test'); ui.testJobId = String(job.id); }
      await refresh();
    } catch (error) { ui.testResult = {connected: false, message: errorMessage(error)}; }
    finally { ui.locks.delete('test'); renderTestResult(); schedulePoll(); }
  }
  function renderTestResult() {
    const job = allJobs().find(job => String(job.id) === ui.testJobId);
    const busy = ui.locks.has('test') || !!job && activeStatuses.has(job.status);
    const button = $('#testApiButton');
    if (!button) return;
    button.disabled = busy;
    button.innerHTML = busy ? '<span class="spinner"></span>正在测试连接与图像…' : icon('spark') + '测试连接与看图能力';
    $('#saveSettingsButton').disabled = busy || ui.locks.has('settings');
    if (busy) $('#apiTestResult').innerHTML = '<p class="test-pending" role="status">正在验证 API 与图像输入，请稍候…</p>';
    else if (ui.testResult) {
      const result = ui.testResult;
      const success = result.connected === true && result.vision_supported === true;
      const headline = success ? '连接成功，支持图像' : result.connected === true ? '连接成功，但图像能力尚未通过验证' : '连接未成功';
      $('#apiTestResult').innerHTML = notice(`<strong>${headline}</strong><p>${escapeHTML(result.message || (success ? '可以保存并开始。' : '请核对 API 地址、密钥和图像模型 ID，然后重试。'))}</p>${result.model ? '<span class="tiny">模型：' + escapeHTML(result.model) + '</span>' : ''}`, success ? 'success' : 'error');
    } else $('#apiTestResult').replaceChildren();
  }

  async function updateLayout(form) {
    if (!form.reportValidity()) return;
    const fields = new FormData(form);
    const layout = {layout_adaptive: true, layout_column_ratio: Number(fields.get('layout_column_ratio')) / 100, layout_vertical_align: fields.get('layout_vertical_align'), layout_order_on_left: fields.get('layout_order_on_left') === 'true', invoice_layout_mode: 'print'};
    for (const key of ['left', 'right', 'top', 'bottom']) layout['invoice_' + key + '_mm'] = Number(fields.get('invoice_' + key + '_mm'));
    await reflowOrder(form.dataset.layoutOrder, layout);
  }
  async function reflowOrder(orderId, requestedLayout) {
    const run = runById();
    if (!run || !orderId || runBusy(run)) return;
    const row = productionFor(run).find(row => String(row.order_id) === String(orderId));
    if (!row) throw new Error('未找到这笔采购，请刷新后重试。');
    const layout = requestedLayout || ui.layoutDrafts.get(String(orderId));
    if (!layout) throw new Error('请先展开调整版式，设置后点击更新预览。');
    ui.reflowOrders.set(String(run.id), String(orderId));
    ui.layoutDrafts.set(String(orderId), layout);
    ui.exportConfirmed.delete(String(run.id));
    await locked('run:' + run.id, async () => {
      const result = await api('/api/flow/reflow', {run_id: run.id, order_id: orderId, layout});
      trackJob(result, run, 'flow-reflow');
      ui.previewCache.delete(String(orderId)); ui.steps.delete(String(run.id)); ui.errors.delete(String(run.id));
      await refresh();
    });
  }

  function materialPageKey(material) { return String(material.id) + ':' + (material.revision || 0); }
  function materialGalleryEntries(material, group) {
    const cached = ui.materialPages.get(materialPageKey(material));
    const pages = normalizePages(cached?.pages || material.preview_pages, material.url || material.thumb_url);
    const total = Math.max(pages.length, Number(material.pages) || 1);
    const download = localURL(cached?.original_url || material.original_url) || `/media/${encodeURIComponent(material.id)}/original`;
    return pages.map(page => ({id: `original:${material.id}:${page.page}`, materialId: String(material.id), kind: 'original', original: true, groupId: group.id, groupTitle: group.title,
      title: (material.name || '原始材料') + (material.is_pdf ? ` · 第 ${page.page}/${total} 页` : ''),
      subtitle: `${group.title} · 原始材料${material.is_pdf ? ' · PDF 全部页面与原件均保留' : ''}`,
      url: page.url, thumb: page.thumb || localURL(material.thumb_url), download, page: page.page, totalPages: total}));
  }
  function productionGalleryEntries(row, group) {
    const previews = previewData(row), result = [];
    for (const [kind, title, pages, download] of [['voucher', '购买及支付凭证', previews.voucherPages, previews.voucherPDF], ['invoice', '发票打印版', previews.invoicePages, previews.invoicePDF]]) {
      for (const page of pages) result.push({id: `${kind}:${row.order_id}:${page.page}`, orderId: String(row.order_id), kind, original: false, groupId: group.id, groupTitle: group.title,
        title: `${row.title || group.title} · ${title}${pages.length > 1 ? ' · 第 ' + page.page + '/' + pages.length + ' 页' : ''}`, subtitle: `${group.title} · ${title} · 原件可从同一缩略条切换`,
        url: page.url, thumb: page.thumb, download: download || page.url, page: page.page, totalPages: pages.length});
    }
    return result;
  }
  function buildBatchGallery(run) {
    if (!run) return [];
    const groups = asArray(run.review?.groups), production = productionFor(run), result = [], seenMaterials = new Set(), seenProduction = new Set();
    groups.forEach((row, index) => {
      const group = {id: groupKey(row, index), title: `${String(index + 1).padStart(2, '0')} · ${row.title || '采购 ' + (index + 1)}`};
      for (const id of ids(row.material_ids)) if (state.materials[id] && !seenMaterials.has(id)) { result.push(...materialGalleryEntries(state.materials[id], group)); seenMaterials.add(id); }
      const product = production.find(item => String(item.order_id) === String(row.order_id));
      if (product) { result.push(...productionGalleryEntries(product, group)); seenProduction.add(String(product.order_id)); }
    });
    production.forEach((row, index) => {
      if (!seenProduction.has(String(row.order_id))) result.push(...productionGalleryEntries(row, {id: groupKey(row, index), title: `${String(index + 1).padStart(2, '0')} · ${row.title || '采购 ' + (index + 1)}`}));
    });
    const trash = new Set(ids(run.review?.trash));
    for (const material of materialsFor(run)) if (!seenMaterials.has(String(material.id))) result.push(...materialGalleryEntries(material, {id: trash.has(String(material.id)) ? 'ignored' : 'unassigned', title: trash.has(String(material.id)) ? '已忽略材料' : '未归组材料'}));
    return result;
  }
  function rebuildGallery(preferredId) {
    ui.galleryAll = buildBatchGallery(runById(ui.galleryRunId));
    ui.gallery = ui.galleryAll.filter(item => ui.galleryFilter === 'all' || item.kind === ui.galleryFilter);
    ui.galleryIndex = Math.max(0, ui.gallery.findIndex(item => item.id === preferredId));
  }
  function startGallery(run, match) {
    ui.galleryVersion++; ui.galleryRunId = String(run.id); ui.galleryFilter = 'all';
    rebuildGallery();
    const index = ui.gallery.findIndex(match);
    if (index < 0) throw new Error('预览还没有准备好，请先制作或更新预览。');
    ui.galleryIndex = index; ui.zoomMode = 'fit'; ui.zoom = 1;
    openDialog('mediaDialog'); renderGallery();
    $('#mediaStage').focus({preventScroll: true});
  }
  function openMaterials(id) {
    const run = runById(); if (!run) return;
    startGallery(run, item => item.materialId === String(id));
  }
  function openProduction(orderId, kind, page = 1) {
    const run = runById(); if (!run) return;
    startGallery(run, item => item.orderId === String(orderId) && item.kind === kind && item.page === Number(page));
  }
  async function loadGalleryPages(item) {
    if (!item?.materialId) return;
    const material = state.materials[item.materialId];
    if (!material?.is_pdf || Array.isArray(material.preview_pages) && material.preview_pages.length >= Number(material.pages || 1)) return;
    const key = materialPageKey(material);
    if (ui.materialPages.has(key)) return;
    const version = ui.galleryVersion;
    try {
      if (!ui.pageRequests.has(key)) ui.pageRequests.set(key, api('/api/material-pages?id=' + encodeURIComponent(material.id)).then(result => { ui.materialPages.set(key, result); return result; }).finally(() => ui.pageRequests.delete(key)));
      await ui.pageRequests.get(key);
      if (version !== ui.galleryVersion || !$('#mediaDialog').open) return;
      const currentId = ui.gallery[ui.galleryIndex]?.id;
      const currentMaterial = ui.gallery[ui.galleryIndex]?.materialId;
      rebuildGallery(currentId);
      if (currentMaterial === item.materialId) renderGallery();
      else renderGalleryNavigation();
    } catch (error) {
      if (version === ui.galleryVersion && $('#mediaDialog').open && ui.gallery[ui.galleryIndex]?.materialId === item.materialId) $('#mediaSubtitle').textContent = item.subtitle + ' · 全部页面暂未加载，请重试或查看本机原始文件';
    }
  }
  function renderGalleryNavigation() {
    const item = ui.gallery[ui.galleryIndex]; if (!item) return;
    $('#mediaCounter').textContent = `${ui.galleryIndex + 1} / ${ui.gallery.length}`;
    $('#mediaPrevious').disabled = ui.galleryIndex === 0; $('#mediaNext').disabled = ui.galleryIndex >= ui.gallery.length - 1;
    const mediaDownload = $('#mediaDownload'); if (mediaDownload) mediaDownload.hidden = true;
    const groups = [...new Map(ui.gallery.map(entry => [entry.groupId, entry.groupTitle])).entries()];
    $('#mediaGroup').innerHTML = groups.map(([id, title]) => `<option value="${escapeHTML(id)}" ${id === item.groupId ? 'selected' : ''}>${escapeHTML(title)}</option>`).join('');
    $('#mediaKind').value = ui.galleryFilter;
    for (const option of $('#mediaKind').options) option.disabled = option.value !== 'all' && !ui.galleryAll.some(entry => entry.kind === option.value);
    const strip = $('#mediaFilmstrip'), scroll = strip.scrollLeft;
    strip.innerHTML = ui.gallery.map((entry, index) => `<button class="filmstrip-item ${index === ui.galleryIndex ? 'selected' : ''} ${index === 0 || ui.gallery[index - 1].groupId !== entry.groupId ? 'group-start' : ''}" data-action="media-select" data-index="${index}" aria-label="${escapeHTML(entry.groupTitle + '，' + entry.title)}" title="${escapeHTML(entry.groupTitle + ' · ' + entry.title)}" ${index === ui.galleryIndex ? 'aria-current="true"' : ''}>${(entry.thumb || entry.url) && !isPDF(entry.thumb || entry.url) ? `<img src="${escapeHTML(entry.thumb || entry.url)}" alt="" loading="lazy">` : icon('file')}<span>${escapeHTML(entry.groupTitle.split(' · ')[0])} · ${entry.original ? '原件' : entry.kind === 'invoice' ? '发票' : '凭证'}${entry.totalPages > 1 ? ' ' + entry.page : ''}</span></button>`).join('');
    strip.scrollLeft = scroll;
    const selected = $('.selected', strip);
    if (selected && (selected.offsetLeft < strip.scrollLeft || selected.offsetLeft + selected.offsetWidth > strip.scrollLeft + strip.clientWidth)) strip.scrollLeft = Math.max(0, selected.offsetLeft - strip.clientWidth / 2 + selected.offsetWidth / 2);
  }
  function renderGallery() {
    const item = ui.gallery[ui.galleryIndex]; if (!item) return;
    $('#mediaTitle').textContent = item.title;
    $('#mediaSubtitle').textContent = item.subtitle + (isPDF(item.url) ? ' · 可在 PDF 内滚动查看' : ' · 拖动或滚动查看，← → 切换，Esc 关闭');
    ui.pan = null;
    const stage = $('#mediaStage');
    stage.classList.remove('is-dragging');
    stage.innerHTML = item.url ? isPDF(item.url) ? `<iframe title="${escapeHTML(item.title)}" src="${escapeHTML(item.url)}"></iframe>` : `<div class="media-canvas"><img id="mediaImage" src="${escapeHTML(item.url)}" alt="${escapeHTML(item.title)}" draggable="false"></div>` : '<div class="empty-state">' + icon('image') + '<p>当前没有预览，请查看本机保存的原始文件。</p></div>';
    stage.scrollLeft = 0; stage.scrollTop = 0;
    const image = $('#mediaImage');
    for (const control of $$('.media-zoom-controls button')) control.disabled = !image;
    if (image) {
      const ready = () => { if ($('#mediaImage') === image) applyGalleryZoom(false); };
      image.addEventListener('load', ready, {once: true});
      if (image.complete && image.naturalWidth) ready();
    } else $('#mediaZoomValue').textContent = '—';
    renderGalleryNavigation();
    loadGalleryPages(item);
  }
  function applyGalleryZoom(preserveCenter = true) {
    const image = $('#mediaImage'), stage = $('#mediaStage');
    if (!image?.naturalWidth || !stage.clientWidth) return;
    const centerX = (stage.scrollLeft + stage.clientWidth / 2) / Math.max(1, stage.scrollWidth);
    const centerY = (stage.scrollTop + stage.clientHeight / 2) / Math.max(1, stage.scrollHeight);
    if (ui.zoomMode === 'fit') ui.zoom = Math.min(1, Math.max(0.01, (stage.clientWidth - 40) / image.naturalWidth), Math.max(0.01, (stage.clientHeight - 40) / image.naturalHeight));
    else if (ui.zoomMode === 'original') ui.zoom = 1;
    image.style.width = Math.max(1, Math.round(image.naturalWidth * ui.zoom)) + 'px';
    image.style.height = Math.max(1, Math.round(image.naturalHeight * ui.zoom)) + 'px';
    $('#mediaZoomValue').textContent = Math.round(ui.zoom * 100) + '%';
    $('#mediaFit').setAttribute('aria-pressed', String(ui.zoomMode === 'fit'));
    $('#mediaOriginal').setAttribute('aria-pressed', String(ui.zoomMode === 'original'));
    $('#mediaZoomOut').disabled = ui.zoom <= 0.02; $('#mediaZoomIn').disabled = ui.zoom >= 8;
    if (preserveCenter) { stage.scrollLeft = centerX * stage.scrollWidth - stage.clientWidth / 2; stage.scrollTop = centerY * stage.scrollHeight - stage.clientHeight / 2; }
    else { stage.scrollLeft = 0; stage.scrollTop = 0; }
  }
  function setGalleryZoom(mode, factor) {
    if (!$('#mediaImage')) return;
    ui.zoomMode = mode;
    if (mode === 'custom') ui.zoom = Math.max(0.02, Math.min(8, ui.zoom * factor));
    applyGalleryZoom(mode === 'custom');
  }
  function moveGallery(index) {
    const next = Math.max(0, Math.min(ui.gallery.length - 1, index));
    if (next === ui.galleryIndex) return;
    ui.galleryIndex = next; ui.zoomMode = 'fit'; renderGallery();
  }
  function filterGallery(kind) {
    const current = ui.gallery[ui.galleryIndex];
    ui.galleryFilter = ['original', 'voucher', 'invoice'].includes(kind) ? kind : 'all';
    rebuildGallery(current?.id);
    const sameGroup = ui.gallery.findIndex(item => item.groupId === current?.groupId);
    if (!ui.gallery.some(item => item.id === current?.id) && sameGroup >= 0) ui.galleryIndex = sameGroup;
    ui.zoomMode = 'fit'; renderGallery();
  }

  async function handleClick(event) {
    const dateInput = event.target.closest('input[type="date"]');
    if (dateInput && !dateInput.disabled && typeof dateInput.showPicker === 'function') {
      try { dateInput.showPicker(); event.preventDefault?.(); } catch { /* Native calendar button remains available. */ }
      return;
    }
    const close = event.target.closest('[data-close]');
    if (close) { closeDialog(close.dataset.close); return; }
    const button = event.target.closest('[data-action]');
    if (!button || button.disabled) return;
    const action = button.dataset.action;
    if (action === 'confirm-yes' || action === 'confirm-no') { resolveConfirm(action === 'confirm-yes'); return; }
    if (action === 'media-previous') { moveGallery(ui.galleryIndex - 1); return; }
    if (action === 'media-next') { moveGallery(ui.galleryIndex + 1); return; }
    if (action === 'media-select') { moveGallery(Number(button.dataset.index)); return; }
    if (action === 'media-fit') { setGalleryZoom('fit'); return; }
    if (action === 'media-original') { setGalleryZoom('original'); return; }
    if (action === 'media-zoom-in' || action === 'media-zoom-out') { setGalleryZoom('custom', action === 'media-zoom-in' ? 1.25 : 0.8); return; }
    await safe(async () => {
      switch (action) {
        case 'home': await home(); break;
        case 'history': navigate('history'); break;
        case 'start': await start(); break;
        case 'settings': openSettings(); break;
        case 'test-api': await testAPI(); break;
        case 'refresh': await refresh(); break;
        case 'clear-error': ui.globalError = ''; render(); break;
        case 'open-run': await openRun(button.dataset.runId); break;
        case 'delete-run': await deleteRun(button.dataset.runId); break;
        case 'pick': if (!ui.upload && !runBusy(runById())) $('#materialPicker').click(); break;
        case 'retry-upload': if (ui.failedUpload) await importFiles(ui.failedUpload.files); break;
        case 'material-preview': openMaterials(button.dataset.materialId); break;
        case 'remove': await removeMaterial(button.dataset.materialId); break;
        case 'remove-order': await removeOrder(button.dataset.orderId); break;
        case 'review': await review(); break;
        case 'save-profile': await saveProfile(); break;
        case 'continue-review': await continueReview(); break;
        case 'make': await make(button.dataset.orderId); break;
        case 'reflow': await reflowOrder(button.dataset.orderId); break;
        case 'export': await exportRun(); break;
        case 'back-collect': if (!runBusy(runById())) { ui.steps.set(String(ui.runId), 'collect'); render(); window.scrollTo({top: 0, behavior: 'smooth'}); } break;
        case 'back-review': if (!runBusy(runById())) { ui.steps.set(String(ui.runId), 'review'); render(); window.scrollTo({top: 0, behavior: 'smooth'}); } break;
        case 'production-preview': openProduction(button.dataset.orderId, button.dataset.kind, button.dataset.page || 1); break;
        case 'batch-layout': batchView(runById()).layout = button.dataset.layout === 'list' ? 'list' : 'grid'; render(); break;
        case 'batch-clear': Object.assign(batchView(runById()), {query: '', group: 'all', onlyProblems: false}); render(); break;
        case 'batch-show-problems': Object.assign(batchView(runById()), {query: '', group: 'all', onlyProblems: true}); render(); break;
        case 'open-output': await api('/api/open-output', {export_id: button.dataset.exportId}); toast('已打开本机文件夹。', 'success'); break;
      }
    }, action === 'test-api' ? 'test' : action);
  }
  function attachEvents() {
    document.addEventListener('click', event => { handleClick(event).catch(error => showError(error)); });
    document.addEventListener('submit', event => {
      if (event.target.id === 'settingsForm') { saveSettings(event).catch(settingsError); return; }
      if (event.target.id === 'profileForm') { event.preventDefault(); safe(saveProfile, 'profile'); return; }
      if (event.target.matches('[data-layout-order]')) { event.preventDefault(); safe(() => updateLayout(event.target), 'layout'); }
    });
    document.addEventListener('input', event => {
      const target = event.target;
      if (target.matches('[data-batch-field]') && ui.runId) {
        batchView(runById())[target.dataset.batchField] = target.type === 'checkbox' ? target.checked : target.value;
        render(); return;
      }
      if (target.matches('[data-profile-field]') && ui.runId) {
        const draft = ui.profileDrafts.get(String(ui.runId)) || {};
        draft[target.dataset.profileField] = target.dataset.profileField === 'declare_date' ? target.value.replaceAll('-', '') : target.value;
        ui.profileDrafts.set(String(ui.runId), draft); ui.dirtyProfiles.add(String(ui.runId));
        if ($('#profileSaveState')) $('#profileSaveState').textContent = '下一步会保存';
      }
      if (target.matches('[data-export-confirmed]')) {
        target.checked ? ui.exportConfirmed.add(String(ui.runId)) : ui.exportConfirmed.delete(String(ui.runId));
        if ($('#exportButton')) $('#exportButton').disabled = !target.checked || runBusy(runById());
      }
      const layoutForm = target.closest('[data-layout-order]');
      if (layoutForm) {
        const orderId = layoutForm.dataset.layoutOrder;
        const draft = ui.layoutDrafts.get(String(orderId)) || {};
        draft[target.name] = target.name === 'layout_column_ratio' ? Number(target.value) / 100 : target.name === 'layout_order_on_left' ? target.value === 'true' : target.type === 'number' ? Number(target.value) : target.value;
        ui.layoutDrafts.set(String(orderId), draft);
        if (target.type === 'range') document.getElementById('ratio-output-' + orderId).textContent = target.value + '%';
      }
    });
    $('#materialPicker').addEventListener('change', event => { safe(() => importFiles(event.target.files), 'upload'); });
    if (window.chrome?.webview) window.chrome.webview.addEventListener('message', event => {
      const data = event.data;
      if (data?.type !== 'desktop-files' || !Array.isArray(data.files)) return;
      const run = runById();
      if (ui.view !== 'run' || !run || !$('[data-dropzone]') || ui.upload || runBusy(run)) {
        toast('请先打开本批收集页面，再拖入材料。'); return;
      }
      safe(() => {
        const files = data.files.map(item => {
          const bytes = Uint8Array.from(atob(item.data), value => value.charCodeAt(0));
          return new File([bytes], item.name);
        });
        return importFiles(files);
      }, 'upload');
    });
    // Capture every external drop before Chromium can navigate to a file URL.
    // Some Windows apps omit the 'Files' type until the actual drop event.
    window.addEventListener('dragover', event => {
      event.preventDefault();
      const drop = event.target.closest?.('[data-dropzone]') || $('[data-dropzone]');
      if (event.dataTransfer) event.dataTransfer.dropEffect = drop && !drop.disabled ? 'copy' : 'none';
      if (drop && !drop.disabled) drop.classList.add('drag-over');
    }, {capture: true, passive: false});
    document.addEventListener('dragleave', event => { const drop = event.target.closest?.('[data-dropzone]'); if (drop && !drop.contains(event.relatedTarget)) drop.classList.remove('drag-over'); });
    window.addEventListener('drop', event => {
      event.preventDefault(); event.stopPropagation();
      $$('.drag-over').forEach(element => element.classList.remove('drag-over'));
      const transfer = event.dataTransfer;
      const files = transfer?.files?.length ? [...transfer.files] : [...(transfer?.items || [])].map(item => item.getAsFile?.()).filter(Boolean);
      if (!files.length) return;
      const run = runById();
      const drop = event.target.closest?.('[data-dropzone]') || $('[data-dropzone]');
      if (ui.view === 'run' && run && drop && !drop.disabled && !ui.upload && !runBusy(run) && !$('#settingsDialog').open && !$('#mediaDialog').open && !$('#confirmDialog').open) {
        safe(() => importFiles(files), 'upload');
      }
    }, {capture: true, passive: false});
    document.addEventListener('dragstart', event => { if (event.target.tagName === 'IMG') event.preventDefault(); });
    document.addEventListener('error', event => {
      const image = event.target;
      if (image.tagName !== 'IMG') return;
      image.hidden = true;
      if (image.parentElement && !$('.image-load-error', image.parentElement)) {
        const fallback = document.createElement('span'); fallback.className = 'image-load-error'; fallback.textContent = '预览暂时不可用，可下载原文件查看'; image.parentElement.append(fallback);
      }
    }, true);
    $('#confirmDialog').addEventListener('cancel', event => { event.preventDefault(); resolveConfirm(false); });
    $('#confirmDialog').addEventListener('close', () => { if (confirmResolver) resolveConfirm(false); });
    $('#settingsDialog').addEventListener('close', () => { $('#settingKey').value = ''; ui.settingsIntent = null; });
    $('#mediaDialog').addEventListener('close', () => { $('#mediaStage').replaceChildren(); ui.gallery = []; ui.galleryAll = []; ui.galleryVersion++; ui.pan = null; });
    $('#mediaKind').addEventListener('change', event => filterGallery(event.target.value));
    $('#mediaGroup').addEventListener('change', event => { const index = ui.gallery.findIndex(item => item.groupId === event.target.value); if (index >= 0) moveGallery(index); });
    const mediaStage = $('#mediaStage');
    mediaStage.addEventListener('pointerdown', event => {
      if (event.button !== 0 || !$('#mediaImage') || event.target.closest('button,a,input,select')) return;
      ui.pan = {id: event.pointerId, x: event.clientX, y: event.clientY, left: mediaStage.scrollLeft, top: mediaStage.scrollTop};
      mediaStage.setPointerCapture(event.pointerId); mediaStage.classList.add('is-dragging'); event.preventDefault();
    });
    mediaStage.addEventListener('pointermove', event => {
      if (ui.pan?.id !== event.pointerId) return;
      mediaStage.scrollLeft = ui.pan.left - event.clientX + ui.pan.x;
      mediaStage.scrollTop = ui.pan.top - event.clientY + ui.pan.y;
    });
    const endPan = event => { if (ui.pan?.id === event.pointerId) { ui.pan = null; mediaStage.classList.remove('is-dragging'); } };
    mediaStage.addEventListener('pointerup', endPan); mediaStage.addEventListener('pointercancel', endPan); mediaStage.addEventListener('lostpointercapture', endPan);
    mediaStage.addEventListener('wheel', event => { if ((event.ctrlKey || event.metaKey) && $('#mediaImage')) { event.preventDefault(); setGalleryZoom('custom', event.deltaY < 0 ? 1.15 : 1 / 1.15); } }, {passive: false});
    document.addEventListener('keydown', event => {
      if (!$('#mediaDialog').open) return;
      if (event.key === 'Escape') { event.preventDefault(); closeDialog('mediaDialog'); return; }
      if (/INPUT|SELECT/.test(event.target.tagName)) return;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); moveGallery(ui.galleryIndex + (event.key === 'ArrowLeft' ? -1 : 1)); }
      else if (['+', '=', '-', '0', '1'].includes(event.key)) { event.preventDefault(); setGalleryZoom(event.key === '0' ? 'fit' : event.key === '1' ? 'original' : 'custom', event.key === '-' ? 0.8 : 1.25); }
    });
    window.addEventListener('resize', () => { if ($('#mediaDialog').open && ui.zoomMode === 'fit') applyGalleryZoom(false); });
    window.addEventListener('hashchange', () => { routeFromHash(); render(); });
    window.addEventListener('focus', () => { if (initialized && !fetching) refresh().catch(() => {}); });
  }

  async function main() {
    attachEvents();
    try { await refresh(); } catch { /* The inline reconnect action remains available. */ }
    window.appReady = initialized;
  }
  main();
})();
