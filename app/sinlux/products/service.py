# -*- coding: utf-8 -*-
"""产品库：CRUD、结构化规格值、相册、文档、复制、合并同类项、供应商比价。"""
import os

from ..core import images as rawimg
from ..core.util import ApiError, like, now, valid_date
from .pricing import CURRENCIES, LEGACY_CURRENCIES, UNCONVERTED, suggest_usd

STATUSES = {'active': '在售', 'draft': '草稿', 'discontinued': '停产'}
SORTS = {'updated': 'p.updated_at DESC, p.id DESC', 'created': 'p.id DESC', 'sku': 'p.sku COLLATE NOCASE ASC',
         'name': 'p.name COLLATE NOCASE ASC', 'cost': 'p.cost IS NULL, p.cost ASC, p.id DESC'}
TEXT_LIMITS = {'brand': 100, 'series': 100, 'hs_code': 20, 'origin': 60, 'unit': 16}


def _num(v, name, lo=None, hi=None, integer=False):
    if v in (None, ''):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ApiError('%s必须是数字' % name)
    if f != f or f in (float('inf'), float('-inf')):
        raise ApiError('%s必须是有效数字' % name)
    if integer:
        if f != int(f):
            raise ApiError('%s必须是整数' % name)
        f = int(f)
    if lo is not None and f < lo:
        raise ApiError('%s不能小于 %s' % (name, lo))
    if hi is not None and f > hi:
        raise ApiError('%s不能大于 %s' % (name, hi))
    return f


def _txt(v):
    return (str(v).strip() if v is not None else '')


class ProductService:
    def __init__(self, db, rates, history, catalog, media):
        self.db = db
        self.rates = rates
        self.history = history
        self.catalog = catalog
        self.media = media

    # ---------- 读 ----------
    _COLS = """p.id, p.sku, p.name, p.category_id, p.image_path, p.cost, p.cost_currency, p.profit_rate,
        p.suggested_price, p.moq, p.lead_time, p.supplier, p.remark, COALESCE(p.spec_text,'') AS spec_text,
        COALESCE(p.status,'active') AS status, COALESCE(p.unit,'pcs') AS unit, COALESCE(p.brand,'') AS brand,
        COALESCE(p.series,'') AS series, COALESCE(p.hs_code,'') AS hs_code, COALESCE(p.origin,'') AS origin,
        p.pcs_per_carton, p.carton_l, p.carton_w, p.carton_h, p.gross_weight, p.net_weight,
        p.created_at, p.updated_at, c.name AS category_name, c.icon AS category_icon,
        (SELECT file FROM product_images i WHERE i.product_id=p.id ORDER BY i.sort_order, i.id LIMIT 1) AS img_file,
        (SELECT thumb FROM product_images i WHERE i.product_id=p.id ORDER BY i.sort_order, i.id LIMIT 1) AS img_thumb,
        (SELECT COUNT(*) FROM product_images i WHERE i.product_id=p.id) AS image_count"""

    def list(self, search='', category_id=None, status='', sort='updated', limit=100, offset=0):
        where, params = ['1=1'], []
        if category_id:
            where.append('p.category_id=?'); params.append(int(category_id))
        if status:
            if status not in STATUSES:
                raise ApiError('状态无效')
            where.append("COALESCE(p.status,'active')=?"); params.append(status)
        if search:
            k = like(search)
            cols = ('p.sku', 'p.name', 'p.supplier', 'p.spec_text', 'p.remark', 'p.brand', 'p.series')
            where.append('(' + ' OR '.join("%s LIKE ? ESCAPE '\\'" % c for c in cols) + ')')
            params += [k] * len(cols)
        if sort not in SORTS:
            raise ApiError('排序方式无效')
        w = ' AND '.join(where)
        total = self.db.scalar('SELECT COUNT(*) FROM products p WHERE ' + w, params)
        limit = max(1, min(int(limit), 1000))
        rows = self.db.query('SELECT %s FROM products p LEFT JOIN categories c ON c.id=p.category_id WHERE %s '
                             'ORDER BY %s LIMIT ? OFFSET ?' % (self._COLS, w, SORTS[sort]),
                             params + [limit, max(0, int(offset))])
        for r in rows:
            self._decorate(r)
        return rows, total

    def require(self, pid):
        r = self.db.one('SELECT %s FROM products p LEFT JOIN categories c ON c.id=p.category_id WHERE p.id=?'
                        % self._COLS, (pid,))
        if not r:
            raise ApiError('产品不存在', 404)
        return self._decorate(r)

    def get(self, pid):
        p = self.require(pid)
        p['field_values'] = {str(r['field_id']): r['value'] for r in self.db.query(
            'SELECT field_id, value FROM product_field_values WHERE product_id=?', (pid,))}
        p['fields'] = self.catalog.fields(p['category_id']) if p['category_id'] else []
        p['images'] = self.media.list_images(pid)
        p['files'] = self.media.list_files(pid)
        return p

    def _decorate(self, p):
        if p.get('img_file'):
            p['image_url'] = '/uploads/' + p['img_file']
            p['thumb_url'] = '/uploads/' + (p['img_thumb'] or p['img_file'])
        elif p.get('image_path'):                     # 极旧数据：只有 image_path、没有相册行
            p['image_url'] = p['thumb_url'] = '/' + p['image_path']
        else:
            p['image_url'] = p['thumb_url'] = ''
        p['status_label'] = STATUSES.get(p['status'], p['status'])
        p['suggested_unconverted'] = p.get('cost_currency') in UNCONVERTED and p.get('suggested_price') is not None
        l, w, h = p.get('carton_l'), p.get('carton_w'), p.get('carton_h')
        p['carton_cbm'] = round(l * w * h / 1e6, 4) if l and w and h else None
        p['unit_cbm'] = round(p['carton_cbm'] / p['pcs_per_carton'], 4) if p['carton_cbm'] and p.get('pcs_per_carton') else None
        return p

    def impact(self, pid):
        self.require(pid)
        n = lambda sql: self.db.scalar(sql, (pid,))
        return {'price_records': n('SELECT COUNT(*) FROM price_history WHERE product_id=?'),
                'supplier_quotes': n('SELECT COUNT(*) FROM supplier_quotes WHERE product_id=?'),
                'quote_items': n('SELECT COUNT(*) FROM quote_items WHERE product_id=?'),
                'images': n('SELECT COUNT(*) FROM product_images WHERE product_id=?'),
                'files': n('SELECT COUNT(*) FROM product_files WHERE product_id=?')}

    def usage(self, pid):
        """这个产品出现在哪些报价单里（只读，报价单本身在第三批）。"""
        self.require(pid)
        return self.db.query("""SELECT q.id AS quote_id, q.quote_no, q.status, q.currency, q.created_at,
              c.id AS customer_id, c.company, i.quantity, i.unit_price
            FROM quote_items i JOIN quotes q ON q.id=i.quote_id LEFT JOIN customers c ON c.id=q.customer_id
            WHERE i.product_id=? ORDER BY q.created_at DESC, q.id DESC LIMIT 200""", (pid,))

    # ---------- 校验 ----------
    def _clean(self, d, cur=None):
        g = lambda k, default=None: d[k] if k in d else (cur[k] if cur else default)
        out = {}
        out['sku'] = _txt(g('sku', ''))
        if not out['sku']:
            raise ApiError('SKU 不能为空')
        if len(out['sku']) > 64:
            raise ApiError('SKU 最长 64 个字符')
        out['name'] = _txt(g('name', ''))
        if not out['name']:
            raise ApiError('产品名称不能为空')
        cid = g('category_id')
        if not cid:
            raise ApiError('必须选择类目')
        self.catalog.require_category(int(cid))
        out['category_id'] = int(cid)
        out['cost'] = _num(g('cost'), '成本价', lo=0)
        cur_c = (g('cost_currency') or ('CNY' if not cur else 'USD')).upper()
        if cur_c not in CURRENCIES and not (cur and cur['cost_currency'] == cur_c and cur_c in LEGACY_CURRENCIES):
            raise ApiError('币种无效，可选：' + '/'.join(CURRENCIES))
        out['cost_currency'] = cur_c
        pr = _num(g('profit_rate'), '利润率', lo=0, hi=10)
        out['profit_rate'] = 0.25 if pr is None else pr
        out['moq'] = _num(g('moq'), 'MOQ', lo=0, integer=True)
        out['lead_time'] = _num(g('lead_time'), '交期', lo=0, integer=True)
        out['supplier'] = _txt(g('supplier')) or None
        out['remark'] = _txt(g('remark')) or None
        out['spec_text'] = _txt(g('spec_text', ''))
        st = _txt(g('status', 'active')) or 'active'
        if st not in STATUSES:
            raise ApiError('状态无效，可选：' + '/'.join(STATUSES))
        out['status'] = st
        for k, lim in TEXT_LIMITS.items():
            v = _txt(g(k, 'pcs' if k == 'unit' else ''))
            if len(v) > lim:
                raise ApiError('%s最长 %d 个字符' % ({'brand': '品牌', 'series': '系列', 'hs_code': 'HS 编码', 'origin': '原产地', 'unit': '单位'}[k], lim))
            out[k] = v or ('pcs' if k == 'unit' else '')
        if out['hs_code'] and not all(ch.isdigit() or ch in '. -' for ch in out['hs_code']):
            raise ApiError('HS 编码只能包含数字、点、空格和短横线')
        out['pcs_per_carton'] = _num(g('pcs_per_carton'), '每箱数量', lo=1, integer=True)
        for k, label in (('carton_l', '外箱长'), ('carton_w', '外箱宽'), ('carton_h', '外箱高')):
            out[k] = _num(g(k), label, lo=0, hi=1000)
        out['gross_weight'] = _num(g('gross_weight'), '毛重', lo=0)
        out['net_weight'] = _num(g('net_weight'), '净重', lo=0)
        if out['gross_weight'] is not None and out['net_weight'] is not None and out['net_weight'] > out['gross_weight']:
            raise ApiError('净重不能大于毛重')
        return out

    def _check_fields(self, category_id, values):
        if values is None:
            return {}
        if not isinstance(values, dict):
            raise ApiError('field_values 必须是对象')
        valid = {f['id'] for f in self.catalog.fields(category_id)}
        out = {}
        for k, v in values.items():
            try:
                fid = int(k)
            except (TypeError, ValueError):
                raise ApiError('字段编号无效: %s' % k)
            if fid not in valid:
                raise ApiError('字段 %s 不属于所选类目' % k)
            if v not in (None, ''):
                out[fid] = str(v)
        return out

    def _save_values(self, pid, vals):
        self.db.execute('DELETE FROM product_field_values WHERE product_id=?', (pid,))
        for fid, v in vals.items():
            self.db.execute('INSERT INTO product_field_values(product_id,field_id,value) VALUES(?,?,?)', (pid, fid, v))

    def _suggest(self, f):
        return suggest_usd(f['cost'], f['cost_currency'], f['profit_rate'], self.rates.rate())

    _WRITE_COLS = ('sku', 'name', 'category_id', 'cost', 'cost_currency', 'profit_rate', 'moq', 'lead_time', 'supplier',
                   'remark', 'spec_text', 'status', 'unit', 'brand', 'series', 'hs_code', 'origin', 'pcs_per_carton',
                   'carton_l', 'carton_w', 'carton_h', 'gross_weight', 'net_weight')

    def _image_ids(self, d):
        """请求里的图片意图 → 图片编号列表；None 表示不改相册。兼容旧版 image_data / remove_image。"""
        if 'images' in d and d['images'] is not None:
            return d['images']
        if d.get('image_data'):
            return [self.media.stage_image(rawimg.decode_data_url(d['image_data'])[1])['id']]
        if d.get('remove_image'):
            return []
        return None

    # ---------- 写 ----------
    def create(self, d):
        f = self._clean(d)
        if self.db.one('SELECT 1 FROM products WHERE sku=?', (f['sku'],)):
            raise ApiError('SKU「%s」已存在' % f['sku'], 409)
        vals = self._check_fields(f['category_id'], d.get('field_values'))
        if d.get('price_date') and not valid_date(d['price_date']):
            raise ApiError('价格日期格式应为 YYYY-MM-DD')
        ids = self._image_ids(d)
        with self.db.tx():
            ts = now()
            cols = list(self._WRITE_COLS) + ['suggested_price', 'created_at', 'updated_at']
            args = [f[c] for c in self._WRITE_COLS] + [self._suggest(f), ts, ts]
            pid = self.db.execute('INSERT INTO products(%s) VALUES(%s)' % (','.join(cols), ','.join('?' * len(cols))),
                                  args).lastrowid
            self._save_values(pid, vals)
            if ids:
                self.media.apply_images(pid, ids)
            if f['cost'] is not None:
                self.history.record(pid, 'cost', f['cost'], f['cost_currency'], effective_date=d.get('price_date'),
                                    source=_txt(d.get('price_source')) or '手动建档')
        return pid

    def update(self, pid, d):
        cur = self.require(pid)
        f = self._clean(d, cur)
        if self.db.one('SELECT 1 FROM products WHERE sku=? AND id<>?', (f['sku'], pid)):
            raise ApiError('SKU「%s」已存在' % f['sku'], 409)
        vals = None
        if 'field_values' in d or f['category_id'] != cur['category_id']:
            vals = self._check_fields(f['category_id'], d.get('field_values'))
        if d.get('price_date') and not valid_date(d['price_date']):
            raise ApiError('价格日期格式应为 YYYY-MM-DD')
        ids = self._image_ids(d)
        # 只有成本/币种/利润率真的变了才重算建议价和记历史（只改名称不应动价格）
        price_changed = (f['cost'], f['cost_currency']) != (cur['cost'], cur['cost_currency'])
        sug = self._suggest(f) if (price_changed or f['profit_rate'] != cur['profit_rate']) else cur['suggested_price']
        gone = []
        with self.db.tx():
            sets = ','.join('%s=?' % c for c in self._WRITE_COLS)
            self.db.execute('UPDATE products SET %s,suggested_price=?,updated_at=? WHERE id=?' % sets,
                            [f[c] for c in self._WRITE_COLS] + [sug, now(), pid])
            if vals is not None:
                self._save_values(pid, vals)
            if ids is not None:
                gone = self.media.apply_images(pid, ids)
            if price_changed and f['cost'] is not None:
                self.history.record(pid, 'cost', f['cost'], f['cost_currency'], effective_date=d.get('price_date'),
                                    source=_txt(d.get('price_source')) or '手动修改')
        self.media.remove_files(gone)

    def delete(self, pid):
        self.require(pid)
        shots = [os.path.basename(r['screenshot_path']) for r in self.db.query(
            "SELECT screenshot_path FROM supplier_quotes WHERE product_id=? AND COALESCE(screenshot_path,'')<>''", (pid,))]
        with self.db.tx():
            self.db.execute('DELETE FROM price_history WHERE product_id=?', (pid,))
            self.db.execute('DELETE FROM supplier_quotes WHERE product_id=?', (pid,))
            self.db.execute('DELETE FROM product_field_values WHERE product_id=?', (pid,))
            self.db.execute('UPDATE quote_items SET product_id=NULL WHERE product_id=?', (pid,))   # 历史报价明细是快照，保留
            imgs = self.media.delete_all_images(pid)
            docs = self.media.delete_all_files(pid)
            self.db.execute('DELETE FROM products WHERE id=?', (pid,))
        self.media.remove_files(imgs + shots)
        self.media.remove_docs(docs)

    def duplicate(self, pid, new_sku):
        """复制产品（常用于同款不同规格）：复制资料、规格值、相册；不复制价格历史、供应商、文档。"""
        src = self.get(pid)
        d = {k: src[k] for k in self._WRITE_COLS}
        d['sku'] = new_sku
        d['name'] = src['name']
        d['field_values'] = src['field_values']
        d['price_source'] = '复制自 ' + src['sku']
        new_id = self.create(d)
        with self.db.tx():
            self.media.copy_images(pid, new_id)
        return new_id

    def recalc_suggested(self, ids=None):
        """按当前汇率重算建议价。只处理"建议价由公式产生"的产品：有成本、且没有售价历史
        （有成交价的产品，建议价是对齐的最新成交价，不动）。"""
        rows = self.db.query('SELECT id, cost, cost_currency, profit_rate, suggested_price FROM products WHERE cost IS NOT NULL')
        if ids is not None:
            keep = {int(i) for i in ids}
            rows = [r for r in rows if r['id'] in keep]
        rate, n, skipped = self.rates.rate(), 0, 0
        with self.db.tx():
            for r in rows:
                if self.db.one("SELECT 1 FROM price_history WHERE product_id=? AND price_type='sell'", (r['id'],)):
                    skipped += 1
                    continue
                sug = suggest_usd(r['cost'], r['cost_currency'], r['profit_rate'], rate)
                if sug != r['suggested_price']:
                    self.db.execute('UPDATE products SET suggested_price=? WHERE id=?', (sug, r['id']))
                    n += 1
        return {'updated': n, 'skipped_has_sell_price': skipped, 'rate': rate}

    # ---------- 合并同类项 ----------
    def merge(self, survivor_id, merge_ids):
        """把 merge_ids 并入 survivor。价格历史/成交明细/供应商比价/相册/文档全部迁移；
        保留产品的规格描述只在为空时才补；合并后按最新历史重算当前价；备注留痕。"""
        survivor_id = int(survivor_id)
        ids = []
        for m in merge_ids or []:
            m = int(m)
            if m != survivor_id and m not in ids:
                ids.append(m)
        if not ids:
            raise ApiError('没有要合并的产品')
        sv = self.require(survivor_id)
        olds = [self.require(m) for m in ids]           # 任何一个不存在就整体拒绝
        merged_skus = []
        with self.db.tx():
            for mp in olds:
                for t in ('price_history', 'quote_items', 'supplier_quotes', 'product_files'):
                    self.db.execute('UPDATE %s SET product_id=? WHERE product_id=?' % t, (survivor_id, mp['id']))
                if not sv['spec_text'].strip() and mp['spec_text'].strip():
                    self.db.execute('UPDATE products SET spec_text=? WHERE id=?', (mp['spec_text'], survivor_id))
                    sv['spec_text'] = mp['spec_text']
                self.media.move_images(mp['id'], survivor_id)       # 图片追加到保留产品相册末尾，保留产品的主图不变
                self.db.execute('DELETE FROM product_field_values WHERE product_id=?', (mp['id'],))
                self.db.execute('DELETE FROM products WHERE id=?', (mp['id'],))
                merged_skus.append(mp['sku'])
            note = ((sv['remark'] or '') + ('\n' if sv['remark'] else '') + '已合并同类项: ' + ', '.join(merged_skus)).strip()
            self.db.execute('UPDATE products SET remark=? WHERE id=?', (note, survivor_id))
            self.history.realign(survivor_id)
        return {'merged': merged_skus, 'survivor_sku': sv['sku']}


class SupplierService:
    """每个产品可记录多个供应商人民币报价，标记一个"采纳"。采纳会同步为产品当前成本并写入价格历史。"""

    def __init__(self, db, uploads_dir, products, history, book):
        self.db = db
        self.book = book
        self.dir = uploads_dir
        self.products = products
        self.history = history

    def list(self, pid):
        self.products.require(pid)
        rows = self.db.query("""SELECT * FROM supplier_quotes WHERE product_id=?
            ORDER BY is_adopted DESC, quote_date DESC, id DESC""", (pid,))
        for r in rows:
            r['is_adopted'] = bool(r['is_adopted'])
            r['screenshot_url'] = '/' + r['screenshot_path'] if r['screenshot_path'] else ''
        return rows

    def create(self, pid, d):
        self.products.require(pid)
        name = _txt(d.get('supplier_name'))
        if not name:
            raise ApiError('供应商名称不能为空')
        price = _num(d.get('price_cny'), '报价', lo=0)
        date = d.get('quote_date') or None
        if date and not valid_date(date):
            raise ApiError('报价日期格式应为 YYYY-MM-DD')
        shot = rawimg.save_data_url(self.dir, d['screenshot_data'], 'sq_') if d.get('screenshot_data') else None
        try:
            with self.db.tx():
                vid = self.book.ensure(name)                     # 供应商档案：没有就自动建一份最简的，之后到「供应商」菜单里补资料
                qid = self.db.execute("""INSERT INTO supplier_quotes(product_id,supplier_name,supplier_id,project,price_cny,quote_date,
                    screenshot_path,remark,is_adopted) VALUES(?,?,?,?,?,?,?,?,0)""",
                                      (pid, name, vid, _txt(d.get('project')), price, date, 'uploads/' + shot if shot else None,
                                       _txt(d.get('remark')) or None)).lastrowid
                if d.get('is_adopted'):
                    self._adopt(qid)
        except Exception:
            rawimg.remove_file(self.dir, shot)
            raise
        return qid

    def adopt(self, qid):
        with self.db.tx():
            return self._adopt(qid)

    def _adopt(self, qid):
        q = self.db.one('SELECT * FROM supplier_quotes WHERE id=?', (qid,))
        if not q:
            raise ApiError('供应商报价不存在', 404)
        if q['price_cny'] is None:
            raise ApiError('这条报价没有填写价格，无法采纳')
        pid = q['product_id']
        self.db.execute('UPDATE supplier_quotes SET is_adopted=0 WHERE product_id=?', (pid,))
        self.db.execute('UPDATE supplier_quotes SET is_adopted=1 WHERE id=?', (qid,))
        p = self.products.require(pid)
        sug = suggest_usd(q['price_cny'], 'CNY', p['profit_rate'], self.products.rates.rate())
        self.db.execute('UPDATE products SET cost=?, cost_currency=\'CNY\', suggested_price=?, supplier=?, updated_at=? WHERE id=?',
                        (q['price_cny'], sug, q['supplier_name'], now(), pid))
        # 生效日期用"采纳当天"：采纳是现在的决定，否则一条日期更早的旧报价会被更晚日期的旧成本压过去
        self.history.record(pid, 'cost', q['price_cny'], 'CNY', source='采纳供应商 ' + q['supplier_name'],
                            note=('供应商报价日期 ' + q['quote_date']) if q['quote_date'] else '')
        return {'product_id': pid}

    def delete(self, qid):
        q = self.db.one('SELECT * FROM supplier_quotes WHERE id=?', (qid,))
        if not q:
            raise ApiError('供应商报价不存在', 404)
        self.db.execute('DELETE FROM supplier_quotes WHERE id=?', (qid,))
        if q['screenshot_path']:
            rawimg.remove_file(self.dir, os.path.basename(q['screenshot_path']))
