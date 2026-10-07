# -*- coding: utf-8 -*-
"""Excel 批量产品导入（5 步向导的后端）：上传 → 选工作表/表头行 → 选类目 → 列映射 → 逐行预览确认 → 写入。
导入会话只放在内存（图片和上传文件放 data/import_tmp/<会话>），程序重启即丢——导入本来就该一次走完；
启动时会清掉上次遗留的临时文件。写入前不动任何产品数据：预览阶段只读。"""
import os
import re
import shutil
import threading
import time
import uuid

from ..core.util import ApiError
from . import xlsx_images as xi

MAX_UPLOAD = 500 * 1024 * 1024
SESSION_TTL = 6 * 3600
PREVIEW_PAGE = 200
FIXED_TARGETS = [('__sku', 'SKU / 款号'), ('__name', '产品名称'), ('__cost', '成本价'), ('__currency', '成本币种（空 = 用下方统一币种）'),
                 ('__moq', 'MOQ'), ('__supplier', '供应商'), ('__spec', '并入规格描述（多列自动拼接）'), ('__ignore', '（不导入）')]
_CUR_ALIASES = {'CNY': 'CNY', 'RMB': 'CNY', '¥': 'CNY', '￥': 'CNY', '人民币': 'CNY', '元': 'CNY', 'USD': 'USD', '$': 'USD', '美元': 'USD', '美金': 'USD'}

# 表头关键词 → 目标（自动猜列用，按顺序匹配，先到先得）
_GUESS = [('__ignore', ('序号', '图片', '图', 'picture', 'image', 'photo', 'no.', '#')),
          ('__sku', ('款号', '货号', '编号', 'sku', 'item no', 'code', 'model', '型号')),
          ('__currency', ('币种', 'currency')),
          ('__cost', ('货盘价', '成本', '单价', '价格', 'cost', 'price')),
          ('__moq', ('moq', '起订')),
          ('__supplier', ('供应商', 'supplier', '工厂', 'factory')),
          ('__name', ('产品名称', '品名', '名称', 'product name', 'name'))]


def parse_money(v):
    """'¥12.5' / '12.5元' / 12.5 → 12.5；空 → None；无法识别 → ValueError。"""
    if v is None or str(v).strip() == '':
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        n = float(v)
    else:
        s = re.sub(r'[^\d.\-]', '', str(v).replace(',', ''))
        if s in ('', '-', '.'):
            raise ValueError(str(v))
        n = float(s)
    if n < 0:
        raise ValueError(str(v))
    return n


class ProductImporter:
    def __init__(self, db, products, catalog, media, tmp_dir):
        self.db, self.products, self.catalog, self.media = db, products, catalog, media
        self.tmp = tmp_dir
        shutil.rmtree(self.tmp, ignore_errors=True)          # 会话在内存里，重启后这些文件没有用了
        os.makedirs(self.tmp, exist_ok=True)
        self.sessions = {}
        self.lock = threading.Lock()

    # ---------- 会话 ----------
    def _gc(self):
        now = time.time()
        for sid in [k for k, v in self.sessions.items() if now - v['touched'] > SESSION_TTL]:
            self.discard(sid)

    def _get(self, sid):
        s = self.sessions.get(sid)
        if not s:
            raise ApiError('导入会话已过期或程序重启过，请重新上传文件', 404)
        s['touched'] = time.time()
        return s

    def new_path(self, filename):
        """给上传的文件分配落盘路径并建会话（上传完成后调用 start）。"""
        with self.lock:
            self._gc()
            sid = uuid.uuid4().hex[:12]
            d = os.path.join(self.tmp, sid)
            os.makedirs(d, exist_ok=True)
            ext = os.path.splitext(filename)[1].lower()
            if ext not in ('.xlsx', '.xlsm'):
                shutil.rmtree(d, ignore_errors=True)
                raise ApiError('只支持 .xlsx 文件（.xls 请先在 Excel 里另存为 .xlsx；PI 的 .xls 请用「PI 导入」）')
            self.sessions[sid] = {'dir': d, 'path': os.path.join(d, 'upload.xlsx'), 'name': filename, 'touched': time.time(), 'stage': 'uploading'}
            return sid, self.sessions[sid]['path']

    def start(self, sid):
        s = self._get(sid)
        try:
            s['sheets'] = xi.list_sheets(s['path'])
        except Exception:
            self.discard(sid)
            raise ApiError('无法读取这个 Excel 文件（可能已损坏或不是 xlsx）')
        s['stage'] = 'uploaded'
        return {'session_id': sid, 'filename': s['name'], 'sheets': s['sheets'], 'size_mb': round(os.path.getsize(s['path']) / 1048576, 1)}

    def discard(self, sid):
        s = self.sessions.pop(sid, None)
        shutil.rmtree(os.path.join(self.tmp, sid), ignore_errors=True)
        return bool(s)

    # ---------- 步骤 2：工作表 + 表头行 ----------
    def choose_sheet(self, sid, sheet, header_row=1):
        s = self._get(sid)
        if sheet not in s['sheets']:
            raise ApiError('工作表不存在')
        try:
            header_row = int(header_row)
        except (TypeError, ValueError):
            raise ApiError('表头行必须是数字')
        if not 1 <= header_row <= 200:
            raise ApiError('表头行应在 1–200 之间')
        info = xi.inspect_sheet(s['path'], sheet, header_row)
        if not info['headers']:
            raise ApiError('第 %d 行没有内容，请选择正确的表头行' % header_row)
        s.update(sheet=sheet, header_row=header_row, headers=info['headers'], stage='sheet')
        s.pop('rows', None)
        return {'headers': info['headers'], 'preview_raw': info['preview_raw'], 'total_rows': info['total_rows'],
                'data_rows': max(0, info['total_rows'] - header_row)}

    # ---------- 步骤 3/4：类目 + 列映射 ----------
    def choose_category(self, sid, category_id):
        s = self._get(sid)
        if 'headers' not in s:
            raise ApiError('请先选择工作表')
        self.catalog.require_category(int(category_id))
        fields = self.catalog.fields(int(category_id))
        s['category_id'] = int(category_id)
        s['fields'] = {f['id']: f for f in fields}
        by_label = {}
        for f in fields:
            by_label[f['label'].strip().lower()] = f['id']
            by_label[f['key'].strip().lower()] = f['id']
        mapping = {}
        for h in s['headers']:
            hl = h.strip().lower()
            target = None
            if hl in by_label:
                target = 'f:%d' % by_label[hl]
            else:
                for t, kws in _GUESS:
                    if any((hl == k) if k in ('图', '#', 'no.') else (k in hl) for k in kws):
                        target = t
                        break
            mapping[h] = target or '__spec'
        s['stage'] = 'category'
        return {'fields': [{'id': f['id'], 'label': f['label'], 'type': f['type'], 'unit': f['unit']} for f in fields],
                'fixed_targets': [{'key': k, 'label': l} for k, l in FIXED_TARGETS], 'suggested_mapping': mapping}

    # ---------- 步骤 5：预览（只读，不写库） ----------
    def preview(self, sid, mapping, currency='CNY', profit_rate=0.25, extract_images=True):
        s = self._get(sid)
        if 'category_id' not in s:
            raise ApiError('请先选择类目')
        currency = (currency or 'CNY').upper()
        if currency not in ('CNY', 'USD'):
            raise ApiError('统一币种只能是 CNY 或 USD')
        try:
            profit_rate = float(profit_rate)
        except (TypeError, ValueError):
            raise ApiError('利润率必须是数字')
        if not 0 <= profit_rate <= 10:
            raise ApiError('利润率应在 0–1000%（0–10）之间')
        if not isinstance(mapping, dict):
            raise ApiError('列映射格式不对')
        headers = s['headers']
        idx = {h: i for i, h in enumerate(headers)}
        valid_targets = {k for k, _ in FIXED_TARGETS} | {'f:%d' % fid for fid in s['fields']}
        cols = {}
        for h, t in mapping.items():
            if h not in idx:
                raise ApiError('找不到列「%s」' % h)
            if t not in valid_targets:
                raise ApiError('列「%s」的目标无效' % h)
            cols[h] = t
        for need, label in (('__sku', 'SKU / 款号'),):
            if need not in cols.values():
                raise ApiError('必须有一列映射到「%s」' % label)
        unmapped = [h for h in headers if h not in cols]            # 没出现在映射里的列：并入规格描述
        spec_cols = [h for h, t in cols.items() if t == '__spec'] + unmapped
        images = {}
        if extract_images:
            imgdir = os.path.join(s['dir'], 'img')
            shutil.rmtree(imgdir, ignore_errors=True)
            try:
                images = xi.extract_row_images(s['path'], s['sheet'], imgdir)
            except Exception:
                images = {}
        existing = {r['sku'].lower(): r['id'] for r in self.db.query('SELECT id, sku FROM products')}
        rows, seen = [], {}
        for rn, vals in xi.iter_rows(s['path'], s['sheet'], s['header_row']):
            get = lambda h: vals[idx[h]] if idx[h] < len(vals) else ''
            rec = {'row': rn, 'sku': '', 'name': '', 'cost': None, 'currency': currency, 'moq': None, 'supplier': '',
                   'field_values': {}, 'warnings': [], 'image': images.get(rn), 'spec_text': ''}
            for h, t in cols.items():
                v = get(h)
                sv = '' if v is None else str(v).strip()
                if t == '__sku':
                    rec['sku'] = sv
                elif t == '__name':
                    rec['name'] = sv
                elif t == '__cost':
                    try:
                        rec['cost'] = parse_money(v)
                    except ValueError:
                        rec['warnings'].append('成本「%s」不是数字，已忽略' % sv)
                elif t == '__currency' and sv:
                    cur = _CUR_ALIASES.get(sv.upper()) or _CUR_ALIASES.get(sv)
                    if cur:
                        rec['currency'] = cur
                    else:
                        rec['warnings'].append('币种「%s」只支持 CNY/USD，已用统一币种' % sv)
                elif t == '__moq' and sv:
                    try:
                        rec['moq'] = int(parse_money(v) or 0) or None
                    except ValueError:
                        rec['warnings'].append('MOQ「%s」不是数字，已忽略' % sv)
                elif t == '__supplier':
                    rec['supplier'] = sv
                elif t.startswith('f:') and sv:
                    rec['field_values'][t[2:]] = sv
            parts = []
            for h in spec_cols:
                sv = '' if get(h) is None else str(get(h)).strip()
                if sv:
                    parts.append('%s: %s' % (h, sv))
            rec['spec_text'] = '\n'.join(parts)
            rec['name'] = rec['name'] or rec['sku']
            if not rec['sku']:
                rec['warnings'].append('SKU 为空，这一行无法导入')
            else:
                key = rec['sku'].lower()
                rec['exists'] = key in existing
                if key in seen:
                    rec['warnings'].append('SKU 与第 %d 行重复，后者会覆盖前者' % seen[key])
                seen[key] = rn
            rec['blocked'] = not rec['sku']
            rows.append(rec)
        s.update(rows=rows, currency=currency, profit_rate=profit_rate, stage='preview')
        return {'count': len(rows), 'with_image': sum(1 for r in rows if r['image']), 'blocked': sum(1 for r in rows if r['blocked']),
                'will_update': sum(1 for r in rows if r.get('exists')), 'warned': sum(1 for r in rows if r['warnings']),
                'rows': rows[:PREVIEW_PAGE]}

    def rows(self, sid, offset=0, limit=PREVIEW_PAGE):
        s = self._get(sid)
        if 'rows' not in s:
            raise ApiError('请先生成预览')
        offset = max(0, int(offset))
        return {'rows': s['rows'][offset: offset + max(1, min(int(limit), 500))], 'count': len(s['rows'])}

    def image_path(self, sid, name):
        s = self._get(sid)
        p = os.path.join(s['dir'], 'img', os.path.basename(name))
        if not os.path.exists(p):
            raise ApiError('图片不存在', 404)
        return p

    # ---------- 写入 ----------
    def apply(self, sid, skip_rows=()):
        s = self._get(sid)
        if s.get('stage') != 'preview':
            raise ApiError('请先生成并确认预览')
        skip = {int(x) for x in (skip_rows or [])}
        created = updated = 0
        failed, skipped = [], 0
        cat = s['category_id']
        for rec in s['rows']:
            if rec['row'] in skip:
                skipped += 1
                continue
            if rec['blocked']:
                failed.append({'row': rec['row'], 'sku': rec['sku'], 'reason': 'SKU 为空'})
                continue
            try:
                kind = self._apply_row(s, cat, rec)
                created += kind == 'new'
                updated += kind == 'updated'
            except ApiError as e:
                failed.append({'row': rec['row'], 'sku': rec['sku'], 'reason': str(e)})
            except Exception as e:                       # 单行出错不拖垮整批
                failed.append({'row': rec['row'], 'sku': rec['sku'], 'reason': '内部错误：%s' % e})
        self.discard(sid)
        return {'created': created, 'updated': updated, 'skipped': skipped, 'failed': failed}

    def _stage_image(self, s, rec):
        if not rec.get('image'):
            return None
        p = os.path.join(s['dir'], 'img', rec['image'])
        if not os.path.exists(p):
            return None
        with open(p, 'rb') as f:
            return self.media.stage_image(f.read())['id']

    def _apply_row(self, s, cat, rec):
        row = self.db.one('SELECT id FROM products WHERE sku=? COLLATE NOCASE', (rec['sku'],))
        src = '导入 ' + s['name']
        d = {'sku': rec['sku'], 'name': rec['name'], 'price_source': src}
        if rec['cost'] is not None:
            d['cost'] = rec['cost']
            d['cost_currency'] = rec['currency']
        if rec['moq']:
            d['moq'] = rec['moq']
        if rec['supplier']:
            d['supplier'] = rec['supplier']
        if rec['spec_text']:
            d['spec_text'] = rec['spec_text']
        if not row:
            d.update(category_id=cat, profit_rate=s['profit_rate'], field_values=rec['field_values'])
            img = self._stage_image(s, rec)
            if img:
                d['images'] = [img]
            self.products.create(d)
            return 'new'
        pid = row['id']
        cur = self.products.get(pid)
        d['sku'] = cur['sku']                                          # 大小写按库里的
        d['category_id'] = cur['category_id']                          # 已有产品不换类目
        if rec['field_values'] and cur['category_id'] == cat:
            d['field_values'] = {**cur['field_values'], **rec['field_values']}     # 只覆盖 Excel 里有值的字段
        if not cur['images']:
            img = self._stage_image(s, rec)
            if img:
                d['images'] = [img]
        self.products.update(pid, d)
        return 'updated'
