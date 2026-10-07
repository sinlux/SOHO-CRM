import {$, $$, esc, updateBadge} from './lib.js';
import * as list from './pages/list.js';
import * as add from './pages/add.js';
import * as customer from './pages/customer.js';
import * as reminders from './pages/reminders.js';
import * as imp from './pages/import.js';
import * as settings from './pages/settings.js';

const PAGES = {list, add, customer, reminders, import: imp, settings};
let seq = 0;

async function route() {
  const h = location.hash.slice(1) || 'list';
  const [view, arg] = h.split('/');
  const page = PAGES[view] || list;
  $$('#nav a').forEach(a => a.classList.toggle('active', a.dataset.v === (PAGES[view] ? view : 'list')
    || (view === 'customer' && a.dataset.v === 'list')));
  const mine = ++seq;                    // 页面异步加载期间用户又点了别处，则丢弃旧结果
  const root = $('#app');
  try {
    await page.render(root, arg, () => mine === seq);
  } catch (e) {
    if (mine === seq) root.innerHTML = `<div class="card err">页面加载失败：${esc(e.message)}</div>`;
  }
  updateBadge();
}

window.addEventListener('hashchange', route);
route();
