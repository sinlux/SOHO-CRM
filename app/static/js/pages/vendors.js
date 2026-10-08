import {$, $$, esc, api, nav, toast, modal, closeModal} from '../lib.js';

// 供应商：一个项目可能同时问几十家，最后只选一家；很多时候只有聊天记录（阿里旺旺 / 微信）。
// 所以这里按「轻量档案 + 聊天截图归档（本机 OCR 建索引）+ 按项目对比」来做。
export const STATUS = {inquiring: '询价中', candidate: '备选', cooperating: '合作中', rejected: '淘汰'};
const STATUS_CLS = {inquiring: '', candidate: 'warn', cooperating: 'good', rejected: 'bad'};
export const stars = n => n ? '★'.repeat(n) + '<span class="muted">' + '★'.repeat(5 - n) + '</span>' : '<span class="muted">—</span>';
const state = {tab: 'list', search: '', status: '', project: '', chatQ: '', viewProject: ''};

export async function render(root, _arg, isCurrent) {
  const projects = (await api('/api/vendor_projects')).projects;
  if (!isCurrent()) return;
  root.innerHTML = `<div class="card"><div class="flex between"><div class="seg" id="tabSeg">
      <button data-t="list" class="${state.tab === 'list' ? 'on' : ''}">🏭 供应商列表</button><button data-t="project" class="${state.tab === 'project' ? 'on' : ''}">📋 按项目对比</button>
      <button data-t="chats" class="${state.tab === 'chats' ? 'on' : ''}">🔎 搜聊天记录</button></div>
      <button class="primary" id="btnNewVendor">＋ 新建供应商</button></div>
    <datalist id="projList">${projects.map(p => `<option value="${esc(p.project)}">`).join('')}</datalist>
    <div id="tabBody" style="margin-top:14px"></div></div>`;
  $('#tabSeg').onclick = e => { const b = e.target.closest('button'); if (b) { state.tab = b.dataset.t; render(root, _arg, isCurrent); } };
  $('#btnNewVendor').onclick = () => newDialog();
  const body = $('#tabBody');
  if (state.tab === 'list') await listTab(body, projects, isCurrent);
  else if (state.tab === 'project') await projectTab(body, projects);
  else chatTab(body);
}

function newDialog() {
  modal(`<h2>新建供应商</h2><p class="muted">先只记名字也行，后面随时补充联系方式、聊天记录。</p>
    <div class="grid" style="margin-top:10px"><div class="field"><label>供应商名称 *</label><input id="nvName"></div>
      <div class="field"><label>联系人</label><input id="nvContact"></div><div class="field"><label>微信</label><input id="nvWx"></div><div class="field"><label>电话</label><input id="nvPhone"></div></div>
    <div class="flex" style="margin-top:14px"><button class="primary" id="nvOk">创建</button><button id="nvCancel">取消</button></div>`);
  $('#nvName').focus();
  $('#nvCancel').onclick = closeModal;
  $('#nvOk').onclick = async () => {
    try {
      const r = await api('/api/vendors', 'POST', {name: $('#nvName').value, contact: $('#nvContact').value, wechat: $('#nvWx').value, phone: $('#nvPhone').value});
      closeModal(); nav('vendor', r.id);
    } catch (e) { toast(e.message); }
  };
}

async function listTab(body, projects, isCurrent) {
  body.innerHTML = `<div class="flex"><input id="vSearch" placeholder="搜索：名称 / 联系人 / 微信 / 备注 / 聊天文字…" style="max-width:360px" value="${esc(state.search)}">
      <select id="vStatus" style="max-width:130px"><option value="">全部状态</option>${Object.entries(STATUS).map(([k, l]) => `<option value="${k}" ${state.status === k ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <select id="vProject" style="max-width:200px"><option value="">全部项目</option>${projects.map(p => `<option ${state.project === p.project ? 'selected' : ''}>${esc(p.project)}</option>`).join('')}</select>
      <span class="muted" id="vCnt"></span></div><div id="vList" style="margin-top:12px">加载中…</div>`;
  const load = async () => {
    if (!$('#vSearch')) return;
    state.search = $('#vSearch').value.trim(); state.status = $('#vStatus').value; state.project = $('#vProject').value;
    const p = new URLSearchParams({search: state.search, status: state.status, project: state.project});
    const d = await api('/api/vendors?' + p);
    if (!isCurrent()) return;
    $('#vCnt').textContent = `共 ${d.total} 家`;
    $('#vList').innerHTML = d.vendors.length ? `<table><tr><th>供应商</th><th>联系方式</th><th>来源</th><th>状态</th><th>评分</th><th>聊天</th><th>报价</th><th>最近沟通</th></tr>
      ${d.vendors.map(v => `<tr class="row" data-id="${v.id}" style="cursor:pointer"><td><b>${esc(v.name)}</b><div class="muted">${esc(v.main_products)}</div></td>
        <td>${esc(v.contact)} ${v.wechat ? `<span class="muted">微信 ${esc(v.wechat)}</span>` : ''}${v.phone ? `<div class="muted">${esc(v.phone)}</div>` : ''}</td><td>${esc(v.platform)}</td>
        <td><span class="tag ${STATUS_CLS[v.status] || ''}">${esc(v.status_label)}</span></td><td>${stars(v.rating)}</td><td>${v.chat_count}</td>
        <td>${v.quote_count}${v.adopted_count ? ` <span class="tag good">采纳 ${v.adopted_count}</span>` : ''}</td><td class="muted">${esc(v.last_chat || '—')}</td></tr>`).join('')}</table>`
      : '<div class="empty">还没有供应商。在产品页添加供应商报价时会自动建档，也可以点右上角「新建供应商」。</div>';
  };
  let t;
  $('#vSearch').oninput = () => { clearTimeout(t); t = setTimeout(load, 250); };
  $('#vStatus').onchange = load; $('#vProject').onchange = load;
  $('#vList').onclick = e => { const r = e.target.closest('tr[data-id]'); if (r) nav('vendor', r.dataset.id); };
  await load();
}

async function projectTab(body, projects) {
  body.innerHTML = `<p class="muted">同一个项目问过的所有供应商放在一起对比：谁报了价、报多少、聊到什么程度。项目名在记录聊天或供应商报价时填写。</p>
    <div class="flex"><input id="pvName" list="projList" placeholder="选择或输入项目名" style="max-width:280px" value="${esc(state.viewProject)}"><button id="pvGo">查看</button></div>
    <div id="pvBody" style="margin-top:12px"></div>`;
  const go = async () => {
    state.viewProject = $('#pvName').value.trim();
    if (!state.viewProject) { $('#pvBody').innerHTML = projects.length ? `<div class="flex">${projects.map(p => `<button class="small" data-p="${esc(p.project)}">${esc(p.project)}（${p.suppliers} 家）</button>`).join('')}</div>` : '<span class="muted">还没有项目。在供应商的「沟通记录」里填项目名就会出现在这里。</span>'; return; }
    try {
      const d = await api('/api/vendor_projects/view?name=' + encodeURIComponent(state.viewProject));
      $('#pvBody').innerHTML = d.suppliers.length ? `<table><tr><th>供应商</th><th>状态</th><th>评分</th><th>聊天</th><th>最近沟通</th><th>本项目报价（¥）</th></tr>${d.suppliers.map(s => `<tr>
        <td><a class="ext" href="#vendor/${s.id}"><b>${esc(s.name)}</b></a><div class="muted">${esc(s.contact)} ${esc(s.wechat)}</div></td><td><span class="tag ${STATUS_CLS[s.status] || ''}">${esc(s.status_label)}</span></td>
        <td>${stars(s.rating)}</td><td>${s.chat_count}</td><td class="muted">${esc(s.last_chat || '')}</td>
        <td>${s.quotes.length ? s.quotes.map(q => `<div>${q.sku ? esc(q.sku) + ' ' : ''}<b>${q.price_cny ?? '—'}</b> ${q.is_adopted ? '<span class="tag good">已采纳</span>' : ''}</div>`).join('') : '<span class="muted">未报价</span>'}</td></tr>`).join('')}</table>`
        : '<span class="muted">这个项目下还没有记录</span>';
    } catch (e) { $('#pvBody').innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  $('#pvGo').onclick = go; $('#pvName').onkeydown = e => { if (e.key === 'Enter') go(); };
  $('#pvBody').onclick = e => { const b = e.target.closest('[data-p]'); if (b) { $('#pvName').value = b.dataset.p; go(); } };
  await go();
}

function chatTab(body) {
  body.innerHTML = `<p class="muted">按聊天截图里的文字（本机识别）、备注、项目名搜索，找到当时是哪家供应商、说了什么，并能看到原始截图。</p>
    <div class="flex"><input id="cqText" placeholder="如：起订量 / 含税 / 30天交期 / 某个型号" style="max-width:420px" value="${esc(state.chatQ)}"><button class="primary" id="cqGo">搜索</button></div>
    <div id="cqBody" style="margin-top:12px"></div>`;
  const go = async () => {
    state.chatQ = $('#cqText').value.trim();
    if (!state.chatQ) { $('#cqBody').innerHTML = ''; return; }
    const d = await api('/api/vendor_chats/search?q=' + encodeURIComponent(state.chatQ));
    $('#cqBody').innerHTML = d.chats.length ? d.chats.map(c => `<div class="flex" style="align-items:flex-start;padding:10px 0;border-bottom:1px dashed var(--line)">
      ${c.image_url ? `<a href="${esc(c.image_url)}" target="_blank" rel="noopener"><img src="${esc(c.thumb_url)}" style="width:84px;height:84px;object-fit:cover;border-radius:10px;border:1px solid var(--line)"></a>` : ''}
      <div style="flex:1"><a class="ext" href="#vendor/${c.supplier_id}"><b>${esc(c.supplier_name)}</b></a> ${c.project ? `<span class="tag">${esc(c.project)}</span>` : ''}
        <span class="muted">${esc(c.chat_date || (c.created_at || '').slice(0, 10))} ${esc(c.title)}</span><div style="margin-top:4px">${esc(c.snippet)}</div></div></div>`).join('')
      : '<span class="muted">没有找到。图片里的文字要识别完成后才能搜到；识别不了的可以在记录里手动补充文字。</span>';
  };
  $('#cqGo').onclick = go; $('#cqText').onkeydown = e => { if (e.key === 'Enter') go(); };
  go();
}
