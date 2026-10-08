import {$, esc, api, avatar} from './lib.js';

// 客户选择器：点开就是完整下拉（可滚动），可输入搜索；支持公司名 / 联系人 / 邮箱 / 网站 / 国家，
// 还支持「英文缩写」：输入 hpa 能匹配 Hotel Playa Azul（每个单词首字母），或按字母顺序模糊匹配。
let cache = null;
export const clearCustomerCache = () => { cache = null; };
export async function allCustomers() {
  if (!cache) cache = (await api('/api/customers/brief')).customers;
  return cache;
}
const words = s => (s || '').toLowerCase().split(/[^a-z0-9一-鿿]+/).filter(Boolean);
export function score(c, q) {
  q = q.toLowerCase().trim();
  if (!q) return 1;
  const name = (c.company || c.name || '').toLowerCase();
  const ws = words(c.company || c.name);
  const initials = ws.map(w => w[0]).join('');
  const other = [c.name, c.emails, c.website, c.country].join(' ').toLowerCase();
  if (name === q) return 100;
  if (name.startsWith(q)) return 90;
  if (ws.some(w => w.startsWith(q))) return 80;
  if (q.length >= 2 && initials.startsWith(q)) return 75;
  if (name.includes(q)) return 60;
  if (other.includes(q)) return 50;
  if (q.length >= 3) {                                  // 按字母顺序的模糊匹配（缩写）：h-p-a 依次出现在公司名里
    let i = 0;
    for (const ch of name) if (ch === q[i]) i++;
    if (i === q.length) return 30;
  }
  return 0;
}
export const filterCustomers = (list, q, limit = 50) => list.map(c => [score(c, q), c]).filter(x => x[0] > 0)
  .sort((a, b) => b[0] - a[0] || (a[1].company || a[1].name || '').localeCompare(b[1].company || b[1].name || '')).slice(0, limit).map(x => x[1]);

// 把 input 变成客户下拉。onPick(customer)。返回 {close}。
export function customerCombo(input, panel, onPick) {
  let list = [], shown = [], active = 0, open = false;
  const draw = () => {
    shown = filterCustomers(list, input.value);
    active = Math.min(active, Math.max(0, shown.length - 1));
    panel.style.display = open ? 'block' : 'none';
    panel.innerHTML = shown.length ? `<div class="muted" style="padding:6px 12px">${input.value ? `匹配 ${shown.length} 位` : `共 ${list.length} 位客户，输入可搜索；支持英文缩写（如 hpa）`}</div>` +
      shown.map((c, i) => `<div class="sugitem ${i === active ? 'on' : ''}" data-i="${i}">${avatar(c.company || c.name, 30)}<div><b>${esc(c.company || c.name || '（无名）')}</b>
        <div class="muted">${esc(c.name && c.company ? c.name + ' · ' : '')}${esc(c.country)} ${esc((c.emails || '').split(' ')[0])}</div></div></div>`).join('')
      : `<div class="muted" style="padding:10px 12px">没有匹配的客户。<a class="ext" href="#add">去「录入客户」新建</a></div>`;
    const el = panel.querySelector('.sugitem.on');
    if (el) el.scrollIntoView({block: 'nearest'});
  };
  const show = async () => { if (!list.length) list = await allCustomers(); open = true; draw(); };
  const pick = i => { const c = shown[i]; if (!c) return; open = false; panel.style.display = 'none'; onPick(c); };
  input.addEventListener('focus', show);
  input.addEventListener('click', show);
  input.addEventListener('input', () => { active = 0; open = true; draw(); });
  input.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); active = Math.min(shown.length - 1, active + 1); open = true; draw(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); active = Math.max(0, active - 1); draw(); }
    else if (e.key === 'Enter') { e.preventDefault(); pick(active); }
    else if (e.key === 'Escape') { open = false; draw(); }
  });
  panel.addEventListener('mousedown', e => { const it = e.target.closest('[data-i]'); if (it) { e.preventDefault(); pick(Number(it.dataset.i)); } });
  document.addEventListener('mousedown', e => { if (open && e.target !== input && !panel.contains(e.target)) { open = false; draw(); } });
  return {refresh: async () => { clearCustomerCache(); list = await allCustomers(); draw(); }};
}
