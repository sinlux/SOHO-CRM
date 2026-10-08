import {$, $$, esc, api, toast, modal, closeModal} from '../lib.js';

// 类目与规格管理：类目（增删改名）、子类（同时管 SKU 前缀）、规格字段（增删改、排序、适用哪些子类）。
// 做法参考行业里常见的"属性集"：每个类目有自己的一套属性；某个属性只对部分子类有意义时，勾选"适用子类"，
// 产品页选了子类就只显示相关的属性，不相关的收起来。
const TYPE_LABEL = {text: '文字', number: '数字', select: '下拉单选', multi: '多选', textarea: '长文字'};

export async function openCatalogAdmin(onChange, startCat) {
  let cats = (await api('/api/categories')).categories;
  let cur = startCat && cats.find(c => c.id === Number(startCat)) ? Number(startCat) : (cats[0] || {}).id;
  let editField = null;                                      // null = 新增；对象 = 正在编辑的字段
  let changed = false;
  const done = () => { closeModal(); if (changed && onChange) onChange(); };

  const draw = async () => {
    cats = (await api('/api/categories')).categories;
    if (!cats.find(c => c.id === cur)) cur = (cats[0] || {}).id;
    const cat = cats.find(c => c.id === cur);
    let subs = [], fields = [], pf = {category_prefix: ''};
    if (cat) [subs, fields, pf] = await Promise.all([api(`/api/categories/${cur}/subcategories`).then(r => r.subcategories),
      api(`/api/categories/${cur}/fields`).then(r => r.fields), api(`/api/categories/${cur}/prefixes`)]);
    const ef = editField && fields.find(f => f.id === editField.id) || null;
    if (editField && editField.id && !ef) editField = null;
    const body = modal(`<div class="flex between"><h2 style="margin:0">类目与规格管理</h2><button id="caClose">完成</button></div>
      <p class="muted" style="margin:6px 0 12px">在这里增删类目、子类和规格字段。改动立即生效；已有产品的数据不会被改坏（删除字段才会清掉该字段的值，删除前会提示影响多少产品）。</p>
      <div class="flex" style="align-items:flex-start;flex-wrap:nowrap;gap:18px">
        <div style="width:190px;flex:none"><div class="chips" style="flex-direction:column;align-items:stretch">${cats.map(c => `<span class="chip ${c.id === cur ? 'on' : ''}" data-cat="${c.id}">${esc(c.icon || '')} ${esc(c.name)}<small>${c.product_count}</small></span>`).join('')}</div>
          <button style="margin-top:10px;width:100%" id="caNewCat">＋ 新建类目</button></div>
        <div style="flex:1;min-width:0">${!cat ? '<span class="muted">还没有类目，点左侧「新建类目」</span>' : `
          <div class="card" style="box-shadow:none;border:1px solid var(--line);margin:0 0 12px"><h3 style="margin-top:0">类目</h3>
            <div class="flex"><input id="caName" value="${esc(cat.name)}" style="max-width:200px"><input id="caIcon" value="${esc(cat.icon || '')}" style="max-width:70px" title="图标（一个 emoji）">
              <button id="caSaveCat">保存名称</button><label class="flex">SKU 总前缀 <input id="caPf" value="${esc(pf.category_prefix || '')}" maxlength="4" style="max-width:80px"></label><button id="caSavePf">保存前缀</button>
              <button class="danger" id="caDelCat">删除类目</button></div>
            <p class="muted" style="margin-top:6px">${cat.product_count} 个产品。${cat.is_builtin ? '内置类目也可以改名或删除（删除后不会再自动长回来）。' : ''}有产品的类目不能删，先把产品移到别的类目。</p></div>
          <div class="card" style="box-shadow:none;border:1px solid var(--line);margin:0 0 12px"><h3 style="margin-top:0">子类 <span class="muted" style="font-weight:400">（决定 SKU 编号：类目前缀 + 子类前缀 + 6 位序号）</span></h3>
            ${subs.length ? `<table><tr><th>子类</th><th>SKU 前缀</th><th>产品数</th><th></th></tr>${subs.map(s => `<tr><td>${esc(s.name)}</td><td>${s.prefix ? `<b>${esc(s.prefix)}</b>` : '<span class="muted">未设置</span>'}</td><td>${s.product_count}</td>
              <td class="flex"><button class="small" data-sren="${esc(s.name)}" data-spf="${esc(s.prefix || '')}">改名/改前缀</button><button class="small danger" data-sdel="${esc(s.name)}" data-sn="${s.product_count}">删除</button></td></tr>`).join('')}</table>` : '<span class="muted">还没有子类</span>'}
            <div class="flex" style="margin-top:10px"><input id="caSubName" placeholder="新子类名称，如 吸顶灯" style="max-width:200px"><input id="caSubPf" placeholder="前缀 如 CL" maxlength="4" style="max-width:100px"><button id="caAddSub">添加子类</button></div></div>
          <div class="card" style="box-shadow:none;border:1px solid var(--line);margin:0"><h3 style="margin-top:0">规格字段 <span class="muted" style="font-weight:400">（产品页「规格」里显示的项目）</span></h3>
            ${fields.length ? `<table><tr><th></th><th>名称</th><th>类型</th><th>单位</th><th>选项</th><th>适用子类</th><th></th></tr>${fields.map((f, i) => `<tr>
              <td class="flex" style="gap:2px"><button class="small" data-fmv="up:${f.id}" ${i === 0 ? 'disabled' : ''}>↑</button><button class="small" data-fmv="down:${f.id}" ${i === fields.length - 1 ? 'disabled' : ''}>↓</button></td>
              <td><b>${esc(f.label)}</b></td><td>${TYPE_LABEL[f.type] || f.type}</td><td>${esc(f.unit || '')}</td>
              <td class="muted" style="max-width:200px">${f.key === 'subcategory' ? '（见上面的子类）' : esc((f.options_list || []).join('、'))}</td>
              <td>${f.key === 'subcategory' ? '—' : f.applies_list.length ? esc(f.applies_list.join('、')) : '<span class="muted">全部</span>'}</td>
              <td class="flex">${f.key === 'subcategory' ? '' : `<button class="small" data-fedit="${f.id}">编辑</button><button class="small danger" data-fdel="${f.id}">删除</button>`}</td></tr>`).join('')}</table>` : '<span class="muted">还没有字段</span>'}
            <div class="card" style="box-shadow:none;background:var(--leaf-soft);margin:12px 0 0"><b>${ef ? '编辑字段：' + esc(ef.label) : '新增字段'}</b>
              <div class="flex" style="margin-top:8px"><input id="cfLabel" placeholder="字段名称，如 克重" value="${esc(ef ? ef.label : '')}" style="max-width:170px">
                <select id="cfType" style="max-width:120px" ${ef ? 'disabled' : ''}>${Object.entries(TYPE_LABEL).map(([k, l]) => `<option value="${k}" ${ef && ef.type === k ? 'selected' : ''}>${l}</option>`).join('')}</select>
                <input id="cfUnit" placeholder="单位 如 kg" value="${esc(ef ? ef.unit || '' : '')}" style="max-width:90px">
                <input id="cfOpts" placeholder="选项（用顿号或逗号分隔，仅下拉/多选需要）" value="${esc(ef ? (ef.options_list || []).join('、') : '')}" style="min-width:240px;flex:1"></div>
              <div style="margin-top:8px"><span class="muted">适用子类（不勾 = 所有子类都显示这个字段）：</span><div class="check-group" id="cfApplies">${subs.map(s => `<label><input type="checkbox" value="${esc(s.name)}" ${ef && ef.applies_list.includes(s.name) ? 'checked' : ''}>${esc(s.name)}</label>`).join('') || '<span class="muted">先添加子类</span>'}</div></div>
              <div class="flex" style="margin-top:8px"><button class="primary" id="cfSave">${ef ? '保存修改' : '添加字段'}</button>${ef ? '<button id="cfCancel">取消编辑</button>' : ''}</div></div></div>`}
        </div></div>`, true);
    wire(body, cat, subs, fields);
  };

  const run = async fn => { try { await fn(); changed = true; await draw(); } catch (e) { toast(e.message); } };
  const opts = s => (s || '').split(/[、,，\n]/).map(x => x.trim()).filter(Boolean);

  const wire = (body, cat) => {
    $('#caClose').onclick = done;
    body.onclick = async e => {
      const t = e.target.closest('button,[data-cat]');
      if (!t) return;
      const d = t.dataset;
      if (d.cat) { cur = Number(d.cat); editField = null; return draw(); }
      if (t.id === 'caNewCat') {
        const name = prompt('新类目名称：');
        if (!name) return;
        return run(async () => { cur = (await api('/api/categories', 'POST', {name})).id; editField = null; });
      }
      if (!cat) return;
      if (t.id === 'caSaveCat') return run(() => api('/api/categories/' + cur, 'PUT', {name: $('#caName').value, icon: $('#caIcon').value}));
      if (t.id === 'caSavePf') return run(() => api(`/api/categories/${cur}/prefix`, 'PUT', {prefix: $('#caPf').value}));
      if (t.id === 'caDelCat') {
        if (!confirm(`确定删除类目「${cat.name}」？\n其下的子类和规格字段设置也会一并删除。`)) return;
        return run(async () => { await api('/api/categories/' + cur, 'DELETE', {}); cur = null; });
      }
      if (t.id === 'caAddSub') return run(async () => { await api(`/api/categories/${cur}/subcategories`, 'POST', {name: $('#caSubName').value, prefix: $('#caSubPf').value}); });
      if (d.sren !== undefined) {
        const nn = prompt('子类新名称（不改名就保持原样）：', d.sren);
        if (nn === null) return;
        const np = prompt('SKU 前缀（1–4 位英文字母，可留空）：', d.spf);
        if (np === null) return;
        return run(() => api(`/api/categories/${cur}/subcategories`, 'PUT', {old: d.sren, new: nn, prefix: np}));
      }
      if (d.sdel !== undefined) {
        const n = Number(d.sn);
        if (!confirm(`删除子类「${d.sdel}」？${n ? `\n有 ${n} 个产品的子类是它，产品数据会保留，但这个子类不再出现在下拉里，需要你重新选择。` : ''}`)) return;
        return run(() => api(`/api/categories/${cur}/subcategories/delete`, 'POST', {name: d.sdel}));
      }
      if (d.fmv) { const [dir, id] = d.fmv.split(':'); return run(() => api(`/api/fields/${id}/move`, 'POST', {direction: dir})); }
      if (d.fedit) { editField = {id: Number(d.fedit)}; return draw(); }
      if (t.id === 'cfCancel') { editField = null; return draw(); }
      if (d.fdel) {
        const n = (await api(`/api/fields/${d.fdel}/usage`)).products_with_value;
        if (!confirm(`删除这个字段？${n ? `\n有 ${n} 个产品填过它，这些值会一起被清掉。` : ''}`)) return;
        return run(() => api('/api/fields/' + d.fdel, 'DELETE', {}));
      }
      if (t.id === 'cfSave') {
        const applies = $$('#cfApplies input:checked').map(i => i.value);
        const payload = {label: $('#cfLabel').value, unit: $('#cfUnit').value, applies_to: applies};
        const o = opts($('#cfOpts').value);
        return run(async () => {
          if (editField && editField.id) { if (o.length || $('#cfOpts').value === '') Object.assign(payload, {options: o}); await api('/api/fields/' + editField.id, 'PUT', stripOpts(payload, o)); editField = null; }
          else await api(`/api/categories/${cur}/fields`, 'POST', {...payload, type: $('#cfType').value, options: o});
        });
      }
    };
  };
  // 编辑非下拉字段时不要带 options（后端会拒绝）
  const stripOpts = (payload, o) => { if (!o.length) delete payload.options; return payload; };
  await draw();
}
