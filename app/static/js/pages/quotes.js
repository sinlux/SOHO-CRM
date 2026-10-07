import {$, $$, esc, api, nav, toast, avatar} from '../lib.js';
const money = (v, cur) => `<span class="money">${esc(cur)} ${Number(v).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</span>`;

const PAGE = 50;
const state = {search: '', status: '', offset: 0};
export const STATUS = {draft: ['草稿', ''], sent: ['已发送', ''], accepted: ['成交', 'good'], rejected: ['未成交', 'bad'], expired: ['过期', 'warn']};
export const statusTag = q => `<span class="tag ${STATUS[q.status]?.[1] || ''}">${esc(STATUS[q.status]?.[0] || q.status)}</span>${q.past_validity ? ' <span class="tag warn" title="已超过有效期，仍未成交也未标记结果">已过有效期</span>' : ''}`;
export function setSearch(q) { state.search = q; state.offset = 0; }

export async function render(root, _arg, isCurrent) {
  root.innerHTML = `<div class="card"><div class="flex between"><div class="chips" id="chips">
      <span class="chip ${state.status === '' ? 'on' : ''}" data-st="">全部</span>
      ${Object.entries(STATUS).map(([k, [l]]) => `<span class="chip ${state.status === k ? 'on' : ''}" data-st="${k}">${l}</span>`).join('')}</div>
      <button class="primary" id="btnNew">＋ 新建报价单</button></div>
    <div class="flex" style="margin-top:14px"><input id="fSearch" placeholder="搜索：单号 / 客户 / 产品 SKU 或名称" style="max-width:380px" value="${esc(state.search)}"><span class="muted" id="cnt"></span></div></div>
    <div class="card" id="listBox">加载中…</div>`;
  const load = async () => {
    state.search = $('#fSearch').value.trim();
    const p = new URLSearchParams({limit: PAGE, offset: state.offset});
    if (state.search) p.set('search', state.search);
    if (state.status) p.set('status', state.status);
    const d = await api('/api/quotes?' + p);
    if (!isCurrent()) return;
    if (!d.quotes.length && state.offset > 0) { state.offset = 0; return load(); }
    $('#cnt').textContent = `共 ${d.total} 张`;
    const to = state.offset + d.quotes.length;
    $('#listBox').innerHTML = d.quotes.length ? `<table><tr><th>单号</th><th>日期</th><th>客户</th><th>项数</th><th>合计</th><th>有效至</th><th>状态</th></tr>
      ${d.quotes.map(q => `<tr class="row" data-id="${q.id}"><td><b>${esc(q.quote_no)}</b></td><td class="muted">${esc(q.created_at.slice(0, 10))}</td>
        <td><div class="who">${avatar(q.company || q.customer_name)}<div>${esc(q.company || q.customer_name)}</div></div></td><td>${q.item_count}</td>
        <td>${money(q.total, q.currency)}</td><td class="muted">${esc(q.valid_until)}</td><td>${statusTag(q)}</td></tr>`).join('')}</table>
      <div class="flex between" style="margin-top:12px"><span class="muted">第 ${state.offset + 1}–${to} 条</span><span class="flex">
        <button id="prev" ${state.offset === 0 ? 'disabled' : ''}>上一页</button><button id="next" ${to >= d.total ? 'disabled' : ''}>下一页</button></span></div>`
      : '<div class="empty">还没有符合条件的报价单。点右上角「新建报价单」，或在客户详情页直接发起。</div>';
    if ($('#prev')) $('#prev').onclick = () => { state.offset = Math.max(0, state.offset - PAGE); load(); };
    if ($('#next')) $('#next').onclick = () => { state.offset += PAGE; load(); };
  };
  $('#listBox').onclick = e => { const tr = e.target.closest('tr.row'); if (tr) nav('quote', tr.dataset.id); };
  $('#btnNew').onclick = () => nav('quotenew');
  $('#chips').onclick = e => {
    const c = e.target.closest('[data-st]');
    if (!c) return;
    state.status = c.dataset.st; state.offset = 0;
    $$('#chips .chip').forEach(x => x.classList.toggle('on', x === c));
    load();
  };
  let t;
  $('#fSearch').oninput = () => { clearTimeout(t); t = setTimeout(() => { state.offset = 0; load(); }, 250); };
  await load();
}
