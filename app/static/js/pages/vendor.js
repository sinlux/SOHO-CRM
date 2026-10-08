import {$, $$, esc, api, nav, toast, modal, closeModal} from '../lib.js';
import {STATUS, stars} from './vendors.js';

const PLATFORMS = ['阿里巴巴', '1688', '微信', '旺旺', '展会', '朋友介绍', '其他'];
const OCR_LABEL = {pending: ['识别中…', 'warn'], done: ['已识别文字', 'good'], unavailable: ['本机无 OCR，可手动补充文字', 'warn'], failed: ['识别失败', 'bad']};
const today = () => new Date().toLocaleDateString('sv');
const MAX_PASTE = 10;

export async function render(root, arg, isCurrent) {
  const id = parseInt(arg, 10);
  if (!id) return nav('vendors');
  let v;
  try { v = await api('/api/vendors/' + id); } catch (e) {
    root.innerHTML = `<div class="card"><button id="back">← 返回</button><p class="err" style="margin-top:10px">${esc(e.message)}</p></div>`;
    $('#back').onclick = () => nav('vendors');
    return;
  }
  const projects = (await api('/api/vendor_projects')).projects;
  if (!isCurrent()) return;
  const reload = () => render(root, arg, isCurrent);
  let staged = [];                                   // 待归档的截图（data URL）
  let lastProject = '';

  root.innerHTML = `
  <div class="card"><div class="flex between"><div class="flex"><button id="back">← 返回</button><h2 style="margin:0">${esc(v.name)}</h2>
      <select id="vStatus" style="width:120px">${Object.entries(STATUS).map(([k, l]) => `<option value="${k}" ${v.status === k ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <select id="vRating" style="width:110px"><option value="">未评分</option>${[1, 2, 3, 4, 5].map(n => `<option value="${n}" ${v.rating === n ? 'selected' : ''}>${'★'.repeat(n)}</option>`).join('')}</select></div>
      <div class="flex"><button id="btnSave">保存资料</button><button class="danger" id="btnDel">删除供应商</button></div></div>
    <div class="grid" style="margin-top:14px">
      ${[['name', '名称'], ['contact', '联系人'], ['wechat', '微信'], ['phone', '电话'], ['email', '邮箱'], ['location', '所在地'], ['link', '店铺 / 网址'], ['main_products', '主营产品']]
        .map(([k, l]) => `<div class="field"><label>${l}</label><input data-f="${k}" value="${esc(v[k])}"></div>`).join('')}
      <div class="field"><label>来源平台</label><input data-f="platform" list="platList" value="${esc(v.platform)}"><datalist id="platList">${PLATFORMS.map(p => `<option>${p}</option>`).join('')}</datalist></div></div>
    <div class="field" style="margin-top:10px"><label>备注（只有你自己看得到：合作评价、付款习惯、质量印象、为什么淘汰……）</label><textarea data-f="notes">${esc(v.notes)}</textarea></div></div>

  <div class="card"><h3 style="margin-top:0">💬 沟通记录（聊天截图归档）</h3>
    <p class="muted">在这个页面按 <b>Ctrl+V</b> 直接粘贴聊天截图（阿里旺旺 / 微信 / 邮件都行，可连续粘贴多张），写上项目名，点「归档」。截图里的文字会在<b>本机</b>自动识别，之后能按文字搜到（不会上传到任何地方）。</p>
    <datalist id="projList">${projects.map(p => `<option value="${esc(p.project)}">`).join('')}</datalist>
    <div class="flex"><input id="cProject" list="projList" placeholder="项目名（如 Hotel Caribe 一期）" style="max-width:240px"><input id="cDate" type="date" value="${today()}" style="max-width:160px">
      <input id="cTitle" placeholder="标题（可选，如 旺旺报价）" style="max-width:220px"><button id="btnPick">选择截图文件</button><input type="file" id="cFile" accept="image/*" multiple style="display:none"></div>
    <textarea id="cNote" placeholder="文字备注（可选）：这次聊到了什么、对方给的价格 / 交期 / 起订量……" style="margin-top:8px;min-height:60px"></textarea>
    <div id="stagedBox" class="flex" style="margin:8px 0"></div>
    <div class="flex"><button class="primary" id="btnArchive">归档</button><span class="muted" id="archHint">还没有粘贴截图（也可以只写文字备注）</span></div>
    <div id="chatList" style="margin-top:14px">${chatsHtml(v.chats)}</div></div>

  <div class="card"><h3 style="margin-top:0">💴 产品报价</h3>${v.quotes.length ? `<table><tr><th>产品</th><th>人民币报价</th><th>日期</th><th>项目</th><th>备注</th></tr>${v.quotes.map(q => `<tr>
      <td>${q.product_id ? `<a class="ext" href="#product/${q.product_id}">${esc(q.sku || '')} ${esc(q.product_name || '')}</a>` : ''}</td><td><b>${q.price_cny ?? '—'}</b> ${q.is_adopted ? '<span class="tag good">已采纳</span>' : ''}</td>
      <td class="muted">${esc(q.quote_date || '')}</td><td>${q.project ? `<span class="tag">${esc(q.project)}</span>` : ''}</td><td class="muted">${esc(q.remark || '')}${q.screenshot_url ? ` <a class="ext" href="${esc(q.screenshot_url)}" target="_blank" rel="noopener">截图</a>` : ''}</td></tr>`).join('')}</table>`
      : '<span class="muted">还没有产品报价。到产品页「供应商」页签添加报价时选这家供应商即可。</span>'}</div>`;

  $('#back').onclick = () => nav('vendors');
  $('#btnSave').onclick = async () => {
    const d = {status: $('#vStatus').value, rating: $('#vRating').value};
    $$('[data-f]', root).forEach(el => d[el.dataset.f] = el.value);
    try { await api('/api/vendors/' + id, 'PUT', d); toast('已保存'); reload(); } catch (e) { toast(e.message); }
  };
  $('#btnDel').onclick = async () => {
    try {
      const {impact: i} = await api(`/api/vendors/${id}/impact`);
      if (!confirm(`确定删除供应商「${v.name}」？\n将同时永久删除：沟通记录 ${i.chats} 条（含截图）。\n产品页上它的 ${i.quotes} 条报价会保留（只是不再关联档案）。\n此操作不可恢复。`)) return;
      await api('/api/vendors/' + id, 'DELETE', {confirm: true});
      toast('已删除'); nav('vendors');
    } catch (e) { toast(e.message); }
  };

  // ---------- 粘贴 / 选择截图 ----------
  const drawStaged = () => {
    $('#stagedBox').innerHTML = staged.map((s, i) => `<span style="position:relative"><img src="${s}" style="height:72px;border-radius:8px;border:1px solid var(--line)"><button class="small danger" data-rm="${i}" style="position:absolute;top:-6px;right:-6px">✕</button></span>`).join('');
    $('#archHint').textContent = staged.length ? `已选 ${staged.length} 张截图，点「归档」保存` : '还没有粘贴截图（也可以只写文字备注）';
  };
  const addFiles = files => {
    for (const f of files) {
      if (staged.length >= MAX_PASTE) { toast(`一次最多 ${MAX_PASTE} 张`); break; }
      const rd = new FileReader();
      rd.onload = () => { staged.push(rd.result); drawStaged(); };
      rd.readAsDataURL(f);
    }
  };
  const onPaste = e => {
    const files = [...(e.clipboardData || {}).items || []].filter(it => it.type.startsWith('image/')).map(it => it.getAsFile());
    if (files.length) { e.preventDefault(); addFiles(files); }
  };
  document.addEventListener('paste', onPaste);
  const stop = () => { document.removeEventListener('paste', onPaste); window.removeEventListener('hashchange', stop); };
  window.addEventListener('hashchange', stop);
  $('#btnPick').onclick = () => $('#cFile').click();
  $('#cFile').onchange = e => { addFiles([...e.target.files]); e.target.value = ''; };
  $('#stagedBox').onclick = e => { const b = e.target.closest('[data-rm]'); if (b) { staged.splice(Number(b.dataset.rm), 1); drawStaged(); } };
  $('#btnArchive').onclick = async () => {
    const note = $('#cNote').value.trim();
    if (!staged.length && !note) return toast('请先粘贴截图，或写一点文字备注');
    $('#btnArchive').disabled = true;
    const base = {project: $('#cProject').value, chat_date: $('#cDate').value, title: $('#cTitle').value};
    try {
      if (!staged.length) await api(`/api/vendors/${id}/chats`, 'POST', {...base, note});
      for (let i = 0; i < staged.length; i++) await api(`/api/vendors/${id}/chats`, 'POST', {...base, image_base64: staged[i], note: i === 0 ? note : ''});
      toast('已归档'); lastProject = base.project; reload();
    } catch (e) { toast(e.message); $('#btnArchive').disabled = false; }
  };

  // ---------- 每条记录的操作 ----------
  $('#chatList').onclick = async e => {
    const b = e.target.closest('button,[data-zoom]');
    if (!b) return;
    const d = b.dataset;
    try {
      if (d.zoom) { modal(`<div style="text-align:center"><img src="${esc(d.zoom)}" style="max-width:100%;max-height:78vh"></div><div class="flex" style="margin-top:10px;justify-content:center"><a class="ext" href="${esc(d.zoom)}" target="_blank" rel="noopener">在新标签页打开原图</a><button id="zx">关闭</button></div>`, true); $('#zx').onclick = closeModal; }
      else if (d.cdel) { if (confirm('删除这条沟通记录和截图？')) { await api('/api/vendor_chats/' + d.cdel, 'DELETE', {}); reload(); } }
      else if (d.cocr) { await api(`/api/vendor_chats/${d.cocr}/ocr`, 'POST', {}); toast('重新识别中…'); reload(); }
      else if (d.csave) {
        const box = $(`[data-cid="${d.csave}"]`, root);
        const payload = {project: $('[data-k=project]', box).value, title: $('[data-k=title]', box).value, note: $('[data-k=note]', box).value, chat_date: $('[data-k=chat_date]', box).value};
        const ot = $('details [data-k=ocr_text]', box);
        if (ot && ot.value !== ot.dataset.orig) payload.ocr_text = ot.value;          // 只有真改过识别文字才提交，否则会把"识别中"的记录误标成已完成
        await api('/api/vendor_chats/' + d.csave, 'PUT', payload);
        toast('已保存'); reload();
      }
    } catch (err) { toast(err.message); }
  };
  // 有图片还在识别：几秒后自动刷新一次状态（最多刷 20 次）
  let polls = 0;
  const poll = () => {
    if (!$('#chatList') || location.hash !== '#vendor/' + id) return;
    if (!v.chats.some(c => c.ocr_status === 'pending') || polls++ > 20) return;
    setTimeout(async () => {
      if (location.hash !== '#vendor/' + id) return;
      try {
        v = await api('/api/vendors/' + id);
        if ($('#chatList') && !$('#chatList').contains(document.activeElement)) $('#chatList').innerHTML = chatsHtml(v.chats);
      } catch (e) { return; }
      poll();
    }, 3000);
  };
  poll();
}

function chatsHtml(chats) {
  if (!chats.length) return '<span class="muted">还没有沟通记录</span>';
  return chats.map(c => {
    const [ol, oc] = c.image_file ? (OCR_LABEL[c.ocr_status] || ['', '']) : ['', ''];
    return `<div class="card" style="box-shadow:none;border:1px solid var(--line);margin:0 0 10px" data-cid="${c.id}"><div class="flex" style="align-items:flex-start;flex-wrap:nowrap;gap:14px">
      ${c.image_url ? `<img src="${esc(c.thumb_url)}" data-zoom="${esc(c.image_url)}" style="width:120px;height:120px;object-fit:cover;border-radius:10px;border:1px solid var(--line);cursor:zoom-in;flex:none" title="点击看大图">` : ''}
      <div style="flex:1;min-width:0"><div class="flex"><input data-k="project" value="${esc(c.project)}" list="projList" placeholder="项目" style="max-width:200px"><input data-k="chat_date" type="date" value="${esc(c.chat_date || (c.created_at || '').slice(0, 10))}" style="max-width:150px">
          <input data-k="title" value="${esc(c.title)}" placeholder="标题" style="max-width:200px">${ol ? `<span class="tag ${oc}" title="${esc(c.ocr_error)}">${ol}</span>` : ''}</div>
        <textarea data-k="note" placeholder="文字备注" style="min-height:44px;margin-top:6px">${esc(c.note)}</textarea>
        ${c.image_file ? `<details style="margin-top:6px"><summary class="muted" style="cursor:pointer">识别出的文字（可手动修改，用于搜索）</summary><textarea data-k="ocr_text" data-orig="${esc(c.ocr_text)}" style="min-height:90px;margin-top:4px">${esc(c.ocr_text)}</textarea>
          ${c.ocr_error && c.ocr_status !== 'done' ? `<div class="err" style="font-size:12px">${esc(c.ocr_error)}</div>` : ''}</details>` : ''}
        <div class="flex" style="margin-top:6px"><button class="small primary" data-csave="${c.id}">保存修改</button>${c.image_file ? `<button class="small" data-cocr="${c.id}">重新识别</button>` : ''}<button class="small danger" data-cdel="${c.id}">删除</button></div></div></div></div>`;
  }).join('');
}
