import {$, $$, esc, api, uploadProgress, toast, nav} from '../lib.js';

// Excel 批量导入产品：5 步向导。每一步都由你确认，最后一步逐行核对后才写入。
const STEPS = ['上传文件', '工作表与表头', '导入到哪个类目', '列映射', '逐行预览并导入'];
const money = n => (n === null || n === undefined) ? '—' : Number(n).toLocaleString('en-US', {maximumFractionDigits: 4});

export async function render(root, _arg, isCurrent) {
  const cats = (await api('/api/categories')).categories;
  if (!isCurrent()) return;
  let sid = null, sheets = [], info = null, meta = null, mapping = {}, pv = null, skip = new Set(), loaded = [];

  const stepBar = n => `<div class="flex" style="margin-bottom:14px">${STEPS.map((t, i) =>
    `<span class="tag ${i + 1 === n ? 'good' : ''}" style="${i + 1 < n ? 'opacity:.6' : ''}">${i + 1}. ${t}</span>`).join('')}</div>`;
  const card = (n, body) => { root.innerHTML = `<div class="card"><h2>Excel 批量导入产品</h2>${stepBar(n)}${body}</div>`; };
  const fail = e => toast(e.message);

  const step1 = () => {
    card(1, `<p class="muted">选择产品表（.xlsx，最大 500MB）。图片会按所在行自动提取；导入前不会改动任何产品数据。</p>
      <div class="flex"><input type="file" id="file" accept=".xlsx,.xlsm" style="max-width:380px"><button class="primary" id="btnUp">上传</button></div>
      <div id="prog" style="margin-top:10px"></div>`);
    $('#btnUp').onclick = async () => {
      const f = $('#file').files[0];
      if (!f) return toast('请先选择 .xlsx 文件');
      $('#btnUp').disabled = true;
      try {
        const r = await uploadProgress('/api/import/products/upload', f, p => { $('#prog').innerHTML = `<progress value="${p}" max="1" style="width:320px"></progress> ${Math.round(p * 100)}%`; });
        sid = r.session_id; sheets = r.sheets;
        step2(r);
      } catch (e) { $('#prog').innerHTML = `<span class="err">${esc(e.message)}</span>`; $('#btnUp').disabled = false; }
    };
  };

  const rawTable = rows => `<div class="scroll"><table>${rows.map((r, i) => `<tr><td class="muted">${i + 1}</td>${r.map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</table></div>`;

  const step2 = async (up, sheet, hdr) => {
    sheet = sheet || sheets[0]; hdr = hdr || 1;
    let r = null;
    try { r = await api(`/api/import/products/${sid}/sheet`, 'POST', {sheet, header_row: hdr}); } catch (e) { if (!up) toast(e.message); }
    card(2, `<p>文件 <b>${esc(up ? up.filename : '')}</b>${up ? `（${up.size_mb}MB）` : ''}</p>
      <div class="flex"><label>工作表 <select id="selSheet">${sheets.map(s => `<option ${s === sheet ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select></label>
        <label>表头在第 <input id="hdrRow" type="number" min="1" max="200" value="${hdr}" style="width:80px"> 行</label><button id="btnReload">刷新预览</button></div>
      <p class="muted" style="margin:10px 0">下面是该表前几行原始内容，表头行应该是「款号 / 品名 / 价格…」那一行（有的表第 1 行是大标题）。</p>
      ${r ? rawTable(r.preview_raw) + `<p class="muted">表头 ${r.headers.length} 列，约 ${r.data_rows} 行数据。</p>` : '<p class="err">请换一个工作表或表头行</p>'}
      <div class="flex" style="margin-top:12px"><button id="btnCancel">放弃导入</button><button class="primary" id="btnNext" ${r ? '' : 'disabled'}>下一步：选类目</button></div>`);
    const re = () => step2(up, $('#selSheet').value, parseInt($('#hdrRow').value, 10) || 0);
    $('#btnReload').onclick = re; $('#selSheet').onchange = re;
    $('#btnCancel').onclick = cancel;
    if (r) { info = r; $('#btnNext').onclick = step3; }
  };

  const cancel = async () => { try { await api('/api/import/products/' + sid, 'DELETE', {}); } catch (e) { /* 会话可能已过期 */ } nav('products'); };

  const step3 = () => {
    card(3, `<p class="muted">这批产品全部导入到哪个类目？已存在的 SKU 保持原类目，只更新你映射的内容。</p>
      <div class="flex"><select id="selCat" style="max-width:260px">${cats.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select></div>
      <div class="flex" style="margin-top:12px"><button id="btnBack">上一步</button><button class="primary" id="btnNext">下一步：列映射</button></div>`);
    $('#btnBack').onclick = () => step2(null);
    $('#btnNext').onclick = async () => {
      try { meta = await api(`/api/import/products/${sid}/category`, 'POST', {category_id: parseInt($('#selCat').value, 10)}); mapping = meta.suggested_mapping; step4(); } catch (e) { fail(e); }
    };
  };

  const step4 = (keep) => {
    const opts = sel => meta.fixed_targets.map(t => `<option value="${t.key}" ${sel === t.key ? 'selected' : ''}>${esc(t.label)}</option>`).join('')
      + `<optgroup label="结构化规格字段">${meta.fields.map(f => `<option value="f:${f.id}" ${sel === 'f:' + f.id ? 'selected' : ''}>${esc(f.label)}${f.unit ? '（' + esc(f.unit) + '）' : ''}</option>`).join('')}</optgroup>`;
    const sample = h => { const i = info.headers.indexOf(h); const r = info.preview_raw.slice(1, 4).map(x => x[i]).filter(x => x !== '' && x != null); return esc(r.join(' / ')).slice(0, 80); };
    card(4, `<p class="muted">系统已按列名自动猜了一遍，请核对。款号→SKU，货盘价→成本，序号/图片列→不导入，其它列→并入规格描述。</p>
      <div class="scroll" style="max-height:340px"><table><tr><th>Excel 列</th><th>样例</th><th>对应到</th></tr>
        ${info.headers.map((h, i) => `<tr><td><b>${esc(h)}</b></td><td class="muted">${sample(h)}</td><td><select data-h="${i}">${opts(mapping[h])}</select></td></tr>`).join('')}</table></div>
      <div class="grid" style="margin-top:12px">
        <div class="field"><label>统一成本币种（某行有币种列时以该行为准）</label><select id="mCur"><option value="CNY">CNY 人民币（对供应商）</option><option value="USD">USD 美元</option></select></div>
        <div class="field"><label>统一利润率（%，仅用于新建的产品）</label><input id="mProfit" type="number" min="0" step="any" value="25"></div>
        <label class="flex" style="align-self:end"><input type="checkbox" id="mImg" checked> 提取表格里的图片（每行取第一张）</label></div>
      <div class="flex" style="margin-top:12px"><button id="btnBack">上一步</button><button class="primary" id="btnNext">下一步：生成预览</button></div>`);
    $('#btnBack').onclick = step3;
    $('#btnNext').onclick = async () => {
      $$('select[data-h]').forEach(s => { mapping[info.headers[+s.dataset.h]] = s.value; });
      $('#btnNext').disabled = true; $('#btnNext').textContent = '生成中…（提取图片可能需要一会儿）';
      try {
        pv = await api(`/api/import/products/${sid}/preview`, 'POST', {mapping, currency: $('#mCur').value, profit_rate: (parseFloat($('#mProfit').value) || 0) / 100, extract_images: $('#mImg').checked});
        skip = new Set(); loaded = pv.rows; step5();
      } catch (e) { fail(e); step4(); }
    };
  };

  const rowHtml = r => `<tr class="prow" data-row="${r.row}" style="${r.blocked ? 'opacity:.5' : ''}">
    <td><input type="checkbox" data-inc="${r.row}" ${skip.has(r.row) || r.blocked ? '' : 'checked'} ${r.blocked ? 'disabled' : ''}></td><td class="muted">${r.row}</td>
    <td>${r.image ? `<img src="/api/import/products/${sid}/image/${encodeURIComponent(r.image)}" style="width:44px;height:44px;object-fit:contain;border-radius:6px;background:#fff">` : ''}</td>
    <td><b>${esc(r.sku)}</b> ${r.blocked ? '' : r.exists ? '<span class="tag warn">更新</span>' : '<span class="tag good">新建</span>'}</td><td>${esc(r.name)}</td>
    <td>${r.cost === null ? '—' : esc(r.currency) + ' ' + money(r.cost)}</td><td>${r.moq ?? ''}</td><td>${esc(r.supplier)}</td>
    <td style="max-width:260px"><div class="spec" data-spec style="cursor:pointer;white-space:pre-wrap;max-height:3.6em;overflow:hidden" title="点击展开/收起">${esc(r.spec_text)}</div></td>
    <td>${r.warnings.map(w => `<div class="err" style="font-size:12px">${esc(w)}</div>`).join('')}</td></tr>`;

  const step5 = () => {
    const incl = () => loaded.length === pv.count ? pv.count - skip.size - pv.blocked : null;
    card(5, `<p>共 <b>${pv.count}</b> 行：带图 ${pv.with_image}，将更新已有产品 ${pv.will_update}，无法导入 ${pv.blocked}，有提醒 ${pv.warned}。<span class="muted">此时还没有写入任何数据。</span></p>
      <div class="flex" style="margin:8px 0"><button class="small" id="btnAll">全选</button><button class="small" id="btnNone">全不选</button><span class="muted" id="selInfo"></span></div>
      <div class="scroll" style="max-height:480px"><table><tr><th></th><th>行</th><th>图片</th><th>SKU</th><th>名称</th><th>成本</th><th>MOQ</th><th>供应商</th><th>规格描述（点击展开）</th><th>提醒</th></tr>
        <tbody id="tb">${loaded.map(rowHtml).join('')}</tbody></table></div>
      <div class="flex" style="margin-top:8px">${loaded.length < pv.count ? `<button id="btnMore">再加载 ${Math.min(200, pv.count - loaded.length)} 行</button>` : ''}
        <span class="muted">${loaded.length < pv.count ? `已显示 ${loaded.length} / ${pv.count} 行；没显示的行默认会导入。` : ''}</span></div>
      <div class="flex" style="margin-top:12px"><button id="btnBack">上一步</button><button id="btnCancel">放弃导入</button><button class="primary" id="btnApply">确认导入</button></div>`);
    const sync = () => {
      const n = pv.count - pv.blocked - [...skip].filter(r => { const x = loaded.find(y => y.row === r); return x && !x.blocked; }).length;
      $('#selInfo').textContent = `将导入 ${n} 行`; $('#btnApply').textContent = `确认导入 ${n} 行`; return n;
    };
    sync();
    $('#tb').onclick = e => {
      const cb = e.target.closest('[data-inc]');
      if (cb) { const r = +cb.dataset.inc; cb.checked ? skip.delete(r) : skip.add(r); sync(); return; }
      const sp = e.target.closest('[data-spec]');
      if (sp) sp.style.maxHeight = sp.style.maxHeight ? '' : '3.6em';
    };
    $('#btnAll').onclick = () => { skip.clear(); $$('[data-inc]').forEach(c => { if (!c.disabled) c.checked = true; }); sync(); };
    $('#btnNone').onclick = () => { loaded.forEach(r => skip.add(r.row)); $$('[data-inc]').forEach(c => { c.checked = false; }); sync(); };
    if ($('#btnMore')) $('#btnMore').onclick = async () => {
      try { const more = await api(`/api/import/products/${sid}/rows?offset=${loaded.length}&limit=200`); loaded = loaded.concat(more.rows); step5(); } catch (e) { fail(e); }
    };
    $('#btnBack').onclick = step4; $('#btnCancel').onclick = cancel;
    $('#btnApply').onclick = async () => {
      if (!confirm(`确定导入 ${sync()} 行？已存在的 SKU 会被更新。`)) return;
      $('#btnApply').disabled = true;
      try { done(await api(`/api/import/products/${sid}/apply`, 'POST', {skip_rows: [...skip]})); } catch (e) { fail(e); $('#btnApply').disabled = false; }
    };
  };

  const done = r => {
    card(5, `<p class="ok"><b>完成：</b>新建 ${r.created}，更新 ${r.updated}，跳过 ${r.skipped}，失败 ${r.failed.length}。</p>
      ${r.failed.length ? `<h3>失败的行</h3><div class="scroll">${r.failed.map(f => `<div class="err">第 ${f.row} 行 ${esc(f.sku)}：${esc(f.reason)}</div>`).join('')}</div>` : ''}
      <div class="flex" style="margin-top:12px"><button class="primary" id="btnGo">查看产品库</button><button id="btnAgain">再导入一个文件</button></div>`);
    $('#btnGo').onclick = () => nav('products'); $('#btnAgain').onclick = step1;
  };
  step1();
}
