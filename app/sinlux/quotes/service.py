# -*- coding: utf-8 -*-
"""报价单：一张报价单含多个产品行，编号 SLQ-YYYYMMDD-NNN。

业务规则（沿用 v4.4 并修正）：
- 明细里的单价、SKU、名称、规格是创建时的快照，之后改产品不影响历史报价。
- 金额逐行四舍五入到分，合计 = 各行金额之和（PDF/Excel/界面三处一致）。
- 标记「成交」才把明细写入售价历史（带客户、来源=报价单号）；重复标记不会重复写；取消成交会撤销这批记录。
- 客户阶段：发出报价 → 已报价；第一张成交 → 成交；该客户已有其它成交报价 → 复购。（由数据推出，反复切换状态不会越推越高）
"""
import datetime
import re
import time
from decimal import Decimal, ROUND_HALF_UP

from ..core.util import ApiError, like, now, today, valid_date

STATUSES = ['draft', 'sent', 'accepted', 'rejected', 'expired']
STATUS_LABELS = {'draft': '草稿', 'sent': '已发送', 'accepted': '成交', 'rejected': '未成交', 'expired': '过期'}
CURRENCIES = ('USD', 'CNY')            # USD 对客户，CNY 对供应商
EARLY_STAGES = ('', '潜在', '已联系', '沉睡')          # 发出报价时可自动推进到「已报价」的阶段
MAX_ITEMS = 200
PREFIX = 'SLQ'


def D(v):
    return Decimal(str(v))


def cents(x):
    return x.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def line_amount(qty, price):
    return cents(D(qty) * D(price))


def _num(v, name, lo=None, positive=False):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ApiError('%s必须是数字' % name)
    if f != f or f in (float('inf'), float('-inf')):
        raise ApiError('%s必须是有效数字' % name)
    if positive and f <= 0:
        raise ApiError('%s必须大于 0' % name)
    if lo is not None and f < lo:
        raise ApiError('%s不能小于 %s' % (name, lo))
    if f > 1e12:
        raise ApiError('%s数值过大' % name)
    return f


def _txt(v, limit=None, name=''):
    s = str(v).strip() if v is not None else ''
    if limit and len(s) > limit:
        raise ApiError('%s最长 %d 个字符' % (name, limit))
    return s


class QuoteService:
    def __init__(self, db, customers, history):
        self.db = db
        self.customers = customers
        self.history = history

    # ---------- 校验 ----------
    def _clean(self, data, keep_currency=None):
        cur = _txt(data.get('currency') or 'USD').upper()
        if cur not in CURRENCIES and cur != keep_currency:      # 旧版遗留的 EUR 报价允许继续编辑，但不能新建
            raise ApiError('币种无效，可选：' + '/'.join(CURRENCIES))
        valid_days = data.get('valid_days')
        valid_days = 30 if valid_days in (None, '') else int(_num(valid_days, '有效期', lo=0))
        if valid_days > 3650:
            raise ApiError('有效期最长 3650 天')
        head = {'currency': cur, 'valid_days': valid_days,
                'lead_time': _txt(data.get('lead_time'), 200, '交期'), 'payment_terms': _txt(data.get('payment_terms'), 300, '付款条件'),
                'shipping_terms': _txt(data.get('shipping_terms'), 300, '贸易条款'), 'notes': _txt(data.get('notes'), 3000, '备注')}
        items = []
        for n, i in enumerate(data.get('items') or [], 1):
            if not isinstance(i, dict):
                raise ApiError('明细格式无效')
            pid = i.get('product_id')
            prod = None
            if pid not in (None, ''):
                prod = self.db.one('SELECT id, sku, name, spec_text FROM products WHERE id=?', (int(pid),))
                if not prod:
                    raise ApiError('第 %d 行的产品不存在（可能已被删除）' % n)
            sku, name = _txt(i.get('sku'), 64, 'SKU'), _txt(i.get('name'), 300, '产品名称')
            if not sku and not name and prod:
                sku, name = prod['sku'], prod['name']
            if not sku and not name:
                if _txt(i.get('unit_price')) not in ('', '0') and _txt(i.get('unit_price')):
                    raise ApiError('第 %d 行填了单价但没有产品名称或 SKU' % n)
                continue                                           # 完全空白的行直接忽略
            label = name or sku
            qty = _num(i.get('quantity') if i.get('quantity') not in (None, '') else 1, '「%s」的数量' % label, positive=True)
            price = _num(i.get('unit_price') if i.get('unit_price') not in (None, '') else 0, '「%s」的单价' % label, lo=0)
            items.append({'product_id': prod['id'] if prod else None, 'sku': sku, 'name': name,
                          'spec': _txt(i.get('spec'), 4000, '规格'), 'quantity': qty, 'unit': _txt(i.get('unit') or 'pcs', 16, '单位') or 'pcs',
                          'unit_price': price, 'remark': _txt(i.get('remark'), 500, '行备注'), 'amount': line_amount(qty, price)})
        if not items:
            raise ApiError('报价单至少要有一个产品行')
        if len(items) > MAX_ITEMS:
            raise ApiError('一张报价单最多 %d 行' % MAX_ITEMS)
        head['total'] = float(sum((i['amount'] for i in items), Decimal('0')))
        return head, items

    # ---------- 编号 ----------
    def _next_no(self):
        day = time.strftime('%Y%m%d')
        mx = 0
        for r in self.db.query('SELECT quote_no FROM quotes WHERE quote_no LIKE ?', ('%s-%s-%%' % (PREFIX, day),)):
            m = re.fullmatch(r'%s-%s-(\d+)' % (PREFIX, day), r['quote_no'])
            if m:
                mx = max(mx, int(m.group(1)))
        return '%s-%s-%03d' % (PREFIX, day, mx + 1)

    # ---------- 增删改查 ----------
    def create(self, data):
        cid = data.get('customer_id')
        if not cid:
            raise ApiError('请选择客户')
        cust = self.customers.require(int(cid))
        head, items = self._clean(data)
        status = data.get('status') or 'sent'
        if status not in STATUSES:
            raise ApiError('状态无效')
        with self.db.tx():
            no = self._next_no()
            ts = now()
            qid = self.db.execute("""INSERT INTO quotes(quote_no,customer_id,currency,status,valid_days,lead_time,payment_terms,
                shipping_terms,notes,total,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                                  (no, cust['id'], head['currency'], status, head['valid_days'], head['lead_time'], head['payment_terms'],
                                   head['shipping_terms'], head['notes'], head['total'], ts, ts)).lastrowid
            self._write_items(qid, items)
            lines = ['【报价单 %s】共%d项，合计 %s %.2f%s' % (no, len(items), head['currency'], head['total'], '（草稿）' if status == 'draft' else '')]
            lines += ['  · %s %s × %g @ %g' % (i['sku'], i['name'], i['quantity'], i['unit_price']) for i in items]
            self.customers.add_system_note(cust['id'], '\n'.join(lines))
            if status == 'accepted':
                self._on_accept(self._row(qid))
            if status != 'draft':
                self._promote_quoted(cust['id'])
        return {'id': qid, 'quote_no': no, 'total': head['total']}

    def _write_items(self, qid, items):
        for n, i in enumerate(items):
            self.db.execute("""INSERT INTO quote_items(quote_id,product_id,sku,name,spec,quantity,unit,unit_price,remark,sort_order)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (qid, i['product_id'], i['sku'], i['name'], i['spec'], i['quantity'], i['unit'],
                                                 i['unit_price'], i['remark'], n))

    def _row(self, qid):
        q = self.db.one('SELECT * FROM quotes WHERE id=?', (qid,))
        if not q:
            raise ApiError('报价单不存在', 404)
        return q

    def get(self, qid):
        q = self.db.one("""SELECT q.*, c.company, c.name AS customer_name, c.country, c.address, c.emails, c.whatsapp
            FROM quotes q JOIN customers c ON c.id=q.customer_id WHERE q.id=?""", (qid,))
        if not q:
            raise ApiError('报价单不存在', 404)
        items = self.db.query("""SELECT i.*, (SELECT COALESCE(NULLIF(m.thumb,''), m.file) FROM product_images m WHERE m.product_id=i.product_id
                ORDER BY m.sort_order, m.id LIMIT 1) AS img FROM quote_items i WHERE i.quote_id=? ORDER BY i.sort_order, i.id""", (qid,))
        for i in items:
            i['amount'] = float(line_amount(i['quantity'], i['unit_price']))
            i['thumb_url'] = '/uploads/' + i.pop('img') if i.get('img') else ''
        q['items'] = items
        return self._decorate(q)

    @staticmethod
    def _decorate(q):
        q['status_label'] = STATUS_LABELS.get(q['status'], q['status'])
        created = (q['created_at'] or '')[:10]
        try:
            until = datetime.date.fromisoformat(created) + datetime.timedelta(days=int(q['valid_days'] or 0))
            q['valid_until'] = until.isoformat()
            q['past_validity'] = q['status'] == 'sent' and until < datetime.date.today()   # 已发出但超过有效期仍没结果
        except ValueError:
            q['valid_until'], q['past_validity'] = '', False
        return q

    def list(self, customer_id=None, status=None, search='', limit=100, offset=0):
        where, params = ['1=1'], []
        if customer_id:
            where.append('q.customer_id=?'); params.append(int(customer_id))
        if status:
            if status not in STATUSES:
                raise ApiError('状态无效')
            where.append('q.status=?'); params.append(status)
        if search:
            k = like(search)
            where.append("""(q.quote_no LIKE ? ESCAPE '\\' OR c.company LIKE ? ESCAPE '\\' OR c.name LIKE ? ESCAPE '\\'
                OR EXISTS(SELECT 1 FROM quote_items i WHERE i.quote_id=q.id AND (i.sku LIKE ? ESCAPE '\\' OR i.name LIKE ? ESCAPE '\\')))""")
            params += [k] * 5
        w = ' AND '.join(where)
        total = self.db.scalar('SELECT COUNT(*) FROM quotes q JOIN customers c ON c.id=q.customer_id WHERE ' + w, params)
        rows = self.db.query("""SELECT q.*, c.company, c.name AS customer_name,
            (SELECT COUNT(*) FROM quote_items i WHERE i.quote_id=q.id) AS item_count
            FROM quotes q JOIN customers c ON c.id=q.customer_id WHERE %s ORDER BY q.id DESC LIMIT ? OFFSET ?""" % w,
                             params + [max(1, min(int(limit), 500)), max(0, int(offset))])
        return [self._decorate(r) for r in rows], total

    def update(self, qid, data):
        q = self._row(qid)
        if q['status'] == 'accepted':
            raise ApiError('已成交的报价单不能修改。如需修改，请先把状态改回「已发送」', 409)
        if data.get('customer_id') not in (None, '', q['customer_id']) and int(data['customer_id']) != q['customer_id']:
            raise ApiError('不能更换报价单的客户；请新建一张报价单')
        head, items = self._clean(data, q['currency'])
        with self.db.tx():
            self.db.execute("""UPDATE quotes SET currency=?,valid_days=?,lead_time=?,payment_terms=?,shipping_terms=?,notes=?,total=?,updated_at=?
                WHERE id=?""", (head['currency'], head['valid_days'], head['lead_time'], head['payment_terms'], head['shipping_terms'],
                                head['notes'], head['total'], now(), qid))
            self.db.execute('DELETE FROM quote_items WHERE quote_id=?', (qid,))
            self._write_items(qid, items)
            self.customers.add_system_note(q['customer_id'], '【报价单 %s 已修改】共%d项，合计 %s %.2f' % (q['quote_no'], len(items), head['currency'], head['total']))

    def duplicate(self, qid):
        """复制为一张新的草稿（沿用原价格快照，可在编辑里重新定价）。"""
        src = self.get(qid)
        data = {k: src[k] for k in ('customer_id', 'currency', 'valid_days', 'lead_time', 'payment_terms', 'shipping_terms', 'notes')}
        data['items'] = [{k: i[k] for k in ('product_id', 'sku', 'name', 'spec', 'quantity', 'unit', 'unit_price', 'remark')} for i in src['items']]
        data['status'] = 'draft'
        for i in data['items']:                      # 原产品已被删除时（product_id 被置空）仍可复制
            if i['product_id'] and not self.db.one('SELECT 1 FROM products WHERE id=?', (i['product_id'],)):
                i['product_id'] = None
        return self.create(data)

    def delete(self, qid):
        q = self._row(qid)
        with self.db.tx():
            if q['status'] == 'accepted':
                self._on_unaccept(q)
            self.db.execute('DELETE FROM quotes WHERE id=?', (qid,))        # 明细随外键级联删除
            self.customers.add_system_note(q['customer_id'], '【报价单 %s 已删除】' % q['quote_no'])

    # ---------- 状态与联动 ----------
    def set_status(self, qid, status, feedback=None):
        if status not in STATUSES:
            raise ApiError('状态无效')
        q = self._row(qid)
        old = q['status']
        with self.db.tx():
            fb = q['customer_feedback'] if feedback is None else _txt(feedback, 2000, '客户反馈')
            self.db.execute('UPDATE quotes SET status=?, customer_feedback=?, updated_at=? WHERE id=?', (status, fb, now(), qid))
            if status == old:
                return
            if status == 'accepted':
                self._on_accept(self._row(qid))
            elif old == 'accepted':
                self._on_unaccept(q)
            if status in ('sent', 'accepted'):
                self._promote_quoted(q['customer_id'])
            if status in ('accepted', 'rejected'):
                self.customers.add_system_note(q['customer_id'], '【报价单 %s】%s%s' % (q['quote_no'], STATUS_LABELS[status], '：' + fb if fb else ''))

    def _promote_quoted(self, cid):
        self.db.execute("UPDATE customers SET stage='已报价', updated_at=? WHERE id=? AND COALESCE(stage,'') IN ('','潜在','已联系','沉睡')",
                        (now(), cid))

    def _on_accept(self, q):
        """成交：明细写入售价历史（先清掉本报价单之前写过的，保证重复触发也只有一份）；客户阶段推进。"""
        self._drop_history(q)
        for i in self.db.query('SELECT product_id, unit_price FROM quote_items WHERE quote_id=? AND product_id IS NOT NULL', (q['id'],)):
            self.history.record(i['product_id'], 'sell', i['unit_price'], q['currency'], customer_id=q['customer_id'],
                                effective_date=today(), source=q['quote_no'], note='报价单成交')
        others = self.db.scalar("SELECT COUNT(*) FROM quotes WHERE customer_id=? AND status='accepted' AND id<>?", (q['customer_id'], q['id']))
        stage = '复购' if others else '成交'
        self.db.execute('UPDATE customers SET stage=?, updated_at=? WHERE id=?', (stage, now(), q['customer_id']))

    def _on_unaccept(self, q):
        pids = self._drop_history(q)
        for pid in pids:
            self.history.realign(pid)

    def _drop_history(self, q):
        rows = self.db.query("SELECT DISTINCT product_id FROM price_history WHERE price_type='sell' AND source=? AND customer_id IS ?",
                             (q['quote_no'], q['customer_id']))
        self.db.execute("DELETE FROM price_history WHERE price_type='sell' AND source=? AND customer_id IS ?", (q['quote_no'], q['customer_id']))
        return [r['product_id'] for r in rows]
