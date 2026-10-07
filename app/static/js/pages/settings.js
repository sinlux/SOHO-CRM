import {$, esc, api, toast} from '../lib.js';

export async function render(root, _arg, isCurrent) {
  const [s, v, b] = await Promise.all([api('/api/settings'), api('/api/version'), api('/api/backups')]);
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
