/* Native Node regression tests. No npm dependencies, real materials, or model calls.
 * Run: node tests/test_wizard_ui_v21.js
 * Visual fixture: node tests/test_wizard_ui_v21.js --serve [port]
 * Open /?scenario=ready#run/r1 (review, issue, running, trash, collect also supported).
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const assets = Object.fromEntries(['index.html', 'wizard.js', 'wizard.css'].map(name => [name, fs.readFileSync(path.join(root, 'app', 'web', name), 'utf8')]));
const clone = value => JSON.parse(JSON.stringify(value));

function fixtureState(scenario = 'ready') {
  const materials = {}, groups = [], production = [], orderIds = [];
  for (let g = 1; g <= 4; g++) {
    const ids = [];
    for (const [role, count] of [['invoice', 2], ['order', 3], ['payment', 2]]) {
      for (let n = 1; n <= count; n++) {
        const id = `g${g}-${role}-${n}`;
        ids.push(id);
        materials[id] = {id, name: `${['', '实验耗材', '设备配件', '办公用品', '数据线'][g]}-${role}-${n}${role === 'invoice' && n === 1 ? '.pdf' : '.png'}`, category: role,
          url: `/fixture/image/${id}/1.svg`, thumb_url: `/fixture/image/${id}/1.svg`, original_url: `/fixture/original/${id}.pdf`,
          is_pdf: role === 'invoice' && n === 1, pages: role === 'invoice' && n === 1 ? 3 : 1, width: 1000, height: role === 'order' ? 2400 : 1400, revision: 0};
      }
    }
    const oid = `o${g}`, title = `采购 ${g} · ${['', '实验耗材', '设备配件', '办公用品', '数据线'][g]}`;
    orderIds.push(oid);
    groups.push({key: `g${g}`, order_id: oid, title, material_ids: ids, checks: [{code: 'amount', status: 'pass', message: '金额一致'}, {code: 'completeness', status: 'pass', message: '材料齐全'}]});
    production.push({order_id: oid, title, status: 'ready', preview_url: `/fixture/image/${oid}-voucher/1.svg`, invoice_url: `/fixture/image/${oid}-invoice/1.svg`,
      voucher_pages: [{url: `/fixture/image/${oid}-voucher/1.svg`, page: 1, total_pages: 1, kind: 'voucher'}],
      invoice_pages: [1, 2, 3].map(page => ({url: `/fixture/image/${oid}-invoice/${page}.svg`, page, total_pages: 3, kind: 'invoice'})),
      voucher_pdf_url: `/fixture/original/${oid}-voucher.pdf`, invoice_pdf_url: `/fixture/original/${oid}-invoice.pdf`, warnings: [], layout: {layout_column_ratio: .5}});
  }
  const run = {id: 'r1', stage: scenario === 'collect' ? 'collect' : ['review', 'issue', 'running', 'trash'].includes(scenario) ? 'review' : 'ready', status: scenario === 'running' ? 'running' : 'idle',
    created: '2026-11-09T12:00:00', profile: {name: '测试姓名', advisor: '测试导师', project_name: '批量报销预览', declare_date: '20261109'},
    revision: 7, material_ids: Object.keys(materials), order_ids: orderIds, review: {groups, issues: [], unassigned: [], trash: [], warnings: []}, production,
    session_summary: {total: 28, processed: 28, batches: 4, turns: 4}, exports: []};
  if (['issue', 'running'].includes(scenario)) {
    run.review.issues.push({code: 'missing_payment', group_key: 'g3', level: 'warning', message: '支付截图不完整，请补交能看到付款结果的页面。'});
    run.review.groups[2].checks.push({code: 'payment', status: 'review', message: '付款结果尚未确认。'});
    const removed = run.review.groups[2].material_ids.filter(id => id.includes('payment'));
    run.review.groups[2].material_ids = run.review.groups[2].material_ids.filter(id => !removed.includes(id));
    run.review.unassigned.push(...removed);
  }
  if (scenario === 'trash') { run.review.trash = [...run.material_ids]; run.review.groups = []; run.order_ids = []; run.production = []; }
  if (scenario === 'collect') { run.review.groups = []; run.review.unassigned = [...run.material_ids]; run.production = []; run.order_ids = []; }
  if (scenario === 'review') run.production = [];
  return {settings: {name: '测试姓名', advisor: '测试导师', project_name: '批量报销预览', output_dir: 'C:\\synthetic-output', ai_api_base: 'https://example.invalid/v1', ai_vision_model: 'synthetic-vision', has_api_key: true},
    materials, orders: groups.map(group => ({id: group.order_id, title: group.title, material_ids: group.material_ids})), jobs: [], exports: [], flow: {runs: [run], active_run_id: 'r1'}};
}

function harness(initial) {
  const nodes = new Map(), requests = [], responses = new Map(), events = new Map();
  function node(id = '') {
    return {id, tagName: 'DIV', innerHTML: '', textContent: '', value: '', disabled: false, checked: false, hidden: false, open: false, options: [], dataset: {},
      classList: {add() {}, remove() {}}, contains: () => false, querySelector: () => null, querySelectorAll: () => [],
      focus() {}, setAttribute() {}, addEventListener() {}, replaceChildren() {}, append() {}, remove() {}, reportValidity: () => true,
      showModal() { this.open = true; }, close() { this.open = false; }};
  }
  const getNode = selector => {
    if (!nodes.has(selector)) nodes.set(selector, node(selector.replace('#', '')));
    return nodes.get(selector);
  };
  const document = {querySelector: selector => selector === 'meta[name="app-token"]' ? {content: 'synthetic-token'} : getNode(selector), querySelectorAll: () => [],
    getElementById: id => getNode('#' + id), createElement: () => node(), activeElement: null, addEventListener(name, fn) { events.set('document:' + name, fn); }};
  let current = clone(initial);
  const sandbox = {document, URL, Map, Set, Date, console, FormData: class {}, setTimeout: () => 1, clearTimeout() {},
    window: {location: {href: 'http://localhost:9999/#run/r1', origin: 'http://localhost:9999', pathname: '/', search: '', hash: '#run/r1'}, history: {replaceState() {}}, scrollTo() {}, addEventListener(name, fn, options) { events.set('window:' + name, {fn, options}); }}, 
    fetch: async (url, options = {}) => {
      const data = options.body && typeof options.body === 'string' ? JSON.parse(options.body) : null;
      requests.push({url, method: options.method || 'GET', data});
      if (responses.has(url)) {
        const {body, status} = responses.get(url);
        return {ok: status >= 200 && status < 300, status, json: async () => clone(body)};
      }
      let result = current;
      if (url === '/api/flow/update') result = current.flow.runs[0];
      else if (url === '/api/flow/delete') { current.flow.runs = current.flow.runs.filter(run => run.id !== data.run_id); result = {ok: true}; }
      else if (url === '/api/flow/remove-order') {
        const run = current.flow.runs[0];
        run.order_ids = run.order_ids.filter(id => id !== data.order_id);
        run.review.groups = run.review.groups.filter(group => group.order_id !== data.order_id);
        run.production = run.production.filter(row => row.order_id !== data.order_id);
        run.revision++;
        result = {run};
      }
      else if (options.method === 'POST') result = {id: 'test-job-' + requests.length, kind: url.split('/').at(-1), status: 'pending', run_id: 'r1'};
      else if (url.startsWith('/api/material-pages')) result = {pages: [1, 2, 3].map(page => ({url: '/page-' + page + '.png', page, total_pages: 3}))};
      return {ok: true, status: 200, json: async () => clone(result)};
    }};
  const expose = `globalThis.__wizard = {ui, normalizeState, runById, runTitle, renderHistory, deleteRun, attachEvents, renderReview, renderMaking, renderDone, runErrorHTML, networkBanner, reviewProblems, groupProblems, issueCategory, checksFor, roleHTML, previewData, buildBatchGallery, batchView, visibleBatchRows, localURL, review, make, removeOrder, handleClick, reflowOrder, exportRun, resolveConfirm, productionWarnings, productionBlockers, refresh, renderGalleryNavigation, setState(value) { state = normalizeState(value); initialized = true; ui.runId = 'r1'; ui.view = 'run'; }};`;
  assert.match(assets['wizard.js'], /\n  main\(\);\n/);
  vm.runInNewContext(assets['wizard.js'].replace(/\n  main\(\);\n/, '\n  ' + expose + '\n'), sandbox, {filename: 'wizard.js'});
  sandbox.__wizard.setState(current);
  return {wizard: sandbox.__wizard, requests, nodes, responses, events, setState(value) { current = clone(value); sandbox.__wizard.setState(current); }};
}

async function runTests() {
  const {wizard: w, requests, nodes, responses, setState} = harness(fixtureState('review'));
  const click = (action, dataset = {}) => w.handleClick({target: {closest: selector => selector === '[data-action]' ? {dataset: {action, ...dataset}, disabled: false} : null}});
  assert.doesNotMatch(assets['index.html'] + assets['wizard.js'] + assets['wizard.css'], /textarea|reviewFeedback|feedbackDrafts|补充说明/);
  assert.doesNotMatch(assets['index.html'], /class="brand"|class="brand-mark"/);
  assert.match(assets['index.html'], /<title>ExpBot<\/title>/);
  assert.match(assets['wizard.css'], /\.media-dialog\{width:98vw;max-width:98vw;height:96dvh/);
  assert.doesNotMatch(assets['index.html'], /brand-period/);

  let data = fixtureState('review'), run = data.flow.runs[0];
  setState(data);
  let html = w.renderReview(run);
  assert.equal((html.match(/class="group-card batch-card/g) || []).length, 4);
  assert.match(html, /28<\/strong><span>份原始材料/);
  assert.match(html, /8<\/strong><span>份 PDF/);
  assert.match(html, /data-batch-field="query"/);
  assert.match(html, /data-batch-field="group"/);
  assert.match(html, /只看有问题/);
  assert.doesNotMatch(html, /supplement-card|需补交|<textarea/);
  for (const id of Object.keys(data.materials)) assert.ok(html.includes(`data-material-id="${id}"`), 'Every material remains reachable: ' + id);
  // Category information may be absent or a composite image may supply several roles.
  for (const material of Object.values(data.materials)) material.category = 'unknown';
  setState(data);
  assert.equal(w.reviewProblems(run).length, 0);
  assert.doesNotMatch(w.renderReview(run), /supplement-card|需补交|缺少/);

  // Only the four user-facing review categories survive older AI/heuristic payloads.
  data = fixtureState('review'); run = data.flow.runs[0];
  const allowedIssues = [
    {code: 'bad_buyer', level: 'error', message: '发票抬头与申报购买方不符'},
    {code: 'shipping_address_mismatch', level: 'error', message: '收货地址与申报地址不符'},
    {code: 'missing_payment', level: 'warning', message: '未提供支付凭证，请补交'},
    {code: 'material_blurry', level: 'warning', message: '材料文字模糊，无法辨认'},
    {code: 'personal_info', level: 'error', message: '个人信息中的姓名有误'},
    {code: 'unknown_buyer_name', level: 'warning', message: '发票购买方字段未识别'},
    {code: 'ai_review', level: 'warning', message: '订单详情中的收货地址有误'}
  ];
  const hiddenIssues = [
    {code: 'amount_mismatch', level: 'error', message: '金额不一致，购买方备注保留'},
    {code: 'order_no_mismatch', level: 'error', message: '订单编号不一致'},
    {code: 'address_unknown', level: 'warning', message: '模型尚未提供address核验结论及理由，未默认通过'},
    {code: 'completeness_unknown', level: 'warning', message: '模型尚未提供完整性结论'},
    {code: 'unknown_check', level: 'warning', message: '模型核验结论或理由缺失，未默认通过'},
    {code: 'multi_invoice', level: 'error', message: '同组有多份发票，须核对是否混单'},
    {code: 'payment_not_expense', level: 'error', message: '付款记录为退款，原始事实保留'},
    {code: 'invoice_from_image', level: 'warning', message: '使用图片发票'},
    {code: 'legibility_review', level: 'warning', message: '版面清晰度尚未通过'},
    {code: 'ai_review', level: 'warning', message: '请核对原件与采购分组'}
  ];
  run.review.issues = [...allowedIssues, ...hiddenIssues].map(issue => ({...issue, group_key: 'g2'}));
  run.review.warnings = ['可在制作前对照原件核对', '已保留文件名和普通元数据'];
  run.review.unassigned = [run.material_ids[0]];
  run.review.groups[1].checks.push({code: 'amount', status: 'fail', message: '金额存在差异'}, {code: 'order_no', status: 'fail', message: '订单号不符'}, {code: 'completeness', status: 'review', message: '材料齐全待确认'});
  setState(data);
  assert.deepEqual(Array.from(w.reviewProblems(run), issue => issue.code), allowedIssues.map(issue => issue.code));
  assert.deepEqual([...new Set(w.reviewProblems(run).map(issue => issue.category))].sort(), ['address', 'legibility', 'missing', 'personal']);
  html = w.renderReview(run);
  for (const issue of allowedIssues) assert.ok(html.includes(issue.message), 'Allowed review issue remains visible: ' + issue.code);
  for (const issue of hiddenIssues) assert.ok(!html.includes(issue.message), 'Unrelated review issue is hidden: ' + issue.code);
  assert.ok(!w.checksFor(run.review.groups[1]).some(check => ['amount', 'order_no'].includes(check.key)), 'Unrelated checks do not affect passed counts');
  for (const issue of [{code: 'readability', status: 'fail'}, {message: '订单图片看不清'}, {message: '缺少电子发票'}, {message: '收货地址不正确'}, {code: 'bad_tax_id', message: '税号不正确'}]) assert.ok(w.issueCategory(issue));
  run.review.issues = hiddenIssues; run.review.groups[1].checks = [];
  setState(data);
  assert.equal(w.reviewProblems(run).length, 0, 'Unassigned materials and hidden heuristics do not invent review errors');
  assert.doesNotMatch(w.renderReview(run), /supplement-card|项待核对/);

  for (const scenario of ['running', 'trash', 'collect']) {
    data = fixtureState(scenario); setState(data); run = data.flow.runs[0];
    assert.equal(w.reviewProblems(run).length, 0, scenario + ' must not report incomplete classification as a failure');
    assert.doesNotMatch(w.renderReview(run), /supplement-card|需补交/, scenario);
  }
  data = fixtureState('issue'); setState(data); run = data.flow.runs[0];
  html = w.renderReview(run);
  assert.match(html, /supplement-card/);
  assert.match(html, /补交材料/);
  assert.match(html, /无需填写文字/);
  assert.match(html, /需补交/);
  assert.equal(w.visibleBatchRows(run, run.review.groups)[0].row.key, 'g3', 'Problem groups sort first');
  const view = w.batchView(run);
  view.onlyProblems = true;
  assert.equal(w.visibleBatchRows(run, run.review.groups).length, 1);
  view.onlyProblems = false; view.query = '设备配件-order-2';
  assert.equal(w.visibleBatchRows(run, run.review.groups)[0].row.key, 'g2');
  view.query = ''; view.group = 'o4';
  assert.equal(w.visibleBatchRows(run, run.review.groups).length, 1);
  Object.assign(view, {group: 'all', query: '', onlyProblems: false});

  data = fixtureState('ready'); setState(data); run = data.flow.runs[0];
  html = w.renderMaking(run, 'ready');
  assert.equal((html.match(/class="production-card batch-card/g) || []).length, 4);
  assert.equal((html.match(/class="paper-preview"/g) || []).length, 8, 'Both PDFs are visible for every group');
  assert.equal((html.match(/class="pdf-page-thumb"/g) || []).length, 12, 'All invoice page thumbnails are reachable');
  assert.match(html, /仅重新制作本笔/);
  assert.match(html, /重新制作本批/);
  assert.equal((html.match(/data-action="remove-order"/g) || []).length, 4, 'Every making card has a visible purchase exclusion control');
  assert.equal((w.renderReview(run).match(/data-action="remove-order"/g) || []).length, 4, 'Every review card has a visible purchase exclusion control');
  assert.match(html, /排除此笔采购/);
  assert.doesNotMatch(html, /preview-download|<a\b[^>]*\bdownload|<a\b[^>]*\.pdf/i, 'Production previews do not offer redundant PDF downloads');
  assert.match(html, /data-action="open-output"/);
  assert.match(html, /打开本机输出文件夹/);
  const gallery = w.buildBatchGallery(run);
  assert.equal(gallery.filter(item => item.original).length, 28);
  assert.equal(gallery.filter(item => item.kind === 'invoice').length, 12);
  assert.equal(gallery.filter(item => item.kind === 'voucher').length, 4);
  assert.equal(new Set(gallery.map(item => item.groupId)).size, 4);
  assert.ok(gallery.some(item => item.orderId === 'o4' && item.kind === 'invoice' && item.page === 3));
  const material = data.materials['g1-invoice-1'];
  w.ui.materialPages.set(material.id + ':0', {pages: [1, 2, 3].map(page => ({url: '/page-' + page + '.png', page, total_pages: 3})), original_url: '/full-original.pdf'});
  const allPages = w.buildBatchGallery(run).filter(item => item.materialId === material.id);
  assert.equal(allPages.length, 3, 'All original PDF pages join the same gallery');
  assert.ok(allPages.every(item => item.download === '/full-original.pdf'));
  assert.equal(w.previewData({order_id: 'legacy', preview_url: '/voucher.jpg', invoice_url: '/invoice.png'}).invoicePages.length, 1);
  assert.equal(w.previewData({order_id: 'alias', invoice_preview_pages: [{url: '/one.png'}, {url: '/two.png'}]}).invoicePages.length, 2);
  assert.equal(w.localURL('https://external.example/secret'), '');
  assert.equal(w.localURL('javascript:alert(1)'), '');

  w.ui.gallery = [gallery[0]]; w.ui.galleryAll = [gallery[0]]; w.ui.galleryIndex = 0;
  w.renderGalleryNavigation();
  assert.equal(nodes.get('#mediaDownload').hidden, true, 'Legacy media download control stays hidden while index removal rolls out');
  assert.equal(nodes.get('#mediaDownload').href, undefined);
  w.ui.gallery = []; w.ui.galleryAll = [];

  requests.length = 0;
  const cancelledRemoval = click('remove-order', {orderId: 'o2'});
  assert.equal(nodes.get('#confirmDialog').open, true);
  assert.match(nodes.get('#confirmTitle').textContent, /排除这笔采购/);
  assert.match(nodes.get('#confirmBody').innerHTML, /原始文件和已经保存的 PDF 不会删除/);
  assert.ok(!requests.some(request => request.url === '/api/flow/remove-order'), 'Purchase exclusion waits for confirmation');
  w.resolveConfirm(false); await cancelledRemoval;
  assert.equal(requests.length, 0, 'Cancelling exclusion sends no request');
  w.ui.previewCache.set('o2', {voucher: '/old.jpg'}); w.ui.layoutDrafts.set('o2', {layout_column_ratio: .7});
  w.ui.force.add('r1'); w.ui.exportConfirmed.add('r1'); w.batchView(run).group = 'o2';
  const removing = click('remove-order', {orderId: 'o2'});
  w.resolveConfirm(true); await removing;
  assert.deepEqual(requests.find(request => request.url === '/api/flow/remove-order').data, {run_id: 'r1', order_id: 'o2', revision: 7});
  assert.equal(w.runById().revision, 8, 'The returned run becomes authoritative without waiting for a state poll');
  assert.ok(!w.runById().order_ids.includes('o2'));
  assert.equal((w.renderMaking(w.runById(), 'ready').match(/class="production-card batch-card/g) || []).length, 3);
  assert.equal(w.ui.previewCache.has('o2'), false); assert.equal(w.ui.layoutDrafts.has('o2'), false);
  assert.equal(w.ui.force.has('r1'), false); assert.equal(w.ui.exportConfirmed.has('r1'), false);
  assert.equal(w.batchView(w.runById()).group, 'all');
  assert.ok(!requests.some(request => /flow\/(?:make|export|remove)$/.test(request.url)), 'Exclusion never starts model work or deletes individual materials');

  setState(fixtureState('ready')); requests.length = 0;
  const changedWhileConfirming = w.removeOrder('o1');
  data = fixtureState('ready'); data.flow.runs[0].revision = 8; setState(data);
  w.resolveConfirm(true);
  await assert.rejects(changedWhileConfirming, /已更新/);
  assert.equal(requests.length, 0, 'A stale confirmation cannot remove a purchase from a newer run');

  responses.set('/api/flow/remove-order', {status: 409, body: {error: '批次已更新，请刷新后重试'}});
  requests.length = 0;
  const conflictRemoval = w.removeOrder('o1'); w.resolveConfirm(true);
  await assert.rejects(conflictRemoval, /已更新/);
  assert.equal(requests.filter(request => request.url === '/api/flow/remove-order').length, 1, 'Revision conflict never retries the mutation automatically');
  assert.ok(requests.some(request => request.url === '/api/state'), 'Revision conflict refreshes the run');
  assert.equal(w.runById().order_ids.length, 4); assert.equal(w.ui.locks.size, 0);
  responses.set('/api/flow/remove-order', {status: 400, body: {error: '批次已更新，请刷新后重试，未应用过期操作'}});
  responses.set('/api/state', {status: 503, body: {error: '本地服务不可用'}});
  const unreadableConflict = w.removeOrder('o1'); w.resolveConfirm(true);
  await assert.rejects(unreadableConflict, /暂时无法读取最新记录/);
  assert.match(w.networkBanner(), /本地服务不可用/); responses.delete('/api/state');
  responses.set('/api/flow/remove-order', {status: 500, body: {error: '本地服务写入失败'}});
  requests.length = 0;
  const failedRemoval = click('remove-order', {orderId: 'o1'}); w.resolveConfirm(true); await failedRemoval;
  assert.equal(w.runById().order_ids.length, 4, 'Failed exclusion preserves the purchase');
  assert.match(w.runErrorHTML(w.runById()), /本地服务写入失败/);
  assert.equal(w.ui.locks.size, 0); responses.clear(); w.ui.errors.clear();
  data = fixtureState('running'); setState(data); requests.length = 0;
  assert.match(w.renderReview(data.flow.runs[0]), /data-action="remove-order"[^>]*disabled/);
  await w.removeOrder('o1'); assert.equal(requests.length, 0, 'Busy runs cannot exclude purchases');
  w.ui.pendingJobs.clear(); setState(fixtureState('ready')); requests.length = 0;
  await click('open-output');
  assert.deepEqual(requests.find(request => request.url === '/api/open-output').data, {}, 'Configured output-folder pathway uses the existing local endpoint');

  requests.length = 0;
  await w.review();
  const reviewRequest = requests.find(request => request.url === '/api/flow/review');
  assert.deepEqual(reviewRequest.data, {run_id: 'r1', consent: true, retry: true});
  assert.ok(!requests.some(request => request.url.includes('ai/test')));
  w.ui.pendingJobs.clear(); setState(fixtureState('ready'));
  requests.length = 0;
  await w.make('o2');
  assert.deepEqual(requests.find(request => request.url === '/api/flow/make').data.only_order_ids, ['o2']);
  w.ui.pendingJobs.clear(); setState(fixtureState('ready'));
  requests.length = 0;
  await w.make();
  assert.ok(!('only_order_ids' in requests.find(request => request.url === '/api/flow/make').data));
  w.ui.pendingJobs.clear(); setState(fixtureState('ready'));
  requests.length = 0;
  await w.reflowOrder('o1', {layout_column_ratio: .6});
  assert.deepEqual(requests.find(request => request.url === '/api/flow/reflow').data, {run_id: 'r1', order_id: 'o1', layout: {layout_column_ratio: .6}});
  assert.ok(!requests.some(request => request.url === '/api/settings'), 'Layout updates do not rewrite global settings');
  w.ui.pendingJobs.clear(); requests.length = 0;
  await w.refresh();
  assert.ok(requests.every(request => request.method === 'GET' && request.url === '/api/state'), 'Refresh does not start AI work or material rendering');
  data = fixtureState('ready'); run = data.flow.runs[0];
  run.production[0].warnings = ['可在导出前对照原始图片核对'];
  run.production[0].info = [{code: 'invoice_from_image', message: '使用图片发票', level: 'info'}];
  run.production[0].legibility = 'review';
  run.production[0].blocking_issues = [];
  setState(data); w.ui.exportConfirmed.add('r1'); w.ui.force.clear(); requests.length = 0;
  assert.equal(w.productionBlockers(run.production[0]).length, 0);
  assert.doesNotMatch(w.renderMaking(run, 'ready'), /使用图片发票|可在导出前对照原始图片核对|<strong>[1-9]\d* 组需要核对/);
  run.production[0].warnings = ['使用图片发票', '收货地址已完整保留', '发票抬头按原件保留', '订单编号不一致', '金额不一致'];
  assert.equal(w.productionWarnings(run.production[0]).length, 0, 'Metadata and amount/order mismatch notes do not become production errors');
  run.production[0].blocking_issues = [{code: 'amount_mismatch', message: '金额不一致'}, {code: 'stitch_alternate_view', message: '拼接区存在视图差异'}, {code: 'legibility_review', message: '版面清晰度尚未通过'}, {code: 'production_unclassified', message: '制作仍有未分类材料'}];
  assert.equal(w.productionBlockers(run.production[0]).length, 0, 'Legacy heuristic blockers do not force export');
  run.production[0].warnings.push('支付凭证模糊，无法辨认');
  assert.equal(w.productionWarnings(run.production[0]).length, 1, 'Actual illegible materials remain visible');
  run.production[0].warnings = []; run.production[0].blocking_issues = [];
  setState(data);
  await w.exportRun();
  assert.deepEqual(requests.find(request => request.url === '/api/flow/export').data, {run_id: 'r1', force: false, confirmed: true}, 'Ordinary production warnings do not force export');
  w.ui.pendingJobs.clear();
  run.production[0].blocking_issues = [{code: 'production_missing_material', message: '制作仍含材料占位', level: 'warning'}];
  setState(data); requests.length = 0;
  assert.equal(w.productionBlockers(run.production[0]).length, 1);
  const exporting = w.exportRun();
  assert.ok(!requests.some(request => request.url === '/api/flow/export'), 'Actual blockers await explicit confirmation');
  w.resolveConfirm(true);
  await exporting;
  assert.equal(requests.find(request => request.url === '/api/flow/export').data.force, true);
  w.ui.pendingJobs.clear();
  assert.equal(w.productionBlockers({legibility: 'poor', warnings: ['legacy'], blocking_issues: []}).length, 1);
  assert.equal(w.productionBlockers({status: 'error', error: 'PDF 写入失败', blocking_issues: hiddenIssues}).length, 1, 'Processing failures survive review category filtering');
  assert.equal(w.productionBlockers({blocking_issues: [{code: 'export_failed', message: '导出失败'}]}).length, 1, 'Explicit export failures are not hidden as heuristic checks');
  assert.equal(w.productionWarnings({warnings: ['PDF 写入失败', '可在导出前对照原件核对']}).length, 1, 'Operational failures in older warning payloads remain visible');
  data = fixtureState('ready'); run = data.flow.runs[0]; run.error = '导出失败，请重试'; setState(data);
  assert.match(w.runErrorHTML(run), /导出失败，请重试/);
  w.ui.networkError = '无法连接本地服务'; assert.match(w.networkBanner(), /无法连接本地服务/); w.ui.networkError = '';
  run.stage = 'done'; run.error = ''; run.forced_steps = [{stage: 'make'}]; run.review.issues = hiddenIssues;
  setState(data);
  assert.doesNotMatch(w.renderDone(run), /仍有已确认的提醒|金额不一致|订单编号不一致/, 'Legacy forced steps do not resurrect unrelated warning categories');
  data = fixtureState('ready'); run = data.flow.runs[0]; run.stage = 'done';
  run.exports = [{id: 'e1', order_id: 'o1', folder: 'saved-locally', created: '2026-11-09'}];
  data.exports = [{id: 'old-removed', run_id: 'r1', order_id: 'o2', invalidated: true, folder: 'old-saved-folder'}];
  setState(data);
  const completed = w.renderDone(run);
  assert.doesNotMatch(completed, /<a\b[^>]*\bdownload|download-links|old-saved-folder/);
  assert.match(completed, /打开文件夹/);
  const small = harness(fixtureState('collect')), v = small.wizard;
  const full = fixtureState('collect'); full.flow.runs[0].profile = {declare_date: '20261029', name: '罗响', advisor: '金骁', project_name: 'RAL智能肌肉康复设备'};
  small.setState(full);
  assert.equal(v.runTitle(full.flow.runs[0]), '20261029-罗响-金骁-RAL智能肌肉康复设备');
  assert.match(v.renderHistory(), /class="run-date"[^>]*>20261029<\/time><span>-罗响-金骁-RAL智能肌肉康复设备/);
  assert.match(v.renderHistory(), /data-action="delete-run"/);
  let picked = 0;
  const dateInput = {disabled: false, showPicker() { picked++; }};
  await v.handleClick({target: {closest: selector => selector === 'input[type="date"]' ? dateInput : null}});
  assert.equal(picked, 1, 'Whole date input invokes native picker');
  v.attachEvents();
  for (const type of ['dragover', 'drop']) {
    let prevented = false;
    const listener = small.events.get('window:' + type);
    listener.fn({target: {closest: () => null}, dataTransfer: {types: ['text/uri-list'], files: [], items: []}, preventDefault() { prevented = true; }, stopPropagation() {}});
    assert.equal(prevented, true, 'File URL drop cannot navigate browser');
    assert.equal(listener.options.capture, true);
  }
  const deletion = v.deleteRun('r1');
  assert.ok(!small.requests.some(row => row.url === '/api/flow/delete'));
  v.resolveConfirm(true); await deletion;
  assert.equal(small.requests.find(row => row.url === '/api/flow/delete').data.run_id, 'r1');
  assert.equal(v.runById('r1'), undefined, 'Deleted history is removed from local cache');
  console.log('PASS: v2.2.1 naming/date highlight, history deletion, full date picker, captured drops, plus prior wizard contracts');
}

function svgDocument(id, page) {
  const isVoucher = id.includes('voucher'), isInvoice = id.includes('invoice'), width = isVoucher ? 1600 : 1000, height = isVoucher ? 1131 : isInvoice ? 1400 : 2600;
  const label = isVoucher ? '购买及支付凭证' : isInvoice ? '电子发票 / 原始票据' : '订单详情 · 原始材料';
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><rect width="100%" height="100%" fill="white"/><rect x="40" y="40" width="${width - 80}" height="${height - 80}" rx="3" fill="none" stroke="#bdc9bd"/><g font-family="Segoe UI,Microsoft YaHei,sans-serif" fill="#354b3b"><text x="70" y="110" font-size="32">${label}</text><text x="70" y="165" font-size="23">${id} · 第 ${page} 页</text><text x="70" y="225" font-size="19" fill="#798b79">合成界面测试资料 · 无真实票据或密钥</text>${Array.from({length: Math.floor((height - 420) / 80)}, (_, n) => `<path d="M70 ${300 + n * 80}H${width - 70}" stroke="#e1e8e0"/><text x="85" y="${340 + n * 80}" font-size="21">材料记录 ${String(n + 1).padStart(2, '0')} · 采购项目与支付信息</text><text x="${width - 210}" y="${340 + n * 80}" font-size="20">￥ 128.50</text>`).join('')}<text x="70" y="${height - 90}" font-size="19">本页用于检查缩放、拖动、原件与跨组跳转。</text></g></svg>`;
}

function serveFixture(port) {
  const http = require('node:http'), requests = [];
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1');
    const referer = new URL(request.headers.referer || '/', 'http://127.0.0.1');
    const scenario = referer.searchParams.get('scenario') || url.searchParams.get('scenario') || 'ready';
    const send = (body, type = 'application/json', status = 200) => { response.writeHead(status, {'Content-Type': type + '; charset=utf-8', 'Cache-Control': 'no-store'}); response.end(typeof body === 'string' ? body : JSON.stringify(body)); };
    if (url.pathname === '/') return send(assets['index.html'].replace('__APP_TOKEN__', 'synthetic-token'), 'text/html');
    if (url.pathname === '/wizard.js' || url.pathname === '/wizard.css') return send(assets[url.pathname.slice(1)], url.pathname.endsWith('.js') ? 'text/javascript' : 'text/css');
    if (url.pathname === '/icon.svg') return send('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><rect width="24" height="24" fill="#22685d"/></svg>', 'image/svg+xml');
    if (url.pathname === '/api/state') return send(fixtureState(scenario));
    if (url.pathname === '/api/material-pages') {
      const id = url.searchParams.get('id');
      return send({pages: [1, 2, 3].map(page => ({url: `/fixture/image/${id}/${page}.svg`, thumb_url: `/fixture/image/${id}/${page}.svg`, page, total_pages: 3})), total_pages: 3, original_url: `/fixture/original/${id}.pdf`});
    }
    const image = /^\/fixture\/image\/([^/]+)\/(\d+)\.svg$/.exec(url.pathname);
    if (image) return send(svgDocument(image[1], image[2]), 'image/svg+xml');
    if (url.pathname === '/fixture/requests') return send(requests);
    if (request.method === 'POST') {
      let body = ''; request.on('data', chunk => { body += chunk; });
      request.on('end', () => { const data = JSON.parse(body || '{}'); requests.push({path: url.pathname, data}); send(url.pathname === '/api/flow/update' ? fixtureState(scenario).flow.runs[0] : {id: 'synthetic-job', status: 'done', kind: url.pathname.split('/').at(-1), run_id: 'r1'}); });
      return;
    }
    return send({error: 'Synthetic fixture route not found'}, 'application/json', 404);
  });
  server.listen(port, '127.0.0.1', () => console.log(`Synthetic UI fixture: http://127.0.0.1:${server.address().port}/?scenario=ready#run/r1`));
}

if (process.argv.includes('--serve')) serveFixture(Number(process.argv[process.argv.indexOf('--serve') + 1]) || 0);
else runTests().catch(error => { console.error(error); process.exitCode = 1; });
