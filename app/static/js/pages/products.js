import {$, $$, esc, api, nav, toast, modal, closeModal} from '../lib.js';

const PAGE = 100;
const state = {search: '', category: '', offset: 0};
const selected = new Set();           // 勾选的产品（用于合并同类项）

export const money = (v, cur) => v === null || v === undefined ? '<span class="muted">—</span>'
  : `<span class="money">${cur ? esc(cur) + ' ' : ''}${Number(v).toLocaleString('en-US', {maximumFractionDigits: 4})}</span>`;

export function thumb(p, size) {
  const st = size ? ` style="width:${size}px;height:${size}px"` : '';
  return p.image_url ? `<img class="thumb" src="${esc(p.image_url)}" alt=""${st}>` : `<span class="thumb ph"${st}>▣</span>`;
}

export async function render(root, _arg, isCurrent) {
  const [cats, rate] = await Promise.all([api('/api/categories'), api('/api/rate')]);
  if (!isCurrent()) return;
  const SRC = {default: '默认值', manual: '手动', online: '在线'};
  root.innerHTML = `<div class="card"><div class="flex between">
      <div class="chips" id="chips"><span class="chip ${state.category === '' ? 'on' : ''}" data-cat="">全部</span>
        ${cats.categories.map(c => `<span class="chip ${String(state.category) === String(c.id) ? 'on' : ''}" data-cat="${c.id}">${esc(c.icon || '')} ${esc(c.name)}<small>${c.product_count}</small></span>`).join('')}</div>
      <div class="flex"><button class="rate-pill" id="btnRate" title="CNY→USD 汇率，用于计算建议价">汇率 1 CNY = ${rate.rate} USD · ${SRC[rate.source] || esc(rate.source)}</button>
        <button id="btnPrefix">SKU前缀</button><button class="primary" id="btnNew">＋ 新建产品</button></div></div>
    <div class="flex" style="margin-top:14px"><input id="fSearch" placeholder="搜索：SKU / 名称 / 供应商 / 规格描述 / 备注" style="max-width:420px" value="${esc(state.search)}">
      <span class="muted" id="cnt"></span></div></div>
    <div class="sel-bar" id="selBar"><b id="selN"></b><button id="btnMerge" class="primary">合并同类项…</button><button id="btnClear">取消勾选</button>
      <span class="muted">把重复的产品合成一个：价格历史、成交明细、供应商比价都会并入保留的那一个</span></div>
    <div class="card" id="listBox">加载中…</div>`;

  const updSel = () => {
    $('#selBar').classList.toggle('on', selected.size > 0);
    $('#selN').textContent = `已勾选 ${selected.size} 个`;
    $('#btnMerge').disabled = selected.size < 2;
  };
  const load = async () => {
    state.search = $('#fSearch').value.trim();
    const p = new URLSearchParams({limit: PAGE, offset: state.offset});
    if (state.search) p.set('search', state.search);
    if (state.category !== '') p.set('category_id', state.category);
    const d = await api('/api/products?' + p);
    if (!isCurrent()) return;
    if (!d.products.length && state.offset > 0) { state.offset = 0; return load(); }
    $('#cnt').textContent = `共 ${d.total} 个产品`;
    const to = state.offset + d.products.length;
    $('#listBox').innerHTML = d.products.length ? `<table><tr><th class="chk"></th><th></th><th>SKU</th><th>名称</th><th>类目</th><th>成本</th><th>建议价(USD)</th><th>MOQ</th><th>供应商</th></tr>
      ${d.products.map(p => `<tr class="row" data-id="${p.id}">
        <td class="chk"><input type="checkbox" data-sel="${p.id}" ${selected.has(p.id) ? 'checked' : ''}></td>
        <td>${thumb(p)}</td><td><b>${esc(p.sku)}</b></td><td>${esc(p.name)}</td>
        <td><span class="tag">${esc(p.category_icon || '')} ${esc(p.category_name || '')}</span></td>
        <td>${money(p.cost, p.cost_currency)}</td>
        <td>${money(p.suggested_price)}${p.suggested_unconverted ? ' <span class="tag warn" title="EUR/VND 成本未换算为美元，数值仅供参考">未换算</span>' : ''}</td>
        <td>${p.moq ?? '<span class="muted">—</span>'}</td><td class="muted">${esc(p.supplier || '')}</td></tr>`).join('')}</table>
      <div class="flex between" style="margin-top:10px"><span class="muted">第 ${state.offset + 1}–${to} 条</span>
        <span class="flex"><button id="prev" ${state.offset === 0 ? 'disabled' : ''}>上一页</button><button id="next" ${to >= d.total ? 'disabled' : ''}>下一页</button></span></div>`
      : '<div class="empty">还没有产品。点右上角「新建产品」，或稍后用 Excel / PI 批量导入。</div>';
    const prev = $('#prev'), next = $('#next');
    if (prev) prev.onclick = () => { state.offset = Math.max(0, state.offset - PAGE); load(); };
    if (next) next.onclick = () => { state.offset += PAGE; load(); };
    updSel();
  };

  $('#listBox').onclick = e => {
    const cb = e.target.closest('[data-sel]');
    if (cb) { const id = Number(cb.dataset.sel); cb.checked ? selected.add(id) : selected.delete(id); updSel(); return; }
    if (e.target.closest('.chk')) return;
    const tr = e.target.closest('tr.row');
    if (tr) nav('product', tr.dataset.id);
  };
  let timer;
  $('#fSearch').oninput = () => { clearTimeout(timer); timer = setTimeout(() => { state.offset = 0; load(); }, 250); };
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
  $('#btnPrefix').onclick = () => prefixDialog(cats.categories);
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
  modal(`<h2>CNY → USD 汇率</h2><p class="muted">用于计算建议价：成本(CNY) × 汇率 × (1 + 利润率)。修改后只影响之后保存的产品，不会改动已有产品。</p>
    <div class="flex" style="margin:12px 0"><input id="rt" value="${r.rate}" style="max-width:160px"><span class="muted">更新于 ${esc(r.updated_at || '—')}</span></div>
    <div class="flex"><button class="primary" id="save">保存</button><button id="fetch">在线获取</button><button id="x">关闭</button></div>
    <div id="msg" style="margin-top:10px"></div>`);
  $('#x').onclick = closeModal;
  $('#save').onclick = async () => {
    try { await api('/api/rate', 'PUT', {rate: $('#rt').value}); closeModal(); toast('汇率已保存'); done(); } catch (e) { $('#msg').innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  $('#fetch').onclick = async () => {
    $('#msg').textContent = '获取中…';
    try { const d = await api('/api/rate/fetch', 'POST', {}); $('#rt').value = d.rate; $('#msg').innerHTML = `<span class="ok">已获取并保存：${d.rate}</span>`; done(); }
    catch (e) { $('#msg').innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
}

async function prefixDialog(cats) {
  modal('<h2>SKU 前缀</h2><p class="muted">SKU = 类目前缀 + 子类前缀 + 6 位序号，如 SL + SP + 000001。前缀为 1–4 位英文字母。</p><div id="pfBody">加载中…</div><div style="margin-top:14px"><button id="x">关闭</button></div>');
  $('#x').onclick = closeModal;
  const draw = async cid => {
    const d = await api(`/api/categories/${cid}/prefixes`);
    $('#pfBody').innerHTML = `<div class="flex" style="margin:10px 0"><select id="pfCat" style="max-width:200px">${cats.map(c => `<option value="${c.id}" ${c.id == cid ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select>
      <span>类目前缀</span><input id="pfMain" value="${esc(d.category_prefix || '')}" style="max-width:100px" maxlength="4"><button id="pfSaveMain">保存</button></div>
      <table><tr><th>子类（须与产品「子类」字段取值一致）</th><th>前缀</th><th></th></tr>
      ${d.subcategories.map(s => `<tr><td>${esc(s.subcategory_value)}</td><td><b>${esc(s.prefix)}</b></td><td><button class="small danger" data-del="${s.id}">删</button></td></tr>`).join('')}
      <tr><td><input id="pfSub" placeholder="子类名，如 射灯"></td><td><input id="pfSubP" maxlength="4" placeholder="SP"></td><td><button class="small primary" id="pfAdd">添加</button></td></tr></table>`;
    $('#pfCat').onchange = e => draw(e.target.value);
    $('#pfSaveMain').onclick = async () => { try { await api(`/api/categories/${cid}/prefix`, 'PUT', {prefix: $('#pfMain').value}); toast('已保存'); draw(cid); } catch (e) { toast(e.message); } };
    $('#pfAdd').onclick = async () => { try { await api(`/api/categories/${cid}/sub_prefixes`, 'POST', {subcategory_value: $('#pfSub').value, prefix: $('#pfSubP').value}); draw(cid); } catch (e) { toast(e.message); } };
    $('#pfBody').onclick = async e => {
      const b = e.target.closest('[data-del]');
      if (b) { try { await api('/api/sub_prefixes/' + b.dataset.del, 'DELETE', {}); draw(cid); } catch (err) { toast(err.message); } }
    };
  };
  draw(cats[0].id);
}
