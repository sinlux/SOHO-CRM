import {$, $$, esc, api, nav, toast, modal, closeModal} from '../lib.js';
import {openCatalogAdmin} from './catalog_admin.js';

const PAGE = 60;
const state = {search: '', category: '', status: '', sort: 'updated', offset: 0};
const selected = new Set();           // 勾选的产品（用于合并同类项）
const store = {get: (k, d) => { try { return localStorage.getItem(k) || d; } catch (e) { return d; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* 无痕模式等：忽略 */ } }};
export function setSearch(q) { state.search = q; state.offset = 0; }
const STATUS_LABEL = {active: '在售', draft: '草稿', discontinued: '停产'};

export const money = (v, cur) => v === null || v === undefined ? '<span class="muted">—</span>'
  : `<span class="money">${cur ? esc(cur) + ' ' : ''}${Number(v).toLocaleString('en-US', {maximumFractionDigits: 4})}</span>`;

// 统一的"方形画框"：任何比例的图片都居中、完整显示（object-fit: contain）
export function thumb(p, size) {
  const st = size ? ` style="width:${size}px;height:${size}px"` : '';
  return p.thumb_url ? `<img class="thumb" src="${esc(p.thumb_url)}" alt="" loading="lazy"${st}>` : `<span class="thumb ph"${st}>▣</span>`;
}

export async function render(root, _arg, isCurrent) {
  const [cats, rate] = await Promise.all([api('/api/categories'), api('/api/rate')]);
  if (!isCurrent()) return;
  const view = store.get('productView', 'table');
  const SRC = {default: '默认值', manual: '手动', boc: '中行现汇买入', online: '在线'};
  root.innerHTML = `<div class="card"><div class="flex between">
      <div class="chips" id="chips"><span class="chip ${state.category === '' ? 'on' : ''}" data-cat="">全部</span>
        ${cats.categories.map(c => `<span class="chip ${String(state.category) === String(c.id) ? 'on' : ''}" data-cat="${c.id}">${esc(c.icon || '')} ${esc(c.name)}<small>${c.product_count}</small></span>`).join('')}</div>
      <div class="flex"><button class="rate-pill" id="btnRate" title="CNY→USD 汇率（中国银行美元现汇买入价），用于计算建议价">汇率 1 CNY = ${rate.rate} USD · ${SRC[rate.source] || esc(rate.source)}${rate.last_error ? ' ⚠' : ''}</button>
        <button id="btnCatAdmin" title="新建/改名/删除类目、子类、规格字段，设置 SKU 前缀">类目与规格管理</button><button id="btnTools">维护 ▾</button><button class="primary" id="btnNew">＋ 新建产品</button></div></div>
    <div class="flex" style="margin-top:14px"><input id="fSearch" placeholder="搜索：SKU / 名称 / 品牌 / 系列 / 供应商 / 规格" style="max-width:380px" value="${esc(state.search)}">
      <select id="fStatus" style="max-width:120px"><option value="">全部状态</option>${Object.entries(STATUS_LABEL).map(([k, l]) => `<option value="${k}" ${state.status === k ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <select id="fSort" style="max-width:150px">${[['updated', '最近更新'], ['created', '最新创建'], ['sku', 'SKU'], ['name', '名称'], ['cost', '成本从低到高']].map(([k, l]) => `<option value="${k}" ${state.sort === k ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <div class="seg" id="viewSeg"><button data-v="table" class="${view === 'table' ? 'on' : ''}" title="列表">☰</button><button data-v="cards" class="${view === 'cards' ? 'on' : ''}" title="卡片">▦</button></div>
      <span class="muted" id="cnt"></span></div></div>
    <div id="toolsMenu" class="menu" style="display:none"><button data-tool="recalc">按最新汇率重算建议价</button><button data-tool="normalize">统一旧图片规格（居中白底高清）</button></div>
    <div class="sel-bar" id="selBar"><b id="selN"></b><button id="btnMerge" class="primary">合并同类项…</button><button id="btnClear">取消勾选</button>
      <span class="muted">把重复的产品合成一个：价格历史、成交明细、供应商比价、图片都会并入保留的那一个</span></div>
    <div id="listBox" class="card">加载中…</div>`;

  const updSel = () => {
    $('#selBar').classList.toggle('on', selected.size > 0);
    $('#selN').textContent = `已勾选 ${selected.size} 个`;
    $('#btnMerge').disabled = selected.size < 2;
  };
  const stTag = p => p.status && p.status !== 'active' ? ` <span class="tag ${p.status === 'draft' ? 'warn' : 'bad'}">${esc(p.status_label)}</span>` : '';
  const priceBits = p => `${money(p.cost, p.cost_currency)}`;
  const load = async () => {
    if (!$('#fSearch')) return;
    state.search = $('#fSearch').value.trim(); state.status = $('#fStatus').value; state.sort = $('#fSort').value;
    const p = new URLSearchParams({limit: PAGE, offset: state.offset, sort: state.sort});
    if (state.search) p.set('search', state.search);
    if (state.category !== '') p.set('category_id', state.category);
    if (state.status) p.set('status', state.status);
    const d = await api('/api/products?' + p);
    if (!isCurrent()) return;
    if (!d.products.length && state.offset > 0) { state.offset = 0; return load(); }
    $('#cnt').textContent = `共 ${d.total} 个产品`;
    const to = state.offset + d.products.length;
    const pager = `<div class="flex between" style="margin-top:12px"><span class="muted">第 ${state.offset + 1}–${to} 条</span>
        <span class="flex"><button id="prev" ${state.offset === 0 ? 'disabled' : ''}>上一页</button><button id="next" ${to >= d.total ? 'disabled' : ''}>下一页</button></span></div>`;
    if (!d.products.length) {
      $('#listBox').innerHTML = '<div class="empty">没有符合条件的产品。点右上角「新建产品」，或稍后用 Excel / PI 批量导入。</div>';
    } else if (store.get('productView', 'table') === 'cards') {
      $('#listBox').innerHTML = `<div class="pcards">${d.products.map(p => `<div class="pcard" data-id="${p.id}">
        <label class="pcheck"><input type="checkbox" data-sel="${p.id}" ${selected.has(p.id) ? 'checked' : ''}></label>
        <div class="pimg">${p.thumb_url ? `<img src="${esc(p.thumb_url)}" alt="" loading="lazy">` : '<span>▣</span>'}</div>
        <div class="pinfo"><div class="psku">${esc(p.sku)}${stTag(p)}</div><div class="pname">${esc(p.name)}</div>
          <div class="pmeta"><span class="tag">${esc(p.category_icon || '')} ${esc(p.category_name || '')}</span><span>${money(p.suggested_price ?? null, 'USD')}</span></div></div></div>`).join('')}</div>${pager}`;
    } else {
      $('#listBox').innerHTML = `<table><tr><th class="chk"></th><th></th><th>SKU</th><th>名称</th><th>类目</th><th>成本</th><th>建议价(USD)</th><th>MOQ</th><th>供应商</th></tr>
      ${d.products.map(p => `<tr class="row" data-id="${p.id}">
        <td class="chk"><input type="checkbox" data-sel="${p.id}" ${selected.has(p.id) ? 'checked' : ''}></td>
        <td>${thumb(p)}</td><td><b>${esc(p.sku)}</b></td><td>${esc(p.name)}${stTag(p)}${p.brand ? `<div class="muted">${esc(p.brand)}${p.series ? ' · ' + esc(p.series) : ''}</div>` : ''}</td>
        <td><span class="tag">${esc(p.category_icon || '')} ${esc(p.category_name || '')}</span></td>
        <td>${priceBits(p)}</td>
        <td>${money(p.suggested_price)}${p.suggested_unconverted ? ' <span class="tag warn" title="EUR/VND 成本未换算为美元，数值仅供参考">未换算</span>' : ''}</td>
        <td>${p.moq ?? '<span class="muted">—</span>'}</td><td class="muted">${esc(p.supplier || '')}</td></tr>`).join('')}</table>${pager}`;
    }
    const prev = $('#prev'), next = $('#next');
    if (prev) prev.onclick = () => { state.offset = Math.max(0, state.offset - PAGE); load(); };
    if (next) next.onclick = () => { state.offset += PAGE; load(); };
    updSel();
  };

  $('#listBox').onclick = e => {
    const cb = e.target.closest('[data-sel]');
    if (cb) { const id = Number(cb.dataset.sel); cb.checked ? selected.add(id) : selected.delete(id); updSel(); return; }
    if (e.target.closest('.chk') || e.target.closest('.pcheck')) return;
    const t = e.target.closest('[data-id]');
    if (t) nav('product', t.dataset.id);
  };
  let timer;
  $('#fSearch').oninput = () => { clearTimeout(timer); timer = setTimeout(() => { state.offset = 0; load(); }, 250); };
  ['#fStatus', '#fSort'].forEach(id => $(id).onchange = () => { state.offset = 0; load(); });
  $('#viewSeg').onclick = e => {
    const b = e.target.closest('[data-v]');
    if (!b) return;
    store.set('productView', b.dataset.v);
    $$('#viewSeg button').forEach(x => x.classList.toggle('on', x === b));
    load();
  };
  $('#chips').onclick = e => {
    const c = e.target.closest('[data-cat]');
    if (!c) return;
    state.category = c.dataset.cat; state.offset = 0;
    $$('#chips .chip').forEach(x => x.classList.toggle('on', x === c));
    load();
  };
  $('#btnNew').onclick = () => nav('product', 'new');
  $('#btnClear').onclick = () => { selected.clear(); $$('[data-sel]').forEach(c => c.checked = false); updSel(); };
  $('#btnMerge').onclick = () => mergeDialog(() => { selected.clear(); render(root, _arg, isCurrent); });
  $('#btnRate').onclick = () => rateDialog(() => render(root, _arg, isCurrent));
  $('#btnCatAdmin').onclick = () => openCatalogAdmin(() => render(root, _arg, isCurrent), state.category || undefined);
  $('#btnTools').onclick = () => { const m = $('#toolsMenu'); m.style.display = m.style.display === 'none' ? '' : 'none'; };
  $('#toolsMenu').onclick = async e => {
    const b = e.target.closest('[data-tool]');
    if (!b) return;
    $('#toolsMenu').style.display = 'none';
    try {
      if (b.dataset.tool === 'recalc') {
        if (!confirm('按当前汇率重算所有产品的建议价？\n（已有成交价记录的产品不会改动）')) return;
        const r = await api('/api/products/recalc_prices', 'POST', {});
        toast(`已重算 ${r.updated} 个产品的建议价（${r.skipped_has_sell_price} 个有成交价，已跳过）`); load();
      } else {
        const r = await api('/api/products/normalize_images', 'POST', {});
        toast(r.no_pillow ? '本机没有可用的 Pillow 图像库，无法统一图片' : `已统一 ${r.done} 张图片${r.failed.length ? '，失败 ' + r.failed.length + ' 张' : ''}`); load();
      }
    } catch (err) { toast(err.message); }
  };
  await load();
}

async function mergeDialog(done) {
  const ids = [...selected];
  const items = (await Promise.all(ids.map(id => api('/api/products/' + id).then(r => r.product)))).filter(Boolean);
  modal(`<h2>合并同类项</h2><p class="muted">选择要<b>保留</b>的那个产品，其余 ${items.length - 1} 个会被并入后删除。</p>
    ${items.map((p, i) => `<label class="pick" style="cursor:pointer"><input type="radio" name="keep" value="${p.id}" ${i === 0 ? 'checked' : ''}>
      ${thumb(p, 40)}<div><b>${esc(p.sku)}</b> ${esc(p.name)}<div class="muted">成本 ${p.cost ?? '-'} ${esc(p.cost_currency || '')}｜建议价 ${p.suggested_price ?? '-'}｜规格 ${(p.spec_text || '').length} 字${p.image_path ? '｜有图' : ''}</div></div></label>`).join('')}
    <ul class="muted" style="margin:12px 0 0 18px"><li>价格历史、成交明细、供应商比价全部迁移到保留的产品</li><li>保留产品的规格描述和图片只在为空时才用被合并者补充</li>
      <li>当前成本和建议价按合并后最新的历史记录重新对齐</li><li>保留产品的备注里会记录「已合并同类项: SKU…」</li></ul>
    <div class="flex" style="margin-top:16px"><button class="primary" id="ok">确认合并</button><button id="no">取消</button></div>`);
  $('#no').onclick = closeModal;
  $('#ok').onclick = async () => {
    const keep = Number(document.querySelector('input[name=keep]:checked').value);
    $('#ok').disabled = true;
    try {
      const r = await api('/api/products/merge', 'POST', {survivor_id: keep, merge_ids: ids.filter(i => i !== keep)});
      closeModal(); toast(`已合并：${r.merged.join(', ')} → ${r.survivor_sku}`); done();
    } catch (e) { toast(e.message); $('#ok').disabled = false; }
  };
}

async function rateDialog(done) {
  const r = await api('/api/rate');
  const auto = r.mode === 'boc';
  modal(`<h2>CNY → USD 汇率</h2>
    <p class="muted">自动模式：每天从中国银行外汇牌价抓取「美元·现汇买入价」（人民币/100美元），汇率 = 100 ÷ 现汇买入价。用于计算建议价：成本(CNY) × 汇率 × (1 + 利润率)。</p>
    <div class="price-card" style="margin:12px 0"><div class="flex between"><div><div class="muted">当前汇率</div><div class="big">1 CNY = ${r.rate} USD</div></div>
      <div class="muted" style="text-align:right">${auto ? '<span class="tag good">自动更新中</span>' : '<span class="tag warn">手动模式（不自动更新）</span>'}
        <div>中行现汇买入价：${r.boc_buy ?? '—'}</div><div>发布时间：${esc(r.boc_published || '—')}</div></div></div></div>
    ${r.last_error ? `<p class="err" style="margin:8px 0">最近一次自动更新失败：${esc(r.last_error)}</p>` : ''}
    <div class="flex"><button class="primary" id="refresh">立即从中国银行更新</button>
      ${auto ? '<button id="toManual">改为手动</button>' : '<button id="toAuto">恢复自动更新</button>'}</div>
    <h3>手动设置</h3><div class="flex"><input id="bocBuy" placeholder="中行现汇买入价，如 712.34" style="max-width:240px"><button id="saveBoc">按牌价保存</button>
      <input id="rt" placeholder="或直接填汇率，如 0.14" style="max-width:200px"><button id="saveRt">保存</button></div>
    <p class="muted" style="margin-top:6px">手动保存会切换为手动模式，之后不再自动覆盖，直到点「恢复自动更新」。</p>
    <h3>每日记录</h3><div class="scroll" style="max-height:200px">${r.history.length ? `<table><tr><th>日期</th><th>现汇买入价</th><th>汇率</th><th>发布时间</th></tr>${r.history.map(h => `<tr><td>${esc(h.day)}</td><td>${h.buy_spot}</td><td>${h.rate}</td><td class="muted">${esc(h.published_at || '')}</td></tr>`).join('')}</table>` : '<span class="muted">还没有记录</span>'}</div>
    <div id="msg" style="margin-top:10px"></div><div style="margin-top:12px"><button id="x">关闭</button></div>`);
  $('#x').onclick = closeModal;
  const act = (fn, ok) => async () => {
    try { await fn(); closeModal(); toast(ok); done(); } catch (e) { $('#msg').innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  $('#refresh').onclick = act(() => api('/api/rate/refresh', 'POST', {}), '已更新');
  if ($('#toManual')) $('#toManual').onclick = act(() => api('/api/rate/mode', 'PUT', {mode: 'manual'}), '已改为手动模式');
  if ($('#toAuto')) $('#toAuto').onclick = async () => {
    try { const d = await api('/api/rate/mode', 'PUT', {mode: 'boc'}); closeModal(); toast(d.refresh_error ? '已恢复自动，但这次更新失败：' + d.refresh_error : '已恢复自动并更新'); done(); }
    catch (e) { $('#msg').innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  $('#saveBoc').onclick = act(() => api('/api/rate', 'PUT', {boc_buy: $('#bocBuy').value}), '已保存');
  $('#saveRt').onclick = act(() => api('/api/rate', 'PUT', {rate: $('#rt').value}), '汇率已保存');
}
