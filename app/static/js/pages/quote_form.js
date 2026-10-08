import {$, $$, esc, api, nav, toast, avatar, mulCents, fmtCents} from '../lib.js';
import {thumb} from './products.js';
import {customerCombo} from '../combo.js';

// 报价单编辑器（新建 / 编辑共用）：
//   #quotenew            新建
//   #quotenew/<客户id>    新建并预选客户（客户详情页的「新建报价」）
//   #quoteedit/<报价id>   编辑已有报价单（已成交的不能编辑）
const TERMS_PAY = ['T/T 30% deposit, 70% before shipment', 'T/T 50% deposit, 50% before shipment', 'L/C at sight', '100% T/T in advance'];
const TERMS_TRADE = ['EXW Shenzhen', 'FOB Shenzhen', 'FOB Ningbo', 'CIF', 'CFR', 'DDP'];
const LEAD = ['15-20 days', '25-30 days', '30-35 days after deposit', '45-60 days'];
const EMPTY_ROW = () => ({product_id: null, sku: '', name: '', spec: '', quantity: '1', unit: 'pcs', unit_price: '', remark: '', thumb_url: '', hint: ''});

export async function render(root, arg, isCurrent) {
  const isEdit = location.hash.startsWith('#quoteedit');
  const rate = (await api('/api/rate')).rate;
  let q = null, customer = null;
  if (isEdit) {
    try { q = (await api('/api/quotes/' + parseInt(arg, 10))).quote; } catch (e) {
      root.innerHTML = `<div class="card"><button id="back">← 返回</button><p class="err" style="margin-top:10px">${esc(e.message)}</p></div>`;
      $('#back').onclick = () => nav('quotes');
      return;
    }
    if (q.status === 'accepted') {
      root.innerHTML = `<div class="card"><p>已成交的报价单不能修改。请先在详情页把状态改回「已发送」，或复制一张新的。</p><button id="back" style="margin-top:10px">返回报价单</button></div>`;
      $('#back').onclick = () => nav('quote', q.id);
      return;
    }
    customer = {id: q.customer_id, label: q.company || q.customer_name};
  } else if (arg && /^\d+$/.test(arg)) {
    try { const c = (await api('/api/customers/' + arg)).customer; customer = {id: c.id, label: c.company || c.name}; } catch (e) { /* 客户不存在就当没预选 */ }
  }
  if (!isCurrent()) return;
  const rows = q ? q.items.map(i => ({product_id: i.product_id, sku: i.sku, name: i.name, spec: i.spec, quantity: String(i.quantity), unit: i.unit,
    unit_price: String(i.unit_price), remark: i.remark || '', thumb_url: i.thumb_url || '', hint: ''})) : [EMPTY_ROW()];

  root.innerHTML = `
  <div class="card"><div class="flex between"><div class="flex"><button id="back">← 返回</button><h2 style="margin:0">${isEdit ? '编辑 ' + esc(q.quote_no) : '新建报价单'}</h2></div>
    <div class="flex">${isEdit ? '<button class="primary" id="btnSave">保存修改</button>' : '<button id="btnDraft">保存为草稿</button><button class="primary" id="btnCreate">创建报价单</button>'}</div></div>
    <div class="grid" style="margin-top:16px">
      <div class="field"><label>客户 *</label><div class="flex nowrap" id="custBox">${customer ? chosen(customer) : ''}</div>
        <div style="position:relative"><input id="qCust" placeholder="点击选择客户，或输入公司名 / 联系人 / 邮箱 / 英文缩写" autocomplete="off" ${customer ? 'style="display:none"' : ''}><div class="sug" id="custSug" style="display:none"></div></div></div>
      <div class="field"><label>币种</label><select id="qCur">${[...new Set(['USD', 'CNY', q?.currency].filter(Boolean))].map(c => `<option ${(q ? q.currency : 'USD') === c ? 'selected' : ''}>${c}</option>`).join('')}</select></div>
      <div class="field"><label>有效期（天）</label><input id="qValid" type="number" min="0" value="${q ? q.valid_days : 30}"></div>
      <div class="field"><label>交期</label><input id="qLead" list="dlLead" value="${esc(q?.lead_time || '')}" placeholder="如 30-35 days after deposit"><datalist id="dlLead">${LEAD.map(x => `<option>${x}</option>`).join('')}</datalist></div>
      <div class="field"><label>付款条件</label><input id="qPay" list="dlPay" value="${esc(q?.payment_terms || '')}"><datalist id="dlPay">${TERMS_PAY.map(x => `<option>${x}</option>`).join('')}</datalist></div>
      <div class="field"><label>贸易条款</label><input id="qShip" list="dlShip" value="${esc(q?.shipping_terms || '')}"><datalist id="dlShip">${TERMS_TRADE.map(x => `<option>${x}</option>`).join('')}</datalist></div></div></div>

  <div class="card"><div class="sec-title"><h3>产品明细</h3><span class="muted">价格是创建时的快照，之后改产品价格不会影响这张报价单</span></div>
    <p class="muted" style="margin:0 0 8px">每一行：选了产品库里的产品，<b>单价会自动带出该产品的建议价</b>（可以直接改成这个客户的价格）；<b>小计 = 数量 × 单价</b>，自动算，不用手填。库里没有的项目（运费等）点「手动加一行」自己填。</p>
    <div class="field" style="position:relative;max-width:520px"><label>从产品库添加</label><input id="pSearch" placeholder="搜索 SKU / 名称 / 系列，回车添加第一个结果" autocomplete="off"><div class="sug" id="pSug"></div></div>
    <div id="itemsBox" style="margin-top:12px"></div>
    <div class="flex between" style="margin-top:12px"><button id="btnRow">＋ 手动加一行</button><div style="font-size:16px">合计：<b class="big-total" id="qTotal">0.00</b></div></div>
    <div class="field" style="margin-top:14px"><label>备注（印在报价单上）</label><textarea id="qNotes" style="min-height:70px">${esc(q?.notes || '')}</textarea></div></div>`;

  function chosen(c) { return `<span class="chosen">${avatar(c.label, 26)} <b>${esc(c.label)}</b> <button class="small" id="custChange">更换</button></span>`; }
  const cur = () => $('#qCur').value;
  const rowEls = () => $$('#itemsBox .qrow');

  // ---------- 明细表 ----------
  const drawRows = () => {
    $('#itemsBox').innerHTML = rows.map((r, n) => `<div class="qrow" data-n="${n}">
      <div class="qthumb">${thumb({thumb_url: r.thumb_url}, 52)}</div>
      <div class="qmain"><div class="grid3"><input data-k="sku" placeholder="SKU" value="${esc(r.sku)}"><input data-k="name" placeholder="产品名称 *" value="${esc(r.name)}"></div>
        <textarea data-k="spec" placeholder="规格 / 说明（印在报价单上）">${esc(r.spec)}</textarea><div class="hint" data-hint>${r.hint}</div></div>
      <div class="qnum"><label>数量</label><input data-k="quantity" inputmode="decimal" value="${esc(r.quantity)}"></div>
      <div class="qnum"><label>单位</label><input data-k="unit" value="${esc(r.unit)}"></div>
      <div class="qnum"><label>单价</label><input data-k="unit_price" inputmode="decimal" value="${esc(r.unit_price)}"></div>
      <div class="qamt"><label title="小计 = 数量 × 单价，自动计算，不用手填">小计</label><b data-amt>0.00</b></div>
      <div class="qtools"><button class="small" data-act="up" title="这一行上移" ${n === 0 ? 'disabled' : ''}>↑</button><button class="small" data-act="down" title="这一行下移" ${n === rows.length - 1 ? 'disabled' : ''}>↓</button><button class="small danger" data-act="del" title="删除这一行">✕</button></div></div>`).join('');
    recalc();
  };
  const recalc = () => {
    let total = 0;
    rowEls().forEach(el => {
      const n = Number(el.dataset.n);
      const c = mulCents(rows[n].quantity, rows[n].unit_price);
      total += c;
      $('[data-amt]', el).textContent = fmtCents(c);
    });
    $('#qTotal').textContent = cur() + ' ' + fmtCents(total);
  };
  $('#itemsBox').addEventListener('input', e => {
    const el = e.target.closest('.qrow');
    if (!el || !e.target.dataset.k) return;
    const r = rows[Number(el.dataset.n)];
    r[e.target.dataset.k] = e.target.value;
    if (e.target.dataset.k !== 'quantity' && e.target.dataset.k !== 'unit_price') return;
    recalc();
  });
  $('#itemsBox').addEventListener('click', e => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const n = Number(b.closest('.qrow').dataset.n);
    if (b.dataset.act === 'del') rows.splice(n, 1);
    else { const j = b.dataset.act === 'up' ? n - 1 : n + 1; [rows[n], rows[j]] = [rows[j], rows[n]]; }
    if (!rows.length) rows.push(EMPTY_ROW());
    drawRows();
  });
  $('#btnRow').onclick = () => { rows.push(EMPTY_ROW()); drawRows(); };
  $('#qCur').onchange = () => { recalc(); };

  // ---------- 谈判提示：该客户上次成交价 / 他人最近成交价 / 当前成本 / 建议价 ----------
  const hintFor = async (r, p) => {
    const parts = [];
    try {
      const lp = await api(`/api/products/${p.id}/last_price?customer_id=${customer ? customer.id : ''}`);
      if (lp.to_this_customer) parts.push(`💡 该客户上次成交 <b>${esc(lp.to_this_customer.currency)} ${lp.to_this_customer.price}</b>（${esc(lp.to_this_customer.effective_date)} ${esc(lp.to_this_customer.source || '')}）`);
      else if (lp.to_anyone) parts.push(`上次成交（其他客户${lp.to_anyone.company ? '·' + esc(lp.to_anyone.company) : ''}）${esc(lp.to_anyone.currency)} ${lp.to_anyone.price}（${esc(lp.to_anyone.effective_date)}）`);
      if (lp.cost_latest) parts.push(`当前成本 ${esc(lp.cost_latest.currency)} ${lp.cost_latest.price}`);
    } catch (e) { /* 提示失败不影响报价 */ }
    if (p.suggested_price != null) parts.push(`建议价 USD ${p.suggested_price}`);
    r.hint = parts.join('　');
  };
  const addProduct = async p => {
    const full = (await api('/api/products/' + p.id)).product;
    const r = EMPTY_ROW();
    Object.assign(r, {product_id: full.id, sku: full.sku, name: full.name, spec: (full.spec_text || '').slice(0, 1500), unit: full.unit || 'pcs',
      thumb_url: full.thumb_url || '', quantity: full.moq ? String(full.moq) : '1'});
    if (full.suggested_price != null) {                     // 建议价是美元：USD 直接用；CNY 按当前汇率换
      if (cur() === 'USD') r.unit_price = String(full.suggested_price);
      else if (cur() === 'CNY') r.unit_price = (Math.round(full.suggested_price / rate * 100) / 100).toString();
    }
    await hintFor(r, full);
    if (rows.length === 1 && !rows[0].sku && !rows[0].name) rows.length = 0;
    rows.push(r);
    drawRows();
  };
  let pt, found = [];
  $('#pSearch').oninput = () => { clearTimeout(pt); pt = setTimeout(async () => {
    const kw = $('#pSearch').value.trim();
    if (!kw) { $('#pSug').innerHTML = ''; found = []; return; }
    found = (await api('/api/products?limit=8&search=' + encodeURIComponent(kw))).products;
    $('#pSug').innerHTML = found.length ? found.map((p, i) => `<div class="sugitem" data-i="${i}">${thumb(p, 34)}<div><b>${esc(p.sku)}</b> ${esc(p.name)}<div class="muted">建议价 ${p.suggested_price ?? '—'} USD · MOQ ${p.moq ?? '—'}</div></div></div>`).join('') : '<div class="muted" style="padding:8px">没有匹配的产品</div>';
  }, 220); };
  const pick = async p => { $('#pSug').innerHTML = ''; $('#pSearch').value = ''; found = []; try { await addProduct(p); } catch (e) { toast(e.message); } };
  $('#pSug').onclick = e => { const it = e.target.closest('[data-i]'); if (it) pick(found[Number(it.dataset.i)]); };
  $('#pSearch').onkeydown = e => { if (e.key === 'Enter' && found.length) { e.preventDefault(); pick(found[0]); } };

  // ---------- 客户选择 ----------
  const bindChange = () => { const b = $('#custChange'); if (b) b.onclick = () => { customer = null; $('#custBox').innerHTML = ''; $('#qCust').style.display = ''; $('#qCust').focus(); }; };
  if (isEdit) { const b = $('#custChange'); if (b) b.style.display = 'none'; } else bindChange();
  customerCombo($('#qCust'), $('#custSug'), c => {
    customer = {id: c.id, label: c.company || c.name};
    $('#custBox').innerHTML = chosen(customer); $('#qCust').style.display = 'none'; $('#qCust').value = ''; bindChange();
  });

  // ---------- 保存 ----------
  const body = status => ({customer_id: customer ? customer.id : null, currency: cur(), valid_days: $('#qValid').value, lead_time: $('#qLead').value,
    payment_terms: $('#qPay').value, shipping_terms: $('#qShip').value, notes: $('#qNotes').value, status,
    items: rows.map(r => ({product_id: r.product_id, sku: r.sku, name: r.name, spec: r.spec, quantity: r.quantity, unit: r.unit, unit_price: r.unit_price, remark: r.remark}))});
  const save = async status => {
    if (!customer) return toast('请先选择客户');
    try {
      if (isEdit) { await api('/api/quotes/' + q.id, 'PUT', body()); toast('已保存'); nav('quote', q.id); }
      else { const r = await api('/api/quotes', 'POST', body(status)); toast('报价单已创建：' + r.quote_no); nav('quote', r.id); }
    } catch (e) { toast(e.message); }
  };
  $('#back').onclick = () => (isEdit ? nav('quote', q.id) : nav('quotes'));
  if (isEdit) $('#btnSave').onclick = () => save();
  else { $('#btnCreate').onclick = () => save('sent'); $('#btnDraft').onclick = () => save('draft'); }
  drawRows();
}
