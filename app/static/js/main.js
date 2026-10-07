import {$, $$, esc, updateBadge} from './lib.js';
import * as list from './pages/list.js';
import * as add from './pages/add.js';
import * as customer from './pages/customer.js';
import * as reminders from './pages/reminders.js';
import * as imp from './pages/import.js';
import * as settings from './pages/settings.js';
import * as products from './pages/products.js';
import * as product from './pages/product.js';

const PAGES = {list, add, customer, reminders, import: imp, settings, products, product};
const TITLES = {list: '客户列表', add: '录入客户', customer: '客户详情', reminders: '跟进提醒', import: 'Excel 导入', settings: '设置', products: '产品库', product: '产品详情'};
let seq = 0;

async function route() {
  const h = location.hash.slice(1) || 'list';
  const [view, arg] = h.split('/');
  const page = PAGES[view] || list;
  $$('#nav a').forEach(a => a.classList.toggle('active', a.dataset.v === (PAGES[view] ? view : 'list')
    || (view === 'customer' && a.dataset.v === 'list') || (view === 'product' && a.dataset.v === 'products')));
  $('#pageTitle').textContent = TITLES[PAGES[view] ? view : 'list'];
  const mine = ++seq;                    // 页面异步加载期间用户又点了别处，则丢弃旧结果
  const root = $('#app');
  root.onclick = null;                  // 各页面自己绑定点击代理；切页时先清掉上一页的
  try {
    await page.render(root, arg, () => mine === seq);
  } catch (e) {
    if (mine === seq) root.innerHTML = `<div class="card err">页面加载失败：${esc(e.message)}</div>`;
  }
  syncSearchHint();
  updateBadge();
}

// 顶栏全局搜索：在产品相关页面搜产品，其它页面搜客户；回车后带着关键词进入对应列表
const isProductView = () => ['products', 'product'].includes(location.hash.slice(1).split('/')[0]);
const syncSearchHint = () => { $('#gSearch').placeholder = isProductView() ? '搜索产品：SKU / 名称 / 品牌 / 系列，回车查看' : '搜索客户：公司 / 联系人 / 邮箱 / 备注，回车查看'; };
$('#gSearch').addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  const q = e.target.value.trim(), prod = isProductView(), target = prod ? 'products' : 'list';
  (prod ? products : list).setSearch(q);
  e.target.value = '';
  if (location.hash.slice(1).split('/')[0] === target) route();
  else location.hash = '#' + target;
});
window.addEventListener('hashchange', route);
route();
