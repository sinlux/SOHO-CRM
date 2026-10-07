# -*- coding: utf-8 -*-
"""从旧 QuoteMaster 迁移数据到新版 CRM。

迁移内容：类目/类目字段（按 code / key 合并）、产品 + 规格值 + 产品图（进相册并自动规范化）、供应商比价 + 截图、
客户（按邮箱 / 公司名与现有客户去重合并，旧客户独有的字段补进已有档案）、旧报价（每条转成单行报价单，单号 OLD-xxxx）。
不会删除或覆盖现有数据；可重复运行（已迁移的 SKU / 报价 / 比价 / 备注都会识别并跳过）。整个迁移在一个事务里，出错整体回滚。
old: 旧库连接（SQLCipher 解密后的连接，或测试用的普通 sqlite3 连接），行要能按列名取值。"""
import os
import shutil
from decimal import Decimal, ROUND_HALF_UP

from ..core.util import ApiError, now
from ..quotes.service import STATUSES

OLD_MARK = '旧QuoteMaster迁移'


def _rows(old, sql):
    try:
        cur = old.execute(sql)
    except Exception:
        return []
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _cents(qty, price):
    return (Decimal(str(qty)) * Decimal(str(price))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


class Migrator:
    def __init__(self, ctx, old, old_uploads):
        self.ctx, self.db, self.old, self.old_uploads = ctx, ctx.db, old, old_uploads
        self.stats, self.warnings = {}, []

    def _bump(self, k, n=1):
        self.stats[k] = self.stats.get(k, 0) + n

    def _find_file(self, rel):
        if not rel:
            return None
        for p in (os.path.join(self.old_uploads, os.path.basename(rel)), os.path.join(os.path.dirname(self.old_uploads), rel.lstrip('/\\'))):
            if os.path.isfile(p):
                return p
        return None

    def run(self):
        with self.db.tx():
            cat_map = self._categories()
            field_map = self._fields(cat_map)
            prod_map, new_pids = self._products(cat_map)
            self._field_values(prod_map, field_map)
            self._suppliers(prod_map)
            cust_map = self._customers()
            self._quotes(cust_map, prod_map)
        self._images(new_pids)                          # 图片放在事务之后：失败只影响图片，不回滚数据
        return {'stats': self.stats, 'warnings': self.warnings}

    # ---------- 1/2 类目与字段 ----------
    def _categories(self):
        m = {}
        for r in _rows(self.old, 'SELECT * FROM categories'):
            cur = self.db.one('SELECT id FROM categories WHERE code=?', (r['code'],))
            if cur:
                m[r['id']] = cur['id']
            else:
                m[r['id']] = self.db.execute('INSERT INTO categories(code,name,icon,sort_order,is_builtin) VALUES(?,?,?,?,?)',
                                             (r['code'], r['name'], r.get('icon'), r.get('sort_order') or 0, r.get('is_builtin') or 0)).lastrowid
                self._bump('类目(新建)')
        self.stats['类目'] = len(m)
        return m

    def _fields(self, cat_map):
        m = {}
        for r in _rows(self.old, 'SELECT * FROM category_fields'):
            cid = cat_map.get(r['category_id'])
            if not cid:
                continue
            cur = self.db.one('SELECT id FROM category_fields WHERE category_id=? AND field_key=?', (cid, r['field_key']))
            if cur:
                m[r['id']] = cur['id']
            else:
                m[r['id']] = self.db.execute("""INSERT INTO category_fields(category_id,field_key,field_label,field_type,is_required,options,unit,
                    placeholder,sort_order) VALUES(?,?,?,?,?,?,?,?,?)""", (cid, r['field_key'], r['field_label'], r['field_type'], r.get('is_required') or 0,
                                                                      r.get('options'), r.get('unit'), r.get('placeholder'), r.get('sort_order') or 0)).lastrowid
                self._bump('类目字段(新建)')
        self.stats['类目字段'] = len(m)
        return m

    # ---------- 3 产品 ----------
    def _products(self, cat_map):
        m, new = {}, []
        fallback = self.db.scalar("SELECT id FROM categories WHERE code='other'") or self.db.scalar('SELECT MIN(id) FROM categories')
        for r in _rows(self.old, 'SELECT * FROM products'):
            cur = self.db.one('SELECT id FROM products WHERE sku=? COLLATE NOCASE', (r['sku'],))
            if cur:
                m[r['id']] = cur['id']
                continue
            ts = r.get('created_at') or now()
            pid = self.db.execute("""INSERT INTO products(sku,name,category_id,cost,cost_currency,profit_rate,suggested_price,moq,lead_time,supplier,
                remark,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                  (r['sku'], r['name'] or r['sku'], cat_map.get(r.get('category_id'), fallback), r.get('cost'), r.get('cost_currency') or 'USD',
                                   r.get('profit_rate') if r.get('profit_rate') is not None else 0.25, r.get('suggested_price'), r.get('moq'),
                                   r.get('lead_time'), r.get('supplier'), r.get('remark'), ts, ts)).lastrowid
            m[r['id']] = pid
            new.append((pid, r.get('image_path')))
            if r.get('cost') and r['cost'] > 0:
                self.ctx.history.record(pid, 'cost', r['cost'], r.get('cost_currency') or 'USD', effective_date=str(ts)[:10], source=OLD_MARK)
            self._bump('产品(新迁入)')
        return m, new

    def _field_values(self, prod_map, field_map):
        n = 0
        for r in _rows(self.old, 'SELECT * FROM product_field_values'):
            pid, fid = prod_map.get(r['product_id']), field_map.get(r['field_id'])
            if pid and fid and not self.db.one('SELECT 1 FROM product_field_values WHERE product_id=? AND field_id=?', (pid, fid)):
                self.db.execute('INSERT INTO product_field_values(product_id,field_id,value) VALUES(?,?,?)', (pid, fid, r['value']))
                n += 1
        self.stats['规格值'] = n

    # ---------- 5 供应商比价 ----------
    def _suppliers(self, prod_map):
        n = 0
        for r in _rows(self.old, 'SELECT * FROM supplier_quotes'):
            pid = prod_map.get(r['product_id'])
            if not pid:
                continue
            if self.db.one('SELECT 1 FROM supplier_quotes WHERE product_id=? AND supplier_name=? AND IFNULL(price_cny,0)=IFNULL(?,0) AND IFNULL(quote_date,\'\')=IFNULL(?,\'\')',
                           (pid, r['supplier_name'], r.get('price_cny'), r.get('quote_date'))):
                continue
            shot = None
            src = self._find_file(r.get('screenshot_path'))
            if src:
                name = 'sq_' + os.path.basename(src)
                os.makedirs(self.ctx.uploads_dir, exist_ok=True)
                if not os.path.exists(os.path.join(self.ctx.uploads_dir, name)):
                    shutil.copy2(src, os.path.join(self.ctx.uploads_dir, name))
                shot = 'uploads/' + name
            self.db.execute("""INSERT INTO supplier_quotes(product_id,supplier_name,price_cny,quote_date,screenshot_path,remark,is_adopted)
                VALUES(?,?,?,?,?,?,?)""", (pid, r['supplier_name'], r.get('price_cny'), r.get('quote_date'), shot, r.get('remark'), r.get('is_adopted') or 0))
            n += 1
        self.stats['供应商比价'] = n

    # ---------- 6 客户 ----------
    def _customers(self):
        m = {}
        for r in _rows(self.old, 'SELECT * FROM customers'):
            emails = [e.strip().lower() for e in (r.get('email') or '').replace(',', ' ').replace(';', ' ').split() if '@' in e]
            match = None
            for e in emails:
                match = self.db.one('SELECT * FROM customers WHERE LOWER(emails) LIKE ? LIMIT 1', ('%' + e + '%',))
                if match:
                    break
            co = (r.get('company') or '').strip()
            if not match and co:
                match = self.db.one('SELECT * FROM customers WHERE LOWER(company)=LOWER(?) LIMIT 1', (co,))
            if not match and not emails and not co and (r.get('name') or '').strip():          # 只有姓名：按姓名 + 其它都为空来认，重复运行才不会再建一份
                match = self.db.one("SELECT * FROM customers WHERE LOWER(name)=LOWER(?) AND IFNULL(company,'')='' AND IFNULL(emails,'')='' LIMIT 1",
                                    (r['name'].strip(),))
            if match:
                m[r['id']] = match['id']
                upd = {}
                for f in ('whatsapp', 'linkedin', 'facebook', 'address', 'main_business', 'website', 'country'):
                    v = (r.get(f) or '').strip()
                    if v and not (match[f] or '').strip():
                        upd[f] = v
                if upd:
                    self.ctx.customers.update(match['id'], upd)
                    self._bump('客户(信息合并)')
                remark = (r.get('remark') or '').strip()
                text = '【旧QuoteMaster备注】' + remark
                if remark and not self.db.one('SELECT 1 FROM notes WHERE customer_id=? AND content=?', (match['id'], text)):
                    self.ctx.customers.add_system_note(match['id'], text)
                continue
            lv = r.get('customer_level')
            try:
                m[r['id']] = self.ctx.customers.create({
                    'name': r.get('name') or '', 'company': co, 'emails': ' '.join(emails) or (r.get('email') or ''), 'whatsapp': r.get('whatsapp') or '',
                    'country': r.get('country') or '', 'address': r.get('address') or '', 'lv': lv if lv in (1, 2, 3, 4, 5, 6) else None,
                    'website': r.get('website') or '', 'main_business': r.get('main_business') or '', 'linkedin': r.get('linkedin') or '',
                    'facebook': r.get('facebook') or ''})
                self._bump('客户(新迁入)')
                if (r.get('remark') or '').strip():
                    self.ctx.customers.add_system_note(m[r['id']], '【旧QuoteMaster备注】' + r['remark'].strip())
            except ApiError as e:
                self.warnings.append('旧客户 #%s（%s）无法迁入：%s' % (r['id'], co or r.get('name') or '', e.message))
        return m

    # ---------- 7 旧报价 → 单行报价单 ----------
    def _quotes(self, cust_map, prod_map):
        n = 0
        sql = 'SELECT q.*, p.sku AS psku, p.name AS pname FROM quotes q LEFT JOIN products p ON p.id=q.product_id'
        for r in _rows(self.old, sql):
            cid = cust_map.get(r.get('customer_id'))
            if not cid:
                self.warnings.append('旧报价 #%s 的客户没有迁入，已跳过' % r['id'])
                continue
            no = 'OLD-%04d' % r['id']
            if self.db.one('SELECT 1 FROM quotes WHERE quote_no=?', (no,)):
                continue
            qty = r.get('quantity') or 1
            price = r.get('price') or 0
            total = _cents(qty, price)
            status = r.get('status') if r.get('status') in STATUSES else 'sent'
            ts = r.get('created_at') or now()
            qid = self.db.execute("""INSERT INTO quotes(quote_no,customer_id,currency,status,valid_days,lead_time,notes,total,customer_feedback,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (no, cid, r.get('currency') or 'USD', status, r.get('valid_days') or 30, str(r.get('lead_time') or ''),
                                                  r.get('message_text') or '', float(total), r.get('customer_feedback') or '', ts, r.get('updated_at') or ts)).lastrowid
            pid = prod_map.get(r.get('product_id'))
            self.db.execute('INSERT INTO quote_items(quote_id,product_id,sku,name,quantity,unit_price) VALUES(?,?,?,?,?,?)',
                            (qid, pid, r.get('psku') or '', r.get('pname') or '(旧记录)', qty, price))
            if status == 'accepted' and pid and price > 0:               # 旧成交价进售价历史（谈判提示用），和新版"标记成交"一致
                self.ctx.history.record(pid, 'sell', price, r.get('currency') or 'USD', customer_id=cid, effective_date=str(ts)[:10], source=no, note=OLD_MARK)
            n += 1
        self.stats['报价历史'] = n

    # ---------- 产品图 ----------
    def _images(self, new_pids):
        n = 0
        for pid, rel in new_pids:
            src = self._find_file(rel)
            if not src:
                if rel:
                    self.warnings.append('产品 #%d 的图片文件找不到：%s' % (pid, rel))
                continue
            try:
                with open(src, 'rb') as f:
                    img = self.ctx.media.stage_image(f.read())
                with self.db.tx():
                    gone = self.ctx.media.apply_images(pid, [img['id']])
                self.ctx.media.remove_files(gone)
                n += 1
            except Exception as e:
                self.warnings.append('产品 #%d 的图片处理失败（%s）：%s' % (pid, os.path.basename(src), e))
        self.stats['产品图'] = n


def migrate(ctx, old_conn, old_uploads):
    return Migrator(ctx, old_conn, old_uploads).run()
