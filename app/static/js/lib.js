// 公共工具：DOM、转义、请求、提示、弹窗、徽标。所有页面共用。
export const $ = (s, root = document) => root.querySelector(s);
export const $$ = (s, root = document) => [...root.querySelectorAll(s)];

const ESC = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
export const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ESC[c]);

export class ApiError extends Error {
  constructor(data, status) { super(data.error || ('请求失败 ' + status)); this.data = data; this.status = status; }
}

export async function api(url, method = 'GET', body) {
  const opt = {method, headers: {}};
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch(url, opt);
  let data;
  try { data = await r.json(); } catch (e) { data = {error: '服务器返回了无法解析的内容'}; }
  if (!r.ok) throw new ApiError(data, r.status);
  return data;
}

export async function upload(url, file) {
  const r = await fetch(url, {method: 'POST', headers: {'X-Filename': encodeURIComponent(file.name)}, body: file});
  let data;
  try { data = await r.json(); } catch (e) { data = {error: '服务器返回了无法解析的内容'}; }
  if (!r.ok) throw new ApiError(data, r.status);
  return data;
}

// 大文件上传：带进度（fetch 拿不到上传进度，这里用 XHR）
export function uploadProgress(url, file, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('POST', url);
    x.setRequestHeader('X-Filename', encodeURIComponent(file.name));
    x.upload.onprogress = e => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    x.onload = () => {
      let data;
      try { data = JSON.parse(x.responseText); } catch (e) { data = {error: '服务器返回了无法解析的内容'}; }
      x.status >= 200 && x.status < 300 ? resolve(data) : reject(new ApiError(data, x.status));
    };
    x.onerror = () => reject(new ApiError({error: '上传失败：连接中断'}, 0));
    x.send(file);
  });
}

let toastTimer;
export function toast(msg) {
  const t = $('#toast');
  t.textContent = msg; t.style.display = 'block';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.style.display = 'none', 2600);
}

export function modal(html) {
  const body = $('#modalBody');
  body.innerHTML = html;
  $('#modal').style.display = 'flex';
  return body;
}
export function closeModal() { $('#modal').style.display = 'none'; }
$('#modal').addEventListener('click', e => { if (e.target.id === 'modal') closeModal(); });

export const LV_DESC = {6: '长期合作', 5: '断续下单', 4: '下过单', 3: '潜在客户', 2: '业务相关', 1: '不确定'};
export const STAGES = ['潜在', '已联系', '已报价', '已寄样', '成交', '复购', '沉睡'];
const STAGE_CLASS = {成交: 'good', 复购: 'good', 沉睡: 'bad', 已报价: 'warn', 已寄样: 'warn'};

export function lvBadge(lv) {
  lv = lv || 0;
  return `<span class="lv lv${lv}" title="${esc(LV_DESC[lv] || '未分级')}">${lv ? 'LV' + lv : '—'}</span>`;
}
const AVATAR_COLORS = ['#6FA287', '#86BCD6', '#E0A458', '#CB705D', '#9C8CC2', '#7FB6A8', '#D98FA0', '#8DA66B'];
// 苹果通讯录风格的圆形首字母头像，颜色由名字稳定决定
export function avatar(name, size) {
  const s = String(name || '?').trim();
  let h = 0;
  for (const ch of s) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  const first = [...s][0] || '?';
  const st = `background:${AVATAR_COLORS[h % AVATAR_COLORS.length]}${size ? `;width:${size}px;height:${size}px;font-size:${Math.round(size * .42)}px` : ''}`;
  return `<span class="avatar" style="${st}">${esc(first.toUpperCase())}</span>`;
}
export const stageBadge = s => s ? `<span class="tag ${STAGE_CLASS[s] || ''}">${esc(s)}</span>` : '';

// 把空白分隔的邮箱/网址渲染成链接。只放行 http/https/mailto，其它协议(javascript: 等)一律当纯文本。
export function linkify(s) {
  return String(s || '').split(/\s+/).filter(Boolean).map(v => {
    if (/^[\w.+-]+@[\w-]+(\.[\w-]+)+$/.test(v)) return `<a class="ext" href="mailto:${esc(v)}">${esc(v)}</a>`;
    if (/^[a-z][a-z0-9+.-]*:/i.test(v) && !/^https?:\/\//i.test(v)) return esc(v);
    if (/^(https?:\/\/|www\.)|^([\w-]+\.)+[a-z]{2,}(\/|$)/i.test(v)) {
      const href = /^https?:\/\//i.test(v) ? v : 'http://' + v;
      return `<a class="ext" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(v)}</a>`;
    }
    return esc(v);
  }).join(' &nbsp;');
}

export function nav(view, arg) { location.hash = '#' + view + (arg !== undefined ? '/' + arg : ''); }

export async function updateBadge() {
  try {
    const s = await api('/api/stats');
    const b = $('#remBadge');
    b.textContent = s.due_reminders;
    b.style.display = s.due_reminders > 0 ? 'inline-block' : 'none';
  } catch (e) { /* 徽标失败不影响页面 */ }
}

export function field(label, id, val, attrs = '') {
  return `<div class="field"><label for="${id}">${esc(label)}</label><input id="${id}" value="${esc(val || '')}" ${attrs}></div>`;
}

// ---- 金额：用整数运算精确到分（四舍五入，和后端 Decimal ROUND_HALF_UP 一致），避免 JS 浮点出现差一分 ----
function dec(v) {
  const s = String(v ?? '').trim();
  const m = /^(-?)(\d*)\.?(\d*)$/.exec(s);
  if (!m || (m[2] === '' && m[3] === '')) return {n: 0n, scale: 0};
  return {n: BigInt(m[1] + (m[2] || '0') + m[3]), scale: m[3].length};
}
export function mulCents(qty, price) {
  const a = dec(qty), b = dec(price);
  const n = a.n * b.n, scale = a.scale + b.scale, neg = n < 0n, abs = neg ? -n : n;
  let cents;
  if (scale <= 2) cents = abs * 10n ** BigInt(2 - scale);
  else { const d = 10n ** BigInt(scale - 2); cents = abs / d + (abs % d * 2n >= d ? 1n : 0n); }
  return Number(neg ? -cents : cents);
}
export const fmtCents = c => (c / 100).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
