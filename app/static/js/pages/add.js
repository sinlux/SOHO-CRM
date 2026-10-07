import {$, esc, api, nav, toast, field, LV_DESC} from '../lib.js';
import {startEnrich} from './enrich.js';

const TYPE_NAME = {email: '邮箱', website: '网址', linkedin: 'LinkedIn链接', facebook: 'Facebook链接',
  social: '社媒链接', company: '公司名'};

export function render(root) {
  root.innerHTML = `<div class="card"><h2>录入新客户</h2>
    <p class="muted" style="margin-bottom:8px">粘贴任何一种信息：公司名、网址、邮箱、LinkedIn/Facebook 链接，系统自动识别并查重。</p>
    <div class="flex"><input id="smartInput" placeholder="例如：www.example.com 或 info@example.com 或 Example Lighting Inc." style="max-width:560px">
    <button class="primary" id="btnParse">识别</button></div>
    <div id="parseResult" style="margin-top:14px"></div></div>`;
  $('#smartInput').onkeydown = e => { if (e.key === 'Enter') $('#btnParse').click(); };
  $('#btnParse').onclick = async () => {
    const v = $('#smartInput').value.trim();
    if (!v) return;
    let r;
    try { r = await api('/api/intake/parse', 'POST', {input: v}); } catch (e) { toast(e.message); return; }
    const f = r.fields;
    const dup = r.duplicates.length ? `<div class="card warnbox"><b>⚠ 发现 ${r.duplicates.length} 个可能重复的已有客户：</b>
      ${r.duplicates.map(d => `<div style="margin-top:6px">· <a class="ext" href="#customer/${d.id}">${esc(d.company || d.name || '（无名）')}</a>
      <span class="muted">${esc(d.emails)}｜${esc(d.why)}</span></div>`).join('')}
      <p class="muted" style="margin-top:6px">如确认是同一客户，请点击上面链接进入已有档案，不要重复建档。</p></div>` : '';
    $('#parseResult').innerHTML = `<p>识别为：<span class="tag">${TYPE_NAME[r.type]}</span></p>${dup}
      <div class="grid" style="margin-top:12px">
        ${field('公司名', 'n_company', f.company)}${field('联系人', 'n_name', '')}
        ${field('国家(代码或名称)', 'n_country', '')}${field('网站', 'n_website', f.website)}
        ${field('邮箱(多个用空格分隔)', 'n_emails', f.emails)}${field('WhatsApp', 'n_whatsapp', '')}
        ${field('LinkedIn', 'n_linkedin', f.linkedin)}${field('Facebook', 'n_facebook', f.facebook)}
        ${field('其他社媒', 'n_other', f.other_social)}
        <div class="field"><label for="n_lv">等级</label><select id="n_lv"><option value="">未分级</option>
          ${[1, 2, 3, 4, 5, 6].map(l => `<option value="${l}">LV${l} ${LV_DESC[l]}</option>`).join('')}</select></div>
      </div>
      <div style="margin-top:14px" class="flex"><button class="primary" id="btnCreate">创建客户</button>
      <label class="flex"><input type="checkbox" id="autoEnrich" checked> 创建后立即AI背调</label></div>`;
    $('#btnCreate').onclick = async () => {
      const d = {company: $('#n_company').value, name: $('#n_name').value, country: $('#n_country').value,
        website: $('#n_website').value, emails: $('#n_emails').value, whatsapp: $('#n_whatsapp').value,
        linkedin: $('#n_linkedin').value, facebook: $('#n_facebook').value, other_social: $('#n_other').value,
        lv: $('#n_lv').value || null};
      try {
        const res = await api('/api/customers', 'POST', d);
        toast('客户已创建');
        const enrich = $('#autoEnrich').checked;
        nav('customer', res.id);
        if (enrich) setTimeout(() => startEnrich(res.id), 400);
      } catch (e) { toast(e.message); }
    };
  };
}
