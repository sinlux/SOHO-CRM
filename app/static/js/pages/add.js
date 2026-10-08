import {$, $$, esc, api, nav, toast, field, LV_DESC, STAGES} from '../lib.js';
import {startEnrich} from './enrich.js';
import {clearCustomerCache} from '../combo.js';

const TYPE_NAME = {email: '邮箱', website: '网址', linkedin: 'LinkedIn链接', facebook: 'Facebook链接',
  social: '社媒链接', company: '公司名'};

// 录入客户：两种方式共用同一张表单
//   ① 手动录入：直接填
//   ② 自动识别（线索）：粘贴公司名/网址/邮箱/社媒链接，系统识别并查重，预填表单；建好后可一键 AI 背调（联网搜索公开信息，汇总客户画像，由你来判断）
export function render(root) {
  let mode = 'manual';
  root.innerHTML = `<div class="card"><h2>录入新客户</h2>
    <div class="seg" id="modeSeg" style="margin:8px 0 14px"><button data-m="manual" class="on">✍️ 手动录入</button><button data-m="auto">🔎 自动识别（给一点线索）</button></div>
    <div id="autoBox" style="display:none"><p class="muted" style="margin-bottom:8px">粘贴任何一种线索：公司名、网址、邮箱、LinkedIn / Facebook 链接，系统自动识别并查重，预填下面的表单。
      建好客户后勾选「立即 AI 背调」，系统会联网搜索官网和社媒的公开信息，汇总出客户画像，<b>由你来判断</b>后再写入。</p>
      <div class="flex"><input id="smartInput" placeholder="例如：www.example.com 或 info@example.com 或 Example Lighting Inc." style="max-width:560px">
      <button class="primary" id="btnParse">识别</button></div></div>
    <div id="parseResult"></div><div id="formBox"></div></div>`;

  const dupBox = list => list.length ? `<div class="card warnbox"><b>⚠ 发现 ${list.length} 个可能重复的已有客户：</b>
    ${list.map(d => `<div style="margin-top:6px">· <a class="ext" href="#customer/${d.id}">${esc(d.company || d.name || '（无名）')}</a>
    <span class="muted">${esc(d.emails)}｜${esc(d.why)}</span></div>`).join('')}
    <p class="muted" style="margin-top:6px">如确认是同一客户，请点击上面链接进入已有档案，不要重复建档。</p></div>` : '';

  const form = (f = {}) => {
    $('#formBox').innerHTML = `<div class="grid" style="margin-top:12px">
        ${field('公司名', 'n_company', f.company)}${field('联系人', 'n_name', f.name)}
        ${field('国家(代码或名称)', 'n_country', f.country)}${field('网站', 'n_website', f.website)}
        ${field('邮箱(多个用空格分隔)', 'n_emails', f.emails)}${field('WhatsApp / 电话', 'n_whatsapp', f.whatsapp)}
        ${field('LinkedIn', 'n_linkedin', f.linkedin)}${field('Facebook', 'n_facebook', f.facebook)}
        ${field('其他社媒', 'n_other', f.other_social)}${field('地址', 'n_address', f.address)}
        <div class="field"><label for="n_lv">等级</label><select id="n_lv"><option value="">未分级</option>
          ${[1, 2, 3, 4, 5, 6].map(l => `<option value="${l}">LV${l} ${LV_DESC[l]}</option>`).join('')}</select></div>
        <div class="field"><label for="n_stage">阶段</label><select id="n_stage"><option value="">未设置</option>${STAGES.map(s => `<option>${s}</option>`).join('')}</select></div>
      </div>
      <div class="field" style="margin-top:10px"><label for="n_biz">主营业务</label><textarea id="n_biz" style="min-height:60px">${esc(f.main_business || '')}</textarea></div>
      <div style="margin-top:14px" class="flex"><button class="primary" id="btnCreate">创建客户</button>
      <label class="flex"><input type="checkbox" id="autoEnrich" ${mode === 'auto' ? 'checked' : ''}> 创建后立即 AI 背调</label></div>`;
    $('#btnCreate').onclick = create;
  };

  const create = async () => {
    const d = {company: $('#n_company').value, name: $('#n_name').value, country: $('#n_country').value,
      website: $('#n_website').value, emails: $('#n_emails').value, whatsapp: $('#n_whatsapp').value,
      linkedin: $('#n_linkedin').value, facebook: $('#n_facebook').value, other_social: $('#n_other').value,
      address: $('#n_address').value, main_business: $('#n_biz').value, lv: $('#n_lv').value || null, stage: $('#n_stage').value};
    if (!(d.company || d.name || d.emails || d.website)) return toast('至少填一项：公司名 / 联系人 / 邮箱 / 网站');
    try {
      const hint = (d.emails || '').split(/\s+/)[0] || d.website || d.company || d.name;      // 创建前再查一次重
      const r = await api('/api/intake/parse', 'POST', {input: hint});
      if (r.duplicates.length && !confirm('发现可能重复的已有客户：\n' + r.duplicates.map(x => '· ' + (x.company || x.name) + '（' + x.why + '）').join('\n') + '\n\n仍然要新建吗？')) return;
      const res = await api('/api/customers', 'POST', d);
      toast('客户已创建'); clearCustomerCache();
      const enrich = $('#autoEnrich').checked;
      nav('customer', res.id);
      if (enrich) setTimeout(() => startEnrich(res.id), 400);
    } catch (e) { toast(e.message); }
  };

  const setMode = m => {
    mode = m;
    $$('#modeSeg button').forEach(b => b.classList.toggle('on', b.dataset.m === m));
    $('#autoBox').style.display = m === 'auto' ? '' : 'none';
    $('#parseResult').innerHTML = '';
    if (m === 'manual') form(); else $('#formBox').innerHTML = '';
    if (m === 'auto') $('#smartInput').focus();
  };
  $('#modeSeg').onclick = e => { const b = e.target.closest('button'); if (b) setMode(b.dataset.m); };
  $('#smartInput').onkeydown = e => { if (e.key === 'Enter') $('#btnParse').click(); };
  $('#btnParse').onclick = async () => {
    const v = $('#smartInput').value.trim();
    if (!v) return;
    let r;
    try { r = await api('/api/intake/parse', 'POST', {input: v}); } catch (e) { toast(e.message); return; }
    $('#parseResult').innerHTML = `<p>识别为：<span class="tag">${TYPE_NAME[r.type]}</span></p>${dupBox(r.duplicates)}`;
    form(r.fields);
  };
  setMode('manual');
}
