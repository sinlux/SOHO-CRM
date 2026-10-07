# -*- coding: utf-8 -*-
"""PI（形式发票）导入，针对固定 PI 模板（.xls）：
  头部：Invoice Number / Issued Date（Excel 日期序列号）/ 买家区（Buyer / Contact Person / Address / Email / Phone）
  明细：从「Item No.」行开始，列 Product Name | Parameters | Price/PCS (USD) | Quantity | Amount | rmb；读到「Total Payment」结束
  没有单价或数量的行（运费、彩盒费）不建产品，但保留在成交明细里。
确认后：产品建档/更新 + 成本历史 + 一张已成交报价单（单号=PI 号）+ 售价历史（由成交单写入，带客户/PI 日期/PI 号）+ 客户备注与阶段。
同一 PI 号重复导入自动跳过。解析阶段只读，不写库。"""
import os
import re

from ..core.util import ApiError, valid_date

MAX_PI_UPLOAD = 20 * 1024 * 1024


def _cell(ws, r, c):
    if r >= ws.nrows or c >= ws.ncols:
        return ''
    v = ws.cell_value(r, c)
    if isinstance(v, float) and v == int(v):
        return int(v)
    return v


def _find_label_value(ws, label, max_row=14):
    for r in range(min(max_row, ws.nrows)):
        for c in range(ws.ncols):
            v = _cell(ws, r, c)
            if isinstance(v, str) and v.strip().rstrip(':：') == label.rstrip(':：'):
                for cc in range(c + 1, ws.ncols):
                    vv = _cell(ws, r, cc)
                    if str(vv).strip() != '':
                        return vv
    return ''


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def parse_pi(path, original_name=''):
    try:
        import xlrd
        wb = xlrd.open_workbook(path)
    except ImportError:
        raise ApiError('缺少 xlrd 库，无法读取 .xls（请确认 app/libs/xlrd 存在）', 500)
    except Exception:
        raise ApiError('无法读取「%s」：不是有效的 .xls 文件（只支持标准 PI 模板的 .xls 格式）' % original_name)
    ws = wb.sheet_by_index(0)
    invoice_no = str(_find_label_value(ws, 'Invoice Number:')).strip()
    raw = _find_label_value(ws, 'Issued Date:')
    date = ''
    if _is_num(raw) and raw > 20000:
        date = xlrd.xldate_as_datetime(raw, wb.datemode).strftime('%Y-%m-%d')
    elif str(raw).strip():
        date = str(raw).strip()[:10].replace('/', '-')
    buyer = {}
    label_map = {'Buyer:': 'company', 'Contact Person:': 'name', 'Address:': 'address', 'Email:': 'emails', 'Phone:': 'whatsapp'}
    for r in range(5, min(14, ws.nrows)):
        lab = str(_cell(ws, r, 0)).strip()
        if lab in label_map:
            buyer[label_map[lab]] = str(_cell(ws, r, 1)).strip()
    header_row, cols = None, {}
    for r in range(8, min(20, ws.nrows)):
        if str(_cell(ws, r, 0)).strip() == 'Item No.':
            header_row = r
            for c in range(ws.ncols):
                h = str(_cell(ws, r, c)).strip()
                hl = h.lower()
                if h == 'Product Name':
                    cols['name'] = c
                elif h == 'Parameters':
                    cols['spec'] = c
                elif hl.startswith('price'):
                    cols['price'] = c
                elif h == 'Quantity':
                    cols['qty'] = c
                elif hl.startswith('amount'):
                    cols['amount'] = c
                elif hl == 'rmb' and 'rmb' not in cols:
                    cols['rmb'] = c
            break
    if header_row is None or 'name' not in cols:
        raise ApiError('无法识别「%s」的明细表头，请确认是标准 PI 模板（需要「Item No.」「Product Name」列）' % original_name)
    currency = 'USD'
    m = re.search(r'\((\w{3})\)', str(_cell(ws, header_row, cols.get('price', 4))))
    if m:
        currency = m.group(1).upper()
    items, total_amount = [], None
    for r in range(header_row + 1, ws.nrows):
        if str(_cell(ws, r, 0)).strip().startswith('Total Payment'):
            ta = _cell(ws, r, cols.get('amount', 6))
            total_amount = round(float(ta), 2) if _is_num(ta) else None
            break
        name_raw = str(_cell(ws, r, cols['name'])).strip()
        amount = _cell(ws, r, cols.get('amount', 6))
        if not name_raw and not _is_num(amount):
            continue
        price = _cell(ws, r, cols['price']) if 'price' in cols else ''
        qty = _cell(ws, r, cols['qty']) if 'qty' in cols else ''
        rmb = _cell(ws, r, cols['rmb']) if 'rmb' in cols else ''
        is_product = _is_num(price) and _is_num(qty) and qty > 0
        lines = [l.strip() for l in name_raw.split('\n') if l.strip()]
        base = lines[0] if lines else ''
        spec = str(_cell(ws, r, cols['spec'])).strip() if 'spec' in cols else ''
        if len(lines) > 1:
            spec = (spec + '\n' + ' '.join(lines[1:])).strip()
        cm = re.search(r'Code[:：]\s*([A-Za-z0-9][A-Za-z0-9\-_./]*)', spec)
        sku = cm.group(1) if cm else base                            # Code 优先，没有再用品名
        cost = round(float(rmb) / float(qty), 3) if is_product and _is_num(rmb) and rmb > 0 else None
        items.append({'row': r + 1, 'is_product': is_product, 'sku': sku, 'name': base, 'spec': spec,
                      'unit_price': float(price) if _is_num(price) else None, 'quantity': float(qty) if _is_num(qty) else None,
                      'amount': round(float(amount), 2) if _is_num(amount) else None, 'unit_cost_cny': cost, 'include': True})
    return {'file': original_name, 'invoice_no': invoice_no, 'date': date, 'currency': currency, 'buyer': buyer,
            'total_amount': total_amount, 'items': items}


class PiImporter:
    def __init__(self, db, products, quotes, customers, history, catalog, tmp_dir):
        self.db, self.products, self.quotes, self.customers, self.history, self.catalog = db, products, quotes, customers, history, catalog
        self.tmp = tmp_dir

    def new_path(self, filename):
        if not filename.lower().endswith('.xls'):
            raise ApiError('PI 导入只支持 .xls 文件')
        os.makedirs(self.tmp, exist_ok=True)
        import uuid
        return os.path.join(self.tmp, 'pi_%s.xls' % uuid.uuid4().hex[:10])

    def preview(self, path, name):
        try:
            d = parse_pi(path, name)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        d['already_imported'] = bool(d['invoice_no']) and bool(self.db.one('SELECT 1 FROM quotes WHERE quote_no=?', (d['invoice_no'],)))
        d['matched_customer'], d['customer_candidates'] = self._match(d['buyer'])
        for it in d['items']:
            it['exists'] = bool(it['is_product'] and it['sku'] and self.db.one('SELECT 1 FROM products WHERE sku=? COLLATE NOCASE', (it['sku'],)))
        calc = round(sum((i['amount'] or 0) for i in d['items']), 2)
        d['sum_matches_total'] = d['total_amount'] is None or abs(calc - d['total_amount']) < 0.01
        return d

    def _match(self, buyer):
        """先按邮箱，再按公司名精确匹配；匹配不上列候选，由用户选——不自动新建客户。"""
        for e in re.split(r'[\s,;；]+', buyer.get('emails') or ''):
            e = e.strip().lower()
            if '@' in e:
                r = self.db.one("SELECT id, company, name, emails FROM customers WHERE LOWER(emails) LIKE ? LIMIT 1", ('%' + e + '%',))
                if r:
                    return r, []
        co = (buyer.get('company') or '').strip()
        if co:
            r = self.db.one('SELECT id, company, name, emails FROM customers WHERE LOWER(company)=LOWER(?) LIMIT 1', (co,))
            if r:
                return r, []
            word = re.split(r'[\s,]+', co)[0]
            if len(word) >= 3:
                return None, self.db.query("SELECT id, company, name, emails FROM customers WHERE company LIKE ? LIMIT 5", ('%' + word + '%',))
        return None, []

    # ---------- 写入 ----------
    def apply(self, d):
        no = str(d.get('invoice_no') or '').strip()
        if not no:
            raise ApiError('PI 号为空，无法导入')
        if self.db.one('SELECT 1 FROM quotes WHERE quote_no=?', (no,)):
            return {'skipped': True, 'quote_no': no, 'reason': '%s 已导入过，自动跳过（防重复）' % no}
        try:
            cid = int(d.get('customer_id'))
        except (TypeError, ValueError):
            raise ApiError('PI %s 还没有指定客户' % no)
        cust = self.customers.require(cid)
        date = str(d.get('date') or '')
        if not valid_date(date):
            raise ApiError('PI %s 的日期无效（应为 YYYY-MM-DD）' % no)
        cur = str(d.get('currency') or 'USD').upper()
        cat = int(d.get('category_id') or 0) or self.db.scalar("SELECT id FROM categories WHERE code='lighting'")
        self.catalog.require_category(cat)
        stats = {'products_new': 0, 'products_updated': 0, 'cost_records': 0}
        lines, seen, touched = [], {}, []
        src = 'PI导入 ' + no
        with self.db.tx():
            for it in d.get('items') or []:
                if not isinstance(it, dict) or it.get('include') is False:
                    continue
                amount, price, qty = it.get('amount'), it.get('unit_price'), it.get('quantity')
                sku = str(it.get('sku') or '').strip()
                name = str(it.get('name') or '').strip()
                spec = str(it.get('spec') or '').strip()
                if it.get('is_product') and sku and price is not None and qty:
                    cost = it.get('unit_cost_cny')
                    try:
                        cost = float(cost) if cost not in (None, '') else None
                    except (TypeError, ValueError):
                        raise ApiError('SKU %s 的成本不是数字' % sku)
                    if cost is not None and cost <= 0:
                        cost = None
                    pid = seen.get(sku.lower())
                    if pid is None:
                        row = self.db.one('SELECT id, spec_text FROM products WHERE sku=? COLLATE NOCASE', (sku,))
                        if row:
                            pid = row['id']
                            if not (row['spec_text'] or '').strip() and spec:                  # 规格只在原来为空时填充
                                self.db.execute('UPDATE products SET spec_text=? WHERE id=?', (spec, pid))
                            stats['products_updated'] += 1
                        else:
                            pid = self.products.create({'sku': sku, 'name': name or sku, 'category_id': cat, 'cost': cost, 'cost_currency': 'CNY',
                                                        'spec_text': spec, 'price_date': date, 'price_source': src})
                            stats['products_new'] += 1
                            cost = None                                                      # create 已经记了这条成本
                            stats['cost_records'] += 1
                        seen[sku.lower()] = pid
                    if cost is not None and self.history.record(pid, 'cost', cost, 'CNY', effective_date=date, source=src):
                        stats['cost_records'] += 1
                    touched.append(pid)
                    lines.append({'product_id': pid, 'sku': sku, 'name': name or sku, 'spec': spec, 'quantity': qty, 'unit_price': price})
                elif amount:                                                                 # 运费、彩盒费等：只留在成交明细里
                    lines.append({'product_id': None, 'sku': '', 'name': name or '(fee)', 'spec': spec, 'quantity': 1, 'unit_price': amount})
            q = self.quotes.create_imported(no, cid, cur, date, lines, 'PI导入')
            for pid in set(touched):
                self.history.realign(pid, use_sell=False)           # 当前成本对齐到日期最新的成本记录；建议价仍按公式
            self.customers.add_system_note(cid, '【PI导入】%s（%s）共%d项，合计 %s %.2f' % (no, date, len(lines), cur, q['total']))
        stats.update(quote_no=no, quote_total=q['total'], customer=cust['company'] or cust['name'], items=len(lines))
        return stats
