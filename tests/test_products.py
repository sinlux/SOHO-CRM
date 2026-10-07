# -*- coding: utf-8 -*-
"""第二批：产品库 + 价格历史 + 供应商比价 + 合并同类项 + SKU + 汇率 + 图片。"""
import base64
import os
import time
import unittest

from helpers import AppTestCase
from legacy_db import build_legacy_db
from sinlux.products import seeds

PNG = 'data:image/png;base64,' + base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'\x00' * 24).decode()
PNG2 = 'data:image/png;base64,' + base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'\x01' * 24).decode()


class TestCatalogSeeds(AppTestCase):
    def test_seed_categories_and_field_counts_match_spec(self):
        cats = {c['code']: c for c in self.c.get('/api/categories')[1]['categories']}
        self.assertEqual({k: cats[k]['field_count'] for k in ('lighting', 'furniture', 'other', 'jewelry')},
                         {'lighting': 15, 'furniture': 21, 'other': 4, 'jewelry': 6})   # 文档：15 / 21 / 4 / 6
        self.assertEqual(cats['jewelry']['name'], '首饰')
        self.assertTrue(all(c['is_builtin'] for c in cats.values()))

    def test_seed_is_idempotent_and_does_not_override_user_edits(self):
        from sinlux.products import catalog
        self.ctx.db.execute("UPDATE categories SET name='我改过的灯饰' WHERE code='lighting'")
        n = self.ctx.db.scalar('SELECT COUNT(*) FROM category_fields')
        self.assertEqual(catalog.ensure_seeds(self.ctx.db), 0)
        self.assertEqual(self.ctx.db.scalar('SELECT COUNT(*) FROM category_fields'), n)
        self.assertEqual(self.ctx.db.one("SELECT name FROM categories WHERE code='lighting'")['name'], '我改过的灯饰')
        self.ctx.db.execute("UPDATE categories SET name='灯饰' WHERE code='lighting'")

    def test_fields_options_parsed(self):
        fields = self.c.get('/api/categories/%d/fields' % self.cat_id('jewelry'))[1]['fields']
        sub = [f for f in fields if f['key'] == 'subcategory'][0]
        self.assertEqual(sub['options_list'], ['项链', '戒指', '耳环', '手链', '其他'])
        self.assertEqual(self.c.get('/api/categories/9999/fields')[0], 404)

    def test_all_structured_fields_optional(self):
        self.assertEqual(self.ctx.db.scalar('SELECT COUNT(*) FROM category_fields WHERE is_required<>0'), 0)


class TestProductCrud(AppTestCase):
    def test_create_get_list_update(self):
        lighting = self.cat_id('lighting')
        fields = {f['key']: f['id'] for f in self.c.get('/api/categories/%d/fields' % lighting)[1]['fields']}
        pid = self.new_product(sku='SKU-1', name='Downlight', cost='100', cost_currency='CNY', profit_rate=0.25,
                               moq='500', lead_time=15, supplier='工厂甲', spec_text='10W 3000K',
                               field_values={str(fields['wattage']): '10', str(fields['cct']): '3000K', str(fields['ip_rating']): ''})
        p = self.c.get('/api/products/%d' % pid)[1]['product']
        self.assertEqual((p['sku'], p['cost'], p['cost_currency'], p['moq'], p['lead_time']), ('SKU-1', 100.0, 'CNY', 500, 15))
        self.assertEqual(p['suggested_price'], round(100 * 0.138 * 1.25, 2))                 # 17.25
        self.assertEqual(p['field_values'], {str(fields['wattage']): '10', str(fields['cct']): '3000K'})   # 空值不存
        self.assertEqual(p['category_name'], '灯饰')
        self.assertEqual(len(p['fields']), 15)
        rows = self.c.get('/api/products?search=downl')[1]
        self.assertEqual(([x['sku'] for x in rows['products']], rows['total']), (['SKU-1'], 1))
        st, r = self.c.put('/api/products/%d' % pid, {'name': 'Downlight 2', 'moq': 800})
        self.assertEqual(st, 200, r)
        q = r['product']
        self.assertEqual((q['name'], q['moq'], q['cost'], q['suggested_price'], q['spec_text']), ('Downlight 2', 800, 100.0, 17.25, '10W 3000K'))
        self.assertEqual(q['field_values'], p['field_values'])           # 没传 field_values 就保持不变

    def test_formula_by_currency_and_rate(self):
        usd = self.new_product(cost=10, cost_currency='USD', profit_rate=0.5)
        self.assertEqual(self.c.get('/api/products/%d' % usd)[1]['product']['suggested_price'], 15.0)
        eur = self.new_product(cost=10, cost_currency='EUR', profit_rate=0.2)
        p = self.c.get('/api/products/%d' % eur)[1]['product']
        self.assertEqual((p['suggested_price'], p['suggested_unconverted']), (12.0, True))     # 沿用旧版：不换算但标注
        zero = self.new_product(cost=0, cost_currency='USD')
        self.assertIsNone(self.c.get('/api/products/%d' % zero)[1]['product']['suggested_price'])
        none = self.new_product()
        p = self.c.get('/api/products/%d' % none)[1]['product']
        self.assertIsNone(p['cost'])
        self.assertEqual(self.ctx.db.scalar('SELECT COUNT(*) FROM price_history WHERE product_id=?', (none,)), 0)

    def test_default_profit_rate_and_currency(self):
        pid = self.new_product(cost=8)
        p = self.c.get('/api/products/%d' % pid)[1]['product']
        self.assertEqual((p['profit_rate'], p['cost_currency']), (0.25, 'CNY'))

    def test_validation(self):
        cat = self.cat_id('lighting')
        base = {'sku': 'V-1', 'name': 'n', 'category_id': cat}
        bad = [({**base, 'sku': ' '}, 'SKU'), ({**base, 'sku': 'x' * 65}, 'SKU'), ({**base, 'name': ''}, '名称'),
               ({**base, 'category_id': None}, '类目'), ({**base, 'category_id': 9999}, '类目'),
               ({**base, 'cost': 'abc'}, '成本'), ({**base, 'cost': -1}, '成本'), ({**base, 'cost': 'nan'}, '成本'),
               ({**base, 'cost_currency': 'XYZ'}, '币种'), ({**base, 'profit_rate': -0.1}, '利润率'),
               ({**base, 'profit_rate': 11}, '利润率'), ({**base, 'moq': 1.5}, 'MOQ'), ({**base, 'moq': -2}, 'MOQ'),
               ({**base, 'lead_time': 'x'}, '交期'), ({**base, 'field_values': {'abc': 'x'}}, '字段'),
               ({**base, 'field_values': {'999999': 'x'}}, '不属于'), ({**base, 'field_values': [1]}, 'field_values'),
               ({**base, 'price_date': '2026-13-01', 'cost': 1}, '日期')]
        for body, kw in bad:
            st, r = self.c.post('/api/products', body)
            self.assertEqual(st, 400 if st != 404 else 404, (body, r))
            self.assertIn(kw, r['error'], (body, r))
        self.assertEqual(self.ctx.db.scalar("SELECT COUNT(*) FROM products WHERE sku='V-1'"), 0)

    def test_field_value_must_belong_to_category(self):
        furn = {f['key']: f['id'] for f in self.c.get('/api/categories/%d/fields' % self.cat_id('furniture'))[1]['fields']}
        st, r = self.c.post('/api/products', {'sku': 'CAT-X', 'name': 'x', 'category_id': self.cat_id('lighting'),
                                              'field_values': {str(furn['material']): '实木'}})
        self.assertEqual(st, 400)
        self.assertIn('不属于', r['error'])

    def test_duplicate_sku(self):
        self.new_product(sku='DUP-1')
        st, r = self.c.post('/api/products', {'sku': 'DUP-1', 'name': 'x', 'category_id': self.cat_id('other')})
        self.assertEqual(st, 409)
        other = self.new_product(sku='DUP-2')
        st, r = self.c.put('/api/products/%d' % other, {'sku': 'DUP-1'})
        self.assertEqual(st, 409)
        st, r = self.c.put('/api/products/%d' % other, {'sku': 'DUP-2'})           # 保持自己的 SKU 不算重复
        self.assertEqual(st, 200)

    def test_changing_category_resets_field_values(self):
        lt = {f['key']: f['id'] for f in self.c.get('/api/categories/%d/fields' % self.cat_id('lighting'))[1]['fields']}
        pid = self.new_product(field_values={str(lt['wattage']): '5'})
        oth = {f['key']: f['id'] for f in self.c.get('/api/categories/%d/fields' % self.cat_id('other'))[1]['fields']}
        st, r = self.c.put('/api/products/%d' % pid, {'category_id': self.cat_id('other'), 'field_values': {str(oth['unit']): 'PCS'}})
        self.assertEqual(st, 200, r)
        self.assertEqual(r['product']['field_values'], {str(oth['unit']): 'PCS'})
        # 换类目却不给 field_values：旧类目的值必须清掉，不能带着无效字段
        pid2 = self.new_product(field_values={str(lt['wattage']): '5'})
        st, r = self.c.put('/api/products/%d' % pid2, {'category_id': self.cat_id('other')})
        self.assertEqual((st, r['product']['field_values']), (200, {}))

    def test_search_filter_escape_pagination(self):
        a = self.new_product(sku='S-100%', name='Percent item', supplier='Zeta Factory')
        b = self.new_product(sku='S-100X', name='Other', category_id=self.cat_id('furniture'), spec_text='contains walnut wood')
        ids = lambda q: [p['id'] for p in self.c.get('/api/products?' + q)[1]['products']]
        self.assertEqual(ids('search=100%25'), [a])               # % 是字面量
        self.assertEqual(ids('search=zeta'), [a])                 # 供应商
        self.assertEqual(ids('search=walnut'), [b])               # 规格描述
        self.assertEqual(ids('category_id=%d&search=S-100' % self.cat_id('furniture')), [b])
        self.assertEqual(self.c.get('/api/products?category_id=x')[0], 400)
        for i in range(12):
            self.new_product(sku='PG-%02d' % i, name='Paged')
        d = self.c.get('/api/products?search=Paged&limit=5&offset=10')[1]
        self.assertEqual((d['total'], len(d['products'])), (12, 2))


class TestPriceHistory(AppTestCase):
    def hist(self, pid, **kw):
        return self.c.get('/api/products/%d/price_history' % pid)[1]['history']

    def test_initial_record_and_dedupe_on_resave(self):
        pid = self.new_product(cost=12.5, cost_currency='CNY', price_date='2026-03-01', price_source='PI 测试')
        h = self.hist(pid)
        self.assertEqual([(x['price_type'], x['price'], x['currency'], x['effective_date'], x['source']) for x in h],
                         [('cost', 12.5, 'CNY', '2026-03-01', 'PI 测试')])
        for _ in range(3):                                           # 连续保存相同成本不产生噪音
            self.c.put('/api/products/%d' % pid, {'cost': 12.5, 'name': 'renamed'})
        self.assertEqual(len(self.hist(pid)), 1)

    def test_cost_change_appends_never_overwrites(self):
        pid = self.new_product(cost=10, cost_currency='CNY', price_date='2026-01-01')
        self.c.put('/api/products/%d' % pid, {'cost': 11, 'price_date': '2026-02-01'})
        self.c.put('/api/products/%d' % pid, {'cost_currency': 'USD'})          # 币种变了也算新记录
        h = self.hist(pid)
        self.assertEqual([(x['price'], x['currency']) for x in h], [(11.0, 'USD'), (11.0, 'CNY'), (10.0, 'CNY')])
        self.assertEqual(sorted(x['effective_date'] for x in h)[0], '2026-01-01')    # 最早的原样保留

    def test_editing_other_fields_does_not_touch_price_or_history(self):
        pid = self.new_product(cost=100, cost_currency='CNY')
        self.ctx.db.execute('UPDATE products SET suggested_price=99.99 WHERE id=?', (pid,))     # 模拟"已对齐到成交价"
        self.c.put('/api/products/%d' % pid, {'name': 'only name changed', 'remark': 'x'})
        p = self.c.get('/api/products/%d' % pid)[1]['product']
        self.assertEqual(p['suggested_price'], 99.99)
        self.assertEqual(len(self.hist(pid)), 1)
        self.c.put('/api/products/%d' % pid, {'profit_rate': 0.5})                # 利润率变了才重算
        self.assertEqual(self.c.get('/api/products/%d' % pid)[1]['product']['suggested_price'], round(100 * 0.138 * 1.5, 2))

    def test_manual_add_and_realign_to_latest(self):
        cust = self.new_customer(company='Hist Buyer')
        pid = self.new_product(cost=10, cost_currency='CNY', price_date='2026-01-01', profit_rate=0.25)
        st, r = self.c.post('/api/products/%d/price_history' % pid, {'price_type': 'sell', 'price': 3.2, 'currency': 'USD',
                                                                      'customer_id': cust, 'effective_date': '2026-03-01', 'source': 'PI-9'})
        self.assertEqual((st, r['recorded']), (200, True), r)
        self.assertEqual(r['product']['suggested_price'], 3.2)                    # 建议价对齐到最新售价
        st, r = self.c.post('/api/products/%d/price_history' % pid, {'price_type': 'cost', 'price': 9, 'currency': 'CNY',
                                                                      'effective_date': '2026-02-01'})
        self.assertEqual(r['product']['cost'], 9.0)
        st, r = self.c.post('/api/products/%d/price_history' % pid, {'price_type': 'cost', 'price': 20, 'currency': 'CNY',
                                                                      'effective_date': '2025-12-01'})   # 更早的记录不改变当前成本
        self.assertEqual(r['product']['cost'], 9.0)
        h = self.hist(pid)
        self.assertEqual(h[0]['customer_company'], 'Hist Buyer')
        self.assertEqual([x['effective_date'] for x in h], sorted([x['effective_date'] for x in h], reverse=True))
        # 重复的相同成本不记
        st, r = self.c.post('/api/products/%d/price_history' % pid, {'price_type': 'cost', 'price': 9, 'currency': 'CNY',
                                                                      'effective_date': '2026-02-02'})
        self.assertEqual(r['recorded'], False)

    def test_delete_record_realigns_and_is_single(self):
        pid = self.new_product(cost=10, cost_currency='CNY', price_date='2026-01-01')
        self.c.post('/api/products/%d/price_history' % pid, {'price_type': 'cost', 'price': 99, 'currency': 'CNY', 'effective_date': '2026-02-01'})
        self.assertEqual(self.c.get('/api/products/%d' % pid)[1]['product']['cost'], 99.0)
        bad = self.hist(pid)[0]
        st, r = self.c.delete('/api/price_history/%d' % bad['id'])
        self.assertEqual((st, r['product']['cost']), (200, 10.0))               # 删掉录错的，当前成本回到上一条
        self.assertEqual(len(self.hist(pid)), 1)
        self.assertEqual(self.c.delete('/api/price_history/%d' % bad['id'])[0], 404)

    def test_manual_validation(self):
        pid = self.new_product(cost=1)
        url = '/api/products/%d/price_history' % pid
        for body in ({'price_type': 'x', 'price': 1}, {'price_type': 'sell', 'price': 'a'}, {'price_type': 'sell', 'price': 0},
                     {'price_type': 'sell', 'price': -1}, {'price_type': 'sell', 'price': 1, 'currency': 'GBP'},
                     {'price_type': 'sell', 'price': 1, 'effective_date': '2026-02-30'},
                     {'price_type': 'cost', 'price': 1, 'customer_id': 1}):
            self.assertEqual(self.c.post(url, body)[0], 400, body)
        self.assertEqual(self.c.post(url, {'price_type': 'sell', 'price': 1, 'customer_id': 987654})[0], 404)
        self.assertEqual(self.c.post('/api/products/987654/price_history', {'price_type': 'sell', 'price': 1})[0], 404)
        self.assertEqual(len(self.hist(pid)), 1)

    def test_last_price_negotiation_hints(self):
        a, b = self.new_customer(company='Buyer A'), self.new_customer(company='Buyer B')
        pid = self.new_product(cost=10, cost_currency='CNY', price_date='2026-01-01')
        add = lambda cid, price, date: self.c.post('/api/products/%d/price_history' % pid, {
            'price_type': 'sell', 'price': price, 'currency': 'USD', 'customer_id': cid, 'effective_date': date, 'source': 'PI-' + date})
        add(a, 2.0, '2026-01-10'); add(a, 2.5, '2026-02-10'); add(b, 3.0, '2026-03-10')
        d = self.c.get('/api/products/%d/last_price?customer_id=%d' % (pid, a))[1]
        self.assertEqual((d['to_this_customer']['price'], d['to_anyone']['price'], d['to_anyone']['company']), (2.5, 3.0, 'Buyer B'))
        self.assertEqual(d['cost_latest']['price'], 10.0)
        d = self.c.get('/api/products/%d/last_price' % pid)[1]               # 没指定客户：只有"他人最近价"
        self.assertEqual((d['to_this_customer'], d['to_anyone']['price']), (None, 3.0))
        c3 = self.new_customer(company='Never bought')
        d = self.c.get('/api/products/%d/last_price?customer_id=%d' % (pid, c3))[1]
        self.assertEqual((d['to_this_customer'], d['to_anyone']['price']), (None, 3.0))


class TestDeleteProduct(AppTestCase):
    def test_confirm_required_then_cascade(self):
        cust = self.new_customer(company='Q buyer')
        pid = self.new_product(cost=10, image_data=PNG)
        img = os.path.basename(self.c.get('/api/products/%d' % pid)[1]['product']['image_path'])
        sid = self.c.post('/api/products/%d/suppliers' % pid, {'supplier_name': 'S1', 'price_cny': 9, 'screenshot_data': PNG2})[1]['suppliers'][0]
        shot = os.path.basename(sid['screenshot_path'])
        qid = self.ctx.db.execute("INSERT INTO quotes(quote_no,customer_id,total) VALUES('Q-DEL',?,10)", (cust,)).lastrowid
        self.ctx.db.execute("INSERT INTO quote_items(quote_id,product_id,sku,name,quantity,unit_price) VALUES(?,?,?,?,1,10)", (qid, pid, 'X', 'Snapshot'))
        st, r = self.c.delete('/api/products/%d' % pid, {})
        self.assertEqual((st, r['needs_confirm'], r['impact']), (409, True, {'price_records': 1, 'supplier_quotes': 1, 'quote_items': 1}))
        self.assertTrue(os.path.exists(os.path.join(self.ctx.uploads_dir, img)))
        self.assertEqual(self.c.delete('/api/products/%d' % pid, {'confirm': True})[0], 200)
        db = self.ctx.db
        for t in ('price_history', 'supplier_quotes'):
            self.assertEqual(db.scalar('SELECT COUNT(*) FROM %s WHERE product_id=?' % t, (pid,)), 0, t)
        self.assertFalse(os.path.exists(os.path.join(self.ctx.uploads_dir, img)))
        self.assertFalse(os.path.exists(os.path.join(self.ctx.uploads_dir, shot)))
        item = db.one('SELECT * FROM quote_items WHERE quote_id=?', (qid,))             # 历史报价明细是快照：保留，仅解除关联
        self.assertEqual((item['name'], item['product_id']), ('Snapshot', None))
        self.assertEqual(db.query('PRAGMA foreign_key_check'), [])
        self.assertEqual(self.c.get('/api/products/%d' % pid)[0], 404)
        self.assertEqual(self.c.delete('/api/products/%d' % pid, {'confirm': True})[0], 404)


class TestMerge(AppTestCase):
    def setup_pair(self):
        cust = self.new_customer(company='Merge buyer')
        lt = {f['key']: f['id'] for f in self.c.get('/api/categories/%d/fields' % self.cat_id('lighting'))[1]['fields']}
        keep = self.new_product(sku='KEEP-1', name='Keep', cost=10, cost_currency='CNY', price_date='2026-01-01', remark='原备注')
        dup = self.new_product(sku='DUP-1', name='Dup', cost=12, cost_currency='CNY', price_date='2026-03-01',
                               spec_text='重复品的规格', image_data=PNG, field_values={str(lt['wattage']): '7'})
        self.c.post('/api/products/%d/price_history' % dup, {'price_type': 'sell', 'price': 3.5, 'currency': 'USD', 'customer_id': cust,
                                                              'effective_date': '2026-03-05', 'source': 'PI-M'})
        self.c.post('/api/products/%d/suppliers' % dup, {'supplier_name': '供应商丙', 'price_cny': 11})
        qid = self.ctx.db.execute("INSERT INTO quotes(quote_no,customer_id,total) VALUES('Q-MRG',?,3.5)", (cust,)).lastrowid
        self.ctx.db.execute("INSERT INTO quote_items(quote_id,product_id,sku,name,quantity,unit_price) VALUES(?,?,?,?,1,3.5)", (qid, dup, 'DUP-1', 'Dup'))
        return keep, dup, qid

    def test_merge_migrates_everything_and_realigns(self):
        keep, dup, qid = self.setup_pair()
        st, r = self.c.post('/api/products/merge', {'survivor_id': keep, 'merge_ids': [dup, dup, keep]})
        self.assertEqual((st, r['merged'], r['survivor_sku']), (200, ['DUP-1'], 'KEEP-1'), r)
        db = self.ctx.db
        self.assertIsNone(db.one('SELECT 1 FROM products WHERE id=?', (dup,)))
        self.assertEqual(db.scalar('SELECT COUNT(*) FROM price_history WHERE product_id=?', (keep,)), 3)     # 2 成本 + 1 售价
        self.assertEqual(db.scalar('SELECT COUNT(*) FROM supplier_quotes WHERE product_id=?', (keep,)), 1)
        self.assertEqual(db.one('SELECT product_id FROM quote_items WHERE quote_id=?', (qid,))['product_id'], keep)
        p = self.c.get('/api/products/%d' % keep)[1]['product']
        self.assertEqual(p['spec_text'], '重复品的规格')                         # 保留者为空 -> 补充
        self.assertTrue(p['image_path'])                                         # 图片补充
        self.assertEqual((p['cost'], p['suggested_price']), (12.0, 3.5))         # 按最新历史对齐：成本取日期最新，建议价取最新售价
        self.assertEqual(p['remark'], '原备注\n已合并同类项: DUP-1')
        self.assertEqual(db.scalar('SELECT COUNT(*) FROM product_field_values WHERE product_id=?', (dup,)), 0)
        self.assertEqual(db.query('PRAGMA foreign_key_check'), [])

    def test_survivor_spec_and_image_not_overwritten(self):
        a = self.new_product(sku='A-1', spec_text='A 的规格', image_data=PNG)
        b = self.new_product(sku='B-1', spec_text='B 的规格', image_data=PNG2)
        b_img = os.path.basename(self.c.get('/api/products/%d' % b)[1]['product']['image_path'])
        self.c.post('/api/products/merge', {'survivor_id': a, 'merge_ids': [b]})
        p = self.c.get('/api/products/%d' % a)[1]['product']
        self.assertEqual(p['spec_text'], 'A 的规格')
        self.assertFalse(os.path.exists(os.path.join(self.ctx.uploads_dir, b_img)))    # 被合并者多余的图片文件不留垃圾

    def test_merge_multiple_and_remark_lists_all(self):
        s = self.new_product(sku='M-S')
        x, y = self.new_product(sku='M-X'), self.new_product(sku='M-Y')
        r = self.c.post('/api/products/merge', {'survivor_id': s, 'merge_ids': [x, y]})[1]
        self.assertEqual(r['merged'], ['M-X', 'M-Y'])
        self.assertEqual(self.c.get('/api/products/%d' % s)[1]['product']['remark'], '已合并同类项: M-X, M-Y')

    def test_merge_validation_and_atomicity(self):
        a, b = self.new_product(), self.new_product()
        self.assertEqual(self.c.post('/api/products/merge', {'merge_ids': [b]})[0], 400)
        self.assertEqual(self.c.post('/api/products/merge', {'survivor_id': a, 'merge_ids': []})[0], 400)
        self.assertEqual(self.c.post('/api/products/merge', {'survivor_id': a, 'merge_ids': [a]})[0], 400)
        st, r = self.c.post('/api/products/merge', {'survivor_id': a, 'merge_ids': [b, 987654]})
        self.assertEqual(st, 404)
        self.assertEqual(self.c.get('/api/products/%d' % b)[0], 200)             # 有一个不存在 -> 整体拒绝，b 还在
        self.assertEqual(self.c.post('/api/products/merge', {'survivor_id': 987654, 'merge_ids': [a]})[0], 404)
        # 中途失败整体回滚
        orig = self.ctx.history.realign
        self.ctx.history.realign = lambda pid: 1 / 0
        try:
            st, r = self.c.post('/api/products/merge', {'survivor_id': a, 'merge_ids': [b]})
        finally:
            self.ctx.history.realign = orig
        self.assertEqual(st, 500)
        self.assertEqual(self.c.get('/api/products/%d' % b)[0], 200)
        self.assertIsNone(self.c.get('/api/products/%d' % a)[1]['product']['remark'])


class TestSupplierQuotes(AppTestCase):
    def test_compare_adopt_syncs_cost_and_history(self):
        pid = self.new_product(cost=20, cost_currency='CNY', profit_rate=0.25, price_date='2026-01-01')
        add = lambda **kw: self.c.post('/api/products/%d/suppliers' % pid, kw)
        self.assertEqual(add(supplier_name='', price_cny=1)[0], 400)
        self.assertEqual(add(supplier_name='X', price_cny='abc')[0], 400)
        self.assertEqual(add(supplier_name='X', price_cny=-3)[0], 400)
        self.assertEqual(add(supplier_name='X', price_cny=3, quote_date='2026-99-01')[0], 400)
        add(supplier_name='甲厂', price_cny=18, quote_date='2026-02-01', remark='含税')
        r = add(supplier_name='乙厂', price_cny=15, quote_date='2026-02-05')[1]
        self.assertEqual(len(r['suppliers']), 2)
        yi = [s for s in r['suppliers'] if s['supplier_name'] == '乙厂'][0]
        r = self.c.post('/api/suppliers/%d/adopt' % yi['id'])[1]
        self.assertEqual([s['supplier_name'] for s in r['suppliers'] if s['is_adopted']], ['乙厂'])
        p = r['product']
        self.assertEqual((p['cost'], p['cost_currency'], p['supplier'], p['suggested_price']), (15.0, 'CNY', '乙厂', round(15 * 0.138 * 1.25, 2)))
        h = self.c.get('/api/products/%d/price_history' % pid)[1]['history']
        self.assertEqual((h[0]['price'], h[0]['source'], h[0]['effective_date'], h[0]['note']),
                         (15.0, '采纳供应商 乙厂', time.strftime('%Y-%m-%d'), '供应商报价日期 2026-02-05'))
        self.c.post('/api/products/%d/price_history' % pid, {'price_type': 'sell', 'price': 9, 'currency': 'USD'})
        self.c.delete('/api/price_history/%d' % self.c.get('/api/products/%d/price_history' % pid)[1]['history'][0]['id'])
        self.assertEqual(self.c.get('/api/products/%d' % pid)[1]['product']['cost'], 15.0)   # 对齐后仍是采纳的价格，不被更早日期的旧成本压过
        jia = [s for s in r['suppliers'] if s['supplier_name'] == '甲厂'][0]
        r = self.c.post('/api/suppliers/%d/adopt' % jia['id'])[1]                 # 改采纳：同一产品只有一个被采纳
        self.assertEqual([s['supplier_name'] for s in r['suppliers'] if s['is_adopted']], ['甲厂'])
        self.assertEqual(r['product']['cost'], 18.0)
        self.assertEqual(len(self.c.get('/api/products/%d/price_history' % pid)[1]['history']), 3)  # 追加，没覆盖

    def test_adopt_without_price_rejected_and_delete(self):
        pid = self.new_product(cost=5)
        r = self.c.post('/api/products/%d/suppliers' % pid, {'supplier_name': '无价', 'screenshot_data': PNG})[1]
        sq = r['suppliers'][0]
        self.assertEqual(self.c.post('/api/suppliers/%d/adopt' % sq['id'])[0], 400)
        path = os.path.join(self.ctx.uploads_dir, os.path.basename(sq['screenshot_path']))
        self.assertTrue(os.path.exists(path))
        self.assertEqual(self.c.delete('/api/suppliers/%d' % sq['id'])[0], 200)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.c.delete('/api/suppliers/%d' % sq['id'])[0], 404)
        self.assertEqual(self.c.post('/api/suppliers/987654/adopt')[0], 404)
        self.assertEqual(self.c.get('/api/products/987654/suppliers')[0], 404)

    def test_create_with_adopt_flag(self):
        pid = self.new_product(cost=50, cost_currency='CNY')
        r = self.c.post('/api/products/%d/suppliers' % pid, {'supplier_name': '直接采纳', 'price_cny': 40, 'is_adopted': True})[1]
        self.assertEqual((r['product']['cost'], r['suppliers'][0]['is_adopted']), (40.0, True))


class TestSku(AppTestCase):
    def test_auto_numbering(self):
        lighting = self.cat_id('lighting')
        nxt = lambda sub: self.c.get('/api/categories/%d/next_sku?subcategory=%s' % (lighting, sub))
        self.assertEqual(nxt('射灯')[1]['sku'], 'SLSP000001')
        self.new_product(sku='SLSP000001'); self.new_product(sku='SLSP000007'); self.new_product(sku='SLSPABCDEF')
        self.new_product(sku='SLSP0000099')        # 7 位数字不是本规则的 SKU，不参与取最大
        self.assertEqual(nxt('射灯')[1]['sku'], 'SLSP000008')       # 取最大+1，不回填空号
        self.assertEqual(nxt('筒灯')[1]['sku'], 'SLDL000001')

    def test_errors(self):
        lighting, jewelry = self.cat_id('lighting'), self.cat_id('jewelry')
        st, r = self.c.get('/api/categories/%d/next_sku?subcategory=' % lighting)
        self.assertEqual(st, 400)
        self.assertIn('子类', r['error'])
        st, r = self.c.get('/api/categories/%d/next_sku?subcategory=不存在的子类' % lighting)
        self.assertIn('尚未配置前缀', r['error'])
        st, r = self.c.get('/api/categories/%d/next_sku?subcategory=项链' % jewelry)
        self.assertEqual(st, 400)
        self.assertIn('总前缀', r['error'])                                # 首饰类目旧版没有预置前缀，不替用户编
        self.assertEqual(self.c.get('/api/categories/9999/next_sku?subcategory=x')[0], 404)

    def test_exhaustion(self):
        lighting = self.cat_id('lighting')
        self.new_product(sku='SLFL999999')
        st, r = self.c.get('/api/categories/%d/next_sku?subcategory=落地灯' % lighting)
        self.assertEqual(st, 400)
        self.assertIn('用尽', r['error'])

    def test_prefix_management(self):
        jewelry = self.cat_id('jewelry')
        self.assertEqual(self.c.put('/api/categories/%d/prefix' % jewelry, {'prefix': 'jw'})[1]['prefix'], 'JW')
        for bad in ('', 'TOOLONG', 'A1', '中文'):
            self.assertEqual(self.c.put('/api/categories/%d/prefix' % jewelry, {'prefix': bad})[0], 400, bad)
        self.assertEqual(self.c.post('/api/categories/%d/sub_prefixes' % jewelry, {'subcategory_value': '项链', 'prefix': 'nl'})[0], 200)
        self.c.post('/api/categories/%d/sub_prefixes' % jewelry, {'subcategory_value': '项链', 'prefix': 'nk'})   # 同名覆盖
        pf = self.c.get('/api/categories/%d/prefixes' % jewelry)[1]
        self.assertEqual((pf['category_prefix'], [(s['subcategory_value'], s['prefix']) for s in pf['subcategories']]), ('JW', [('项链', 'NK')]))
        self.assertEqual(self.c.get('/api/categories/%d/next_sku?subcategory=项链' % jewelry)[1]['sku'], 'JWNK000001')
        self.assertEqual(self.c.delete('/api/sub_prefixes/%d' % pf['subcategories'][0]['id'])[0], 200)
        self.assertEqual(self.c.delete('/api/sub_prefixes/987654')[0], 404)
        self.assertEqual(self.c.post('/api/categories/%d/sub_prefixes' % jewelry, {'subcategory_value': ' ', 'prefix': 'AB'})[0], 400)


class TestRate(AppTestCase):
    def test_default_manual_online(self):
        d = self.c.get('/api/rate')[1]
        self.assertEqual((d['rate'], d['source']), (0.138, 'default'))
        for bad in ('abc', 0, -1, 11, None, ''):
            self.assertEqual(self.c.put('/api/rate', {'rate': bad})[0], 400, bad)
        d = self.c.put('/api/rate', {'rate': '0.142'})[1]
        self.assertEqual((d['rate'], d['source']), (0.142, 'manual'))
        pid = self.new_product(cost=100, cost_currency='CNY', profit_rate=0)
        self.assertEqual(self.c.get('/api/products/%d' % pid)[1]['product']['suggested_price'], 14.2)    # 新汇率用于之后保存的产品
        type(self).rate_result = 0.139
        d = self.c.post('/api/rate/fetch')[1]
        self.assertEqual((d['rate'], d['source']), (0.139, 'online'))
        type(self).rate_result = OSError('网络不通')
        st, r = self.c.post('/api/rate/fetch')
        self.assertEqual(st, 502)
        self.assertIn('网络不通', r['error'])
        self.assertEqual(self.c.get('/api/rate')[1]['rate'], 0.139)             # 失败不改动现有汇率
        type(self).rate_result = 99
        self.assertEqual(self.c.post('/api/rate/fetch')[0], 502)
        self.assertEqual(self.c.get('/api/rate')[1]['rate'], 0.139)
        type(self).rate_result = 0.14


class TestProductImages(AppTestCase):
    def test_upload_replace_remove_serve(self):
        pid = self.new_product(image_data=PNG)
        p = self.c.get('/api/products/%d' % pid)[1]['product']
        self.assertTrue(p['image_path'].startswith('uploads/'))
        self.assertEqual(p['image_url'], '/' + p['image_path'])
        st, raw, h = self.c.call('GET', p['image_url'])
        self.assertEqual((st, h['Content-Type']), (200, 'image/png'))
        old = os.path.join(self.ctx.uploads_dir, os.path.basename(p['image_path']))
        self.c.put('/api/products/%d' % pid, {'image_data': PNG2})
        self.assertFalse(os.path.exists(old))                                       # 换图删旧文件
        p2 = self.c.get('/api/products/%d' % pid)[1]['product']
        self.assertNotEqual(p2['image_path'], p['image_path'])
        self.c.put('/api/products/%d' % pid, {'name': 'keeps image'})
        self.assertEqual(self.c.get('/api/products/%d' % pid)[1]['product']['image_path'], p2['image_path'])
        self.c.put('/api/products/%d' % pid, {'remove_image': True})
        self.assertEqual(self.c.get('/api/products/%d' % pid)[1]['product']['image_path'], None)
        self.assertEqual(len([f for f in os.listdir(self.ctx.uploads_dir) if f == os.path.basename(p2['image_path'])]), 0)

    def test_bad_images_rejected_without_leaving_files_or_rows(self):
        before = set(os.listdir(self.ctx.uploads_dir))
        for bad in ('data:image/svg+xml;base64,' + 'AAAA', 'data:image/png;base64,', 'data:text/html;base64,PGI+'):
            st, r = self.c.post('/api/products', {'sku': 'BADIMG', 'name': 'x', 'category_id': self.cat_id('other'), 'image_data': bad})
            self.assertEqual(st, 400, bad)
        self.assertEqual(self.ctx.db.scalar("SELECT COUNT(*) FROM products WHERE sku='BADIMG'"), 0)
        self.assertEqual(set(os.listdir(self.ctx.uploads_dir)), before)
        # 重复 SKU 时先存了图再失败 -> 不能留孤儿文件
        self.new_product(sku='IMGDUP')
        before = set(os.listdir(self.ctx.uploads_dir))
        self.assertEqual(self.c.post('/api/products', {'sku': 'IMGDUP', 'name': 'x', 'category_id': self.cat_id('other'), 'image_data': PNG})[0], 409)
        self.assertEqual(set(os.listdir(self.ctx.uploads_dir)), before)

    def test_serve_traversal_and_types(self):
        for p in ('/uploads/..%2fcrm.db', '/uploads/x.html', '/uploads/missing.png'):
            st, raw, _ = self.c.call('GET', p)
            self.assertEqual(st, 404, p)
            self.assertNotIn(b'SQLite format', raw)


class TestLegacyProducts(AppTestCase):
    """旧库里的产品、规格值、价格历史、供应商报价，新版必须能读、能改、不丢。"""
    legacy = staticmethod(lambda d: build_legacy_db(d, 10))

    def test_legacy_rows_visible(self):
        d = self.c.get('/api/products')[1]
        self.assertEqual(sorted(p['sku'] for p in d['products']), ['SF-001', 'SL-001', 'SL-002'])
        p = [x for x in d['products'] if x['sku'] == 'SL-001'][0]
        self.assertEqual((p['cost'], p['cost_currency'], p['spec_text'], p['category_name']), (12.5, 'CNY', '规格描述 SL-001', '灯饰'))
        self.assertEqual(p['suggested_price'], 16.25)                 # 旧值原样（12.5*1.3），不被重算
        full = self.c.get('/api/products/%d' % p['id'])[1]['product']
        self.assertEqual(full['field_values'], {'1': '10'})           # 旧规格值，字段 id=1(wattage)
        self.assertEqual(len(full['fields']), 15)                     # 旧库的灯饰类目补齐到 15 个字段
        h = self.c.get('/api/products/%d/price_history' % p['id'])[1]['history']
        self.assertEqual(sorted((x['price_type'], x['price']) for x in h), [('cost', 12.5), ('sell', 2.2)])
        self.assertEqual([s['supplier_name'] for s in self.c.get('/api/products/%d/suppliers' % p['id'])[1]['suppliers']], ['供应商甲', '供应商乙'])
        # 旧库有的 SKU 前缀表数据：灯饰默认前缀被补齐
        self.assertEqual(self.c.get('/api/categories/%d/next_sku?subcategory=射灯' % full['category_id'])[1]['sku'], 'SLSP000001')
        cats = {c['code'] for c in self.c.get('/api/categories')[1]['categories']}
        self.assertEqual(cats, {'lighting', 'furniture', 'other', 'jewelry'})   # 旧库缺的类目自动补上



class TestLegacyProductEdit(AppTestCase):
    legacy = staticmethod(lambda d: build_legacy_db(d, 10))

    def test_edit_legacy_product_and_history_kept(self):
        pid = self.ctx.db.one("SELECT id FROM products WHERE sku='SL-002'")['id']
        st, r = self.c.put('/api/products/%d' % pid, {'cost': 33, 'cost_currency': 'CNY'})
        self.assertEqual(st, 200, r)
        h = self.c.get('/api/products/%d/price_history' % pid)[1]['history']
        self.assertEqual([x['price'] for x in h if x['price_type'] == 'cost'], [33.0, 30.0])   # 旧记录还在，新记录追加



class TestLegacyProductMergeDelete(AppTestCase):
    legacy = staticmethod(lambda d: build_legacy_db(d, 10))

    def test_legacy_merge_and_delete(self):
        a = self.ctx.db.one("SELECT id FROM products WHERE sku='SL-001'")['id']
        b = self.ctx.db.one("SELECT id FROM products WHERE sku='SL-002'")['id']
        st, r = self.c.post('/api/products/merge', {'survivor_id': a, 'merge_ids': [b]})
        self.assertEqual(st, 200, r)
        self.assertEqual(self.ctx.db.scalar("SELECT COUNT(*) FROM price_history WHERE product_id=?", (a,)), 3)
        st, r = self.c.delete('/api/products/%d' % a, {'confirm': True})
        self.assertEqual(st, 200)
        self.assertEqual(self.ctx.db.scalar('SELECT COUNT(*) FROM supplier_quotes'), 0)
        self.assertEqual(self.ctx.db.scalar("SELECT COUNT(*) FROM quote_items WHERE product_id IS NOT NULL AND product_id=?", (a,)), 0)
        self.assertEqual(self.ctx.db.scalar('SELECT COUNT(*) FROM quote_items'), 3)          # 报价明细全保留


if __name__ == '__main__':
    unittest.main()
