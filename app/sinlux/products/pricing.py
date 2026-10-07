# -*- coding: utf-8 -*-
"""建议价公式与价格历史（只追加不覆盖）。

建议价(USD) = 成本 × 汇率 × (1 + 利润率)。CNY 乘汇率；USD 直接用；
只用 CNY（供应商/成本）和 USD（客户/售价）。旧数据里已有的 EUR/VND 成本仍照旧版规则（不换算，界面标"未换算"），但不能再新增。
"""
from ..core.util import ApiError, now, today, valid_date

CURRENCIES = ('CNY', 'USD')
LEGACY_CURRENCIES = ('EUR', 'VND')       # 只读保留：旧数据可以继续存在，新输入不接受
PRICE_TYPES = ('cost', 'sell')
UNCONVERTED = ('EUR', 'VND')


def suggest_usd(cost, currency, profit_rate, cny_usd_rate):
    if cost is None or cost <= 0:
        return None
    if profit_rate is None:
        profit_rate = 0.25
    usd = cost * cny_usd_rate if (currency or 'USD').upper() == 'CNY' else cost
    return round(usd * (1 + profit_rate), 2)


class PriceHistory:
    def __init__(self, db, rates):
        self.db = db
        self.rates = rates

    # ---------- 读 ----------
    def latest(self, product_id, price_type):
        return self.db.one("""SELECT price, currency, effective_date, source FROM price_history
            WHERE product_id=? AND price_type=? ORDER BY effective_date DESC, id DESC LIMIT 1""",
                           (product_id, price_type))

    def list(self, product_id, limit=200):
        return self.db.query("""SELECT ph.*, c.company AS customer_company, c.name AS customer_name
            FROM price_history ph LEFT JOIN customers c ON c.id=ph.customer_id
            WHERE ph.product_id=? ORDER BY ph.effective_date DESC, ph.id DESC LIMIT ?""", (product_id, limit))

    def last_prices(self, product_id, customer_id=None):
        """报价单"谈判提示"：该客户上次成交价 / 他人最近成交价 / 当前成本。"""
        out = {'to_this_customer': None, 'to_anyone': None, 'cost_latest': None}
        if customer_id:
            out['to_this_customer'] = self.db.one("""SELECT price,currency,effective_date,source FROM price_history
                WHERE product_id=? AND price_type='sell' AND customer_id=?
                ORDER BY effective_date DESC, id DESC LIMIT 1""", (product_id, customer_id))
        out['to_anyone'] = self.db.one("""SELECT ph.price,ph.currency,ph.effective_date,ph.source,c.company
            FROM price_history ph LEFT JOIN customers c ON c.id=ph.customer_id
            WHERE ph.product_id=? AND ph.price_type='sell'
            ORDER BY ph.effective_date DESC, ph.id DESC LIMIT 1""", (product_id,))
        out['cost_latest'] = self.latest(product_id, 'cost')
        return out

    # ---------- 写（只追加） ----------
    def record(self, product_id, price_type, price, currency, customer_id=None, effective_date=None,
               source='', note=''):
        """追加一条。返回 True=已记录，False=与最新一条成本完全相同而跳过（连续保存不产生噪音）。"""
        if price is None:
            return False
        price = float(price)
        if price_type == 'cost':
            last = self.latest(product_id, 'cost')
            if last and abs(last['price'] - price) < 1e-9 and last['currency'] == currency:
                return False
        self.db.execute("""INSERT INTO price_history(product_id,price_type,price,currency,customer_id,effective_date,source,note)
            VALUES(?,?,?,?,?,?,?,?)""", (product_id, price_type, price, currency, customer_id,
                                         effective_date or today(), source or '', note or ''))
        return True

    def add_manual(self, product_id, d):
        t = d.get('price_type')
        if t not in PRICE_TYPES:
            raise ApiError('价格类型必须是 cost（成本）或 sell（售价）')
        try:
            price = float(d.get('price'))
        except (TypeError, ValueError):
            raise ApiError('价格必须是数字')
        if price <= 0:
            raise ApiError('价格必须大于 0')
        cur = (d.get('currency') or ('CNY' if t == 'cost' else 'USD')).upper()
        if cur not in CURRENCIES:
            raise ApiError('币种无效，可选：' + '/'.join(CURRENCIES))
        date = d.get('effective_date') or today()
        if not valid_date(date):
            raise ApiError('日期格式应为 YYYY-MM-DD')
        cid = d.get('customer_id')
        if cid not in (None, ''):
            if t == 'cost':
                raise ApiError('成本记录不关联客户')
            if not self.db.one('SELECT 1 FROM customers WHERE id=?', (int(cid),)):
                raise ApiError('客户不存在', 404)
            cid = int(cid)
        else:
            cid = None
        with self.db.tx():
            ok = self.record(product_id, t, price, cur, cid, date, (d.get('source') or '手动录入').strip(),
                             (d.get('note') or '').strip())
            if ok:
                self.realign(product_id)
        return ok

    def delete(self, record_id):
        r = self.db.one('SELECT product_id FROM price_history WHERE id=?', (record_id,))
        if not r:
            raise ApiError('价格记录不存在', 404)
        with self.db.tx():
            self.db.execute('DELETE FROM price_history WHERE id=?', (record_id,))
            self.realign(r['product_id'])
        return r['product_id']

    # ---------- 对齐：产品当前价 = 最新历史记录 ----------
    def realign(self, product_id):
        """当前成本 ← 日期最新的成本记录；建议价 ← 最新售价记录，没有售价记录则按公式由成本算出。
        没有任何成本记录时不动产品现有数据（不凭空清空）。"""
        p = self.db.one('SELECT cost, cost_currency, profit_rate FROM products WHERE id=?', (product_id,))
        if not p:
            return
        lc, ls = self.latest(product_id, 'cost'), self.latest(product_id, 'sell')
        cost, cur = (lc['price'], lc['currency']) if lc else (p['cost'], p['cost_currency'])
        if ls:
            sug = ls['price']
        else:
            sug = suggest_usd(cost, cur, p['profit_rate'], self.rates.rate())
        self.db.execute('UPDATE products SET cost=?, cost_currency=?, suggested_price=?, updated_at=? WHERE id=?',
                        (cost, cur, sug, now(), product_id))
