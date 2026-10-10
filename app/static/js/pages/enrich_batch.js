import {$, $$, esc, api, toast} from '../lib.js';
import {review} from './enrich.js';

// 批量背调：选范围 → 看成本估算 → 开始 → 后台逐个查 → 你逐个核对（勾选有效项 / 剔除无效项 / 继续下一轮）。
// 批量只负责"查"，结果都是待确认，不会改动客户档案。
const STATE = {queued: '排队中', running: '进行中', done: '已完成', failed: '失败', cancelled: '已取消'};
const EN = {pending: '待确认', applied: '已处理', failed: '失败'};
const DEPTH = {quick: '快速（约3次搜索/客户）', standard: '标准（约6次搜索/客户）', deep: '深度（约10次搜索/客户）'};

export async function render(root, _arg, isCurrent) {
  const cs = await api('/api/countries');
  if (!isCurrent()) return;
  let timer = null, filter = '';
  root.innerHTML = `<div class="card"><h2>批量背调</h2>
    <p class="muted">对导入的客户按外贸背调流程批量查公开信息：官网 → 联系方式 → LinkedIn → Facebook/Instagram → 企业黄页 → 进口贸易记录 → 新闻/评价/投诉 → B2B 平台（拉美客户还会用西语搜）。
      <b>只发送「公司名 + 国家」去搜索，不上传你的报价、订单或联系人私人资料；不登录任何平台。</b>
      每个客户的结果都是<b>待确认</b>：你核对后勾选有效项写入，无效项剔除并让系统继续查下一轮。需要在「设置」里填好 Tavily 和 DeepSeek 的 Key。</p>
    <div class="flex" style="flex-wrap:wrap;gap:8px">
      <select id="bMode"><option value="unresearched">还没背调过的客户</option><option value="all">所有客户（含已背调过的）</option></select>
      <select id="bLv"><option value="">客户等级不限</option>${[6, 5, 4, 3, 2, 1].map(n => `<option value="${n}">LV${n} 及以上</option>`).join('')}</select>
      <select id="bCountry"><option value="">国家不限</option>${cs.countries.map(c => `<option value="${esc(c.country)}">${esc(c.country)} (${c.n})</option>`).join('')}</select>
      <select id="bDepth">${Object.entries(DEPTH).map(([k, v]) => `<option value="${k}" ${k === 'standard' ? 'selected' : ''}>${v}</option>`).join('')}</select>
      <button id="bEst">估算数量和成本</button></div>
    <div id="bEstOut" class="muted" style="margin-top:8px"></div></div>
    <div class="card"><div class="flex between"><h3 style="margin:0">进度与结果</h3>
      <div class="flex"><button id="bStart">开始 / 继续</button><button id="bPause">暂停</button><button id="bRetry">重试失败的</button><button id="bClear" class="danger">清空排队</button></div></div>
    <div id="bStatus" style="margin-top:10px"></div></div>`;

  const scope = () => ({mode: $('#bMode').value, lv_min: $('#bLv').value, country: $('#bCountry').value});
  $('#bEst').onclick = async () => {
    try {
      const e = await api('/api/enrich/batch/estimate', 'POST', {scope: scope(), depth: $('#bDepth').value});
      if (!e.customers) { $('#bEstOut').innerHTML = '没有符合条件的客户（已在队列里的不重复排）。'; return; }
      $('#bEstOut').innerHTML = `将背调 <b>${e.customers}</b> 个客户，约 <b>${e.searches}</b> 次 Tavily 搜索 + <b>${e.ai_calls}</b> 次 DeepSeek 调用。
        <span class="muted">Tavily 免费账号每月约 1000 次额度，额度用完批量会自动暂停，不会白白失败；DeepSeek 每次约几分钱。</span>
        <div style="margin-top:8px"><button class="primary" id="bGo">确认加入队列并开始</button></div>`;
      $('#bGo').onclick = async () => {
        try {
          const r = await api('/api/enrich/batch', 'POST', {scope: scope(), depth: $('#bDepth').value, start: true});
          toast(`已加入 ${r.queued} 个客户，开始背调`); $('#bEstOut').innerHTML = ''; refresh();
        } catch (e) { toast(e.message); }
      };
    } catch (e) { toast(e.message); }
  };
  const act = (id, url, msg) => { $(id).onclick = async () => { try { await api(url, 'POST', {}); if (msg) toast(msg); refresh(); } catch (e) { toast(e.message); } }; };
  act('#bStart', '/api/enrich/batch/start', '已开始'); act('#bPause', '/api/enrich/batch/pause', '已暂停（正在查的客户会查完）');
  act('#bRetry', '/api/enrich/batch/retry', '失败的已重新排队，点「开始 / 继续」'); 
  $('#bClear').onclick = async () => { if (!confirm('清空所有排队中的客户？（已完成的结果保留）')) return; await api('/api/enrich/batch/clear', 'POST', {}); refresh(); };

  async function refresh() {
    if (!isCurrent() || !$('#bStatus')) return clearInterval(timer);
    let s;
    try { s = await api('/api/enrich/batch?limit=500' + (filter ? '&state=' + filter : '')); } catch (e) { return; }
    const c = s.counts, total = Object.values(c).reduce((a, b) => a + b, 0);
    const fin = (c.done || 0) + (c.failed || 0) + (c.cancelled || 0);
    $('#bStatus').innerHTML = !total ? '<span class="muted">还没有批量任务。</span>' : `
      <div class="muted">共 ${total}：已完成 ${c.done || 0}，进行中 ${c.running || 0}，排队 ${c.queued || 0}，失败 ${c.failed || 0}
        ${s.paused ? ` · <b class="err">已暂停</b>${s.reason ? '：' + esc(s.reason) : ''}` : s.active ? ' · <b>运行中…</b>' : ''}</div>
      <div style="background:var(--line);border-radius:6px;height:8px;margin:6px 0"><div style="width:${total ? fin / total * 100 : 0}%;height:8px;border-radius:6px;background:var(--accent,#3b82f6)"></div></div>
      <div class="flex" style="margin:8px 0">筛选：${[['', '全部'], ['done', '已完成'], ['failed', '失败'], ['queued', '排队']].map(([k, l]) => `<button data-f="${k}" class="small ${filter === k ? 'primary' : ''}">${l}</button>`).join('')}</div>
      <table><thead><tr><th>客户</th><th>国家</th><th>状态</th><th>完整度</th><th>找到联系方式</th><th>相关度</th><th>风险</th><th></th></tr></thead><tbody>
      ${s.items.map(it => `<tr><td><a href="#customer/${it.customer_id}">${esc(it.company || it.name)}</a></td><td>${esc(it.country)}</td>
        <td>${STATE[it.state] || it.state}${it.enrich_status ? ` · <span class="tag">${EN[it.enrich_status] || ''}</span>` : ''}${it.round > 1 ? ` · 第${it.round}轮` : ''}${it.error ? `<div class="err" style="font-size:12px">${esc(it.error)}</div>` : ''}</td>
        <td>${it.enrichment_id ? it.score : ''}</td><td>${it.enrichment_id ? it.found : ''}</td><td>${esc(it.relevance || '')}</td>
        <td>${it.risks ? `<b class="err">${it.risks}</b>` : ''}</td>
        <td>${it.enrichment_id && it.enrich_status === 'pending' ? `<button class="small primary" data-rv="${it.enrichment_id}" data-c="${it.customer_id}">查看并确认</button>`
          : it.enrichment_id ? `<button class="small" data-rv="${it.enrichment_id}" data-c="${it.customer_id}" data-ro="1">查看</button>` : ''}</td></tr>`).join('')}</tbody></table>`;
  }
  root.onclick = async e => {
    const t = e.target.closest('button');
    if (!t) return;
    if (t.dataset.f !== undefined) { filter = t.dataset.f; return refresh(); }
    if (t.dataset.rv) {
      try { await review(t.dataset.rv, t.dataset.c, !!t.dataset.ro); } catch (er) { toast(er.message); }
      refresh();
    }
  };
  await refresh();
  timer = setInterval(refresh, 3000);
}
