import {$, $$, esc, api, upload, toast, nav} from '../lib.js';
import {clearCustomerCache, allCustomers, filterCustomers} from '../combo.js';

// 导入 PI / 供应商报价单 / 产品清单（.xls / .xlsx 都行）：
// 识别 → 数据清洗（查重、校验）→ 你逐项确认 → 写入。有疑似重复的产品必须先决定「新建 / 合并 / 跳过」才能导入。
const KIND = {pi: '客户 PI（有售价，也可能有采购价）', supplier: '供应商报价单（人民币报价）', list: '产品清单（只建产品）'};
const FIELD_LABEL = {sku: 'SKU / 型号', name: '品名', spec: '规格', qty: '数量', unit: '单位', price: '单价 / 售价', amount: '金额', cost_unit: '采购价（单件）',
  cost_total: '采购总价（整行）', moq: 'MOQ', image: '图片（xlsx 自动提取）'};
const REASON = {sku_same: '同 SKU', sku_near: 'SKU 近似', name_high: '名称高度相似', name_mid: '名称相似', image_high: '图片高度相似', image_mid: '图片相似'};
const num = v => v === null || v === undefined || v === '' ? '' : v;

const marginText = (it, d) => {
  if (d.kind !== 'pi' || !it.unit_cost_cny || !it.unit_price || d.currency !== 'USD') return '<span class="muted">—</span>';
  const m = (it.unit_price - it.unit_cost_cny * d.rate) / it.unit_price * 100;
  return `<b class="${m < 10 ? 'err' : ''}">${m.toFixed(1)}%</b>`;
};
const profitText = d => {
  if (d.kind !== 'pi' || d.currency !== 'USD') return '';
  let rev = 0, cost = 0;
  d.items.forEach(it => { if (it.include !== false && it.is_product && it.unit_cost_cny && it.unit_price && it.quantity) { rev += it.unit_price * it.quantity; cost += it.unit_cost_cny * d.rate * it.quantity; } });
  return rev ? `毛利估算（按当前汇率 1 CNY = ${d.rate} USD，不含运费）：售价合计 $${rev.toFixed(2)}，采购合计 $${cost.toFixed(2)}，毛利 <b>$${(rev - cost).toFixed(2)}（${((rev - cost) / rev * 100).toFixed(1)}%）</b>` : '';
};

export async function render(root, _arg, isCurrent) {
  const [cats, vendorNames] = await Promise.all([api('/api/categories'), api('/api/vendors/names').catch(() => ({names: []}))]);
  const customers = await allCustomers().catch(() => []);
  if (!isCurrent()) return;
  const lighting = (cats.categories.find(c => c.code === 'lighting') || cats.categories[0] || {}).id;
  let docs = [];                       // 每份文件：{name, d(识别结果), dec{row→decision}, status, msg, showMap}

  root.innerHTML = `<div class="card"><h2>导入 PI / 供应商报价单 / 产品清单</h2>
    <p class="muted">支持 <b>.xls 和 .xlsx</b>。系统自动找表头、认列（品名 / 型号 / 数量 / 单价 / 采购价 / 图片…中英文都行），认不准的可以手动指定。
      导入前会做<b>数据清洗</b>：检查缺失和异常（售价低于采购价、数量不是整数…），并用<b>名称、图片、SKU</b>查找和库里已有产品（以及本文件内部）重复的产品——疑似重复的必须由你决定合并还是新建。
      xlsx 里的产品图片会自动取出；xls 格式的图片取不出来（建议另存为 xlsx）。</p>
    <div class="flex"><input type="file" id="files" accept=".xls,.xlsx,.xlsm" multiple style="max-width:420px"><button class="primary" id="btnUp">读取并识别</button></div>
    <div id="out" style="margin-top:14px"></div></div>
    <datalist id="dlVendors">${vendorNames.names.map(n => `<option value="${esc(n)}">`).join('')}</datalist>
    <datalist id="dlCust">${customers.slice(0, 3000).map(c => `<option value="${esc(c.company || c.name)}  #${c.id}">`).join('')}</datalist>`;

  const defaultDecisions = doc => {
    doc.dec = {};
    doc.d.items.forEach(it => {
      if (!it.is_product || !it.suggest) return;
      if (it.suggest.action === 'merge') doc.dec[it.row] = {action: 'merge', target_id: it.suggest.target_id};
      if (it.suggest.action === 'link_row') doc.dec[it.row] = {action: 'link_row', target_row: it.suggest.target_row};
    });
  };
  const pending = doc => doc.d.items.filter(it => it.is_product && it.include !== false && it.suggest && it.suggest.needs_decision && !doc.dec[it.row]);
  const errorRows = doc => doc.d.items.filter(it => it.is_product && it.include !== false && it.flags.some(f => f.level === 'error'));

  const decisionHtml = (doc, it, i) => {
    const cur = doc.dec[it.row], strong = it.matches.filter(m => m.reasons.some(r => ['name_high', 'image_high', 'sku_near', 'sku_same'].includes(r)));
    const opts = [];
    const name = `dec_${i}_${it.row}`;
    opts.push(`<label class="flex" style="gap:6px"><input type="radio" name="${name}" data-dec="${i}:${it.row}:new" ${cur && cur.action === 'new' ? 'checked' : ''}> 新建为新产品</label>`);
    it.matches.slice(0, 3).forEach(m => {
      opts.push(`<label class="flex" style="gap:8px;align-items:center"><input type="radio" name="${name}" data-dec="${i}:${it.row}:merge:${m.id}" ${cur && cur.action === 'merge' && cur.target_id === m.id ? 'checked' : ''}>
        ${m.thumb_url ? `<img src="${esc(m.thumb_url)}" style="width:40px;height:40px;object-fit:contain;border-radius:6px;background:#fff;border:1px solid var(--line)">` : '<span class="thumb ph" style="width:40px;height:40px">▣</span>'}
        <span>合并到 <b>${esc(m.sku)}</b> ${esc(m.name)} <span class="muted">（${m.reasons.map(r => REASON[r] || r).join('、')}）</span></span></label>`);
    });
    if (it.suggest && it.suggest.action === 'link_row') opts.push(`<label class="flex" style="gap:6px"><input type="radio" name="${name}" data-dec="${i}:${it.row}:link_row:${it.suggest.target_row}" ${cur && cur.action === 'link_row' ? 'checked' : ''}> 并入本文件第 ${it.suggest.target_row} 行（同一个产品）</label>`);
    opts.push(`<label class="flex" style="gap:6px"><input type="radio" name="${name}" data-dec="${i}:${it.row}:skip" ${cur && cur.action === 'skip' ? 'checked' : ''}> 跳过（不导入这个产品）</label>`);
    const need = it.suggest && it.suggest.needs_decision && !cur;
    return `<tr class="dec-row"><td></td><td colspan="9"><div style="border:1px solid ${need ? 'var(--rust, #CB705D)' : 'var(--line)'};border-radius:10px;padding:8px 12px;background:${need ? '#FFF5F2' : 'var(--leaf-soft)'}">
      <div class="muted" style="margin-bottom:4px">第 ${it.row} 行「${esc(it.name)}」${need ? ' <b class="err">需要你决定</b>' : ''}</div>${opts.join('')}</div></td></tr>`;
  };

  const mapPanel = (doc, i) => {
    const d = doc.d, heads = d.headers || [];
    const colOpt = sel => `<option value="">（没有）</option>` + heads.map((h, c) => `<option value="${c}" ${sel === c ? 'selected' : ''}>第 ${c + 1} 列：${esc(h || '空')}</option>`).join('');
    const prev = d.grid_preview || [];
    return `<div class="card" style="box-shadow:none;border:1px solid var(--line);margin:8px 0">
      <p class="muted">手动指定：先填表头在<b>第几行</b>，再为每个字段选对应的列。至少要有「品名或型号」和「数量或单价」。</p>
      <div class="scroll" style="max-height:220px"><table>${prev.slice(0, 15).map((r, k) => `<tr><td class="muted">${k + 1}</td>${r.map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</table></div>
      <div class="flex" style="margin-top:8px"><label>表头在第 <input type="number" min="1" max="60" id="mHdr_${i}" value="${d.header_row || ''}" style="width:70px"> 行</label>
        ${d.sheets && d.sheets.length > 1 ? `<label>工作表 <select id="mSheet_${i}">${d.sheets.map(s => `<option ${s === d.sheet ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select></label>` : ''}</div>
      ${d.headers ? `<div class="grid" style="margin-top:8px">${Object.entries(FIELD_LABEL).map(([f, l]) => `<div class="field"><label>${l}</label><select data-mapf="${i}:${f}">${colOpt((d.columns || {})[f])}</select></div>`).join('')}</div>` : '<p class="muted">先填表头行并点「重新识别」，再选各列。</p>'}
      <div class="flex" style="margin-top:8px"><button class="primary" data-remap="${i}">重新识别</button></div></div>`;
  };

  const card = (doc, i) => {
    if (doc.status === 'error') return `<div class="card" style="box-shadow:none;border:1px solid var(--line)"><b>${esc(doc.name)}</b><p class="err">${esc(doc.msg)}</p></div>`;
    const d = doc.d;
    if (doc.status === 'done') return `<div class="card" style="box-shadow:none;border:1px solid var(--line)"><b>${esc(doc.name)}</b> <span class="tag good">已导入</span><p class="ok">${esc(doc.msg)}</p></div>`;
    if (!d.ok) return `<div class="card" style="box-shadow:none;border:1px solid var(--line)"><b>${esc(doc.name)}</b><p class="err">${d.warnings.map(esc).join('<br>')}</p>${mapPanel(doc, i)}</div>`;
    const m = d.meta || {}, p = pending(doc), errs = errorRows(doc);
    const custSel = d.matched_customer ? `<span class="tag good">已匹配：${esc(d.matched_customer.company || d.matched_customer.name)}</span>` : '<span class="tag warn">没匹配到客户，请指定</span>';
    const kindBlock = d.kind === 'pi' ? `
      <div class="flex"><label>PI 号 <input data-meta="${i}:invoice_no" value="${esc(m.invoice_no || '')}" style="max-width:180px"></label>
        <label>日期 <input type="date" data-meta="${i}:date" value="${esc(m.date || '')}"></label>
        <label>客户 <input data-cust="${i}" list="dlCust" value="${esc(doc.custLabel || (d.matched_customer ? (d.matched_customer.company || d.matched_customer.name) : ''))}" placeholder="选择客户" style="min-width:260px"></label>${custSel}
        ${d.customer_candidates.length ? `<span class="muted">候选：${d.customer_candidates.map(c => `<a href="#" data-pick="${i}:${c.id}">${esc(c.company)}</a>`).join(' / ')}</span>` : ''}
        <label class="flex"><input type="checkbox" data-quote="${i}" ${doc.createQuote !== false ? 'checked' : ''}> 生成「已成交」报价单（会写入这个客户的售价记录）</label></div>`
      : d.kind === 'supplier' ? `
      <div class="flex"><label>供应商 <input data-sup="${i}" list="dlVendors" value="${esc(doc.supplier || '')}" placeholder="哪家供应商的报价" style="min-width:260px"></label>
        ${(d.supplier_candidates.existing || []).map(n => `<a class="ext" href="#" data-pick-sup="${i}:${esc(n)}">${esc(n)}</a>`).join(' / ')}${(d.supplier_candidates.guesses || []).map(n => `<span class="muted">（猜：<a class="ext" href="#" data-pick-sup="${i}:${esc(n)}">${esc(n)}</a>）</span>`).join('')}
        <label>询价项目 <input data-proj="${i}" placeholder="可选，如 Hotel Caribe" value="${esc(doc.project || '')}" style="max-width:200px"></label>
        <label>报价日期 <input type="date" data-meta="${i}:date" value="${esc(m.date || '')}"></label></div>` : '';
    return `<div class="card" style="box-shadow:none;border:1px solid var(--line)" data-doc="${i}">
      <div class="flex between"><h3 style="margin:0">${esc(doc.name)} ${d.already_imported ? '<span class="tag warn">此 PI 号已导入过，会自动跳过</span>' : ''}</h3>
        <div class="flex"><label>类型 <select data-kind="${i}">${Object.entries(KIND).map(([k, l]) => `<option value="${k}" ${d.kind === k ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></label>
          <label>币种 <select data-cur="${i}"><option ${d.currency === 'USD' ? 'selected' : ''}>USD</option><option ${d.currency === 'CNY' ? 'selected' : ''}>CNY</option></select></label>
          <button class="small" data-toggle-map="${i}">识别不对？手动指定列</button></div></div>
      <p class="muted" style="margin:6px 0">表头在第 ${d.header_row} 行，共 ${d.summary.products} 个产品行，${d.summary.with_image} 个带图${d.is_xls ? '（xls 取不出图片）' : ''}。${d.warnings.length ? '<br><span class="err">' + d.warnings.map(esc).join('；') + '</span>' : ''}
        ${d.sum_matches_total ? '' : '<br><span class="err">各行金额之和与文件里的合计不一致，请核对</span>'}</p>
      ${doc.showMap ? mapPanel(doc, i) : ''}
      ${kindBlock}
      <div class="flex" style="margin-top:8px"><label>新建产品默认类目 <select data-cat="${i}">${cats.categories.map(c => `<option value="${c.id}" ${(doc.category || lighting) === c.id ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></label>
        <button class="small" data-allsuggest="${i}">全部按建议处理</button><button class="small" data-allnew="${i}">疑似重复的全部新建</button></div>
      <div class="scroll" style="margin-top:10px;max-height:520px"><table><tr><th></th><th>图片</th><th>SKU</th><th>品名</th><th>规格</th><th>数量</th><th>${d.kind === 'supplier' ? '报价' : '单价'}</th><th>采购价¥/件</th><th>${d.kind === 'pi' ? '毛利率' : ''}</th><th>清洗提示</th></tr>
        ${d.items.map(it => itemRow(doc, it, i)).join('')}</table></div>
      <p class="muted" style="margin-top:6px" data-profit="${i}">${profitText(d)}</p>
      <div class="flex" style="margin-top:10px"><button class="primary" data-apply="${i}" ${p.length || errs.length || d.already_imported ? 'disabled' : ''}>确认导入</button>
        <span class="${p.length || errs.length ? 'err' : 'muted'}" data-status="${i}">${p.length ? `还有 ${p.length} 个疑似重复的产品没决定` : ''}${p.length && errs.length ? '；' : ''}${errs.length ? `${errs.length} 行有错误（改正或取消勾选）` : ''}${!p.length && !errs.length ? '核对完成，可以导入' : ''}</span></div></div>`;
  };

  const flagHtml = it => it.flags.filter(f => f.level !== 'info' || ['exists', 'sku_from_name'].includes(f.code)).map(f => `<div class="${f.level === 'error' ? 'err' : f.level === 'warn' ? '' : 'muted'}" style="font-size:12px;${f.level === 'warn' ? 'color:#9A6A1E' : ''}">${f.level === 'error' ? '⛔' : f.level === 'warn' ? '⚠' : 'ℹ'} ${esc(f.msg)}</div>`).join('');

  const itemRow = (doc, it, i) => {
    const d = doc.d, r = it.row;
    if (!it.is_product) return `<tr style="opacity:.75"><td><input type="checkbox" data-inc="${i}:${r}" ${it.include === false ? '' : 'checked'}></td><td></td><td colspan="3"><span class="tag">非产品行</span> ${esc(it.name)}</td><td></td><td>${num(it.amount)}</td><td></td><td></td><td>${flagHtml(it)}</td></tr>`;
    const img = it.image ? `<img src="/api/import/doc/${doc.sid}/image/${encodeURIComponent(it.image)}" style="width:46px;height:46px;object-fit:contain;border-radius:6px;background:#fff;border:1px solid var(--line)">` : '';
    const main = `<tr data-row="${i}:${r}" style="${it.include === false ? 'opacity:.5' : ''}"><td><input type="checkbox" data-inc="${i}:${r}" ${it.include === false ? '' : 'checked'}></td><td>${img}</td>
      <td><input data-f="${i}:${r}:sku" value="${esc(it.sku)}" style="width:120px"></td><td><input data-f="${i}:${r}:name" value="${esc(it.name)}" style="min-width:180px"></td>
      <td style="max-width:220px"><div data-spec style="cursor:pointer;white-space:pre-wrap;max-height:3.6em;overflow:hidden;font-size:12px" title="点击展开">${esc(it.spec)}</div></td>
      <td><input data-f="${i}:${r}:quantity" value="${num(it.quantity)}" style="width:70px"></td><td><input data-f="${i}:${r}:unit_price" value="${num(it.unit_price)}" style="width:80px"></td>
      <td><input data-f="${i}:${r}:unit_cost_cny" value="${num(it.unit_cost_cny)}" style="width:80px"></td><td data-margin="${i}:${r}">${marginText(it, d)}</td><td style="min-width:230px">${flagHtml(it)}</td></tr>`;
    const needDec = it.matches.length || (it.suggest && it.suggest.action === 'link_row');
    return main + (needDec && it.include !== false ? decisionHtml(doc, it, i) : '');
  };

  const draw = () => {
    $('#out').innerHTML = docs.map(card).join('') || '';
  };
  const redraw = () => { const y = window.scrollY; draw(); window.scrollTo(0, y); };

  const addDoc = (name, d) => {
    const doc = {name, d, sid: d.session_id, status: d.ok === false || d.ok === undefined ? 'ok' : 'ok', category: lighting};
    if (d.ok) { defaultDecisions(doc); if (d.matched_customer) doc.custId = d.matched_customer.id; if (d.supplier_candidates && d.supplier_candidates.existing[0]) doc.supplier = d.supplier_candidates.existing[0]; }
    return doc;
  };

  $('#btnUp').onclick = async () => {
    const fs = [...$('#files').files];
    if (!fs.length) return toast('请先选择 .xls / .xlsx 文件');
    $('#btnUp').disabled = true; docs = [];
    for (const f of fs) {
      $('#out').textContent = `读取 ${f.name} …`;
      try { docs.push(addDoc(f.name, await upload('/api/import/doc/upload', f))); } catch (e) { docs.push({name: f.name, status: 'error', msg: e.message}); }
    }
    $('#btnUp').disabled = false; draw();
  };

  const reparse = async (i, extra) => {
    const doc = docs[i];
    try {
      const d = await api(`/api/import/doc/${doc.sid}/reparse`, 'POST', extra);
      doc.d = d; doc.d.session_id = doc.sid;
      doc.custId = d.matched_customer ? d.matched_customer.id : doc.custId;
      defaultDecisions(doc);
      redraw();
    } catch (e) { toast(e.message); }
  };

  const out = $('#out');
  out.onchange = async e => {
    const t = e.target, ds = t.dataset;
    if (ds.inc) { const [i, r] = ds.inc.split(':').map(Number); docs[i].d.items.find(x => x.row === r).include = t.checked; return redraw(); }
    if (ds.kind) return reparse(+ds.kind, {kind: t.value, currency: docs[+ds.kind].d.currency, sheet: docs[+ds.kind].d.sheet, header_row: docs[+ds.kind].d.header_row, columns: docs[+ds.kind].d.columns});
    if (ds.cur) return reparse(+ds.cur, {kind: docs[+ds.cur].d.kind, currency: t.value, sheet: docs[+ds.cur].d.sheet, header_row: docs[+ds.cur].d.header_row, columns: docs[+ds.cur].d.columns});
    if (ds.cat) { docs[+ds.cat].category = +t.value; return; }
    if (ds.quote) { docs[+ds.quote].createQuote = t.checked; return; }
    if (ds.dec) {
      const [i, r, action, target] = ds.dec.split(':'), doc = docs[+i];
      doc.dec[+r] = action === 'merge' ? {action, target_id: +target} : action === 'link_row' ? {action, target_row: +target} : {action};
      return redraw();
    }
    if (ds.f) {
      const [i, r, k] = ds.f.split(':'), it = docs[+i].d.items.find(x => x.row === +r);
      it[k] = ['quantity', 'unit_price', 'unit_cost_cny'].includes(k) ? (t.value === '' ? null : parseFloat(t.value)) : t.value;
      return redraw();
    }
    if (ds.meta) { const [i, k] = ds.meta.split(':'); docs[+i].d.meta[k] = t.value; return; }
    if (ds.sup !== undefined) { docs[+ds.sup].supplier = t.value; return; }
    if (ds.proj !== undefined) { docs[+ds.proj].project = t.value; return; }
    if (ds.cust !== undefined) {
      const m = /#(\d+)\s*$/.exec(t.value);
      if (m) { docs[+ds.cust].custId = +m[1]; docs[+ds.cust].custLabel = t.value.replace(/\s*#\d+\s*$/, ''); }
      else {
        const hit = filterCustomers(customers, t.value, 1)[0];                    // 输入缩写/名称也行：取最匹配的一个
        if (hit && t.value.trim()) { docs[+ds.cust].custId = hit.id; docs[+ds.cust].custLabel = hit.company || hit.name; redraw(); }
      }
    }
  };
  out.onclick = async e => {
    const t = e.target.closest('button,a,[data-spec]');
    if (!t) return;
    const ds = t.dataset;
    if (ds.spec !== undefined) { t.style.maxHeight = t.style.maxHeight ? '' : '3.6em'; return; }
    if (ds.pick) { e.preventDefault(); const [i, id] = ds.pick.split(':').map(Number), c = docs[i].d.customer_candidates.find(x => x.id === id); docs[i].custId = id; docs[i].custLabel = c.company; return redraw(); }
    if (ds.pickSup) { e.preventDefault(); const k = ds.pickSup.indexOf(':'); docs[+ds.pickSup.slice(0, k)].supplier = ds.pickSup.slice(k + 1); return redraw(); }
    if (ds.toggleMap !== undefined) { docs[+ds.toggleMap].showMap = !docs[+ds.toggleMap].showMap; return redraw(); }
    if (ds.remap !== undefined) {
      const i = +ds.remap, doc = docs[i], cols = {};
      $$(`[data-mapf^="${i}:"]`).forEach(s => { if (s.value !== '') cols[s.dataset.mapf.split(':')[1]] = +s.value; });
      return reparse(i, {header_row: parseInt($(`#mHdr_${i}`).value, 10) || null, sheet: $(`#mSheet_${i}`) ? $(`#mSheet_${i}`).value : doc.d.sheet, columns: Object.keys(cols).length ? cols : null});
    }
    if (ds.allsuggest !== undefined) {
      const doc = docs[+ds.allsuggest];
      doc.d.items.forEach(it => { if (it.is_product && it.suggest && !doc.dec[it.row]) { const s = it.suggest; doc.dec[it.row] = s.action === 'link_row' ? {action: 'link_row', target_row: s.target_row} : {action: 'merge', target_id: s.target_id}; } });
      return redraw();
    }
    if (ds.allnew !== undefined) {
      const doc = docs[+ds.allnew];
      doc.d.items.forEach(it => { if (it.is_product && it.suggest && it.suggest.needs_decision && !doc.dec[it.row]) doc.dec[it.row] = {action: 'new'}; });
      return redraw();
    }
    if (ds.apply !== undefined) {
      const i = +ds.apply, doc = docs[i], d = doc.d;
      const body = {kind: d.kind, currency: d.currency, date: d.meta.date || '', invoice_no: d.meta.invoice_no || '', category_id: doc.category || lighting,
        customer_id: doc.custId || null, create_quote: doc.createQuote !== false, supplier_name: doc.supplier || '', project: doc.project || '',
        items: d.items.map(it => ({row: it.row, include: it.include !== false, sku: it.sku, name: it.name, spec: it.spec, unit: it.unit, quantity: it.quantity, unit_price: it.unit_price,
          unit_cost_cny: it.unit_cost_cny, decision: doc.dec[it.row] || null}))};
      if (d.kind === 'pi' && !body.customer_id) return toast('请先指定客户（在「客户」框里选一个）');
      if (!confirm(`确认导入「${doc.name}」？\n${d.kind === 'pi' && body.create_quote ? '会生成一张已成交报价单，并写入售价/成本记录。' : '会按你确认的结果新建 / 合并产品。'}`)) return;
      t.disabled = true;
      try {
        const r = await api(`/api/import/doc/${doc.sid}/apply`, 'POST', body);
        doc.status = 'done';
        doc.msg = r.skipped ? r.reason : `完成：新建产品 ${r.products_new}，并入已有 ${r.products_merged}，跳过 ${r.products_skipped}，成本记录 ${r.cost_records}${r.supplier_quotes ? '，供应商报价 ' + r.supplier_quotes : ''}${r.quote_no ? '，成交单 ' + r.quote_no + '（' + d.currency + ' ' + r.quote_total + '）' : ''}。`;
        clearCustomerCache(); redraw();
      } catch (err) { toast(err.message); t.disabled = false; }
    }
  };
}
