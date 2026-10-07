import {$, $$, esc, api, nav, toast} from '../lib.js';
import {money} from './products.js';

const CURRENCIES = ['CNY', 'USD', 'EUR', 'VND'];
const readFile = f => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });

function fieldInput(f, val) {
  const unit = f.unit ? `<span class="unit">${esc(f.unit)}</span>` : '';
  const id = 'fv_' + f.id;
  if (f.type === 'select') return `<select id="${id}" data-fid="${f.id}"><option value=""></option>${f.options_list.map(o => `<option ${o === val ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`;
  if (f.type === 'multi') {
    const cur = new Set((val || '').split(',').map(s => s.trim()).filter(Boolean));
    return `<div class="check-group" data-fid="${f.id}" data-multi="1">${f.options_list.map(o => `<label><input type="checkbox" value="${esc(o)}" ${cur.has(o) ? 'checked' : ''}>${esc(o)}</label>`).join('')}</div>`;
  }
  if (f.type === 'textarea') return `<textarea id="${id}" data-fid="${f.id}" placeholder="${esc(f.placeholder || '')}">${esc(val || '')}</textarea>`;
  return `<div class="flex" style="flex-wrap:nowrap"><input id="${id}" data-fid="${f.id}" ${f.type === 'number' ? 'type="number" step="any"' : ''} value="${esc(val || '')}" placeholder="${esc(f.placeholder || '')}">${unit}</div>`;
}

export async function render(root, arg, isCurrent) {
  const isNew = arg === 'new';
  const pid = isNew ? null : parseInt(arg, 10);
  if (!isNew && !pid) return nav('products');
  const [cats, rate] = await Promise.all([api('/api/categories'), api('/api/rate')]);
  let p;
  if (isNew) {
    p = {sku: '', name: '', category_id: cats.categories[0]?.id, cost: null, cost_currency: 'CNY', profit_rate: 0.25, moq: null, lead_time: null,
      supplier: '', remark: '', spec_text: '', image_url: '', field_values: {}, suggested_price: null};
  } else {
    try { p = (await api('/api/products/' + pid)).product; } catch (e) {
      root.innerHTML = `<div class="card"><button id="back">← 返回</button><p class="err" style="margin-top:10px">${esc(e.message)}</p></div>`;
      $('#back').onclick = () => nav('products');
      return;
    }
  }
  if (!isCurrent()) return;
  let fields = isNew ? (await api(`/api/categories/${p.category_id}/fields`)).fields : p.fields;
  let values = {...p.field_values};
  let newImage = null, removeImage = false;

  root.innerHTML = `<div class="card"><div class="flex between"><div class="flex"><button id="back">← 返回</button>
      <h2 style="margin:0">${isNew ? '新建产品' : esc(p.sku) + ' · ' + esc(p.name)}</h2></div>
      <div class="flex"><button class="primary" id="btnSave">${isNew ? '创建产品' : '保存修改'}</button>${isNew ? '' : '<button class="danger" id="btnDel">删除产品</button>'}</div></div>
    <div class="flex" style="align-items:flex-start;gap:22px;margin-top:16px">
      <div><div class="img-box" id="imgBox" tabindex="0">${p.image_url ? `<img src="${esc(p.image_url)}" alt="">` : '点击选择图片<br>或在此处 Ctrl+V 粘贴'}</div>
        <div class="flex" style="margin-top:8px"><button class="small" id="btnPick">选择文件</button><button class="small danger" id="btnRmImg" ${p.image_url ? '' : 'style="display:none"'}>移除</button></div>
        <input type="file" id="imgFile" accept="image/*" style="display:none"></div>
      <div style="flex:1;min-width:300px"><div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(210px,1fr))">
        <div class="field"><label>类目</label><select id="pCat">${cats.categories.map(c => `<option value="${c.id}" ${c.id === p.category_id ? 'selected' : ''}>${esc(c.icon || '')} ${esc(c.name)}</option>`).join('')}</select></div>
        <div class="field"><label>SKU</label><div class="flex" style="flex-wrap:nowrap"><input id="pSku" value="${esc(p.sku)}"><button id="btnAuto" title="按类目和子类前缀自动生成">自动编号</button></div></div>
        <div class="field"><label>产品名称</label><input id="pName" value="${esc(p.name)}"></div>
        <div class="field"><label>供应商</label><input id="pSup" value="${esc(p.supplier || '')}"></div>
        <div class="field"><label>成本价</label><div class="flex" style="flex-wrap:nowrap"><input id="pCost" type="number" step="any" min="0" value="${p.cost ?? ''}">
          <select id="pCur" style="max-width:100px">${CURRENCIES.map(c => `<option ${c === p.cost_currency ? 'selected' : ''}>${c}</option>`).join('')}</select></div></div>
        <div class="field"><label>利润率（%）</label><input id="pProfit" type="number" step="any" min="0" value="${Math.round(p.profit_rate * 10000) / 100}"></div>
        <div class="field"><label>MOQ</label><input id="pMoq" type="number" min="0" value="${p.moq ?? ''}"></div>
        <div class="field"><label>交期（天）</label><input id="pLead" type="number" min="0" value="${p.lead_time ?? ''}"></div>
      </div></div>
      <div class="price-card"><div class="muted">建议价（USD）</div><div class="big" id="sugg">—</div><div class="muted" id="suggNote"></div>
        <div class="muted" style="margin-top:8px">汇率 1 CNY = ${rate.rate} USD</div></div></div>
    <div class="field" style="margin-top:16px"><label>规格描述（主规格字段：把尺寸、材质、参数、包装等都写在这里）</label><textarea id="pSpec" style="min-height:120px">${esc(p.spec_text)}</textarea></div>
    <h3>结构化规格（选填）</h3><div class="grid" id="fieldsBox"></div>
    <div class="field" style="margin-top:14px"><label>内部备注</label><textarea id="pRemark">${esc(p.remark || '')}</textarea></div></div>
    ${isNew ? '' : `<div class="card"><div class="sec-title"><h3>🏭 供应商比价</h3></div><div id="supBox">加载中…</div>
      <div class="flex" style="margin-top:12px"><input id="sName" placeholder="供应商名" style="max-width:180px"><input id="sPrice" type="number" step="any" min="0" placeholder="人民币报价" style="max-width:140px">
        <input id="sDate" type="date" style="max-width:160px"><input id="sRemark" placeholder="备注" style="max-width:220px"><button id="btnAddSup">添加报价</button></div></div>
    <div class="card"><div class="sec-title"><h3>📈 价格历史</h3><span class="muted">只追加不覆盖；连续保存相同成本不重复记录</span></div><div id="histBox">加载中…</div>
      <div class="flex" style="margin-top:14px"><select id="hType" style="max-width:110px"><option value="cost">成本</option><option value="sell">售价</option></select>
        <input id="hPrice" type="number" step="any" min="0" placeholder="金额" style="max-width:120px"><select id="hCur" style="max-width:100px">${CURRENCIES.map(c => `<option>${c}</option>`).join('')}</select>
        <input id="hDate" type="date" style="max-width:160px"><input id="hCust" list="custList" placeholder="客户（售价时可选）" style="max-width:200px"><datalist id="custList"></datalist>
        <input id="hSrc" placeholder="来源，如 PI 号" style="max-width:160px"><button id="btnAddHist">添加记录</button></div></div>`}`;

  const drawFields = () => {
    $('#fieldsBox').innerHTML = fields.length ? fields.map(f => `<div class="field" ${f.type === 'textarea' ? 'style="grid-column:1/-1"' : ''}><label>${esc(f.label)}</label>${fieldInput(f, values[f.id])}</div>`).join('')
      : '<span class="muted">该类目没有结构化字段</span>';
  };
  const collectValues = () => {
    const out = {};
    $$('#fieldsBox [data-fid]').forEach(el => {
      const id = el.dataset.fid;
      out[id] = el.dataset.multi ? $$('input:checked', el).map(i => i.value).join(',') : el.value;
    });
    return out;
  };
  const suggest = () => {
    const cost = parseFloat($('#pCost').value), cur = $('#pCur').value, pr = parseFloat($('#pProfit').value);
    const saved = !isNew && cost === p.cost && cur === p.cost_currency && Math.abs(pr / 100 - p.profit_rate) < 1e-9;
    let v = null;
    if (saved) v = p.suggested_price;
    else if (cost > 0) v = Math.round(cost * (cur === 'CNY' ? rate.rate : 1) * (1 + (isNaN(pr) ? 25 : pr) / 100) * 100) / 100;
    $('#sugg').textContent = v === null || v === undefined ? '—' : '$ ' + v.toFixed(2);
    $('#suggNote').textContent = (cur === 'EUR' || cur === 'VND') && v ? `${cur} 成本未换算为美元，仅供参考` : (saved ? '已保存的值（可能已对齐最新成交价）' : '保存后生效');
  };
  drawFields(); suggest();
  ['#pCost', '#pCur', '#pProfit'].forEach(s => $(s).addEventListener('input', suggest));
  $('#back').onclick = () => nav('products');

  $('#pCat').onchange = async e => {
    values = collectValues();
    fields = (await api(`/api/categories/${e.target.value}/fields`)).fields;
    const ids = new Set(fields.map(f => String(f.id)));
    values = Object.fromEntries(Object.entries(values).filter(([k]) => ids.has(k)));
    drawFields();
  };
  $('#btnAuto').onclick = async () => {
    const sub = fields.find(f => f.key === 'subcategory');
    const el = sub && $(`#fv_${sub.id}`);
    try {
      const r = await api(`/api/categories/${$('#pCat').value}/next_sku?subcategory=${encodeURIComponent(el ? el.value : '')}`);
      $('#pSku').value = r.sku;
    } catch (e) { toast(e.message); }
  };

  // 图片：点击选择 / Ctrl+V 粘贴
  const setImage = async file => {
    newImage = await readFile(file); removeImage = false;
    $('#imgBox').innerHTML = `<img src="${newImage}" alt="">`; $('#btnRmImg').style.display = '';
  };
  $('#btnPick').onclick = () => $('#imgFile').click();
  $('#imgBox').onclick = () => $('#imgFile').click();
  $('#imgFile').onchange = e => e.target.files[0] && setImage(e.target.files[0]);
  const onPaste = e => {
    for (const it of (e.clipboardData || {}).items || []) if (it.type.startsWith('image/')) { setImage(it.getAsFile()); e.preventDefault(); return; }
  };
  document.addEventListener('paste', onPaste);
  const stopPaste = () => { document.removeEventListener('paste', onPaste); window.removeEventListener('hashchange', stopPaste); };
  window.addEventListener('hashchange', stopPaste);
  $('#btnRmImg').onclick = () => { newImage = null; removeImage = true; $('#imgBox').innerHTML = '点击选择图片<br>或在此处 Ctrl+V 粘贴'; $('#btnRmImg').style.display = 'none'; };

  const num = id => $(id).value === '' ? null : $(id).value;
  $('#btnSave').onclick = async () => {
    const pr = parseFloat($('#pProfit').value);
    const body = {category_id: Number($('#pCat').value), sku: $('#pSku').value, name: $('#pName').value, supplier: $('#pSup').value,
      cost: num('#pCost'), cost_currency: $('#pCur').value, profit_rate: isNaN(pr) ? null : pr / 100, moq: num('#pMoq'), lead_time: num('#pLead'),
      spec_text: $('#pSpec').value, remark: $('#pRemark').value, field_values: collectValues()};
    if (newImage) body.image_data = newImage; else if (removeImage) body.remove_image = true;
    $('#btnSave').disabled = true;
    try {
      if (isNew) { const r = await api('/api/products', 'POST', body); toast('产品已创建'); nav('product', r.id); }
      else { await api('/api/products/' + pid, 'PUT', body); toast('已保存'); render(root, arg, isCurrent); }
    } catch (e) { toast(e.message); $('#btnSave').disabled = false; }
  };
  if (isNew) return;

  $('#btnDel').onclick = async () => {
    try {
      const {impact: i} = await api(`/api/products/${pid}/impact`);
      if (!confirm(`确定删除「${p.sku} ${p.name}」？\n将同时永久删除：价格记录 ${i.price_records} 条、供应商报价 ${i.supplier_quotes} 条。\n已有的报价单明细（${i.quote_items} 条）会保留为快照。\n此操作不可恢复。`)) return;
      await api('/api/products/' + pid, 'DELETE', {confirm: true});
      toast('已删除'); nav('products');
    } catch (e) { toast(e.message); }
  };

  // ---- 供应商比价 ----
  const drawSup = list => {
    $('#supBox').innerHTML = list.length ? `<table><tr><th>供应商</th><th>人民币报价</th><th>日期</th><th>备注</th><th></th></tr>
      ${list.map(s => `<tr><td><b>${esc(s.supplier_name)}</b> ${s.is_adopted ? '<span class="tag good">已采纳</span>' : ''}</td><td>${money(s.price_cny, '¥')}</td><td>${esc(s.quote_date || '')}</td>
        <td class="muted">${esc(s.remark || '')}${s.screenshot_url ? ` <a class="ext" href="${esc(s.screenshot_url)}" target="_blank" rel="noopener">截图</a>` : ''}</td>
        <td class="flex">${s.is_adopted ? '' : `<button class="small primary" data-adopt="${s.id}">采纳</button>`}<button class="small danger" data-sdel="${s.id}">删</button></td></tr>`).join('')}</table>
      <p class="muted" style="margin-top:6px">「采纳」会把该报价设为产品当前成本（CNY）并写入价格历史。</p>` : '<span class="muted">还没有供应商报价</span>';
  };
  const loadSup = async () => drawSup((await api(`/api/products/${pid}/suppliers`)).suppliers);
  const loadHist = async () => {
    const h = (await api(`/api/products/${pid}/price_history`)).history;
    $('#histBox').innerHTML = h.length ? `<div class="timeline">${h.map(x => `<div class="tl-item ${x.price_type}"><div class="flex">
      <b>${x.price_type === 'cost' ? '成本' : '售价'}</b>${money(x.price, x.currency)}<span class="muted">${esc(x.effective_date || '')}</span>
      ${x.customer_company ? `<span class="tag">${esc(x.customer_company)}</span>` : ''}${x.source ? `<span class="muted">${esc(x.source)}</span>` : ''}
      <button class="small danger" data-hdel="${x.id}">删</button></div>${x.note ? `<div class="muted">${esc(x.note)}</div>` : ''}</div>`).join('')}</div>` : '<span class="muted">暂无价格记录</span>';
  };
  await Promise.all([loadSup(), loadHist()]);

  root.onclick = async e => {
    const b = e.target.closest('button');
    if (!b) return;
    try {
      if (b.dataset.adopt) { const r = await api(`/api/suppliers/${b.dataset.adopt}/adopt`, 'POST', {}); toast('已采纳，成本已更新'); render(root, arg, isCurrent); return r; }
      if (b.dataset.sdel) { if (confirm('删除这条供应商报价？')) { await api('/api/suppliers/' + b.dataset.sdel, 'DELETE', {}); loadSup(); } }
      if (b.dataset.hdel) { if (confirm('删除这条价格记录？当前成本/建议价会按剩余的最新记录重新对齐。')) { await api('/api/price_history/' + b.dataset.hdel, 'DELETE', {}); toast('已删除'); render(root, arg, isCurrent); } }
    } catch (err) { toast(err.message); }
  };
  $('#btnAddSup').onclick = async () => {
    try {
      await api(`/api/products/${pid}/suppliers`, 'POST', {supplier_name: $('#sName').value, price_cny: num('#sPrice'), quote_date: $('#sDate').value || null, remark: $('#sRemark').value});
      toast('已添加'); loadSup(); ['#sName', '#sPrice', '#sDate', '#sRemark'].forEach(s => $(s).value = '');
    } catch (e) { toast(e.message); }
  };
  // 客户联想：输入 -> datalist；选中项形如「公司名 #12」
  let ctimer;
  $('#hCust').oninput = () => { clearTimeout(ctimer); ctimer = setTimeout(async () => {
    const q = $('#hCust').value.trim();
    if (q.length < 1 || /#\d+$/.test(q)) return;
    const d = await api('/api/customers?limit=8&search=' + encodeURIComponent(q));
    $('#custList').innerHTML = d.customers.map(c => `<option value="${esc((c.company || c.name) + ' #' + c.id)}"></option>`).join('');
  }, 200); };
  $('#btnAddHist').onclick = async () => {
    const m = $('#hCust').value.match(/#(\d+)$/);
    if ($('#hCust').value.trim() && !m) return toast('客户请从联想列表里选择');
    try {
      const r = await api(`/api/products/${pid}/price_history`, 'POST', {price_type: $('#hType').value, price: $('#hPrice').value, currency: $('#hCur').value,
        effective_date: $('#hDate').value || null, customer_id: m ? Number(m[1]) : null, source: $('#hSrc').value});
      toast(r.recorded ? '已添加，当前价已按最新记录对齐' : '与最新一条成本相同，未重复记录');
      render(root, arg, isCurrent);
    } catch (e) { toast(e.message); }
  };
}
