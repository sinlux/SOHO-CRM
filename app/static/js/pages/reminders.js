import {$, esc, api, toast, updateBadge} from '../lib.js';

export async function render(root, _arg, isCurrent) {
  const d = await api('/api/reminders');
  if (!isCurrent()) return;
  root.innerHTML = `<div class="card"><h2>跟进提醒（今天: ${esc(d.today)}）</h2>
    ${d.reminders.length ? `<table><tr><th>日期</th><th>客户</th><th>内容</th><th></th></tr>
      ${d.reminders.map(r => `<tr><td class="${r.overdue ? 'overdue' : ''}">${esc(r.due_date)}${r.overdue ? ' ⚠' : ''}</td>
        <td><a class="ext" href="#customer/${r.customer_id}">${esc(r.company || r.name || '（无名）')}</a></td>
        <td>${esc(r.content)}</td><td><button data-done="${r.id}">完成</button></td></tr>`).join('')}</table>`
      : '<div class="empty">没有待办提醒。在客户详情页可以添加。</div>'}</div>`;
  root.onclick = async e => {
    const b = e.target.closest('[data-done]');
    if (!b) return;
    try { await api(`/api/reminders/${b.dataset.done}/done`, 'POST', {}); await render(root, _arg, isCurrent); updateBadge(); }
    catch (err) { toast(err.message); }
  };
}
