import {$, esc, api, upload, toast} from '../lib.js';

export async function render(root, _arg, isCurrent) {
  const [s, v, b, qs, rt, uv] = await Promise.all([api('/api/settings'), api('/api/version'), api('/api/backups'), api('/api/settings/quote'), api('/api/rate'), api('/api/update/versions')]);
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

    <div class="card"><h2>HS 编码查询</h2>
      <p class="muted" style="margin-bottom:8px">填入海关（或报关行）提供的 HS 编码查询网站网址。产品页「HS 编码」旁的「查询」按钮会用这个网址在新标签页打开。
        网址里可以写 <code>{keyword}</code>，会被替换成产品名称（例如 <code>https://example.com/search?q={keyword}</code>）；不写就直接打开首页。</p>
      <div class="flex"><input id="sHs" value="${esc(s.hs_lookup_url)}" placeholder="https://…" style="max-width:560px"><button id="btnSaveHs">保存</button></div></div>

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

    <div class="card"><h2>汇率（CNY → USD）</h2>
      <p>当前 <b>1 CNY = ${rt.rate} USD</b>（≈ ${(1 / rt.rate).toFixed(4)} CNY/USD）　<span class="tag ${rt.mode === 'boc' ? 'good' : 'warn'}">${rt.mode === 'boc' ? '自动：中国银行现汇买入价' : '手动'}</span>
        <span class="muted">${rt.boc_buy ? `现汇买入价 ${rt.boc_buy}（人民币/100美元）` : ''} ${rt.last_success ? '最近成功更新 ' + esc(rt.last_success) : ''}</span></p>
      ${rt.last_error ? `<p class="err">最近一次自动更新失败：${esc(rt.last_error)}</p>` : ''}
      <div class="flex"><button id="btnRateRefresh">立即从中国银行更新</button><input id="rateManual" type="number" step="any" min="0" placeholder="手动汇率，如 0.138" style="max-width:180px">
        <button id="btnRateSave">保存为手动汇率</button>${rt.mode === 'manual' ? '<button id="btnRateAuto">恢复自动更新</button>' : ''}</div></div>

    <div class="card"><h2>数据备份与导出</h2>
      <p class="muted" style="margin-bottom:8px">一键生成包含数据库和图片的 zip，保存在 data/backups 文件夹。</p>
      <div class="flex"><button id="btnBackup">立即备份</button><button id="btnExportCust">导出全部客户 Excel</button></div>
      <div id="bkList" style="margin-top:10px">${renderBackups(b.backups)}</div></div>

    <div class="card"><h2>版本与升级</h2><p>当前版本：<b>${esc(v.version)}</b></p>
      <p class="muted" style="margin:6px 0">升级包是一个 .zip（含 manifest.json），只会改动 app/ 目录，<b>绝不碰 data/ 里的数据</b>；升级前自动快照当前版本，随时可以回退。升级或回退后需要关闭程序窗口再重新打开。</p>
      <div class="flex"><input type="file" id="updFile" accept=".zip" style="max-width:320px"><button id="btnUpdCheck">检查升级包</button></div>
      <div id="updOut" style="margin-top:10px"></div>
      <h3 style="margin:14px 0 6px">历史版本（升级前的快照）</h3>
      ${uv.versions.length ? uv.versions.slice(0, 15).map(x => `<div class="flex" style="padding:2px 0"><span>${esc(x.time)}</span><b>v${esc(x.version)}</b><span class="muted">${esc(x.name)}</span>
        <button class="small" data-rollback="${esc(x.name)}">回退到这个版本</button></div>`).join('') : '<span class="muted">还没有升级过</span>'}
      <h3 style="margin:14px 0 6px">更新日志</h3>
      <pre class="muted" style="white-space:pre-wrap">${esc(v.changelog)}</pre></div>`;

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
  $('#btnSaveHs').onclick = async () => { try { await api('/api/settings', 'PUT', {hs_lookup_url: $('#sHs').value}); toast('已保存'); } catch (e) { toast(e.message); } };
  $('#btnSaveQuote').onclick = () => saveQuote({});
  $('#btnResetWa').onclick = () => saveQuote({whatsapp_template: ''});
  $('#btnTest').onclick = async () => {
    $('#testOut').textContent = '测试中…（需要先保存 Key）';
    try {
      const r = await api('/api/settings/test', 'POST', {});
      $('#testOut').innerHTML = `<div>Tavily：${esc(r.tavily)}</div><div>DeepSeek：${esc(r.deepseek)}</div>`;
    } catch (e) { $('#testOut').innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  const act = (fn, okMsg) => async () => { try { await fn(); toast(okMsg); render(root, _arg, isCurrent); } catch (e) { toast(e.message); } };
  $('#btnRateRefresh').onclick = act(() => api('/api/rate/refresh', 'POST', {}), '汇率已更新');
  $('#btnRateSave').onclick = act(() => api('/api/rate', 'PUT', {rate: $('#rateManual').value}), '已保存为手动汇率');
  if ($('#btnRateAuto')) $('#btnRateAuto').onclick = act(() => api('/api/rate/mode', 'PUT', {mode: 'boc'}), '已恢复自动更新');
  $('#btnExportCust').onclick = async () => {
    try { const r = await api('/api/customers/export', 'POST', {}); window.location.href = r.url; toast('已导出 ' + r.filename); } catch (e) { toast(e.message); }
  };
  $('#btnUpdCheck').onclick = async () => {
    const f = $('#updFile').files[0];
    if (!f) return toast('请先选择升级包 .zip');
    $('#updOut').textContent = '检查中…';
    try {
      const i = await upload('/api/update/upload', f);
      $('#updOut').innerHTML = `<div class="card" style="box-shadow:none;border:1px solid var(--line)"><p>升级包版本 <b>v${esc(i.version)}</b>（当前 v${esc(i.current_version)}）${i.same_version ? ' <span class="tag warn">和当前版本相同</span>' : ''}</p>
        <pre class="muted" style="white-space:pre-wrap">${esc(i.changelog)}</pre><p class="muted">将更新 ${i.files.length} 个文件，删除 ${i.delete.length} 个文件。</p>
        <button class="primary" id="btnUpdApply">确认升级</button></div>`;
      $('#btnUpdApply').onclick = async () => {
        if (!confirm(`确定升级到 v${i.version}？升级前会自动快照当前版本，data/ 里的数据不受影响。`)) return;
        try {
          const r = await api('/api/update/apply', 'POST', {token: i.token});
          $('#updOut').innerHTML = `<p class="ok"><b>升级完成（v${esc(r.applied_version)}）。</b>已快照旧版本：${esc(r.backup)}。<b>请关闭程序窗口后重新打开</b>，新版本才会生效。</p>`;
        } catch (e) { $('#updOut').innerHTML = `<p class="err">${esc(e.message)}</p>`; }
      };
    } catch (e) { $('#updOut').innerHTML = `<p class="err">${esc(e.message)}</p>`; }
  };
  root.onclick = async e => {
    const b = e.target.closest('[data-rollback]');
    if (!b) return;
    if (!confirm('确定回退到这个版本？回退前会先快照当前状态，之后还能回来。回退后需要关闭程序窗口再重新打开。')) return;
    try {
      const r = await api('/api/update/rollback', 'POST', {name: b.dataset.rollback});
      $('#updOut').innerHTML = `<p class="ok"><b>已回退。</b>回退前的状态已快照为 ${esc(r.pre_rollback_backup)}。<b>请关闭程序窗口后重新打开</b>。</p>`;
    } catch (err) { toast(err.message); }
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
