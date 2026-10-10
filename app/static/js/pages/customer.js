import {$, $$, esc, api, nav, toast, avatar, lvBadge, linkify, LV_DESC, STAGES} from '../lib.js';
import {startEnrich, review} from './enrich.js';

const FIELDS = [['country', '国家'], ['name', '联系人'], ['company', '公司名'], ['website', '网站'], ['emails', '邮箱'],
  ['whatsapp', 'WhatsApp/电话'], ['linkedin', 'LinkedIn'], ['facebook', 'Facebook'], ['other_social', '其他社媒'],
  ['address', '地址'], ['company_size', '公司规模'], ['founded_year', '成立年份'], ['customer_type', '客户类型'],
  ['certifications', '认证要求']];
const LINK_FIELDS = ['website', 'emails', 'linkedin', 'facebook', 'other_social'];
const EN_STATUS = {pending: '待确认', applied: '已写入', failed: '失败', running: '进行中'};
const today = () => new Date().toLocaleDateString('sv');   // YYYY-MM-DD（本地时区）

const remRow = r => `<div class="flex" style="padding:3px 0">
  <span class="${!r.done && r.due_date <= today() ? 'overdue' : ''}">${esc(r.due_date)}</span>
  <span style="${r.done ? 'text-decoration:line-through;color:var(--sub)' : ''}">${esc(r.content)}</span>
  ${r.done ? '' : `<button class="small" data-rem-done="${r.id}">完成</button>`}
  <button class="small danger" data-rem-del="${r.id}">删</button></div>`;

const noteRow = n => `<div class="note"><div class="muted">${esc(n.created_at)}
  <button class="small danger" style="float:right" data-note-del="${n.id}">删</button></div>
  <div style="white-space:pre-wrap;margin-top:3px">${esc(n.content)}</div>
  ${n.image_path ? `<img src="/images/${encodeURIComponent(n.image_path)}" data-zoom alt="备注图片">` : ''}</div>`;

export async function render(root, arg, isCurrent) {
  const id = parseInt(arg, 10);
  if (!id) return nav('list');
  let d;
  try { d = await api('/api/customers/' + id); } catch (e) {
    root.innerHTML = `<div class="card"><button data-back>← 返回</button><p class="err" style="margin-top:10px">${esc(e.message)}</p></div>`;
    $('[data-back]', root).onclick = () => nav('list');
    return;
  }
  if (!isCurrent()) return;
  const c = d.customer;
  const reload = () => render(root, arg, isCurrent);

  root.innerHTML = `
  <div class="card"><div class="flex between">
    <div class="flex"><button id="btnBack">← 返回</button>
      ${avatar(c.company || c.name, 44)}<h2 style="margin:0">${esc(c.company || c.name || '（无名客户）')}</h2>${lvBadge(c.lv)}
      <select id="cLv" style="width:150px">${['', 1, 2, 3, 4, 5, 6].map(l => `<option value="${l}" ${String(c.lv || '') === String(l) ? 'selected' : ''}>${l ? 'LV' + l + ' ' + LV_DESC[l] : '未分级'}</option>`).join('')}</select>
      <select id="cStage" style="width:110px"><option value="">未设置阶段</option>${STAGES.map(s => `<option ${c.stage === s ? 'selected' : ''}>${s}</option>`).join('')}</select></div>
    <div class="flex"><button class="primary" id="btnEnrich">🔍 AI背调</button>
      <button id="btnSave">保存修改</button><button class="danger" id="btnDel">删除客户</button></div></div>
    <div class="grid" style="margin-top:14px">
      ${FIELDS.map(([k, l]) => `<div class="field"><label>${l}</label><input data-f="${k}" value="${esc(c[k])}">
        <div class="muted" style="margin-top:2px">${LINK_FIELDS.includes(k) ? linkify(c[k]) : ''}</div></div>`).join('')}
    </div>
    <div class="field" style="margin-top:10px"><label>主营业务</label><textarea data-f="main_business">${esc(c.main_business)}</textarea></div>
    ${c.ai_summary ? `<div class="field" style="margin-top:10px"><label>AI背调摘要</label><div class="summary">${esc(c.ai_summary)}</div></div>` : ''}
  </div>

  <div class="card"><div class="sec-title"><h3>📄 报价历史</h3><button class="primary small" id="btnNewQuote">＋ 新建报价</button></div>
    ${d.quotes.length ? d.quotes.map(q => `<div class="flex" style="padding:3px 0"><a class="ext" href="#quote/${q.id}"><b>${esc(q.quote_no)}</b></a>
      <span>${esc((q.created_at || '').slice(0, 10))}</span><span>${q.item_count}项</span>
      <b>${esc(q.currency)} ${Number(q.total || 0).toFixed(2)}</b><span class="tag">${esc(q.status_label)}</span></div>`).join('')
      : '<span class="muted">暂无报价记录</span>'}</div>

  <div class="card"><h3 style="margin-top:0">⏰ 跟进提醒</h3>
    <div class="flex"><input id="remContent" placeholder="提醒内容，如：3个月后再联系报价" style="max-width:360px">
      <input type="date" id="remDate" style="max-width:160px"><button id="btnAddRem">添加提醒</button></div>
    <div id="remList" style="margin-top:10px">${d.reminders.map(remRow).join('') || '<span class="muted">暂无提醒</span>'}</div></div>

  <div class="card"><h3 style="margin-top:0">📝 备注 / 跟进历史（支持 Ctrl+V 直接粘贴截图）</h3>
    <textarea id="noteText" placeholder="写点什么，或直接在此处 Ctrl+V 粘贴截图…"></textarea>
    <div id="pastePreview" style="margin:6px 0"></div>
    <button class="primary" id="btnAddNote" style="margin-top:6px">保存备注</button>
    <div id="noteList" style="margin-top:14px">${d.notes.map(noteRow).join('') || '<span class="muted">暂无备注</span>'}</div></div>

  <div class="card"><h3 style="margin-top:0">背调历史</h3>
    ${d.enrichments.length ? d.enrichments.map(e => `<div class="flex" style="padding:4px 0">
      <span class="muted">${esc(e.created_at)}</span><span class="tag">第${e.round || 1}轮</span><span class="tag">${EN_STATUS[e.status] || esc(e.status)}</span>${e.status !== 'failed' ? `<span class="muted">完整度 ${e.score || 0}</span>` : ''}
      ${e.status === 'pending' ? `<button data-review="${e.id}">查看并确认</button>` : ''}
      ${e.status === 'failed' ? `<span class="muted err">${esc(e.error)}</span>` : ''}
      ${e.status === 'applied' ? `<button data-review="${e.id}" data-ro="1">查看原始材料</button>` : ''}
    </div>`).join('') : '<span class="muted">还没做过背调</span>'}</div>`;

  $('#btnBack').onclick = () => nav('list');
  $('#btnNewQuote').onclick = () => nav('quotenew', id);
  $('#btnEnrich').onclick = () => startEnrich(id);
  $('#btnSave').onclick = async () => {
    const upd = {};
    $$('[data-f]', root).forEach(el => upd[el.dataset.f] = el.value);
    upd.lv = $('#cLv').value || null;
    upd.stage = $('#cStage').value;
    try { await api('/api/customers/' + id, 'PUT', upd); toast('已保存'); reload(); } catch (e) { toast(e.message); }
  };
  $('#btnDel').onclick = async () => {
    try {
      const {impact: i} = await api(`/api/customers/${id}/impact`);
      const msg = `确定删除「${c.company || c.name || '该客户'}」？\n将同时永久删除：备注 ${i.notes} 条、提醒 ${i.reminders} 条、背调 ${i.enrichments} 条、报价单 ${i.quotes} 张。\n此操作不可恢复。`;
      if (!confirm(msg)) return;
      await api('/api/customers/' + id, 'DELETE', {confirm: true});
      toast('已删除'); nav('list');
    } catch (e) { toast(e.message); }
  };
  $('#btnAddRem').onclick = async () => {
    if (!$('#remDate').value) return toast('请选择提醒日期');
    try {
      await api(`/api/customers/${id}/reminders`, 'POST', {content: $('#remContent').value, due_date: $('#remDate').value});
      toast('提醒已添加'); reload();
    } catch (e) { toast(e.message); }
  };
  let pasted = null;
  $('#noteText').addEventListener('paste', e => {
    for (const item of e.clipboardData.items) {
      if (item.type.startsWith('image/')) {
        const rd = new FileReader();
        rd.onload = () => {
          pasted = rd.result;
          $('#pastePreview').innerHTML = `<img src="${pasted}" style="max-height:160px;border:1px solid var(--line);border-radius:6px"> <button id="rmImg">移除图片</button>`;
          $('#rmImg').onclick = () => { pasted = null; $('#pastePreview').innerHTML = ''; };
        };
        rd.readAsDataURL(item.getAsFile());
        e.preventDefault();
      }
    }
  });
  $('#btnAddNote').onclick = async () => {
    const txt = $('#noteText').value.trim();
    if (!txt && !pasted) return toast('备注为空');
    try {
      await api(`/api/customers/${id}/notes`, 'POST', {content: txt, image_base64: pasted || ''});
      toast('备注已保存'); reload();
    } catch (e) { toast(e.message); }
  };
  root.onclick = async e => {
    const t = e.target.closest('button,img');
    if (!t) return;
    try {
      if (t.dataset.remDone) { await api(`/api/reminders/${t.dataset.remDone}/done`, 'POST', {}); reload(); }
      else if (t.dataset.remDel) { await api('/api/reminders/' + t.dataset.remDel, 'DELETE', {}); reload(); }
      else if (t.dataset.noteDel) { if (confirm('删除这条备注？')) { await api('/api/notes/' + t.dataset.noteDel, 'DELETE', {}); reload(); } }
      else if (t.dataset.review) { await review(t.dataset.review, id, !!t.dataset.ro); }
      else if (t.dataset.zoom !== undefined) { window.open(t.src); }
    } catch (err) { toast(err.message); }
  };
}
