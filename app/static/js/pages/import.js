import {$, esc, api, upload, toast, nav, lvBadge} from '../lib.js';

// 流程：选文件 → 上传并预览（尚未写入）→ 核对 → 确认导入
export async function render(root) {
  const s = await api('/api/stats');
  root.innerHTML = `<div class="card"><h2>Excel 导入客户</h2>
    <p>选择客户汇总表（.xlsx），读取第一个工作表。列名：LV / Country / Name / Company / Website / Email / Main business / Address / Whatsapp / Linkedin / Facebook / Remark。
    当前库中已有 <b>${s.total}</b> 个客户，公司名或邮箱重复的行会自动跳过。<b>先预览，确认后才会写入。</b></p>
    <label class="flex" style="margin:12px 0"><input type="checkbox" id="remapLv">
      反转LV等级（1↔6、2↔5、3↔4 互换）—— 如果旧表里 1 代表最重要客户，请勾选</label>
    <div class="flex"><input type="file" id="file" accept=".xlsx" style="max-width:360px"><button class="primary" id="btnUp">上传并预览</button></div>
    <div id="out" style="margin-top:14px"></div></div>`;
  let sid = null;

  const showPreview = p => {
    sid = p.session_id;
    $('#out').innerHTML = `<p><b>预览（工作表「${esc(p.sheet)}」）：</b>将导入 <b class="ok">${p.to_import}</b> 条，
      重复 ${p.duplicates} 条，共跳过 ${p.skipped} 条。<span class="muted">此时还没有写入任何数据。</span></p>
      ${p.sample.length ? `<div class="scroll"><table><tr><th>行</th><th>LV</th><th>国家</th><th>公司</th><th>联系人</th><th>邮箱</th></tr>
        ${p.sample.map(r => `<tr><td>${r.row}</td><td>${lvBadge(r.data.lv)}</td><td>${esc(r.data.country)}</td>
          <td>${esc(r.data.company)}</td><td>${esc(r.data.name)}</td><td>${esc(r.data.emails)}</td></tr>`).join('')}</table></div>
        <p class="muted">仅显示前 ${p.sample.length} 条可导入的记录。LV 显示的是${p.remap_lv ? '反转后' : '原始'}的值。</p>` : ''}
      ${p.problems.length ? `<h3>需要你知晓的 ${p.problems.length} 条记录：</h3><div class="scroll">${p.problems.map(x => `<div class="muted">· ${esc(x)}</div>`).join('')}</div>` : ''}
      <div class="flex" style="margin-top:12px"><button class="primary" id="btnApply" ${p.to_import ? '' : 'disabled'}>确认导入 ${p.to_import} 条</button></div>`;
    $('#btnApply').onclick = apply;
  };
  const apply = async () => {
    $('#btnApply').disabled = true;
    try {
      const r = await api('/api/import/customers/apply', 'POST', {session_id: sid, remap_lv: $('#remapLv').checked});
      $('#out').innerHTML = `<p class="ok"><b>完成：</b>成功导入 ${r.imported} 条，跳过 ${r.skipped} 条。</p>
        <button class="primary" id="btnGo">查看客户列表</button>`;
      $('#btnGo').onclick = () => nav('list');
    } catch (e) { $('#out').innerHTML = `<p class="err">${esc(e.message)}</p>`; }
  };
  $('#btnUp').onclick = async () => {
    const f = $('#file').files[0];
    if (!f) return toast('请先选择 .xlsx 文件');
    $('#btnUp').disabled = true; $('#out').textContent = '读取中…';
    try {
      showPreview(await upload('/api/import/customers/upload?remap_lv=' + ($('#remapLv').checked ? 1 : 0), f));
    } catch (e) { $('#out').innerHTML = `<p class="err">${esc(e.message)}</p>`; }
    $('#btnUp').disabled = false;
  };
  $('#remapLv').onchange = async () => {      // 切换反转开关：用同一个会话重新预览
    if (!sid) return;
    try { showPreview(await api('/api/import/customers/preview', 'POST', {session_id: sid, remap_lv: $('#remapLv').checked})); }
    catch (e) { $('#out').innerHTML = `<p class="err">${esc(e.message)}</p>`; }
  };
}
