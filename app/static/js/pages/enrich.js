import {$, $$, esc, api, modal, closeModal, toast, nav} from '../lib.js';

export async function startEnrich(cid) {
  modal('<h2>AI背调进行中…</h2><p class="muted">正在搜索公开信息并调用 DeepSeek 提取，通常需要 20~90 秒，请勿关闭。</p>');
  try {
    const r = await api(`/api/customers/${cid}/enrich`, 'POST', {});
    await review(r.enrichment_id, cid);
  } catch (e) {
    modal(`<h2>背调失败</h2><p class="err">${esc(e.message)}</p><button id="x">关闭</button>`);
    $('#x').onclick = closeModal;
  }
}

const srcLink = u => `<a class="ext" href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(u)}</a>`;

export async function review(eid, cid, readonly = false) {
  const en = await api('/api/enrichments/' + eid);
  const ex = en.extracted || {};
  const cb = (attr, value) => readonly ? '' : `<input type="checkbox" ${attr} value="${esc(value)}" checked>`;
  const multi = (key, label) => {
    const items = (ex[key] || []).filter(i => i && i.value);
    if (!items.length) return '';
    return `<h3>${label}</h3>` + items.map(i => `<div class="pick">${cb(`data-k="${key}"`, i.value)}
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
    return `<div class="pick">${cb('data-p="1"', label)}<div><b>${esc(p.name)}</b> ${esc(p.title || '')}
      <div class="src">来源: ${srcLink(p.source)}</div></div></div>`;
  }).join('') : '';
  const anything = ['emails', 'whatsapp', 'phones', 'linkedin', 'facebook', 'instagram', 'other_social']
      .some(k => (ex[k] || []).some(i => i && i.value)) || people.length
    || ['company_size', 'founded_year', 'customer_type', 'main_products', 'certifications'].some(k => ex[k] && ex[k].value)
    || ex.summary;
  const body = modal(`<div class="flex between"><h2>背调结果${readonly ? '（只读）' : '：请核对后选择写入项'}</h2>
    <button id="btnClose">关闭</button></div>
    <p class="muted">以下每条信息均标注来源，可点击核实。未勾选的不会写入档案。查不到的字段不显示（模型被禁止编造，来源无效的条目已被系统丢弃）。</p>
    ${en.error ? `<p class="muted">提示：${esc(en.error)}</p>` : ''}
    ${anything ? '' : '<div class="empty">这次搜索没有提取到有效信息。可以补全客户的公司名或网站后重试。</div>'}
    ${multi('emails', '邮箱')}${multi('whatsapp', 'WhatsApp')}${multi('phones', '电话')}
    ${multi('linkedin', 'LinkedIn')}${multi('facebook', 'Facebook')}${multi('instagram', 'Instagram')}${multi('other_social', '其他社媒')}
    ${peopleHtml}
    ${single('company_size', '公司规模')}${single('founded_year', '成立年份')}${single('customer_type', '客户类型')}
    ${single('main_products', '主营产品')}${single('certifications', '认证信息')}
    ${ex.summary ? `<h3>AI摘要</h3><div class="pick">${readonly ? '' : '<input type="checkbox" id="pickSummary" checked>'}<div>${esc(ex.summary)}</div></div>` : ''}
    <h3>原始搜索材料（${en.sources.length}个来源）</h3>
    <div class="scroll" style="max-height:180px;border:1px solid var(--line);border-radius:6px;padding:8px">
      ${en.sources.map(s => `<div class="src" style="margin-bottom:4px">· <a class="ext" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title || s.url)}</a></div>`).join('')}</div>
    ${readonly || !anything ? '' : '<div style="margin-top:16px"><button class="primary" id="btnApply">写入选中项到客户档案</button></div>'}`);
  $('#btnClose', body).onclick = closeModal;
  const apply = $('#btnApply', body);
  if (!apply) return;
  const checked = sel => $$(sel, body).filter(i => i.checked).map(i => i.value);
  apply.onclick = async () => {
    const sel = {};
    for (const k of ['emails', 'linkedin', 'facebook']) sel[k] = checked(`[data-k="${k}"]`);
    sel.whatsapp = checked('[data-k="whatsapp"]').concat(checked('[data-k="phones"]'));
    sel.other_social = checked('[data-k="other_social"]').concat(checked('[data-k="instagram"]'));
    for (const i of $$('[data-s]', body)) if (i.checked) sel[i.dataset.s] = i.value;
    sel.key_people = checked('[data-p]');
    if ($('#pickSummary', body) && $('#pickSummary', body).checked) sel.summary = ex.summary;
    apply.disabled = true;
    try {
      await api(`/api/enrichments/${eid}/apply`, 'POST', sel);
      closeModal(); toast('已写入客户档案');
      if (location.hash === '#customer/' + cid) window.dispatchEvent(new HashChangeEvent('hashchange'));
      else nav('customer', cid);
    } catch (e) { toast(e.message); apply.disabled = false; }
  };
}
