# -*- coding: utf-8 -*-
"""LED 灯饰 / 家具 / 装饰材料（加勒比 FF&E）：SKU 格式预置、产品新属性、状态/排序、批量重算建议价、使用记录。"""
import unittest

from helpers import AppTestCase
from legacy_db import build_legacy_db
from sinlux.products import catalog, seeds


class TestSkuFormats(AppTestCase):
    def sub_options(self, code):
        cid = self.cat_id(code)
        f = [x for x in self.c.get('/api/categories/%d/fields' % cid)[1]['fields'] if x['key'] == 'subcategory'][0]
        return cid, f['options_list']

    def test_every_selectable_subcategory_has_a_prefix_and_vice_versa(self):
        """旧版的隐患：灯饰里有前缀却选不到的子类（吸顶灯…），也有能选却没前缀的（灯带…）。现在两边必须一致。"""
        for code in ('lighting', 'furniture', 'decor'):
            cid, options = self.sub_options(code)
            prefixed = {s['subcategory_value'] for s in self.c.get('/api/categories/%d/prefixes' % cid)[1]['subcategories']}
            self.assertEqual(set(options), prefixed, code)

    def test_default_format_per_business_line(self):
        cases = [('lighting', '射灯', 'SLSP000001'), ('lighting', '灯带', 'SLLS000001'), ('lighting', '吸顶灯', 'SLCL000001'),
                 ('furniture', '沙发', 'GLSF000001'), ('furniture', '床垫', 'GLMT000001'), ('furniture', '户外家具', 'GLOF000001'),
                 ('decor', '毯子', 'DCBL000001'), ('decor', '枕头', 'DCPW000001'), ('decor', '玻璃', 'DCGS000001'),
                 ('decor', '窗帘', 'DCCU000001'), ('decor', '地毯', 'DCRG000001'), ('decor', '其他', 'DCOT000001')]
        for code, sub, sku in cases:
            r = self.c.get('/api/categories/%d/next_sku?subcategory=%s' % (self.cat_id(code), sub))
            self.assertEqual((r[0], r[1]['sku']), (200, sku), (code, sub))

    def test_sequence_increments_per_subcategory(self):
        self.new_product(sku='DCBL000001', category_id=self.cat_id('decor'))
        self.new_product(sku='DCBL000002', category_id=self.cat_id('decor'))
        nxt = lambda sub: self.c.get('/api/categories/%d/next_sku?subcategory=%s' % (self.cat_id('decor'), sub))[1]['sku']
        self.assertEqual(nxt('毯子'), 'DCBL000003')
        self.assertEqual(nxt('枕头'), 'DCPW000001')                 # 子类之间互不影响

    def test_full_prefixes_are_globally_unique(self):
        rows = self.ctx.db.query("""SELECT cp.prefix || sp.prefix AS full FROM subcategory_prefixes sp
            JOIN category_sku_prefixes cp ON cp.category_id=sp.category_id""")
        fulls = [r['full'] for r in rows]
        self.assertEqual(len(fulls), len(set(fulls)))
        self.assertGreater(len(fulls), 55)

    def test_collisions_rejected(self):
        lighting, furniture, other = self.cat_id('lighting'), self.cat_id('furniture'), self.cat_id('other')
        st, r = self.c.post('/api/categories/%d/sub_prefixes' % lighting, {'subcategory_value': '新灯', 'prefix': 'SP'})
        self.assertEqual(st, 400)
        self.assertIn('射灯', r['error'])                              # 同类目前缀重复
        self.c.put('/api/categories/%d/prefix' % other, {'prefix': 'SL'})
        st, r = self.c.post('/api/categories/%d/sub_prefixes' % other, {'subcategory_value': 'x', 'prefix': 'SP'})
        self.assertEqual(st, 400)
        self.assertIn('SLSP', r['error'])                              # 跨类目拼出同一个完整前缀
        self.c.put('/api/categories/%d/prefix' % other, {'prefix': 'OT'})
        st, r = self.c.put('/api/categories/%d/prefix' % furniture, {'prefix': 'SL'})
        self.assertEqual(st, 400)                                      # 家具改成 SL 会和灯饰撞（SL+ST 等）
        self.assertEqual(self.c.get('/api/categories/%d/prefixes' % furniture)[1]['category_prefix'], 'GL')
        st, r = self.c.post('/api/categories/%d/sub_prefixes' % lighting, {'subcategory_value': '射灯', 'prefix': 'SP'})
        self.assertEqual(st, 200)                                      # 同名子类改回自己的前缀不算冲突

    def test_user_customised_prefix_not_overridden_by_seed(self):
        decor = self.cat_id('decor')
        self.c.post('/api/categories/%d/sub_prefixes' % decor, {'subcategory_value': '毯子', 'prefix': 'BK'})
        catalog.ensure_seeds(self.ctx.db)
        pf = {s['subcategory_value']: s['prefix'] for s in self.c.get('/api/categories/%d/prefixes' % decor)[1]['subcategories']}
        self.assertEqual(pf['毯子'], 'BK')
        self.assertEqual(self.c.get('/api/categories/%d/next_sku?subcategory=毯子' % decor)[1]['sku'], 'DCBK000001')
        self.c.post('/api/categories/%d/sub_prefixes' % decor, {'subcategory_value': '毯子', 'prefix': 'BL'})

    def test_seed_options_merge_keeps_user_options(self):
        cid = self.cat_id('furniture')
        fid = self.ctx.db.one("SELECT id FROM category_fields WHERE category_id=? AND field_key='subcategory'", (cid,))['id']
        self.ctx.db.execute('UPDATE category_fields SET options=? WHERE id=?', ('沙发|我自己加的款', fid))
        catalog.ensure_seeds(self.ctx.db)
        opts = self.ctx.db.one('SELECT options FROM category_fields WHERE id=?', (fid,))['options'].split('|')
        self.assertEqual(opts[:2], ['沙发', '我自己加的款'])              # 用户的选项和顺序保留
        self.assertIn('床垫', opts)                                      # 种子里的新选项被补上
        self.assertEqual(len(opts), len(set(opts)))
        self.assertEqual(catalog.ensure_seeds(self.ctx.db), 0)           # 幂等

    def test_decor_category_fields_are_ffe_oriented(self):
        keys = [f['key'] for f in self.c.get('/api/categories/%d/fields' % self.cat_id('decor'))[1]['fields']]
        for k in ('subcategory', 'material', 'dimensions', 'gsm', 'fill', 'fire_rating', 'certifications'):
            self.assertIn(k, keys)
        fr = [f for f in self.c.get('/api/categories/%d/fields' % self.cat_id('decor'))[1]['fields'] if f['key'] == 'fire_rating'][0]
        self.assertEqual((fr['type'], 'NFPA 701' in fr['options_list']), ('multi', True))
        furn = [f['key'] for f in self.c.get('/api/categories/%d/fields' % self.cat_id('furniture'))[1]['fields']]
        self.assertIn('outdoor_use', furn)

    def test_jewelry_still_has_no_invented_prefix(self):
        r = self.c.get('/api/categories/%d/next_sku?subcategory=项链' % self.cat_id('jewelry'))
        self.assertEqual(r[0], 400)
        self.assertIn('总前缀', r[1]['error'])


class TestProductAttributes(AppTestCase):
    def test_new_core_fields_roundtrip_and_defaults(self):
        pid = self.new_product()
        p = self.c.get('/api/products/%d' % pid)[1]['product']
        self.assertEqual((p['status'], p['unit'], p['brand'], p['series'], p['hs_code'], p['origin']), ('active', 'pcs', '', '', '', ''))
        self.assertEqual((p['carton_cbm'], p['unit_cbm']), (None, None))
        self.c.put('/api/products/%d' % pid, {'status': 'draft', 'unit': 'SET', 'brand': 'Sinlux', 'series': 'Palm Collection', 'hs_code': '9405.42',
                                              'origin': 'China', 'pcs_per_carton': 4, 'carton_l': 60, 'carton_w': 40, 'carton_h': 50,
                                              'gross_weight': 12.5, 'net_weight': 11})
        p = self.c.get('/api/products/%d' % pid)[1]['product']
        self.assertEqual((p['status'], p['status_label'], p['unit'], p['brand'], p['hs_code'], p['pcs_per_carton']), ('draft', '草稿', 'SET', 'Sinlux', '9405.42', 4))
        self.assertEqual((p['carton_cbm'], p['unit_cbm']), (0.12, 0.03))          # 60×40×50cm = 0.12 m³；每箱 4 件 → 0.03

    def test_validation(self):
        pid = self.new_product()
        for body in ({'status': 'sold-out'}, {'hs_code': 'abc'}, {'pcs_per_carton': 0}, {'pcs_per_carton': 2.5}, {'carton_l': -1},
                     {'carton_h': 5000}, {'gross_weight': -2}, {'gross_weight': 5, 'net_weight': 6}, {'brand': 'x' * 101}, {'unit': 'x' * 17}):
            self.assertEqual(self.c.put('/api/products/%d' % pid, body)[0], 400, body)
        self.assertEqual(self.c.put('/api/products/%d' % pid, {'gross_weight': 5, 'net_weight': 5})[0], 200)

    def test_filters_sort_search(self):
        a = self.new_product(sku='ZZ-1', name='Banana Lamp', brand='Alpha', series='Tropic', status='active', cost=30, cost_currency='USD')
        b = self.new_product(sku='AA-1', name='Cherry Chair', brand='Beta', series='Orchard', status='draft', cost=10, cost_currency='USD',
                             category_id=self.cat_id('furniture'))
        c = self.new_product(sku='MM-1', name='Apple Pillow', brand='Alpha', status='discontinued', category_id=self.cat_id('decor'))
        ids = lambda q: [p['id'] for p in self.c.get('/api/products?' + q)[1]['products']]
        self.assertEqual(ids('status=draft&search=Chair'), [b])
        self.assertEqual(ids('status=discontinued'), [c])
        self.assertEqual(ids('search=Tropic'), [a])                              # 系列可搜
        self.assertEqual(set(ids('search=Alpha')), {a, c})                       # 品牌可搜
        base = 'search=-1&' if False else ''
        mine = lambda q: [i for i in ids(q + '&limit=1000') if i in (a, b, c)]
        self.assertEqual(mine('sort=sku'), [b, c, a])
        self.assertEqual(mine('sort=name'), [c, a, b])
        self.assertEqual(mine('sort=cost'), [b, a, c])                           # 无成本的排最后
        self.assertEqual(mine('sort=created'), [c, b, a])
        self.assertEqual(self.c.get('/api/products?sort=hack;drop')[0], 400)
        self.assertEqual(self.c.get('/api/products?status=weird')[0], 400)
        self.assertEqual(self.c.get('/api/products?sort=name&search=%25')[0], 200)


class TestRecalcAndUsage(AppTestCase):
    def test_recalc_uses_current_rate_and_skips_sold_products(self):
        self.c.put('/api/rate', {'rate': '0.14'})
        a = self.new_product(cost=100, cost_currency='CNY', profit_rate=0.25)
        b = self.new_product(cost=100, cost_currency='CNY', profit_rate=0.25)
        c = self.new_product(cost=100, cost_currency='USD', profit_rate=0.25)
        cust = self.new_customer(company='Recalc buyer')
        self.c.post('/api/products/%d/price_history' % b, {'price_type': 'sell', 'price': 30, 'currency': 'USD', 'customer_id': cust})
        self.assertEqual(self.c.get('/api/products/%d' % a)[1]['product']['suggested_price'], 17.5)
        self.c.put('/api/rate', {'rate': '0.15'})
        self.assertEqual(self.c.get('/api/products/%d' % a)[1]['product']['suggested_price'], 17.5)       # 汇率变了不会自动改已有产品
        r = self.c.post('/api/products/recalc_prices', {'ids': [a, b, c]})[1]
        self.assertEqual((r['updated'], r['skipped_has_sell_price'], r['rate']), (1, 1, 0.15))
        self.assertEqual(self.c.get('/api/products/%d' % a)[1]['product']['suggested_price'], 18.75)
        self.assertEqual(self.c.get('/api/products/%d' % b)[1]['product']['suggested_price'], 30.0)       # 成交价不动
        self.assertEqual(self.c.get('/api/products/%d' % c)[1]['product']['suggested_price'], 125.0)
        self.assertEqual(self.c.post('/api/products/recalc_prices', {'ids': [a]})[1]['updated'], 0)       # 幂等
        self.assertEqual(self.c.post('/api/products/recalc_prices', {})[0], 200)                          # 不传 ids = 全部

    def test_usage_lists_quotes_containing_product(self):
        cust = self.new_customer(company='Usage buyer')
        pid = self.new_product()
        for no in ('Q-U1', 'Q-U2'):
            qid = self.ctx.db.execute("INSERT INTO quotes(quote_no,customer_id,total,status) VALUES(?,?,10,'sent')", (no, cust)).lastrowid
            self.ctx.db.execute("INSERT INTO quote_items(quote_id,product_id,sku,name,quantity,unit_price) VALUES(?,?,'x','n',5,2)", (qid, pid))
        u = self.c.get('/api/products/%d/usage' % pid)[1]['quotes']
        self.assertEqual(sorted(q['quote_no'] for q in u), ['Q-U1', 'Q-U2'])
        self.assertEqual((u[0]['company'], u[0]['quantity'], u[0]['unit_price']), ('Usage buyer', 5, 2))
        self.assertEqual(self.c.get('/api/products/987654/usage')[0], 404)


class TestLegacyHasNewCategories(AppTestCase):
    legacy = staticmethod(lambda d: build_legacy_db(d, 3))

    def test_legacy_db_gets_decor_and_ffe_prefixes(self):
        cats = {c['code']: c for c in self.c.get('/api/categories')[1]['categories']}
        self.assertEqual(set(cats), {'lighting', 'furniture', 'decor', 'other', 'jewelry'})
        # 旧库里已有的灯饰类目只有 wattage 一个字段：补齐到 15 个，原字段 id 不变
        self.assertEqual(cats['lighting']['field_count'], 15)
        self.assertEqual(self.ctx.db.one("SELECT id FROM category_fields WHERE field_key='wattage'")['id'], 1)
        self.assertEqual(self.c.get('/api/categories/%d/next_sku?subcategory=毯子' % cats['decor']['id'])[1]['sku'], 'DCBL000001')
        self.assertEqual(self.c.get('/api/categories/%d/next_sku?subcategory=沙发' % cats['furniture']['id'])[1]['sku'], 'GLSF000001')
        self.assertEqual(cats['lighting']['sort_order'], 1)


if __name__ == '__main__':
    unittest.main()
