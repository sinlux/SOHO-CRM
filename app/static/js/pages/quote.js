import {$, esc, api, nav, toast, modal, closeModal, avatar} from '../lib.js';
import {thumb} from './products.js';

// 报价单里的金额：至少两位小数（单价允许到四位，如 1.005）
const money = (v, cur) => `<span class="money">${esc(cur)} ${Number(v).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 4})}</span>`;
import {STATUS, statusTag} from './quotes.js';

export async function render(root, arg, isCurrent) {
  const qid = parseInt(arg, 10);
  if (!qid) return nav('quotes');
  let q;
  try { q = (await api('/api/quotes/' + qid)).quote; } catch (e) {
    root.innerHTML = `<div class="card"><button id="back">← 返回</button><p class="err" style="margin-top:10px">${esc(e.message)}</p></div>`;
    $('#back').onclick = () => nav('quotes');
    return;
  }
  if (!isCurrent()) return;
  const locked = q.status === 'accepted';
  const flow = ['draft', 'sent', 'accepted', 'rejected', 'expired'];
  root.innerHTML = `
  <div class="card"><div class="flex between"><div class="flex"><button id="back">← 返回</button>
      <div><div class="idline"><b>${esc(q.quote_no)}</b>${statusTag(q)}</div>
        <div class="idname">${avatar(q.company || q.customer_name, 22)} <a class="ext" href="#customer/${q.customer_id}">${esc(q.company || q.customer_name)}</a>
          · ${esc(q.created_at.slice(0, 10))} · 有效至 ${esc(q.valid_until)}（${q.valid_days} 天）</div></div></div>
    <div class="flex"><button id="btnPdf" class="primary">生成 PDF</button><button id="btnXls">生成 Excel</button><button id="btnWa">WhatsApp 文案</button>
      <button id="btnEdit" ${locked ? 'disabled title="已成交的报价单不能修改，请先改回「已发送」"' : ''}>编辑</button><button id="btnDup">复制</button><button class="danger" id="btnDel">删除</button></div></div>
    <div id="exportResult" style="margin-top:10px"></div></div>

  <div class="card"><div class="sec-title"><h3>报价状态</h3><span class="muted">成交会把明细价格记入产品价格历史，并推进客户阶段；改回非成交会撤销这些记录</span></div>
    <div class="seg" id="stSeg">${flow.map(s => `<button data-st="${s}" class="${q.status === s ? 'on' : ''}">${STATUS[s][0]}</button>`).join('')}</div>
    <div class="field" style="margin-top:12px;max-width:560px"><label>客户反馈 / 备忘（标记「未成交」时建议写上原因）</label><input id="feedback" value="${esc(q.customer_feedback || '')}" placeholder="如：价格偏高，竞品报 $2.1"></div></div>

  <div class="card"><table><tr><th>#</th><th></th><th>SKU</th><th>名称 / 规格</th><th>数量</th><th>单价</th><th>金额</th></tr>
    ${q.items.map((i, n) => `<tr><td>${n + 1}</td><td>${thumb({thumb_url: i.thumb_url})}</td><td><b>${esc(i.sku)}</b></td>
      <td>${esc(i.name)}${i.spec ? `<div class="muted" style="white-space:pre-wrap;max-width:420px">${esc(i.spec.length > 240 ? i.spec.slice(0, 240) + '…' : i.spec)}</div>` : ''}${i.remark ? `<div class="muted">${esc(i.remark)}</div>` : ''}</td>
      <td>${i.quantity} ${esc(i.unit)}</td><td>${money(i.unit_price, q.currency)}</td><td>${money(i.amount, q.currency)}</td></tr>`).join('')}
    <tr><td colspan="6" style="text-align:right"><b>合计</b></td><td><b class="big-total">${money(q.total, q.currency)}</b></td></tr></table>
    <div class="grid" style="margin-top:14px">
      ${q.lead_time ? `<div><span class="muted">交期</span><div>${esc(q.lead_time)}</div></div>` : ''}${q.payment_terms ? `<div><span class="muted">付款条件</span><div>${esc(q.payment_terms)}</div></div>` : ''}
      ${q.shipping_terms ? `<div><span class="muted">贸易条款</span><div>${esc(q.shipping_terms)}</div></div>` : ''}${q.notes ? `<div><span class="muted">备注（印在报价单上）</span><div style="white-space:pre-wrap">${esc(q.notes)}</div></div>` : ''}</div></div>`;

  $('#back').onclick = () => nav('quotes');
  $('#btnEdit').onclick = () => nav('quoteedit', qid);
  $('#btnDup').onclick = async () => {
    try { const r = await api(`/api/quotes/${qid}/duplicate`, 'POST', {}); toast('已复制为草稿 ' + r.quote_no); nav('quoteedit', r.id); } catch (e) { toast(e.message); }
  };
  $('#btnDel').onclick = async () => {
    if (!confirm(`删除报价单 ${q.quote_no}？${locked ? '\n它已成交：删除会同时撤销写入产品售价历史的成交价。' : ''}`)) return;
    try { await api('/api/quotes/' + qid, 'DELETE', {confirm: true}); toast('已删除'); nav('quotes'); } catch (e) { toast(e.message); }
  };
  $('#stSeg').onclick = async e => {
    const b = e.target.closest('[data-st]');
    if (!b || b.dataset.st === q.status) return;
    try {
      await api(`/api/quotes/${qid}/status`, 'POST', {status: b.dataset.st, feedback: $('#feedback').value});
      toast('状态已更新：' + STATUS[b.dataset.st][0]); render(root, arg, isCurrent);
    } catch (err) { toast(err.message); }
  };
  $('#feedback').onchange = async () => { try { await api(`/api/quotes/${qid}/status`, 'POST', {status: q.status, feedback: $('#feedback').value}); toast('已保存'); } catch (e) { toast(e.message); } };
  const gen = kind => async () => {
    $('#exportResult').textContent = '生成中…';
    try {
      const d = await api(`/api/quotes/${qid}/${kind}`, 'POST', {});
      $('#exportResult').innerHTML = `<span class="ok">✓ 已生成</span> <a class="ext" id="dl" href="${esc(d.url)}" download="${esc(d.filename)}">${esc(d.filename)}</a> <span class="muted">（文件同时保存在 data/exports 文件夹）</span>`;
      $('#dl').click();
    } catch (e) { $('#exportResult').innerHTML = `<span class="err">生成失败：${esc(e.message)}</span>`; }
  };
  $('#btnPdf').onclick = gen('pdf');
  $('#btnXls').onclick = gen('excel');
  $('#btnWa').onclick = async () => {
    try {
      const d = await api(`/api/quotes/${qid}/whatsapp`);
      modal(`<div class="flex between"><h2>WhatsApp 文案</h2><button id="x">关闭</button></div>
        <textarea id="waText" style="min-height:300px;font-family:Consolas,monospace">${esc(d.text)}</textarea>
        <div class="flex" style="margin-top:10px"><button class="primary" id="copy">复制到剪贴板</button>
          ${d.wa_number ? '<a class="ext" id="open" target="_blank" rel="noopener noreferrer">在 WhatsApp 中打开此客户的对话（+' + esc(d.wa_number) + '）</a>' : '<span class="muted">客户资料里没有可识别的 WhatsApp 号码</span>'}</div>
        <p class="muted" style="margin-top:8px">文案模板可在「设置 → 报价单抬头与 WhatsApp 模板」里修改；这里改的内容只用于本次复制。</p>`);
      $('#x').onclick = closeModal;
      $('#copy').onclick = () => navigator.clipboard.writeText($('#waText').value).then(() => toast('已复制'), () => { $('#waText').select(); document.execCommand('copy'); toast('已复制'); });
      if ($('#open')) $('#open').onclick = () => { $('#open').href = `https://wa.me/${d.wa_number}?text=${encodeURIComponent($('#waText').value)}`; };
    } catch (e) { toast(e.message); }
  };
}
