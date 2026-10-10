# -*- coding: utf-8 -*-
"""文档导入：客户 PI / 供应商报价单 / 产品清单（.xls .xlsx）→ 识别 → 数据清洗（查重、校验）→ 用户逐项确认 → 写入。

写入前不动任何数据。写入分两阶段：先把所有条目校验一遍（SKU 冲突、缺客户、币种不支持…），有问题一条都不写；校验通过后在一个事务里写。
"""
import os
import re
import shutil
import threading
import time
import uuid

from ..core.util import ApiError, today, valid_date
from . import dedupe, docparse
from . import grid as gridmod
from . import xlsx_images as xi

MAX_UPLOAD = 60 * 1024 * 1024
SESSION_TTL = 6 * 3600
KINDS = ('pi', 'supplier', 'list')
KIND_LABEL = {'pi': '客户 PI', 'supplier': '供应商报价单', 'list': '产品清单'}


class DocImporter:
    def __init__(self, db, products, quotes, customers, vendors, product_suppliers, history, catalog, media, tmp_dir, uploads_dir):
        self.db, self.products, self.quotes, self.customers = db, products, quotes, customers
        self.vendors, self.product_suppliers, self.history, self.catalog, self.media = vendors, product_suppliers, history, catalog, media
        self.tmp, self.uploads = tmp_dir, uploads_dir
        shutil.rmtree(self.tmp, ignore_errors=True)                     # 会话在内存里，重启后这些文件没用了
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
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ('.xls', '.xlsx', '.xlsm'):
            raise ApiError('只支持 .xls / .xlsx 文件（PDF 或图片格式的报价单暂不支持，请先在 Excel 里整理）')
        with self.lock:
            self._gc()
            sid = uuid.uuid4().hex[:12]
            d = os.path.join(self.tmp, sid)
            os.makedirs(d, exist_ok=True)
            self.sessions[sid] = {'dir': d, 'path': os.path.join(d, 'upload' + ext), 'name': filename, 'ext': ext, 'touched': time.time(), 'imgs': {}}
            return sid, self.sessions[sid]['path']

    def discard(self, sid):
        s = self.sessions.pop(sid, None)
        shutil.rmtree(os.path.join(self.tmp, sid), ignore_errors=True)
        return bool(s)

    def image_path(self, sid, name):
        s = self._get(sid)
        p = os.path.join(s['dir'], 'img', os.path.basename(name))
        if not os.path.isfile(p):
            raise ApiError('图片不存在', 404)
        return p

    # ---------- 识别 ----------
    def start(self, sid):
        try:
            return self.analyze(sid)
        except ApiError:
            self.discard(sid)
            raise
        except Exception as e:
            self.discard(sid)
            raise ApiError('无法读取这个文件（%s）。请确认是有效的 Excel 文件。' % str(e)[:100])

    def analyze(self, sid, sheet=None, header_row=None, columns=None, kind=None, currency=None):
        s = self._get(sid)
        g = gridmod.read_grid(s['path'], s['name'], sheet)
        s['grid'] = g
        images = {}
        if s['ext'] in ('.xlsx', '.xlsm') and g.sheet:
            imgdir = os.path.join(s['dir'], 'img')
            shutil.rmtree(imgdir, ignore_errors=True)
            try:
                images = xi.extract_row_images(s['path'], g.sheet, imgdir)
            except Exception:
                images = {}
        hr = None if header_row in (None, '') else int(header_row) - 1                   # 前端用 Excel 行号（从 1 开始）
        res = docparse.parse_grid(g, images, hr, columns)
        out = {'session_id': sid, 'filename': s['name'], 'sheet': g.sheet, 'sheets': g.sheets, 'ok': res['ok'], 'warnings': res.get('warnings', []),
               'has_images': bool(images), 'is_xls': s['ext'] == '.xls',
               'grid_preview': [[g.text(r, c) for c in range(min(g.ncols, 14))] for r in range(min(g.nrows, 40))]}
        if not res['ok']:
            out['header_row'] = None
            s['parsed'] = None
            return out
        if kind in KINDS:
            res['kind'] = kind
        if currency in ('USD', 'CNY'):
            res['currency'] = currency
        s['parsed'] = res
        out.update(kind=res['kind'], kind_label=KIND_LABEL[res['kind']], header_row=res['header_row'] + 1, columns=res['columns'], headers=res['headers'],
                   unmapped=res['unmapped'], currency=res['currency'], meta=res['meta'], items=res['items'], total_amount=res['total_amount'])
        out['warnings'] = res['warnings']
        self._clean(s, out)
        return out

    # ---------- 数据清洗 ----------
    @staticmethod
    def _hash_image(sdir, name):
        """查重用的图：先走和产品相册一样的规范化（裁边、居中、白底），这样才能和库里已规范化的图比较。"""
        src = os.path.join(sdir, 'img', name)
        dst = os.path.join(sdir, 'img', 'n_' + os.path.splitext(name)[0] + '.jpg')
        if os.path.isfile(dst):
            return dst
        try:
            from ..core import imaging
            if imaging.available():
                with open(src, 'rb') as f:
                    out = imaging.process(f.read())
                with open(dst, 'wb') as f:
                    f.write(out['thumb'])
                return dst
        except Exception:
            pass
        return src

    def _existing(self):
        rows = self.db.query("""SELECT p.id, p.sku, p.name, (SELECT COALESCE(NULLIF(i.thumb,''), i.file) FROM product_images i WHERE i.product_id=p.id
            ORDER BY i.sort_order, i.id LIMIT 1) AS img FROM products p""")
        for r in rows:
            r['image_path'] = os.path.join(self.uploads, r['img']) if r['img'] else None
            r['thumb_url'] = '/uploads/' + r['img'] if r['img'] else ''
        return rows

    def _clean(self, s, out):
        kind, items, cur = out['kind'], out['items'], out['currency']
        sdir = s['dir']
        prod = [it for it in items if it['is_product']]
        for it in prod:
            it['key'] = it['row']
            it['image_path'] = self._hash_image(sdir, it['image']) if it.get('image') else None
        existing = self._existing()
        matches = dedupe.find_matches(prod, existing)
        groups = dedupe.find_groups(prod)
        rate = self.products.rates.rate()
        first_in_group = {}
        for gi, grp in enumerate(groups):
            for k in grp:
                first_in_group[k] = (gi, grp[0])
        for it in items:
            it['flags'], it['matches'], it['suggest'] = [], [], None
            if not it['is_product']:
                it['flags'].append({'code': 'fee', 'level': 'info', 'msg': '没有单价/数量的行（运费等）：不建产品，只保留在成交明细里' if kind == 'pi' else '没有单价/数量的行：不导入'})
                continue
            f = it['flags']
            add = lambda code, level, msg: f.append({'code': code, 'level': level, 'msg': msg})
            if not it['name']:
                add('name_missing', 'error', '没有品名')
            if not it['sku']:
                add('sku_missing', 'error', '没有 SKU / 型号')
            elif it.get('sku_from_name'):
                add('sku_from_name', 'info', '文件里没有型号列，先用品名当 SKU（可以改）')
            if not it['quantity'] or it['quantity'] <= 0:
                if kind == 'pi':
                    add('qty_bad', 'error', '数量缺失或不是正数')
            elif it['quantity'] != int(it['quantity']):
                add('qty_frac', 'warn', '数量不是整数：%g' % it['quantity'])
            price = it['unit_price']
            if kind == 'pi':
                if price is None or price <= 0:
                    add('price_missing', 'error', '没有售价')
                elif it['unit_cost_cny'] and cur == 'USD' and it['unit_cost_cny'] * rate > price:
                    add('below_cost', 'warn', '售价低于采购价（按当前汇率折算，亏本）')
            elif kind == 'supplier':
                if (price is None or price <= 0) and not it['unit_cost_cny']:
                    add('cost_missing', 'warn', '没有报价/采购价')
            m = matches.get(it['key'], [])
            it['matches'] = m
            same = next((x for x in m if 'sku_same' in x['reasons']), None)
            strong = [x for x in m if set(x['reasons']) & {'name_high', 'image_high', 'sku_near'}]
            if same:
                it['suggest'] = {'action': 'merge', 'target_id': same['id'], 'needs_decision': False}
                add('exists', 'info', '库里已有同 SKU 的产品「%s」：会并入它（只补空白信息，不覆盖）' % same['name'][:30])
            elif strong:
                it['suggest'] = {'action': 'review', 'target_id': strong[0]['id'], 'needs_decision': True}
                add('similar', 'warn', '和库里 %d 个产品高度相似，请决定：新建 / 合并 / 跳过' % len(strong))
            elif m:
                add('similar_mid', 'info', '和库里的产品有些相似，可对照看看')
            if it['key'] in first_in_group:
                gi, lead = first_in_group[it['key']]
                if it['key'] != lead:
                    it['suggest'] = {'action': 'link_row', 'target_row': lead, 'needs_decision': True}
                    add('dup_in_file', 'warn', '和本文件第 %d 行重复：默认并入那一行' % lead)
                    a = next(x for x in prod if x['key'] == lead)
                    if a['unit_price'] != it['unit_price'] and a['unit_price'] is not None and it['unit_price'] is not None:
                        add('price_conflict', 'warn', '同一个产品两行价格不同（%g / %g），请核对' % (a['unit_price'], it['unit_price']))
        for it in items:                                           # 前端不需要服务器上的路径
            it.pop('image_path', None)
        s['needs'] = {it['row'] for it in items if it['is_product'] and (it['suggest'] or {}).get('needs_decision')}
        s['suggest'] = {it['row']: it['suggest'] for it in items if it['suggest']}
        blocking = len(s['needs'])
        out['summary'] = {'products': len(prod), 'with_image': sum(1 for it in prod if it.get('image')), 'needs_decision': blocking,
                          'errors': sum(1 for it in prod if any(f['level'] == 'error' for f in it['flags'])),
                          'warnings': sum(1 for it in prod if any(f['level'] == 'warn' for f in it['flags']))}
        calc = round(sum((i['amount'] or 0) for i in items if i['amount'] is not None), 2)
        out['sum_matches_total'] = out['total_amount'] is None or abs(calc - out['total_amount']) < 0.01
        out['rate'] = rate
        meta = out['meta']
        out['already_imported'] = bool(meta.get('invoice_no')) and bool(self.db.one('SELECT 1 FROM quotes WHERE quote_no=?', (meta['invoice_no'],)))
        out['matched_customer'], out['customer_candidates'] = self._match_customer(meta)
        out['supplier_candidates'] = self._supplier_candidates(s, out)
        out['categories'] = [{'id': c['id'], 'name': c['name']} for c in self.catalog.categories()]

    def _match_customer(self, meta):
        for e in re.split(r'[\s,;；]+', meta.get('email') or ''):
            e = e.strip().lower()
            if '@' in e:
                r = self.db.one('SELECT id, company, name, emails FROM customers WHERE LOWER(emails) LIKE ? LIMIT 1', ('%' + e + '%',))
                if r:
                    return r, []
        co = (meta.get('buyer') or '').strip()
        if co:
            r = self.db.one('SELECT id, company, name, emails FROM customers WHERE LOWER(company)=LOWER(?) LIMIT 1', (co,))
            if r:
                return r, []
            word = re.split(r'[\s,]+', co)[0]
            if len(word) >= 3:
                return None, self.db.query('SELECT id, company, name, emails FROM customers WHERE company LIKE ? LIMIT 5', ('%' + word + '%',))
        return None, []

    @staticmethod
    def _strip_title(t):
        return re.sub(r'[\s_\-]*(报价单|报价表|报价|quotation|quote|price\s*list|pricelist)[\s_\-]*$', '', t.strip(), flags=re.I).strip()

    def _supplier_candidates(self, s, out):
        """供应商报价单：从表头上方文字 / 文件名里找公司名，和已有供应商档案对一下。"""
        g = s['grid']
        texts = []
        hr = (out.get('header_row') or 1) - 1
        for r in range(min(hr, 12)):
            for t in g.row_texts(r):
                if t and len(t) < 90 and re.search(r'(co\.?,?\s*ltd|limited|公司|厂|factory|trading|lighting|electronic)', t, re.I):
                    texts.append(self._strip_title(t))
        texts.append(self._strip_title(os.path.splitext(s['name'])[0]))
        names = [x['name'] for x in self.db.query('SELECT name FROM suppliers')]
        found = []
        for t in texts:
            for n in names:
                if n.lower() in t.lower() or t.lower() in n.lower():
                    if n not in found:
                        found.append(n)
        return {'existing': found[:5], 'guesses': [t for t in texts[:3] if t not in found]}

    # ---------- 写入 ----------
    def apply(self, sid, d):
        s = self._get(sid)
        res = s.get('parsed')
        if not res:
            raise ApiError('这份文件还没有识别成功，无法导入')
        kind = d.get('kind') or res['kind']
        if kind not in KINDS:
            raise ApiError('文件类型无效')
        cur = (d.get('currency') or res['currency'] or 'USD').upper()
        if cur not in ('USD', 'CNY'):
            raise ApiError('币种只支持 USD / CNY')
        date = (d.get('date') or res['meta'].get('date') or today())
        if not valid_date(date):
            raise ApiError('日期无效（应为 YYYY-MM-DD）')
        src_items = {it['row']: it for it in res['items']}
        sent = d.get('items')
        if not isinstance(sent, list):
            raise ApiError('缺少条目')
        # ---- 1. 整理 + 校验（不写库）----
        plan, errors = [], []
        rowpos = {}
        default_cat = int(d.get('category_id') or 0) or self.db.scalar("SELECT id FROM categories WHERE code='lighting'") or self.db.scalar('SELECT MIN(id) FROM categories')
        self.catalog.require_category(default_cat)
        for it in sent:
            if not isinstance(it, dict):
                continue
            base = src_items.get(it.get('row'))
            if not base:
                errors.append('第 %s 行不在文件里' % it.get('row'))
                continue
            def num(v, name, allow_none=True):
                if v in (None, ''):
                    return None
                try:
                    return float(v)
                except (TypeError, ValueError):
                    errors.append('第 %d 行的%s不是数字' % (base['row'], name))
                    return None
            p = {'row': base['row'], 'is_product': base['is_product'], 'include': it.get('include', True) is not False,
                 'sku': str(it.get('sku') or '').strip(), 'name': str(it.get('name') or '').strip(), 'spec': str(it.get('spec') or '').strip(),
                 'unit': str(it.get('unit') or 'pcs').strip() or 'pcs', 'quantity': num(it.get('quantity'), '数量'), 'unit_price': num(it.get('unit_price'), '单价'),
                 'amount': base.get('amount'), 'cost': num(it.get('unit_cost_cny'), '采购价'), 'moq': base.get('moq'),
                 'image': base.get('image'), 'category_id': int(it.get('category_id') or 0) or default_cat,
                 'decision': it.get('decision') if isinstance(it.get('decision'), dict) else None}
            plan.append(p)
            rowpos[p['row']] = p
        if errors:
            raise ApiError('；'.join(errors[:8]))
        if kind == 'pi':
            cid = d.get('customer_id')
            try:
                cust = self.customers.require(int(cid))
            except (TypeError, ValueError):
                raise ApiError('请先指定客户（PI 要记到哪个客户名下）')
            no = str(d.get('invoice_no') or res['meta'].get('invoice_no') or '').strip()
            if d.get('create_quote', True):
                if not no:
                    raise ApiError('PI 号为空：请填写 PI 号，或取消「生成成交报价单」')
                if self.db.one('SELECT 1 FROM quotes WHERE quote_no=?', (no,)):
                    return {'skipped': True, 'quote_no': no, 'reason': '%s 已导入过，自动跳过（防重复）' % no}
        vendor_name = str(d.get('supplier_name') or '').strip()
        if kind == 'supplier' and not vendor_name:
            raise ApiError('请填写这份报价单是哪家供应商的')
        if kind == 'supplier' and cur != 'CNY':
            raise ApiError('这份供应商报价单的币种是 %s：供应商报价目前只记录人民币。请把币种改成 CNY（价格确实是人民币时），或先把价格换算成人民币' % cur)
        project = str(d.get('project') or '').strip()[:80]
        pending = 0
        for p in plan:
            if not p['is_product'] or not p['include']:
                continue
            dec = p['decision']
            act = (dec or {}).get('action')
            if act not in ('new', 'merge', 'link_row', 'skip'):
                sug = (s.get('suggest') or {}).get(p['row']) or {}
                if p['row'] in (s.get('needs') or ()):
                    pending += 1                                              # 疑似重复，必须由用户决定
                    continue
                p['decision'] = {'action': 'merge', 'target_id': sug['target_id']} if sug.get('action') == 'merge' else {'action': 'new'}
                dec = p['decision']
                act = dec['action']
            if act == 'merge' and not self.db.one('SELECT 1 FROM products WHERE id=?', ((dec or {}).get('target_id'),)):
                errors.append('第 %d 行要合并的产品不存在' % p['row'])
            if act == 'link_row' and (dec or {}).get('target_row') not in rowpos:
                errors.append('第 %d 行要并入的行不存在' % p['row'])
            if p['decision']['action'] == 'new':
                if not p['sku']:
                    errors.append('第 %d 行没有 SKU' % p['row'])
                elif self.db.one('SELECT sku, name FROM products WHERE sku=? COLLATE NOCASE', (p['sku'],)):
                    ex = self.db.one('SELECT sku, name FROM products WHERE sku=? COLLATE NOCASE', (p['sku'],))
                    errors.append('第 %d 行：SKU「%s」库里已经有「%s」，请选「合并到它」或改 SKU' % (p['row'], p['sku'], ex['name'][:20]))
                if not p['name']:
                    errors.append('第 %d 行没有品名' % p['row'])
            if kind == 'pi' and d.get('create_quote', True) and p['include']:
                if p['quantity'] is None or p['quantity'] <= 0 or p['unit_price'] is None or p['unit_price'] <= 0:
                    errors.append('第 %d 行：成交单需要正数的数量和单价' % p['row'])
        seen_new = {}
        for p in plan:
            if p['is_product'] and p['include'] and p['decision'] and p['decision']['action'] == 'new':
                k = p['sku'].lower()
                if k in seen_new:
                    errors.append('第 %d 行和第 %d 行的 SKU「%s」重复，请改成「并入那一行」' % (p['row'], seen_new[k], p['sku']))
                seen_new[k] = p['row']
        if pending:
            raise ApiError('还有 %d 个疑似重复的产品没有决定（新建 / 合并 / 跳过），请逐个确认后再导入' % pending, 409)
        if errors:
            raise ApiError('；'.join(errors[:8]))
        # ---- 2. 写库（一个事务）----
        stats = {'products_new': 0, 'products_merged': 0, 'products_skipped': 0, 'cost_records': 0, 'supplier_quotes': 0}
        src = '%s导入 %s' % (KIND_LABEL[kind], s['name'])
        prod_of_row = {}
        with self.db.tx():
            if kind == 'supplier':
                vid = self.vendors.ensure(vendor_name, status='inquiring')
            for p in plan:
                if not p['is_product'] or not p['include']:
                    continue
                dec = p['decision']
                if dec['action'] == 'skip':
                    stats['products_skipped'] += 1
                    continue
                if dec['action'] == 'link_row':
                    continue                                                  # 第二轮再解析
                cost_cny = p['cost'] if p['cost'] else (p['unit_price'] if kind == 'supplier' and cur == 'CNY' else None)
                if kind == 'list' and p['unit_price']:
                    cost_cny = p['unit_price'] if cur == 'CNY' else None
                if dec['action'] == 'merge':
                    pid = int(dec['target_id'])
                    row = self.db.one('SELECT spec_text FROM products WHERE id=?', (pid,))
                    if not (row['spec_text'] or '').strip() and p['spec']:
                        self.db.execute('UPDATE products SET spec_text=? WHERE id=?', (p['spec'], pid))
                    self._add_image_if_none(pid, s, p)
                    stats['products_merged'] += 1
                else:
                    body = {'sku': p['sku'], 'name': p['name'], 'category_id': p['category_id'], 'spec_text': p['spec'], 'unit': p['unit'],
                            'price_date': date, 'price_source': src}
                    if p['moq']:
                        body['moq'] = p['moq']
                    if cost_cny:
                        body.update(cost=cost_cny, cost_currency='CNY')
                    elif kind == 'list' and p['unit_price'] and cur == 'USD':
                        body.update(cost=p['unit_price'], cost_currency='USD')
                    if kind == 'supplier':
                        body['supplier'] = vendor_name
                    img = self._stage(s, p)
                    if img:
                        body['images'] = [img]
                    pid = self.products.create(body)
                    stats['products_new'] += 1
                    if cost_cny:
                        cost_cny = None                                          # create 已经记了这条成本
                        stats['cost_records'] += 1
                if cost_cny and dec['action'] == 'merge' and kind != 'supplier':
                    if self.history.record(pid, 'cost', cost_cny, 'CNY', effective_date=date, source=src):
                        stats['cost_records'] += 1
                    self.history.realign(pid, use_sell=False)
                prod_of_row[p['row']] = pid
                if kind == 'supplier' and (p['cost'] or p['unit_price']):
                    price = p['cost'] or p['unit_price']
                    if cur == 'CNY' and price:
                        self.product_suppliers.create(pid, {'supplier_name': vendor_name, 'price_cny': price, 'quote_date': date, 'project': project,
                                                            'remark': ('导入自 ' + s['name'])})
                        stats['supplier_quotes'] += 1
            for p in plan:                                                    # link_row：用目标行的产品
                if p['is_product'] and p['include'] and p['decision'] and p['decision']['action'] == 'link_row':
                    t = p['decision']['target_row']
                    if t in prod_of_row:
                        prod_of_row[p['row']] = prod_of_row[t]
                    stats['products_skipped'] += 1
            if kind == 'pi' and d.get('create_quote', True):
                lines = []
                for p in plan:
                    if not p['include']:
                        continue
                    if p['is_product']:
                        lines.append({'product_id': prod_of_row.get(p['row']), 'sku': p['sku'], 'name': p['name'], 'spec': p['spec'],
                                      'quantity': p['quantity'], 'unit': p['unit'], 'unit_price': p['unit_price']})
                    elif p['amount']:
                        lines.append({'product_id': None, 'sku': '', 'name': p['name'] or '(fee)', 'spec': p['spec'], 'quantity': 1, 'unit': 'pcs',
                                      'unit_price': p['amount']})
                q = self.quotes.create_imported(no, cust['id'], cur, date, lines, d.get('notes') or 'PI导入')
                self.customers.add_system_note(cust['id'], '【PI导入】%s（%s）共%d项，合计 %s %.2f' % (no, date, len(lines), cur, q['total']))
                stats.update(quote_no=no, quote_total=q['total'], customer=cust['company'] or cust['name'])
        self.discard(sid)
        return stats

    def _stage(self, s, p):
        if not p.get('image'):
            return None
        path = os.path.join(s['dir'], 'img', p['image'])
        if not os.path.isfile(path):
            return None
        try:
            with open(path, 'rb') as f:
                return self.media.stage_image(f.read())['id']
        except Exception:
            return None

    def _add_image_if_none(self, pid, s, p):
        if self.db.scalar('SELECT COUNT(*) FROM product_images WHERE product_id=?', (pid,)):
            return
        img = self._stage(s, p)
        if img:
            self.media.apply_images(pid, [img])
