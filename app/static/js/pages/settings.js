import {$, esc, api, toast} from '../lib.js';

export async function render(root, _arg, isCurrent) {
  const [s, v, b, qs] = await Promise.all([api('/api/settings'), api('/api/version'), api('/api/backups'), api('/api/settings/quote')]);
  if (!isCurrent()) return;
  const keyField = (id, label, k) => `<div class="field" style="max-width:560px;margin-top:10px"><label for="${id}">${label}
    ${s[k + '_set'] ? `<span class="tag good">已设置 ${esc(s[k + '_hint'])}</span>` : '<span class="tag warn">未设置</span>'}</label>
    <input id="${id}" type="password" autocomplete="off" placeholder="${s[k + '_set'] ? '留空 = 保持不变，填写新值 = 替换' : '粘贴 API Key'}"></div>`;
  root.innerHTML = `<div class="card"><h2>AI 背调设置</h2>
    ${keyField('sDs', 'DeepSeek API Key（platform.deepseek.com 申请）', 'deepseek_key')}
    <div class="field" style="max-width:560px;margin-top:10px"><label for="sModel">DeepSeek 模型名（默认 deepseek-chat）</label>
      <input id="sModel" value="${esc(s.deepseek_model)}"></div>
    ${keyField('sTv', 'Tavily API Key（tavily.com 注册，每月1000次免费搜索）', 'tavily_key')}
    <div class="flex" style="margin-top:14px"><button class="primary" id="btnSave">保存</button>
      <button id="btnTest">测试连通性</button></div><div id="testOut" style="margin-top:10px"></div></div>

    <div class="card"><h2>报价单抬头 / 收款信息 / WhatsApp 模板</h2>
      <p class="muted" style="margin-bottom:10px">抬头和联系方式印在报价单 PDF / Excel 的顶部和页脚；收款信息单独成框，印在条款下方。</p>
      <div class="flex" style="margin-bottom:12px"><img id="logoImg" src="/api/settings/quote/logo?t=${Date.now()}" alt="LOGO" style="height:52px;max-width:260px;object-fit:contain;border:1px solid var(--line);border-radius:8px;padding:6px;background:#fff">
        <input type="file" id="logoFile" accept="image/*" style="display:none"><button id="btnLogo">更换 LOGO</button>${qs.logo_custom ? '<button id="btnLogoReset">恢复默认 LOGO</button>' : ''}
        <span class="muted">建议用背景干净的 PNG；背景色会自动去掉。</span></div>
      <div class="grid"><div class="field"><label for="qName">公司名称</label><input id="qName" value="${esc(qs.company_name)}" placeholder="SINLUX"></div>
        <div class="field"><label for="qContact">联系人</label><input id="qContact" value="${esc(qs.contact_name)}"></div>
        <div class="field"><label for="qEmail">邮箱</label><input id="qEmail" value="${esc(qs.company_email)}"></div>
        <div class="field"><label for="qPhone">电话</label><input id="qPhone" value="${esc(qs.company_phone)}"></div>
        <div class="field" style="grid-column:1/-1"><label for="qAddr">地址</label><input id="qAddr" value="${esc(qs.company_address)}"></div></div>
      <h3 style="margin:16px 0 8px">Payment Information（收款信息）</h3>
      <label class="flex" style="margin-bottom:8px"><input type="checkbox" id="qShowBank" ${qs.show_bank === '1' ? 'checked' : ''}> 在报价单上显示收款信息</label>
      <div class="grid"><div class="field"><label for="bNo">Account Number</label><input id="bNo" value="${esc(qs.bank_account_number)}"></div>
        <div class="field"><label for="bName">Bank Name</label><input id="bName" value="${esc(qs.bank_name)}"></div>
        <div class="field"><label for="bSwift">SWIFT Code</label><input id="bSwift" value="${esc(qs.bank_swift)}"></div>
        <div class="field"><label for="bAcc">Account Name</label><input id="bAcc" value="${esc(qs.bank_account_name)}"></div>
        <div class="field" style="grid-column:1/-1"><label for="bAddr">Bank Address</label><input id="bAddr" value="${esc(qs.bank_address)}"></div></div>
      <div class="field" style="margin-top:12px"><label for="qWa">WhatsApp 报价文案模板（留空 = 使用默认模板）</label>
        <textarea id="qWa" style="min-height:200px;font-family:Consolas,monospace" placeholder="${esc(qs.whatsapp_template_effective)}">${esc(qs.whatsapp_template)}</textarea></div>
      <p class="muted" style="margin-top:6px">可用变量：${qs.variables.map(x => `<code>{${x}}</code>`).join(' ')}。不认识的 <code>{xxx}</code> 会原样保留。</p>
      <div class="flex" style="margin-top:10px"><button class="primary" id="btnSaveQuote">保存抬头 / 收款信息 / 模板</button><button id="btnResetWa">恢复默认模板</button></div></div>

    <div class="card"><h2>数据备份</h2>
      <p class="muted" style="margin-bottom:8px">一键生成包含数据库和图片的 zip，保存在 data/backups 文件夹。</p>
      <button id="btnBackup">立即备份</button>
      <div id="bkList" style="margin-top:10px">${renderBackups(b.backups)}</div></div>

    <div class="card"><h2>版本</h2><p>当前版本：<b>${esc(v.version)}</b></p>
      <pre class="muted" style="white-space:pre-wrap;margin-top:8px">${esc(v.changelog)}</pre></div>`;

  $('#btnSave').onclick = async () => {
    const body = {deepseek_model: $('#sModel').value, deepseek_key: $('#sDs').value, tavily_key: $('#sTv').value};
    try { await api('/api/settings', 'PUT', body); toast('已保存'); render(root, _arg, isCurrent); } catch (e) { toast(e.message); }
  };
  const saveQuote = async extra => {
    try {
      await api('/api/settings/quote', 'PUT', {company_name: $('#qName').value, contact_name: $('#qContact').value, company_email: $('#qEmail').value, company_phone: $('#qPhone').value,
        company_address: $('#qAddr').value, show_bank: $('#qShowBank').checked ? '1' : '0',
        bank_account_number: $('#bNo').value, bank_name: $('#bName').value, bank_swift: $('#bSwift').value, bank_account_name: $('#bAcc').value,
        bank_address: $('#bAddr').value, whatsapp_template: $('#qWa').value, ...extra});
      toast('已保存'); render(root, _arg, isCurrent);
    } catch (e) { toast(e.message); }
  };
  $('#btnLogo').onclick = () => $('#logoFile').click();
  $('#logoFile').onchange = e => {
    const f = e.target.files[0];
    if (!f) return;
    const rd = new FileReader();
    rd.onload = async () => {
      try { await api('/api/settings/quote/logo', 'POST', {image_base64: rd.result}); toast('LOGO 已更新'); render(root, _arg, isCurrent); } catch (err) { toast(err.message); }
    };
    rd.readAsDataURL(f);
  };
  if ($('#btnLogoReset')) $('#btnLogoReset').onclick = async () => {
    try { await api('/api/settings/quote/logo', 'DELETE', {}); toast('已恢复默认 LOGO'); render(root, _arg, isCurrent); } catch (err) { toast(err.message); }
  };
  $('#btnSaveQuote').onclick = () => saveQuote({});
  $('#btnResetWa').onclick = () => saveQuote({whatsapp_template: ''});
  $('#btnTest').onclick = async () => {
    $('#testOut').textContent = '测试中…（需要先保存 Key）';
    try {
      const r = await api('/api/settings/test', 'POST', {});
      $('#testOut').innerHTML = `<div>Tavily：${esc(r.tavily)}</div><div>DeepSeek：${esc(r.deepseek)}</div>`;
    } catch (e) { $('#testOut').innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  $('#btnBackup').onclick = async () => {
    try {
      const r = await api('/api/backup', 'POST', {});
      toast(`备份完成：${r.file}（${r.size_mb}MB）`);
      $('#bkList').innerHTML = renderBackups((await api('/api/backups')).backups);
    } catch (e) { toast(e.message); }
  };
}

function renderBackups(list) {
  return list.length ? list.slice(0, 10).map(x => `<div class="flex" style="padding:2px 0"><span>${esc(x.time)}</span>
    <a class="ext" href="/backups/${encodeURIComponent(x.file)}">${esc(x.file)}</a><span class="muted">${x.size_mb}MB</span></div>`).join('')
    : '<span class="muted">还没有备份</span>';
}
