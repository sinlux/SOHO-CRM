import {$, $$, esc, api, modal, closeModal, toast, nav} from '../lib.js';

export async function startEnrich(cid, depth = 'standard') {
  modal('<h2>AI背调进行中…</h2><p class="muted">正在多个渠道（官网、LinkedIn、社媒、企业黄页、进口记录、新闻评价…）搜索公开信息并调用 DeepSeek 提取，通常需要 30~120 秒，请勿关闭。</p>');
  try {
    const r = await api(`/api/customers/${cid}/enrich`, 'POST', {depth});
    await review(r.enrichment_id, cid);
  } catch (e) {
    modal(`<h2>背调失败</h2><p class="err">${esc(e.message)}</p><button id="x">关闭</button>`);
    $('#x').onclick = closeModal;
  }
}

const srcLink = u => `<a class="ext" href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(u)}</a>`;

const DIM = {official: '官网', contact: '联系方式', linkedin: 'LinkedIn', social: 'Facebook/Instagram', directory: '企业黄页', local: '当地语言', trade: '进口记录', news: '新闻/评价/投诉', products: '产品', b2b: 'B2B平台'};
const REL_CLASS = {高: 'ok', 中: '', 低: 'muted'};

export async function review(eid, cid, readonly = false) {
  const en = await api('/api/enrichments/' + eid);
  const ex = en.extracted || {};
  const cb = (attr, value) => readonly ? '' : `<input type="checkbox" ${attr} value="${esc(value)}" checked>`;
  const multi = (key, label, cls = '') => {
    const items = (ex[key] || []).filter(i => i && i.value);
    if (!items.length) return '';
    return `<h3 class="${cls}">${label}</h3>` + items.map(i => `<div class="pick">${cb(`data-k="${key}"`, i.value)}
      <div><b>${esc(i.value)}</b><div class="src">来源: ${srcLink(i.source)}</div></div></div>`).join('');
  };
  const single = (key, label) => {
    const o = ex[key];
    if (!o || !o.value) return '';
    return `<h3>${label}</h3><div class="pick">${cb(`data-s="${key}"`, o.value)}
      <div><b>${esc(o.value)}</b><div class="src">来源: ${srcLink(o.source)}</div></div></div>`;
  };
  const people = (ex.key_people || []).filter(p => p && p.name);
  const peopleHtml = people.length ? '<h3>关键人物</h3>' + people.map(p => {
    const label = p.title ? `${p.name}(${p.title})` : p.name;
    return `<div class="pick">${cb(`data-p="1" data-name="${esc(p.name)}"`, label)}<div><b>${esc(p.name)}</b> ${esc(p.title || '')}
      <div class="src">来源: ${srcLink(p.source)}</div></div></div>`;
  }).join('') : '';
  const rel = ex.relevance;
  const relHtml = rel ? `<h3>与我们业务的相关度</h3><div class="pick">${cb('data-r="1"', rel.value)}<div><b class="${REL_CLASS[rel.value] || ''}">${esc(rel.value)}</b>
      ${rel.reason ? ' · ' + esc(rel.reason) : ''}<div class="src">来源: ${srcLink(rel.source)}</div></div></div>` : '';
  const anything = ['emails', 'whatsapp', 'phones', 'linkedin', 'facebook', 'instagram', 'other_social', 'import_signals', 'risk_flags']
      .some(k => (ex[k] || []).some(i => i && i.value)) || people.length || rel
    || ['company_size', 'founded_year', 'customer_type', 'main_products', 'certifications'].some(k => ex[k] && ex[k].value)
    || ex.summary;
  const plan = en.plan || [];
  const MISS = {emails: '邮箱', phones: '电话/WhatsApp', linkedin: 'LinkedIn', facebook: '社媒主页', company_size: '公司规模', customer_type: '客户类型',
    main_products: '主营产品', key_people: '关键人物', import_signals: '进口记录'};
  const body = modal(`<div class="flex between"><h2>背调结果 · 第 ${en.round || 1} 轮${readonly ? '（只读）' : ''}</h2>
    <button id="btnClose">关闭</button></div>
    <p class="muted">资料完整度 <b>${en.score || 0}</b>/100${en.missing && en.missing.length ? '；还缺：' + en.missing.map(m => MISS[m]).filter(Boolean).join('、') : ''}。
      ${readonly ? '' : '<b>勾选 = 这条信息有效；不勾 = 无效</b>。可以只写入勾选项，也可以「剔除未勾选项并继续下一轮」——未勾选的会被永久记住，以后不再提取。'}
      每条信息都标注来源，可点击核实（模型被禁止编造，来源无效的条目已被系统丢弃）。</p>
    ${en.error ? `<p class="muted">提示：${esc(en.error)}</p>` : ''}
    ${anything ? '' : '<div class="empty">这次搜索没有提取到有效信息。可以补全客户的公司名或网站后重试，或换「深度」再来一轮。</div>'}
    ${multi('risk_flags', '⚠ 风险提示', 'err')}
    ${multi('emails', '邮箱')}${multi('whatsapp', 'WhatsApp')}${multi('phones', '电话')}
    ${multi('linkedin', 'LinkedIn')}${multi('facebook', 'Facebook')}${multi('instagram', 'Instagram')}${multi('other_social', '其他社媒')}
    ${peopleHtml}
    ${single('company_size', '公司规模')}${single('founded_year', '成立年份')}${single('customer_type', '客户类型')}
    ${single('main_products', '主营产品')}${single('certifications', '认证信息')}
    ${multi('import_signals', '进口 / 采购线索')}
    ${relHtml}
    ${ex.summary ? `<h3>AI摘要</h3><div class="pick">${readonly ? '' : '<input type="checkbox" id="pickSummary" checked>'}<div>${esc(ex.summary)}</div></div>` : ''}
    <h3>这一轮查了哪些渠道</h3>
    <div class="muted" style="font-size:12px">${plan.length ? plan.map(p => `${DIM[p.dim] || p.dim}（${p.hits}条）`).join(' · ') : '—'}</div>
    <h3>原始搜索材料（${en.sources.length}个来源）</h3>
    <div class="scroll" style="max-height:180px;border:1px solid var(--line);border-radius:6px;padding:8px">
      ${en.sources.map(s => `<div class="src" style="margin-bottom:4px">· ${s.type ? `<span class="tag">${esc(s.type)}</span> ` : ''}<a class="ext" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title || s.url)}</a></div>`).join('')}</div>
    ${readonly ? '' : `<div class="flex" style="margin-top:16px">
      ${anything ? '<button class="primary" id="btnApply">写入勾选项（结束）</button>' : ''}
      <button id="btnNext">${anything ? '写入勾选项，剔除未勾选项，' : ''}继续下一轮</button>
      <select id="nextDepth" style="max-width:150px"><option value="standard">标准</option><option value="deep">深度</option><option value="quick">快速</option></select></div>
      <p class="muted" style="font-size:12px">下一轮只会针对还缺的信息换渠道、换问法再查，并自动排除你否掉的内容。</p>`}`);
  $('#btnClose', body).onclick = closeModal;
  const apply = $('#btnApply', body);
  if (readonly) return;
  const val = (sel, root) => $$(sel, root || body);
  const collect = () => {
    const checked = sel => val(sel).filter(i => i.checked).map(i => i.value);
    const unchecked = sel => val(sel).filter(i => !i.checked).map(i => i.dataset.name || i.value);
    const sel = {}, rej = {};
    for (const k of ['emails', 'linkedin', 'facebook', 'import_signals', 'risk_flags', 'whatsapp', 'phones', 'instagram', 'other_social']) {
      rej[k] = unchecked(`[data-k="${k}"]`);
    }
    for (const k of ['emails', 'linkedin', 'facebook', 'import_signals', 'risk_flags']) sel[k] = checked(`[data-k="${k}"]`);
    sel.whatsapp = checked('[data-k="whatsapp"]').concat(checked('[data-k="phones"]'));
    sel.other_social = checked('[data-k="other_social"]').concat(checked('[data-k="instagram"]'));
    for (const i of val('[data-s]')) { if (i.checked) sel[i.dataset.s] = i.value; else (rej[i.dataset.s] = rej[i.dataset.s] || []).push(i.value); }
    sel.key_people = checked('[data-p]');
    rej.key_people = unchecked('[data-p]');
    const r = $('[data-r]', body);
    if (r && r.checked) sel.relevance = r.value;
    if ($('#pickSummary', body) && $('#pickSummary', body).checked) sel.summary = ex.summary;
    return {sel, rej};
  };
  const done = () => {
    if (location.hash === '#customer/' + cid) window.dispatchEvent(new HashChangeEvent('hashchange'));
    else nav('customer', cid);
  };
  if (apply) apply.onclick = async () => {
    apply.disabled = true;
    try {
      await api(`/api/enrichments/${eid}/apply`, 'POST', collect().sel);
      closeModal(); toast('已写入客户档案'); done();
    } catch (e) { toast(e.message); apply.disabled = false; }
  };
  $('#btnNext', body).onclick = async () => {
    const {sel, rej} = collect(), depth = $('#nextDepth', body).value;
    const rejN = Object.values(rej).reduce((n, a) => n + a.length, 0);
    if (rejN && !confirm(`将永久排除你没勾选的 ${rejN} 条信息（以后背调不再出现），并写入勾选项，然后开始下一轮。继续？`)) return;
    modal('<h2>第 ' + ((en.round || 1) + 1) + ' 轮背调进行中…</h2><p class="muted">已写入勾选项，正在针对还缺的信息换渠道再查，请勿关闭。</p>');
    try {
      const r = await api(`/api/enrichments/${eid}/continue`, 'POST', {select: sel, rejected: rej, depth});
      await review(r.enrichment_id, cid);
    } catch (e) {
      modal(`<h2>下一轮失败</h2><p class="err">${esc(e.message)}</p><p class="muted">勾选项已写入档案，未勾选项已记住。</p><button id="x">关闭</button>`);
      $('#x').onclick = () => { closeModal(); done(); };
    }
  };
}
