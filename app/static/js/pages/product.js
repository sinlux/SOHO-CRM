import {openCatalogAdmin} from './catalog_admin.js';
import {$, $$, esc, api, upload, nav, toast} from '../lib.js';
import {money} from './products.js';

// 产品详情页布局（参照 Odoo / Akeneo / Salesforce CPQ 等的产品主数据页）：
//   顶部：身份栏（缩略图 · SKU · 名称 · 状态 · 保存/复制/删除）
//   左侧（吸顶）：相册 + 价格摘要
//   右侧：页签 概览 / 规格 / 价格与成本 / 包装物流 / 供应商 / 文档 / 使用记录
// 所有页签的输入共用一个「保存」；供应商、价格记录、文档是即时操作（需要先有产品）。

const CURRENCIES = ['CNY', 'USD'];            // CNY 对供应商、USD 对客户；旧数据里的 EUR/VND 只在已有产品上保留显示
const STATUSES = [['active', '在售'], ['draft', '草稿'], ['discontinued', '停产']];
const DOC_KINDS = ['规格书', '认证', '图纸', '报价/合同', '其他'];
const TABS = [['overview', '概览'], ['specs', '规格'], ['pricing', '价格与成本'], ['logistics', '包装物流'], ['suppliers', '供应商'], ['docs', '文档'], ['usage', '使用记录']];
let activeTab = 'overview', lastKey = null;     // 同一个产品内保存/刷新时记住页签；换产品回到「概览」

const num = id => $(id).value === '' ? null : $(id).value;
const fmtSize = n => n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB';

function fieldInput(f, val) {
  const unit = f.unit ? `<span class="unit">${esc(f.unit)}</span>` : '';
  const id = 'fv_' + f.id;
  if (f.type === 'select') return `<select id="${id}" data-fid="${f.id}"><option value=""></option>${f.options_list.map(o => `<option ${o === val ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`;
  if (f.type === 'multi') {
    const cur = new Set((val || '').split(',').map(s => s.trim()).filter(Boolean));
    return `<div class="check-group" data-fid="${f.id}" data-multi="1">${f.options_list.map(o => `<label><input type="checkbox" value="${esc(o)}" ${cur.has(o) ? 'checked' : ''}>${esc(o)}</label>`).join('')}</div>`;
  }
  if (f.type === 'textarea') return `<textarea id="${id}" data-fid="${f.id}" placeholder="${esc(f.placeholder || '')}">${esc(val || '')}</textarea>`;
  return `<div class="flex nowrap"><input id="${id}" data-fid="${f.id}" ${f.type === 'number' ? 'type="number" step="any"' : ''} value="${esc(val || '')}" placeholder="${esc(f.placeholder || '')}">${unit}</div>`;
}

export async function render(root, arg, isCurrent) {
  if (arg !== lastKey) activeTab = 'overview';
  lastKey = arg;
  const isNew = arg === 'new';
  const pid = isNew ? null : parseInt(arg, 10);
  if (!isNew && !pid) return nav('products');
  const [cats, rate] = await Promise.all([api('/api/categories'), api('/api/rate')]);
  let p;
  if (isNew) {
    p = {sku: '', name: '', category_id: cats.categories[0]?.id, cost: null, cost_currency: 'CNY', profit_rate: 0.25, moq: null, lead_time: null,
      supplier: '', remark: '', spec_text: '', status: 'active', unit: 'pcs', brand: '', series: '', hs_code: '', origin: '', pcs_per_carton: null,
      carton_l: null, carton_w: null, carton_h: null, gross_weight: null, net_weight: null, field_values: {}, suggested_price: null, images: [], files: []};
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
  let images = [...p.images];            // 相册状态；保存时按此顺序提交
  let sel = 0;                           // 当前大图序号
  let dirty = false;
  const markDirty = () => { if (!dirty) { dirty = true; $('#dirty').style.display = ''; } };

  root.innerHTML = `
  <div class="card idbar"><div class="flex between"><div class="flex">
      <button id="back">← 返回</button><span id="hdrThumb"></span>
      <div><div class="idline"><b id="hdrSku">${esc(p.sku || '新产品')}</b><span class="tag" id="hdrCat"></span>
        <select id="pStatus" class="status-sel s-${p.status}">${STATUSES.map(([k, l]) => `<option value="${k}" ${k === p.status ? 'selected' : ''}>${l}</option>`).join('')}</select>
        <span class="dirty" id="dirty" style="display:none">● 有未保存的修改</span></div>
        <div class="idname" id="hdrName">${esc(p.name || '请填写产品名称')}</div></div></div>
    <div class="flex"><button class="primary" id="btnSave">${isNew ? '创建产品' : '保存修改'}</button>
      ${isNew ? '' : '<button id="btnDup">复制</button><button class="danger" id="btnDel">删除</button>'}</div></div></div>

  <div class="pgrid">
    <aside class="pside">
      <div class="card gallery"><div class="gmain" id="gMain"></div><div class="gthumbs" id="gThumbs"></div>
        <div class="gtools" id="gTools"></div>
        <div class="gdrop" id="gDrop">点击选择 / 拖拽到这里 / Ctrl+V 粘贴<br><span class="muted">任何格式、任何尺寸都行，自动处理成居中的白底高清方图</span></div>
        <input type="file" id="imgFile" accept="image/*" multiple style="display:none"></div>
      <div class="card sumcard"><div class="muted">建议价（USD）</div><div class="big" id="sugg">—</div><div class="muted" id="suggNote"></div>
        <div class="kv"><span>成本</span><b id="sumCost">—</b></div><div class="kv"><span>利润率</span><b id="sumProfit">—</b></div>
        <div class="kv"><span>MOQ</span><b id="sumMoq">—</b></div><div class="kv"><span>交期</span><b id="sumLead">—</b></div>
        <div class="kv"><span>每件体积</span><b id="sumCbm">—</b></div><div class="kv"><span>汇率</span><b>1 CNY = ${rate.rate} USD</b></div></div>
    </aside>

    <section class="pmain"><div class="card">
      <div class="tabs" id="tabs">${TABS.map(([k, l]) => `<button class="tab" data-tab="${k}">${l}</button>`).join('')}</div>

      <div class="tabpanel" data-panel="overview"><div class="grid">
        <div class="field"><label>类目</label><select id="pCat">${cats.categories.map(c => `<option value="${c.id}" ${c.id === p.category_id ? 'selected' : ''}>${esc(c.icon || '')} ${esc(c.name)}</option>`).join('')}</select></div>
        <div class="field"><label>SKU</label><div class="flex nowrap"><input id="pSku" value="${esc(p.sku)}"><button id="btnAuto" title="按类目 + 子类前缀自动生成">自动编号</button></div>
          <div class="muted" id="skuHint" style="margin-top:3px"></div></div>
        <div class="field wide"><label>产品名称</label><input id="pName" value="${esc(p.name)}"></div>
        <div class="field"><label>品牌 / 制造商</label><input id="pBrand" value="${esc(p.brand)}"></div>
        <div class="field"><label>系列 / 款式组</label><input id="pSeries" value="${esc(p.series)}" placeholder="同一系列的不同规格可填同一个名字"></div>
        <div class="field"><label>计量单位</label><input id="pUnit" value="${esc(p.unit)}" list="unitList" placeholder="pcs"><datalist id="unitList">${['pcs', 'set', 'pair', 'm', 'm²', 'kg', 'carton'].map(u => `<option>${u}</option>`).join('')}</datalist></div>
        <div class="field"><label>原产地</label><input id="pOrigin" value="${esc(p.origin)}" placeholder="如 China"></div>
        <div class="field"><label>HS 编码（报关税则号）</label><div class="flex nowrap"><input id="pHs" list="hsList" value="${esc(p.hs_code)}" placeholder="如 9405.42"><button id="btnHs" title="用海关提供的查询网站，按产品名称查 HS 编码">查询</button></div>
          <datalist id="hsList"></datalist><div class="muted" id="hsHint" style="margin-top:3px">下拉里是本库已经用过的编码，同类产品可直接复用</div></div>
      </div>
      <div class="field" style="margin-top:14px"><label>规格描述（主规格字段：尺寸、材质、参数、颜色、包装等都可以写在这里）</label><textarea id="pSpec" style="min-height:150px">${esc(p.spec_text)}</textarea></div>
      <div class="field" style="margin-top:14px"><label>内部备注（只有你自己看得到，不会印在报价单上，也不会给客户）</label>
        <textarea id="pRemark" placeholder="例：和供应商沟通的要点（含税价、起订量、交期、付款方式）、报价背景（这个价是给哪个客户/项目的）、质量问题、特别注意事项、下次跟进该问什么……">${esc(p.remark || '')}</textarea></div></div>

      <div class="tabpanel" data-panel="specs"><p class="muted" style="margin-bottom:10px">结构化规格全部选填，用于筛选和对比；详细描述写在「概览」的规格描述里。选了「子类」后，只显示和该子类有关的规格项。</p>
        <div class="flex" style="margin-bottom:10px"><button id="btnManageFields">⚙ 管理子类和规格字段…</button><label class="flex"><input type="checkbox" id="showAllFields"> 显示全部字段</label><span class="muted" id="hiddenInfo"></span></div>
        <div class="grid" id="fieldsBox"></div></div>

      <div class="tabpanel" data-panel="pricing"><div class="grid">
        <div class="field"><label>成本价</label><div class="flex nowrap"><input id="pCost" type="number" step="any" min="0" value="${p.cost ?? ''}">
          <select id="pCur" style="max-width:100px">${[...new Set([...CURRENCIES, p.cost_currency].filter(Boolean))].map(c => `<option ${c === p.cost_currency ? 'selected' : ''}>${c}</option>`).join('')}</select></div></div>
        <div class="field"><label>利润率（%）</label><input id="pProfit" type="number" step="any" min="0" value="${Math.round(p.profit_rate * 10000) / 100}"></div>
        <div class="field"><label>MOQ（最小起订量）</label><input id="pMoq" type="number" min="0" value="${p.moq ?? ''}"></div>
        <div class="field"><label>交期（天）</label><input id="pLead" type="number" min="0" value="${p.lead_time ?? ''}"></div></div>
        <p class="muted" style="margin:10px 0">建议价 = 成本 × 汇率 × (1 + 利润率)。修改成本会自动追加一条价格历史；只改其它字段不会动价格。</p>
        ${isNew ? '<div class="muted">保存产品后，可在这里查看价格历史并手动添加成本/售价记录。</div>' : `
        <div class="sec-title"><h3>📈 价格历史</h3><span class="muted">只追加不覆盖；连续保存相同成本不重复记录</span></div><div id="histBox">加载中…</div>
        <div class="flex" style="margin-top:14px"><select id="hType" style="max-width:110px"><option value="cost">成本</option><option value="sell">售价</option></select>
          <input id="hPrice" type="number" step="any" min="0" placeholder="金额" style="max-width:120px"><select id="hCur" style="max-width:100px">${CURRENCIES.map(c => `<option>${c}</option>`).join('')}</select>
          <input id="hDate" type="date" style="max-width:160px"><input id="hCust" list="custList" placeholder="客户（售价时可选）" style="max-width:200px"><datalist id="custList"></datalist>
          <input id="hSrc" placeholder="来源，如 PI 号" style="max-width:160px"><button id="btnAddHist">添加记录</button></div>`}</div>

      <div class="tabpanel" data-panel="logistics"><div class="grid">
        <div class="field"><label>每箱数量（件）</label><input id="pPcs" type="number" min="1" value="${p.pcs_per_carton ?? ''}"></div>
        <div class="field"><label>外箱尺寸 长×宽×高（cm）</label><div class="flex nowrap"><input id="pCl" type="number" step="any" min="0" value="${p.carton_l ?? ''}" placeholder="长">
          <input id="pCw" type="number" step="any" min="0" value="${p.carton_w ?? ''}" placeholder="宽"><input id="pCh" type="number" step="any" min="0" value="${p.carton_h ?? ''}" placeholder="高"></div></div>
        <div class="field"><label>毛重（kg / 箱）</label><input id="pGw" type="number" step="any" min="0" value="${p.gross_weight ?? ''}"></div>
        <div class="field"><label>净重（kg / 箱）</label><input id="pNw" type="number" step="any" min="0" value="${p.net_weight ?? ''}"></div></div>
        <div class="price-card" style="margin-top:16px;max-width:420px"><div class="kv"><span>每箱体积</span><b id="lgCbm">—</b></div><div class="kv"><span>每件体积</span><b id="lgUnit">—</b></div></div>
        <p class="muted" style="margin-top:10px">体积由外箱尺寸自动计算；装柜数量等因品类而异，可在「规格」页签里的对应字段填写。</p></div>

      <div class="tabpanel" data-panel="suppliers">
        <div class="field" style="max-width:380px;margin-bottom:12px"><label>主供应商（采纳报价时会自动更新）</label><input id="pSup" list="vendorList" value="${esc(p.supplier || '')}" placeholder="从供应商档案里选，或直接输入新名字"><datalist id="vendorList"></datalist></div>
        ${isNew ? '<div class="muted">保存产品后可记录多个供应商的报价并比价。</div>' : `<div class="sec-title"><h3>🏭 供应商比价</h3><a class="ext" href="#vendors">打开「供应商」菜单（聊天记录、截图归档、按项目对比）</a></div><div id="supBox">加载中…</div>
        <div class="flex" style="margin-top:12px"><input id="sName" list="vendorList" placeholder="供应商名（可选已有的）" style="max-width:200px"><input id="sProject" placeholder="询价项目（可选）" style="max-width:170px"><input id="sPrice" type="number" step="any" min="0" placeholder="人民币报价" style="max-width:140px">
          <input id="sDate" type="date" style="max-width:160px"><input id="sRemark" placeholder="备注" style="max-width:220px"><button id="btnAddSup">添加报价</button></div>`}</div>

      <div class="tabpanel" data-panel="docs">${isNew ? '<div class="muted">保存产品后可上传规格书、认证、图纸等文档。</div>' : `
        <div class="flex"><select id="docKind" style="max-width:140px">${DOC_KINDS.map(k => `<option>${k}</option>`).join('')}</select>
          <input type="file" id="docFile" style="max-width:320px"><button id="btnDoc" class="primary">上传</button></div>
        <p class="muted" style="margin:6px 0 12px">支持 PDF / Word / Excel / PPT / 图片 / 压缩包 / CAD（dwg、dxf、step），单个最大 50MB。</p><div id="docBox"></div>`}</div>

      <div class="tabpanel" data-panel="usage">${isNew ? '<div class="muted">产品被报价后，这里会列出相关报价单。</div>' : '<div id="usageBox">加载中…</div>'}</div>
    </div></section>
  </div>`;

  // ---------- 相册 ----------
  const drawGallery = () => {
    const cur = images[sel] || images[0];
    $('#gMain').innerHTML = cur ? `<img src="${esc(cur.url)}" alt="产品图">${cur.low_res ? '<span class="lowres" title="原图分辨率偏低，建议换更清晰的原图">分辨率偏低</span>' : ''}${i0(cur)}`
      : '<div class="gempty">▣<br>还没有图片</div>';
    $('#gThumbs').innerHTML = images.map((im, i) => `<div class="gth ${i === sel ? 'on' : ''}" data-gi="${i}"><img src="${esc(im.thumb_url)}" alt="">${i === 0 ? '<i>主图</i>' : ''}</div>`).join('');
    $('#gTools').innerHTML = images.length ? `<button class="small" data-gt="primary" ${sel === 0 ? 'disabled' : ''}>设为主图</button><button class="small" data-gt="left" ${sel === 0 ? 'disabled' : ''}>← 前移</button>
      <button class="small" data-gt="right" ${sel >= images.length - 1 ? 'disabled' : ''}>后移 →</button><button class="small danger" data-gt="del">删除这张</button>` : '';
    $('#hdrThumb').innerHTML = images[0] ? `<img class="thumb" src="${esc(images[0].thumb_url)}" alt="" style="width:48px;height:48px">` : '<span class="thumb ph" style="width:48px;height:48px">▣</span>';
  };
  const i0 = im => im.normalized ? '' : '<span class="rawimg" title="旧版原图，保存后可在产品库页点「统一图片规格」自动规范">原图（未规范化）</span>';
  const addFiles = async files => {
    for (const f of files) {
      if (!f.type.startsWith('image/') && !/\.(heic|avif|tiff?|bmp)$/i.test(f.name)) { toast(`「${f.name}」不是图片`); continue; }
      $('#gDrop').classList.add('busy');
      try {
        const r = await upload('/api/images/upload', f);
        images.push(r.image); sel = images.length - 1; markDirty(); drawGallery();
      } catch (e) { toast(`${f.name}：${e.message}`); }
      $('#gDrop').classList.remove('busy');
    }
  };
  drawGallery();
  $('#gDrop').onclick = () => $('#imgFile').click();
  $('#imgFile').onchange = e => { addFiles([...e.target.files]); e.target.value = ''; };
  const gd = $('#gDrop');
  ['dragenter', 'dragover'].forEach(ev => gd.addEventListener(ev, e => { e.preventDefault(); gd.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => gd.addEventListener(ev, e => { e.preventDefault(); gd.classList.remove('over'); }));
  gd.addEventListener('drop', e => addFiles([...e.dataTransfer.files]));
  const onPaste = e => {
    const files = [...(e.clipboardData || {}).items || []].filter(it => it.type.startsWith('image/')).map(it => it.getAsFile());
    if (files.length) { e.preventDefault(); addFiles(files); }
  };
  document.addEventListener('paste', onPaste);
  const stopPaste = () => { document.removeEventListener('paste', onPaste); window.removeEventListener('hashchange', stopPaste); };
  window.addEventListener('hashchange', stopPaste);
  $('.gallery').onclick = e => {
    const th = e.target.closest('[data-gi]');
    if (th) { sel = Number(th.dataset.gi); drawGallery(); return; }
    const b = e.target.closest('[data-gt]');
    if (!b) return;
    const t = b.dataset.gt;
    if (t === 'del') { images.splice(sel, 1); sel = Math.max(0, Math.min(sel, images.length - 1)); }
    else if (t === 'primary') { images.unshift(images.splice(sel, 1)[0]); sel = 0; }
    else { const j = t === 'left' ? sel - 1 : sel + 1; [images[sel], images[j]] = [images[j], images[sel]]; sel = j; }
    markDirty(); drawGallery();
  };

  // ---------- 规格字段 / 联动 ----------
  const drawFields = () => {
    $('#fieldsBox').innerHTML = fields.length ? fields.map(f => `<div class="field ${f.type === 'textarea' || f.type === 'multi' ? 'wide' : ''}" data-applies="${esc((f.applies_list || []).join('|'))}"><label>${esc(f.label)}</label>${fieldInput(f, values[f.id])}</div>`).join('')
      : '<span class="muted">该类目没有结构化字段</span>';
    applyVisibility();
  };
  // 只对部分子类有意义的字段：选了子类就收起无关的（仍保留在页面里，值不会丢）
  const applyVisibility = () => {
    const sf = subField(), el = sf && $(`#fv_${sf.id}`), sub = el ? el.value : '', all = $('#showAllFields').checked;
    let hidden = 0;
    $$('#fieldsBox .field[data-applies]').forEach(w => {
      const ap = w.dataset.applies ? w.dataset.applies.split('|') : [];
      const hide = !all && sub && ap.length && !ap.includes(sub);
      w.style.display = hide ? 'none' : '';
      hidden += hide ? 1 : 0;
    });
    $('#hiddenInfo').textContent = hidden ? `已收起 ${hidden} 个与「${sub}」无关的字段` : '';
  };
  const collectValues = () => {
    const out = {};
    $$('#fieldsBox [data-fid]').forEach(el => {
      out[el.dataset.fid] = el.dataset.multi ? $$('input:checked', el).map(i => i.value).join(',') : el.value;
    });
    return out;
  };
  const catName = () => { const c = cats.categories.find(c => c.id === Number($('#pCat').value)); return c ? `${c.icon || ''} ${c.name}` : ''; };
  const subField = () => fields.find(f => f.key === 'subcategory');
  const suggest = () => {
    const cost = parseFloat($('#pCost').value), cur = $('#pCur').value, pr = parseFloat($('#pProfit').value);
    const saved = !isNew && cost === p.cost && cur === p.cost_currency && Math.abs(pr / 100 - p.profit_rate) < 1e-9;
    let v = null;
    if (saved) v = p.suggested_price;
    else if (cost > 0) v = Math.round(cost * (cur === 'CNY' ? rate.rate : 1) * (1 + (isNaN(pr) ? 25 : pr) / 100) * 100) / 100;
    $('#sugg').textContent = v === null || v === undefined ? '—' : '$ ' + v.toFixed(2);
    $('#suggNote').textContent = (cur === 'EUR' || cur === 'VND') && v ? `${cur} 成本未换算为美元，仅供参考` : (saved ? '已保存的值（可能已对齐最新成交价）' : '保存后生效');
    $('#sumCost').textContent = isNaN(cost) ? '—' : `${cur} ${cost}`;
    $('#sumProfit').textContent = isNaN(pr) ? '—' : pr + '%';
    $('#sumMoq').textContent = $('#pMoq').value || '—';
    $('#sumLead').textContent = $('#pLead').value ? $('#pLead').value + ' 天' : '—';
  };
  const logistics = () => {
    const l = parseFloat($('#pCl').value), w = parseFloat($('#pCw').value), h = parseFloat($('#pCh').value), n = parseInt($('#pPcs').value, 10);
    const cbm = l > 0 && w > 0 && h > 0 ? l * w * h / 1e6 : null;
    $('#lgCbm').textContent = cbm ? cbm.toFixed(4) + ' m³' : '—';
    const unit = cbm && n > 0 ? cbm / n : null;
    $('#lgUnit').textContent = unit ? unit.toFixed(4) + ' m³' : '—';
    $('#sumCbm').textContent = unit ? unit.toFixed(4) + ' m³' : '—';
  };
  const header = () => {
    $('#hdrSku').textContent = $('#pSku').value || '新产品';
    $('#hdrName').textContent = $('#pName').value || '请填写产品名称';
    $('#hdrCat').textContent = catName();
    $('#pStatus').className = 'status-sel s-' + $('#pStatus').value;
  };
  drawFields(); suggest(); logistics(); header();
  $('.pmain').addEventListener('input', e => { markDirty(); suggest(); logistics(); header(); });
  $('.pmain').addEventListener('change', e => { markDirty(); suggest(); logistics(); header(); });
  $('#pStatus').onchange = () => { markDirty(); header(); };

  // ---------- 页签 ----------
  const showTab = k => {
    activeTab = TABS.some(t => t[0] === k) ? k : 'overview';
    $$('.tab').forEach(b => b.classList.toggle('on', b.dataset.tab === activeTab));
    $$('.tabpanel').forEach(d => d.style.display = d.dataset.panel === activeTab ? '' : 'none');
  };
  showTab(activeTab);
  $('#tabs').onclick = e => { const b = e.target.closest('[data-tab]'); if (b) showTab(b.dataset.tab); };
  $('#back').onclick = () => nav('products');

  $('#pCat').onchange = async e => {
    values = collectValues();
    fields = (await api(`/api/categories/${e.target.value}/fields`)).fields;
    const ids = new Set(fields.map(f => String(f.id)));
    values = Object.fromEntries(Object.entries(values).filter(([k]) => ids.has(k)));
    drawFields(); header();
  };
  // SKU：选好子类后若 SKU 为空（或还是上一次自动生成的），自动给出下一个编号
  let lastAuto = '';
  (async () => {
    try { $('#hsList').innerHTML = (await api('/api/hs_codes')).codes.map(c => `<option value="${esc(c.hs_code)}">${esc(c.names)}（本库 ${c.n} 个产品用过）</option>`).join(''); } catch (e) { /* 提示失败不影响编辑 */ }
  })();
  $('#btnHs').onclick = async () => {
    const url = (await api('/api/settings')).hs_lookup_url;
    if (!url) {
      toast('还没设置 HS 编码查询网址：到「设置」页填入海关提供的查询网站（可用 {keyword} 代表产品名）');
      return;
    }
    const kw = $('#pName').value.trim() || $('#pHs').value.trim();
    window.open(url.includes('{keyword}') ? url.replace('{keyword}', encodeURIComponent(kw)) : url, '_blank', 'noopener');
    $('#hsHint').textContent = '在查询网站里找到编码后，复制粘贴到这里。';
  };
  const autoSku = async (silent) => {
    const sf = subField(), el = sf && $(`#fv_${sf.id}`);
    try {
      const r = await api(`/api/categories/${$('#pCat').value}/next_sku?subcategory=${encodeURIComponent(el ? el.value : '')}`);
      $('#pSku').value = r.sku; lastAuto = r.sku; $('#skuHint').textContent = '已按类目与子类前缀自动编号'; header(); markDirty();
    } catch (e) { if (!silent) toast(e.message); else $('#skuHint').textContent = ''; }
  };
  $('#btnAuto').onclick = () => autoSku(false);
  $('#showAllFields').onchange = applyVisibility;
  $('#btnManageFields').onclick = () => openCatalogAdmin(async () => {
    values = collectValues();
    fields = (await api(`/api/categories/${$('#pCat').value}/fields`)).fields;
    drawFields();
  }, $('#pCat').value);
  $('#fieldsBox').addEventListener('change', e => {
    const sf = subField();
    if (sf && e.target.id === 'fv_' + sf.id) applyVisibility();
    if (sf && e.target.id === 'fv_' + sf.id && isNew && ($('#pSku').value === '' || $('#pSku').value === lastAuto)) autoSku(true);
  });

  // ---------- 保存 ----------
  $('#btnSave').onclick = async () => {
    const pr = parseFloat($('#pProfit').value);
    const body = {category_id: Number($('#pCat').value), sku: $('#pSku').value, name: $('#pName').value, status: $('#pStatus').value,
      brand: $('#pBrand').value, series: $('#pSeries').value, unit: $('#pUnit').value, origin: $('#pOrigin').value, hs_code: $('#pHs').value,
      supplier: $('#pSup').value, cost: num('#pCost'), cost_currency: $('#pCur').value, profit_rate: isNaN(pr) ? null : pr / 100,
      moq: num('#pMoq'), lead_time: num('#pLead'), spec_text: $('#pSpec').value, remark: $('#pRemark').value,
      pcs_per_carton: num('#pPcs'), carton_l: num('#pCl'), carton_w: num('#pCw'), carton_h: num('#pCh'),
      gross_weight: num('#pGw'), net_weight: num('#pNw'), field_values: collectValues(), images: images.map(i => i.id)};
    $('#btnSave').disabled = true;
    try {
      if (isNew) { const r = await api('/api/products', 'POST', body); toast('产品已创建'); nav('product', r.id); }
      else { await api('/api/products/' + pid, 'PUT', body); toast('已保存'); render(root, arg, isCurrent); }
    } catch (e) { toast(e.message); $('#btnSave').disabled = false; }
  };
  if (isNew) return;

  $('#btnDup').onclick = async () => {
    const sku = prompt('新产品的 SKU（复制资料、规格和图片，不复制价格历史/供应商/文档）：', p.sku + '-2');
    if (!sku) return;
    try { const r = await api(`/api/products/${pid}/duplicate`, 'POST', {sku}); toast('已复制'); nav('product', r.id); } catch (e) { toast(e.message); }
  };
  $('#btnDel').onclick = async () => {
    try {
      const {impact: i} = await api(`/api/products/${pid}/impact`);
      if (!confirm(`确定删除「${p.sku} ${p.name}」？\n将同时永久删除：价格记录 ${i.price_records} 条、供应商报价 ${i.supplier_quotes} 条、图片 ${i.images} 张、文档 ${i.files} 个。\n已有的报价单明细（${i.quote_items} 条）会保留为快照。\n此操作不可恢复。`)) return;
      await api('/api/products/' + pid, 'DELETE', {confirm: true});
      toast('已删除'); nav('products');
    } catch (e) { toast(e.message); }
  };

  // ---------- 供应商 / 价格历史 / 文档 / 使用记录（即时操作） ----------
  const drawSup = list => {
    $('#supBox').innerHTML = list.length ? `<table><tr><th>供应商</th><th>人民币报价</th><th>日期</th><th>备注</th><th></th></tr>
      ${list.map(s => `<tr><td>${s.supplier_id ? `<a class="ext" href="#vendor/${s.supplier_id}"><b>${esc(s.supplier_name)}</b></a>` : `<b>${esc(s.supplier_name)}</b>`}${s.project ? ` <span class="tag">${esc(s.project)}</span>` : ''} ${s.is_adopted ? '<span class="tag good">已采纳</span>' : ''}</td><td>${money(s.price_cny, '¥')}</td><td>${esc(s.quote_date || '')}</td>
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
  const drawDocs = list => {
    $('#docBox').innerHTML = list.length ? `<table><tr><th>文件</th><th>类别</th><th>大小</th><th>上传时间</th><th></th></tr>${list.map(f => `<tr>
      <td><a class="ext" href="${esc(f.url)}">${esc(f.name)}</a></td><td><span class="tag">${esc(f.kind)}</span></td><td class="muted">${fmtSize(f.size)}</td>
      <td class="muted">${esc((f.created_at || '').slice(0, 16))}</td><td><button class="small danger" data-fdel="${f.id}">删</button></td></tr>`).join('')}</table>` : '<span class="muted">还没有文档</span>';
  };
  const loadUsage = async () => {
    const u = (await api(`/api/products/${pid}/usage`)).quotes;
    $('#usageBox').innerHTML = u.length ? `<table><tr><th>报价单</th><th>客户</th><th>数量</th><th>单价</th><th>日期</th></tr>${u.map(q => `<tr><td><b>${esc(q.quote_no)}</b> <span class="tag">${esc(q.status)}</span></td>
      <td>${q.customer_id ? `<a class="ext" href="#customer/${q.customer_id}">${esc(q.company || '')}</a>` : ''}</td><td>${q.quantity}</td><td>${money(q.unit_price, q.currency)}</td><td class="muted">${esc((q.created_at || '').slice(0, 10))}</td></tr>`).join('')}</table>`
      : '<span class="muted">还没有出现在任何报价单里</span>';
  };
  api('/api/vendors/names').then(r => { const dl = $('#vendorList'); if (dl) dl.innerHTML = r.names.map(n => `<option value="${esc(n)}">`).join(''); }).catch(() => {});
  drawDocs(p.files);
  await Promise.all([loadSup(), loadHist(), loadUsage()]);

  const reloadKeepTab = () => render(root, arg, isCurrent);
  root.onclick = async e => {
    const b = e.target.closest('button');
    if (!b) return;
    try {
      if (b.dataset.adopt) { await api(`/api/suppliers/${b.dataset.adopt}/adopt`, 'POST', {}); toast('已采纳，成本已更新'); reloadKeepTab(); }
      else if (b.dataset.sdel) { if (confirm('删除这条供应商报价？')) { await api('/api/suppliers/' + b.dataset.sdel, 'DELETE', {}); loadSup(); } }
      else if (b.dataset.hdel) { if (confirm('删除这条价格记录？当前成本/建议价会按剩余的最新记录重新对齐。')) { await api('/api/price_history/' + b.dataset.hdel, 'DELETE', {}); toast('已删除'); reloadKeepTab(); } }
      else if (b.dataset.fdel) { if (confirm('删除这个文档？')) { await api('/api/product_files/' + b.dataset.fdel, 'DELETE', {}); toast('已删除'); reloadKeepTab(); } }
    } catch (err) { toast(err.message); }
  };
  $('#btnAddSup').onclick = async () => {
    try {
      await api(`/api/products/${pid}/suppliers`, 'POST', {supplier_name: $('#sName').value, project: $('#sProject').value, price_cny: num('#sPrice'), quote_date: $('#sDate').value || null, remark: $('#sRemark').value});
      toast('已添加'); loadSup(); ['#sName', '#sProject', '#sPrice', '#sDate', '#sRemark'].forEach(s => $(s).value = '');
    } catch (e) { toast(e.message); }
  };
  $('#btnDoc').onclick = async () => {
    const f = $('#docFile').files[0];
    if (!f) return toast('请先选择文件');
    $('#btnDoc').disabled = true;
    try {
      const r = await fetch(`/api/products/${pid}/files?kind=${encodeURIComponent($('#docKind').value)}`, {method: 'POST', headers: {'X-Filename': encodeURIComponent(f.name)}, body: f});
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || '上传失败');
      toast('已上传'); drawDocs(d.files); $('#docFile').value = '';
    } catch (e) { toast(e.message); }
    $('#btnDoc').disabled = false;
  };
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
      reloadKeepTab();
    } catch (e) { toast(e.message); }
  };
}
