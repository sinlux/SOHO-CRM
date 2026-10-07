# -*- coding: utf-8 -*-
"""数据看板：统计全部在数据库里算，页面只负责展示。

口径（写死在这里，页面不再二次计算）：
- 报价数 / 转化率只算非草稿报价单；转化率 = 已成交 ÷ 非草稿报价数。
- 金额统一折成美元排名：USD 原值，CNY 按当前汇率折算；旧数据里的 EUR/VND 无法可靠折算，不进排名，只在 other_currency_quotes 里计数。
- 沉睡预警：LV≥4，且最近 90 天既没有备注（含系统自动备注）也没有新报价单的客户。
"""
import time

from .core.util import now
from .quotes.service import STATUS_LABELS, STATUSES


def _usd(amount_col, cur_col, rate):
    return "(CASE %s WHEN 'USD' THEN %s WHEN 'CNY' THEN %s*%r ELSE 0 END)" % (cur_col, amount_col, amount_col, rate)


class Dashboard:
    def __init__(self, db, rates):
        self.db, self.rates = db, rates

    def overview(self):
        db = self.db
        rate = self.rates.rate()
        cutoff30 = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() - 30 * 86400))
        cutoff90 = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() - 90 * 86400))
        sent = db.scalar("SELECT COUNT(*) FROM quotes WHERE status<>'draft'")
        won = db.scalar("SELECT COUNT(*) FROM quotes WHERE status='accepted'")
        won_by_cur = {r['currency']: round(r['s'], 2) for r in db.query(
            "SELECT currency, SUM(total) s FROM quotes WHERE status='accepted' GROUP BY currency")}
        qusd = _usd('q.total', 'q.currency', rate)
        top_customers = db.query("""SELECT c.id, c.company, c.name, COUNT(q.id) quote_count,
              ROUND(SUM(CASE WHEN q.status='accepted' THEN %(u)s ELSE 0 END), 2) won_usd, ROUND(SUM(%(u)s), 2) quoted_usd
            FROM customers c JOIN quotes q ON q.customer_id=c.id WHERE q.status<>'draft'
            GROUP BY c.id ORDER BY won_usd DESC, quoted_usd DESC, c.id LIMIT 10""" % {'u': qusd})
        iusd = "(CASE q.currency WHEN 'USD' THEN i.quantity*i.unit_price WHEN 'CNY' THEN i.quantity*i.unit_price*%r ELSE 0 END)" % rate
        top_products = db.query("""SELECT MAX(i.product_id) product_id, i.sku, i.name, COUNT(DISTINCT i.quote_id) quote_count,
              ROUND(SUM(i.quantity), 2) total_qty, ROUND(SUM(%s), 2) amount_usd
            FROM quote_items i JOIN quotes q ON q.id=i.quote_id WHERE q.status<>'draft'
            GROUP BY COALESCE(i.product_id, -1), CASE WHEN i.product_id IS NULL THEN i.sku || '|' || i.name ELSE '' END
            ORDER BY amount_usd DESC, i.sku LIMIT 10""" % iusd)
        dormant = db.query("""SELECT c.id, c.company, c.name, c.lv, c.stage,
              (SELECT MAX(created_at) FROM notes n WHERE n.customer_id=c.id) last_note,
              (SELECT MAX(created_at) FROM quotes q WHERE q.customer_id=c.id) last_quote
            FROM customers c WHERE IFNULL(c.lv,0) >= 4
              AND NOT EXISTS (SELECT 1 FROM notes n WHERE n.customer_id=c.id AND n.created_at >= ?)
              AND NOT EXISTS (SELECT 1 FROM quotes q WHERE q.customer_id=c.id AND q.created_at >= ?)
            ORDER BY c.lv DESC, c.id LIMIT 20""", (cutoff90, cutoff90))
        dormant_total = db.scalar("""SELECT COUNT(*) FROM customers c WHERE IFNULL(c.lv,0) >= 4
              AND NOT EXISTS (SELECT 1 FROM notes n WHERE n.customer_id=c.id AND n.created_at >= ?)
              AND NOT EXISTS (SELECT 1 FROM quotes q WHERE q.customer_id=c.id AND q.created_at >= ?)""", (cutoff90, cutoff90))
        from .core.schema import STAGES
        stage_rows = {r['stage']: r['n'] for r in db.query("SELECT stage, COUNT(*) n FROM customers GROUP BY stage")}
        stage_dist = [{'stage': s, 'n': stage_rows.get(s, 0)} for s in STAGES]
        stage_dist.append({'stage': '未设置', 'n': stage_rows.get('', 0) + stage_rows.get(None, 0)})
        status_rows = {r['status']: r['n'] for r in db.query("SELECT status, COUNT(*) n FROM quotes GROUP BY status")}
        return {
            'customers': db.scalar('SELECT COUNT(*) FROM customers'), 'products': db.scalar('SELECT COUNT(*) FROM products'),
            'quotes': sent, 'drafts': db.scalar("SELECT COUNT(*) FROM quotes WHERE status='draft'"),
            'quotes_30d': db.scalar("SELECT COUNT(*) FROM quotes WHERE status<>'draft' AND created_at >= ?", (cutoff30,)),
            'won': won, 'conversion': round(won / sent * 100, 1) if sent else 0.0,
            'won_amount_by_currency': won_by_cur,
            'won_amount_usd': round(sum(v * (rate if c == 'CNY' else 1) for c, v in won_by_cur.items() if c in ('USD', 'CNY')), 2),
            'other_currency_quotes': db.scalar("SELECT COUNT(*) FROM quotes WHERE currency NOT IN ('USD','CNY') AND status<>'draft'"),
            'rate': rate,
            'status_dist': [{'status': s, 'label': STATUS_LABELS[s], 'n': status_rows.get(s, 0)} for s in STATUSES],
            'stage_dist': stage_dist, 'top_customers': top_customers, 'top_products': top_products,
            'dormant': dormant, 'dormant_total': dormant_total,
            'reminders_due': db.scalar("SELECT COUNT(*) FROM reminders WHERE done=0 AND due_date <= ?", (time.strftime('%Y-%m-%d'),)),
            'generated_at': now()}
