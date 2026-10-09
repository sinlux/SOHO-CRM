import {$, $$, esc, api, upload, toast, nav} from '../lib.js';

// PI 导入：可一次选多个 .xls。每张 PI 先预览（产品、成本、客户匹配），确认后才写入。
const marginText = (it, d) => {
  if (!it.unit_cost_cny || !it.unit_price || d.currency !== 'USD') return '<span class="muted">—</span>';
  const m = (it.unit_price - it.unit_cost_cny * d.rate) / it.unit_price * 100;
  return `<b class="${m < 10 ? 'err' : ''}">${m.toFixed(1)}%</b>`;
};
const profitText = d => {
  let rev = 0, cost = 0;
  d.items.forEach(it => { if (it.include !== false && it.is_product && it.unit_cost_cny && it.unit_price && d.currency === 'USD') { rev += it.unit_price * it.quantity; cost += it.unit_cost_cny * d.rate * it.quantity; } });
  return rev ? `毛利估算（按当前汇率 1 CNY = ${d.rate} USD，不含运费等）：售价合计 $${rev.toFixed(2)}，采购合计 $${cost.toFixed(2)}，毛利 <b>$${(rev - cost).toFixed(2)}（${((rev - cost) / rev * 100).toFixed(1)}%）</b>。` : '';
};
export async function render(root, _arg, isCurrent) {
  const cats = (await api('/api/categories')).categories;
  if (!isCurrent()) return;
  const lighting = (cats.find(c => c.code === 'lighting') || cats[0]).id;
  let pis = [];     // 每张 PI：{data, error, status}

  root.innerHTML = `<div class="card"><h2>PI 导入（.xls）</h2>
    <p class="muted">一张 PI 会生成：产品档案 + 成本/售价历史 + 一张「已成交」报价单（单号 = PI 号）+ 客户备注，并把客户阶段推进到成交/复购。
    SKU 取 Parameters 里的 <code>Code: xxx</code>，没有就用品名；没有单价/数量的行（运费等）只保留在成交明细里。同一个 PI 号重复导入会自动跳过。</p>
    <div class="flex"><input type="file" id="files" accept=".xls" multiple style="max-width:420px"><button class="primary" id="btnUp">读取并预览</button></div>
    <div id="out" style="margin-top:14px"></div></div>
    <datalist id="dlCust"></datalist>`;

  const draw = () => {
    $('#out').innerHTML = pis.map((p, i) => {
      if (p.error) return `<div class="card" style="box-shadow:none;border:1px solid var(--line)"><b>${esc(p.name)}</b><p class="err">${esc(p.error)}</p></div>`;
      const d = p.data;
      const cust = d.matched_customer ? `<span class="tag good">已匹配：${esc(d.matched_customer.company || d.matched_customer.name)}</span>` : '<span class="tag warn">未匹配到客户，请指定</span>';
      return `<div class="card" style="box-shadow:none;border:1px solid var(--line)" data-pi="${i}">
        <div class="flex between"><h3 style="margin:0">${esc(d.invoice_no || '(无PI号)')} <span class="muted">${esc(d.date)} · ${esc(d.currency)} · 合计 ${d.total_amount ?? '—'}</span></h3>
          ${d.already_imported ? '<span class="tag warn">已导入过，将自动跳过</span>' : p.status === 'done' ? '<span class="tag good">已导入</span>' : ''}</div>
        <p class="muted">买家：${esc(d.buyer.company)} ${esc(d.buyer.name)} ${esc(d.buyer.emails)}　${cust}${d.sum_matches_total ? '' : ' <span class="err">明细金额之和与 Total 不一致，请核对</span>'}</p>
        ${p.status === 'done' ? `<p class="ok">${esc(p.msg)}</p>` : `
        <div class="flex"><label>客户 <input data-cust="${i}" list="dlCust" placeholder="输入公司名/邮箱搜索，选一个" style="min-width:280px" value="${d.customer_id ? esc(p.custLabel || '') : (d.matched_customer ? esc(d.matched_customer.company || d.matched_customer.name) : '')}"></label>
          ${d.customer_candidates.length ? `<span class="muted">候选：${d.customer_candidates.map(c => `<a href="#" data-pick="${i}:${c.id}">${esc(c.company)}</a>`).join(' / ')}</span>` : ''}
          <label>新产品类目 <select data-cat="${i}">${cats.map(c => `<option value="${c.id}" ${c.id === (d.category_id || lighting) ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></label></div>
        <div class="scroll" style="margin-top:10px;max-height:340px"><table><tr><th></th><th>SKU</th><th>名称</th><th>规格</th><th>数量</th><th>单价</th><th>金额</th><th>采购价 ¥/件</th><th>毛利率</th></tr>
          ${d.items.map((it, j) => `<tr><td><input type="checkbox" data-inc="${i}:${j}" ${it.include === false ? '' : 'checked'}></td>
            <td>${it.is_product ? `<b>${esc(it.sku)}</b> ${it.exists ? '<span class="tag warn">已有</span>' : '<span class="tag good">新建</span>'}` : '<span class="tag">非产品行</span>'}</td><td>${esc(it.name)}</td>
            <td style="max-width:240px"><div data-spec style="cursor:pointer;white-space:pre-wrap;max-height:3.6em;overflow:hidden" title="点击展开">${esc(it.spec)}</div></td>
            <td>${it.quantity ?? ''}</td><td>${it.unit_price ?? ''}</td><td>${it.amount ?? ''}</td>
            <td>${it.is_product ? `<input data-cost="${i}:${j}" type="number" step="any" min="0" value="${it.unit_cost_cny ?? ''}" style="width:90px">` : ''}</td>
            <td data-margin="${i}:${j}">${it.is_product ? marginText(it, d) : ''}</td></tr>`).join('')}</table></div>
        <p class="muted" style="margin-top:6px">每行：<b>售价（USD）</b>会存为这个客户的售价记录，<b>采购价（人民币）</b>会存为产品的成本记录（只有你看得到，不会出现在给客户的报价单上）。<span data-profit="${i}">${profitText(d)}</span></p>
        <div class="flex" style="margin-top:10px"><button class="primary" data-apply="${i}" ${d.already_imported ? 'disabled' : ''}>确认导入这张 PI</button></div>`}
      </div>`;
    }).join('') || '';
  };

  $('#btnUp').onclick = async () => {
    const fs = [...$('#files').files];
    if (!fs.length) return toast('请先选择 .xls 文件');
    $('#btnUp').disabled = true; pis = [];
    for (const f of fs) {
      $('#out').textContent = `读取 ${f.name} …`;
      try { pis.push({name: f.name, data: await upload('/api/import/pi/preview', f)}); } catch (e) { pis.push({name: f.name, error: e.message}); }
    }
    $('#btnUp').disabled = false; draw();
  };

  const out = $('#out');
  out.oninput = async e => {
    const el = e.target.closest('[data-cust]');
    if (el) {
      const i = +el.dataset.cust;
      const r = await api('/api/import/pi/customers?q=' + encodeURIComponent(el.value));
      $('#dlCust').innerHTML = r.customers.map(c => `<option value="${esc(c.company || c.name)}  #${c.id}">${esc(c.emails)}</option>`).join('');
      const m = /#(\d+)\s*$/.exec(el.value);
      if (m) { pis[i].data.customer_id = +m[1]; pis[i].custLabel = el.value.replace(/\s*#\d+\s*$/, ''); }
      return;
    }
    const cost = e.target.closest('[data-cost]');
    if (cost) {
      const [i, j] = cost.dataset.cost.split(':').map(Number), d = pis[i].data;
      d.items[j].unit_cost_cny = cost.value === '' ? null : parseFloat(cost.value);
      const m = $(`[data-margin="${i}:${j}"]`); if (m) m.innerHTML = marginText(d.items[j], d);       // 改采购价，毛利率和合计马上跟着变
      const pt = $(`[data-profit="${i}"]`); if (pt) pt.innerHTML = profitText(d);
    }
  };
  out.onchange = e => {
    const inc = e.target.closest('[data-inc]');
    if (inc) { const [i, j] = inc.dataset.inc.split(':').map(Number); pis[i].data.items[j].include = inc.checked; }
    const cat = e.target.closest('[data-cat]');
    if (cat) pis[+cat.dataset.cat].data.category_id = +cat.value;
  };
  out.onclick = async e => {
    const pick = e.target.closest('[data-pick]');
    if (pick) { e.preventDefault(); const [i, id] = pick.dataset.pick.split(':').map(Number); const c = pis[i].data.customer_candidates.find(x => x.id === id); pis[i].data.customer_id = id; pis[i].custLabel = c.company; draw(); return; }
    const sp = e.target.closest('[data-spec]');
    if (sp) { sp.style.maxHeight = sp.style.maxHeight ? '' : '3.6em'; return; }
    const ap = e.target.closest('[data-apply]');
    if (!ap) return;
    const p = pis[+ap.dataset.apply], d = p.data;
    if (!d.customer_id && d.matched_customer) d.customer_id = d.matched_customer.id;
    if (!d.customer_id) return toast('请先指定客户（在输入框里搜索并从列表里选一个）');
    ap.disabled = true;
    try {
      const r = await api('/api/import/pi/apply', 'POST', d);
      p.status = 'done';
      p.msg = r.skipped ? r.reason : `导入完成：新建产品 ${r.products_new}，已有产品 ${r.products_updated}，成本记录 ${r.cost_records}，成交单 ${r.quote_no}（${d.currency} ${r.quote_total}），客户 ${r.customer}。`;
      draw();
    } catch (err) { toast(err.message); ap.disabled = false; }
  };
}
