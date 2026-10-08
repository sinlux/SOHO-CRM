import {$, esc, api, nav, lvBadge, stageBadge, toast, avatar, LV_DESC, STAGES} from '../lib.js';

const PAGE = 100;
// 筛选条件保留在模块里：从详情页返回列表时不丢
const state = {search: '', lv: '', country: '', stage: '', offset: 0};

export function setSearch(q) { state.search = q; state.offset = 0; }

export async function render(root, _arg, isCurrent) {
  const cs = await api('/api/countries');
  if (!isCurrent()) return;
  root.innerHTML = `<div class="card"><div class="flex">
    <input id="fSearch" placeholder="搜索：公司 / 联系人 / 邮箱 / 备注内容…" style="max-width:320px" value="${esc(state.search)}">
    <select id="fLv" style="max-width:170px"><option value="">全部等级</option>
      ${[6, 5, 4, 3, 2, 1].map(l => `<option value="${l}" ${state.lv == l ? 'selected' : ''}>LV${l} ${LV_DESC[l]}</option>`).join('')}</select>
    <select id="fStage" style="max-width:130px"><option value="">全部阶段</option>
      ${STAGES.map(s => `<option value="${s}" ${state.stage === s ? 'selected' : ''}>${s}</option>`).join('')}</select>
    <select id="fCountry" style="max-width:180px"><option value="">全部国家</option>
      ${cs.countries.map(c => `<option value="${esc(c.country)}" ${state.country === c.country ? 'selected' : ''}>${esc(c.country)} (${c.n})</option>`).join('')}</select>
    <button id="btnExport">导出Excel</button>
    <span class="muted" id="cnt"></span></div></div>
    <div class="card" id="listBox">加载中…</div>`;

  let timer;
  const load = async () => {
    if (!$('#fSearch')) return;
    state.search = $('#fSearch').value.trim(); state.lv = $('#fLv').value;
    state.country = $('#fCountry').value; state.stage = $('#fStage').value;
    const p = new URLSearchParams({limit: PAGE, offset: state.offset});
    for (const k of ['search', 'lv', 'country', 'stage']) if (state[k]) p.set(k, state[k]);
    const d = await api('/api/customers?' + p);
    if (!isCurrent()) return;
    if (d.customers.length === 0 && state.offset > 0) { state.offset = 0; return load(); }
    $('#cnt').textContent = `共 ${d.total} 个客户`;
    const from = state.offset + 1, to = state.offset + d.customers.length;
    $('#listBox').innerHTML = d.customers.length ? `<table><tr><th>等级</th><th>阶段</th><th>国家</th><th>公司</th><th>联系人</th><th>邮箱</th><th>主营/摘要</th></tr>
      ${d.customers.map(c => `<tr class="row" data-id="${c.id}">
        <td>${lvBadge(c.lv)}</td><td>${stageBadge(c.stage)}</td><td>${esc(c.country)}</td>
        <td><div class="who">${avatar(c.company || c.name)}<div><b>${esc(c.company) || '<span class="muted">（无公司名）</span>'}</b><div class="muted">${esc(c.website)}</div></div></div></td>
        <td>${esc(c.name)}</td><td>${esc(c.emails)}</td>
        <td class="muted">${esc((c.main_business || c.ai_summary || '').slice(0, 80))}</td></tr>`).join('')}</table>
      <div class="flex between" style="margin-top:10px"><span class="muted">第 ${from}–${to} 条</span>
        <span class="flex"><button id="prev" ${state.offset === 0 ? 'disabled' : ''}>上一页</button>
        <button id="next" ${to >= d.total ? 'disabled' : ''}>下一页</button></span></div>`
      : `<div class="empty">${d.total === 0 && !state.search && !state.lv && !state.country && !state.stage
        ? '还没有客户。可以「＋录入客户」，或用「Excel导入」批量导入。' : '没有符合条件的客户'}</div>`;
    $('#listBox').onclick = e => {
      const tr = e.target.closest('tr.row');
      if (tr) nav('customer', tr.dataset.id);
    };
    const prev = $('#prev'), next = $('#next');
    if (prev) prev.onclick = () => { state.offset = Math.max(0, state.offset - PAGE); load(); };
    if (next) next.onclick = () => { state.offset += PAGE; load(); };
  };
  const reset = () => { state.offset = 0; load(); };
  $('#fSearch').oninput = () => { clearTimeout(timer); timer = setTimeout(reset, 250); };
  for (const id of ['#fLv', '#fStage', '#fCountry']) $(id).onchange = reset;
  $('#btnExport').onclick = async () => {
    try {
      const r = await api('/api/customers/export', 'POST', {});
      toast('已导出：' + r.filename);
      location.href = r.url;
    } catch (e) { toast('导出失败：' + e.message); }
  };
  await load();
}
