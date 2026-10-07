import {$, esc, api, nav} from '../lib.js';

const money = n => Number(n || 0).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const STAGE_COLOR = {'潜在': '#B9C7D3', '已联系': '#8FB8CE', '已报价': '#E3C27D', '已寄样': '#D9A86C', '成交': '#6FA287', '复购': '#4F8468', '沉睡': '#C9B9A6', '未设置': '#E4DCCB'};
const STATUS_COLOR = {draft: '#D8D2C4', sent: '#8FB8CE', accepted: '#6FA287', rejected: '#D98F7E', expired: '#C9B9A6'};

function bars(rows, key, label, color) {
  const max = Math.max(1, ...rows.map(r => r.n));
  return rows.map(r => `<div class="flex nowrap" style="gap:10px;margin:6px 0"><span style="width:64px;flex:none">${esc(r[label])}</span>
    <div style="flex:1;background:#F0EADB;border-radius:999px;height:12px;overflow:hidden"><div style="width:${r.n / max * 100}%;height:100%;background:${color[r[key]] || '#8FB8CE'};border-radius:999px"></div></div>
    <b style="width:36px;text-align:right">${r.n}</b></div>`).join('');
}

export async function render(root, _arg, isCurrent) {
  const d = await api('/api/dashboard');
  if (!isCurrent()) return;
  const stat = (label, value, sub = '') => `<div class="card" style="margin:0;padding:16px 20px"><div class="muted">${label}</div>
    <div style="font-size:28px;font-weight:700;margin-top:4px">${value}</div><div class="muted" style="margin-top:2px">${sub}</div></div>`;
  const byCur = Object.entries(d.won_amount_by_currency).map(([c, v]) => `${c} ${money(v)}`).join(' + ');
  root.innerHTML = `
  <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr));margin-bottom:16px">
    ${stat('客户', d.customers, d.reminders_due ? `<a href="#reminders" class="ext">${d.reminders_due} 条提醒待跟进</a>` : '没有逾期提醒')}
    ${stat('产品', d.products)}
    ${stat('报价单', d.quotes, `近 30 天 ${d.quotes_30d} 张${d.drafts ? ` · 另有 ${d.drafts} 张草稿` : ''}`)}
    ${stat('成交转化率', d.conversion + '%', `已成交 ${d.won} / ${d.quotes}（草稿不计）`)}
    ${stat('累计成交额', '$ ' + money(d.won_amount_usd), esc(byCur) + (d.other_currency_quotes ? ` · ${d.other_currency_quotes} 张旧欧元等报价未计入` : ''))}
  </div>
  <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(340px,1fr))">
    <div class="card" style="margin:0"><h3 style="margin-top:0">报价状态</h3>${bars(d.status_dist, 'status', 'label', STATUS_COLOR)}</div>
    <div class="card" style="margin:0"><h3 style="margin-top:0">客户阶段</h3>${bars(d.stage_dist, 'stage', 'stage', STAGE_COLOR)}</div>
    <div class="card" style="margin:0"><h3 style="margin-top:0">Top 10 客户（按成交额，USD 折算）</h3>
      ${d.top_customers.length ? `<table><tr><th>客户</th><th>报价</th><th>成交 $</th><th>报价额 $</th></tr>${d.top_customers.map(c => `<tr><td><a class="ext" href="#customer/${c.id}">${esc(c.company || c.name)}</a></td>
        <td>${c.quote_count}</td><td><b>${money(c.won_usd)}</b></td><td class="muted">${money(c.quoted_usd)}</td></tr>`).join('')}</table>` : '<span class="muted">还没有报价数据</span>'}</div>
    <div class="card" style="margin:0"><h3 style="margin-top:0">Top 10 产品（按报价金额，USD 折算）</h3>
      ${d.top_products.length ? `<table><tr><th>产品</th><th>报价单</th><th>数量</th><th>金额 $</th></tr>${d.top_products.map(p => `<tr><td>${p.product_id ? `<a class="ext" href="#product/${p.product_id}">${esc(p.sku || p.name)}</a>` : esc(p.sku || p.name)}
        <div class="muted">${esc(p.name)}</div></td><td>${p.quote_count}</td><td>${p.total_qty}</td><td><b>${money(p.amount_usd)}</b></td></tr>`).join('')}</table>` : '<span class="muted">还没有报价数据</span>'}</div>
  </div>
  <div class="card" style="margin-top:16px"><h3 style="margin-top:0">😴 沉睡预警 <span class="muted">LV4 以上、90 天内没有备注也没有报价${d.dormant_total > d.dormant.length ? `（共 ${d.dormant_total} 位，显示前 ${d.dormant.length}）` : `（共 ${d.dormant_total} 位）`}</span></h3>
    ${d.dormant.length ? `<table><tr><th>客户</th><th>LV</th><th>阶段</th><th>最近备注</th><th>最近报价</th></tr>${d.dormant.map(c => `<tr><td><a class="ext" href="#customer/${c.id}">${esc(c.company || c.name)}</a></td>
      <td>${c.lv}</td><td>${esc(c.stage)}</td><td class="muted">${esc((c.last_note || '—').slice(0, 10))}</td><td class="muted">${esc((c.last_quote || '—').slice(0, 10))}</td></tr>`).join('')}</table>` : '<span class="ok">没有需要唤醒的重点客户 👍</span>'}</div>`;
}
