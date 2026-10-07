# -*- coding: utf-8 -*-
"""产品库：CRUD、结构化规格值、图片、供应商比价、合并同类项。"""
import os

from ..core import images
from ..core.util import ApiError, like, now, valid_date
from .pricing import CURRENCIES, UNCONVERTED, suggest_usd

IMG_PREFIX = 'uploads/'   # products.image_path 沿用旧版存法：uploads/<文件名>


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
    def __init__(self, db, uploads_dir, rates, history, catalog):
        self.db = db
        self.dir = uploads_dir
        self.rates = rates
        self.history = history
        self.catalog = catalog
        os.makedirs(uploads_dir, exist_ok=True)

    # ---------- 读 ----------
    _COLS = """p.id, p.sku, p.name, p.category_id, p.image_path, p.cost, p.cost_currency, p.profit_rate,
        p.suggested_price, p.moq, p.lead_time, p.supplier, p.remark, COALESCE(p.spec_text,'') AS spec_text,
        p.created_at, p.updated_at, c.name AS category_name, c.icon AS category_icon"""

    def list(self, search='', category_id=None, limit=100, offset=0):
        where, params = ['1=1'], []
        if category_id:
            where.append('p.category_id=?'); params.append(int(category_id))
        if search:
            k = like(search)
            where.append("(p.sku LIKE ? ESCAPE '\\' OR p.name LIKE ? ESCAPE '\\' OR p.supplier LIKE ? ESCAPE '\\' "
                         "OR p.spec_text LIKE ? ESCAPE '\\' OR p.remark LIKE ? ESCAPE '\\')")
            params += [k] * 5
        w = ' AND '.join(where)
        total = self.db.scalar('SELECT COUNT(*) FROM products p WHERE ' + w, params)
        limit = max(1, min(int(limit), 1000))
        rows = self.db.query('SELECT %s FROM products p LEFT JOIN categories c ON c.id=p.category_id WHERE %s '
                             'ORDER BY p.updated_at DESC, p.id DESC LIMIT ? OFFSET ?' % (self._COLS, w),
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
        return p

    @staticmethod
    def _decorate(p):
        p['image_url'] = '/' + p['image_path'] if p.get('image_path') else ''
        p['suggested_unconverted'] = p.get('cost_currency') in UNCONVERTED and p.get('suggested_price') is not None
        return p

    def impact(self, pid):
        self.require(pid)
        n = lambda sql: self.db.scalar(sql, (pid,))
        return {'price_records': n('SELECT COUNT(*) FROM price_history WHERE product_id=?'),
                'supplier_quotes': n('SELECT COUNT(*) FROM supplier_quotes WHERE product_id=?'),
                'quote_items': n('SELECT COUNT(*) FROM quote_items WHERE product_id=?')}

    # ---------- 校验 ----------
    def _clean(self, d, cur=None):
        """返回规整后的字段 dict。cur=None 为新建；否则为更新（缺省字段沿用现值）。"""
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
        if cur_c not in CURRENCIES:
            raise ApiError('币种无效，可选：' + '/'.join(CURRENCIES))
        out['cost_currency'] = cur_c
        pr = _num(g('profit_rate'), '利润率', lo=0, hi=10)
        out['profit_rate'] = 0.25 if pr is None else pr
        out['moq'] = _num(g('moq'), 'MOQ', lo=0, integer=True)
        out['lead_time'] = _num(g('lead_time'), '交期', lo=0, integer=True)
        out['supplier'] = _txt(g('supplier')) or None
        out['remark'] = _txt(g('remark')) or None
        out['spec_text'] = _txt(g('spec_text', ''))
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

    # ---------- 写 ----------
    def create(self, d):
        f = self._clean(d)
        if self.db.one('SELECT 1 FROM products WHERE sku=?', (f['sku'],)):
            raise ApiError('SKU「%s」已存在' % f['sku'], 409)
        vals = self._check_fields(f['category_id'], d.get('field_values'))
        if d.get('price_date') and not valid_date(d['price_date']):
            raise ApiError('价格日期格式应为 YYYY-MM-DD')
        img = images.save_data_url(self.dir, d['image_data']) if d.get('image_data') else None
        try:
            with self.db.tx():
                ts = now()
                pid = self.db.execute("""INSERT INTO products(sku,name,category_id,image_path,cost,cost_currency,
                    profit_rate,suggested_price,moq,lead_time,supplier,remark,spec_text,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                     (f['sku'], f['name'], f['category_id'], IMG_PREFIX + img if img else None,
                                      f['cost'], f['cost_currency'], f['profit_rate'], self._suggest(f), f['moq'],
                                      f['lead_time'], f['supplier'], f['remark'], f['spec_text'], ts, ts)).lastrowid
                self._save_values(pid, vals)
                if f['cost'] is not None:
                    self.history.record(pid, 'cost', f['cost'], f['cost_currency'], effective_date=d.get('price_date'),
                                        source=_txt(d.get('price_source')) or '手动建档')
        except Exception:
            images.remove_file(self.dir, img)
            raise
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
        old_img = os.path.basename(cur['image_path']) if cur['image_path'] else None
        new_img, drop_old = None, False
        if d.get('image_data'):
            new_img, drop_old = images.save_data_url(self.dir, d['image_data']), True
            path = IMG_PREFIX + new_img
        elif d.get('remove_image'):
            path, drop_old = None, True
        else:
            path = cur['image_path']
        # 只有成本/币种/利润率真的变了才重算建议价和记历史（只改名称不应动价格）
        price_changed = (f['cost'], f['cost_currency']) != (cur['cost'], cur['cost_currency'])
        sug = self._suggest(f) if (price_changed or f['profit_rate'] != cur['profit_rate']) else cur['suggested_price']
        try:
            with self.db.tx():
                self.db.execute("""UPDATE products SET sku=?,name=?,category_id=?,image_path=?,cost=?,cost_currency=?,
                    profit_rate=?,suggested_price=?,moq=?,lead_time=?,supplier=?,remark=?,spec_text=?,updated_at=? WHERE id=?""",
                                (f['sku'], f['name'], f['category_id'], path, f['cost'], f['cost_currency'],
                                 f['profit_rate'], sug, f['moq'], f['lead_time'], f['supplier'], f['remark'],
                                 f['spec_text'], now(), pid))
                if vals is not None:
                    self._save_values(pid, vals)
                if price_changed and f['cost'] is not None:
                    self.history.record(pid, 'cost', f['cost'], f['cost_currency'], effective_date=d.get('price_date'),
                                        source=_txt(d.get('price_source')) or '手动修改')
        except Exception:
            images.remove_file(self.dir, new_img)
            raise
        if drop_old:
            images.remove_file(self.dir, old_img)

    def delete(self, pid):
        p = self.require(pid)
        shots = [r['screenshot_path'] for r in self.db.query(
            "SELECT screenshot_path FROM supplier_quotes WHERE product_id=? AND COALESCE(screenshot_path,'')<>''", (pid,))]
        with self.db.tx():
            self.db.execute('DELETE FROM price_history WHERE product_id=?', (pid,))
            self.db.execute('DELETE FROM supplier_quotes WHERE product_id=?', (pid,))
            self.db.execute('DELETE FROM product_field_values WHERE product_id=?', (pid,))
            self.db.execute('UPDATE quote_items SET product_id=NULL WHERE product_id=?', (pid,))   # 历史报价明细是快照，保留
            self.db.execute('DELETE FROM products WHERE id=?', (pid,))
        for path in [p['image_path']] + shots:
            if path:
                images.remove_file(self.dir, os.path.basename(path))

    # ---------- 合并同类项 ----------
    def merge(self, survivor_id, merge_ids):
        """把 merge_ids 并入 survivor。价格历史/成交明细/供应商比价全部迁移；
        保留产品的规格和图片只在为空时才补；合并后按最新历史重算当前价；备注留痕。"""
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
        merged_skus, orphan_files = [], []
        with self.db.tx():
            for mp in olds:
                for t in ('price_history', 'quote_items', 'supplier_quotes'):
                    self.db.execute('UPDATE %s SET product_id=? WHERE product_id=?' % t, (survivor_id, mp['id']))
                if not sv['spec_text'].strip() and mp['spec_text'].strip():
                    self.db.execute('UPDATE products SET spec_text=? WHERE id=?', (mp['spec_text'], survivor_id))
                    sv['spec_text'] = mp['spec_text']
                if mp['image_path']:
                    if not sv['image_path']:
                        self.db.execute('UPDATE products SET image_path=? WHERE id=?', (mp['image_path'], survivor_id))
                        sv['image_path'] = mp['image_path']
                    else:
                        orphan_files.append(os.path.basename(mp['image_path']))
                self.db.execute('DELETE FROM product_field_values WHERE product_id=?', (mp['id'],))
                self.db.execute('DELETE FROM products WHERE id=?', (mp['id'],))
                merged_skus.append(mp['sku'])
            note = ((sv['remark'] or '') + ('\n' if sv['remark'] else '') + '已合并同类项: ' + ', '.join(merged_skus)).strip()
            self.db.execute('UPDATE products SET remark=? WHERE id=?', (note, survivor_id))
            self.history.realign(survivor_id)
        for fn in orphan_files:
            images.remove_file(self.dir, fn)
        return {'merged': merged_skus, 'survivor_sku': sv['sku']}


class SupplierService:
    """每个产品可记录多个供应商人民币报价，标记一个"采纳"。采纳会同步为产品当前成本并写入成本历史。"""

    def __init__(self, db, uploads_dir, products, history):
        self.db = db
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
        shot = images.save_data_url(self.dir, d['screenshot_data'], 'sq_') if d.get('screenshot_data') else None
        try:
            with self.db.tx():
                qid = self.db.execute("""INSERT INTO supplier_quotes(product_id,supplier_name,price_cny,quote_date,
                    screenshot_path,remark,is_adopted) VALUES(?,?,?,?,?,?,0)""",
                                      (pid, name, price, date, IMG_PREFIX + shot if shot else None,
                                       _txt(d.get('remark')) or None)).lastrowid
                if d.get('is_adopted'):
                    self._adopt(qid)
        except Exception:
            images.remove_file(self.dir, shot)
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
            images.remove_file(self.dir, os.path.basename(q['screenshot_path']))
