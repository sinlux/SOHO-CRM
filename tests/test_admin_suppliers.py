# -*- coding: utf-8 -*-
"""类目/字段/子类管理 + 供应商档案/沟通记录/OCR。"""
import base64
import os
import unittest

from helpers import AppTestCase
from imgutil import PNG_RED_BOX, data_url
from sinlux.core import migrations
from sinlux.products import catalog as catalog_mod
from sinlux.suppliers import ocr


class TestCatalogAdmin(AppTestCase):
    def cat(self, name='测试类目', **kw):
        st, r = self.c.post('/api/categories', {'name': name, **kw})
        self.assertEqual(st, 200, r)
        return r['id']

    def fields(self, cid):
        return self.c.get('/api/categories/%d/fields' % cid)[1]['fields']

    def test_category_crud(self):
        cid = self.cat('地毯专区', icon='🧶', prefix='RG')
        cats = {c['id']: c for c in self.c.get('/api/categories')[1]['categories']}
        self.assertEqual((cats[cid]['name'], cats[cid]['icon'], cats[cid]['is_builtin']), ('地毯专区', '🧶', 0))
        self.assertEqual([f['key'] for f in self.fields(cid)], ['subcategory', 'dimensions', 'material', 'color', 'remark'])     # 通用起步字段
        self.assertEqual(self.c.get('/api/categories/%d/prefixes' % cid)[1]['category_prefix'], 'RG')
        self.assertEqual(self.c.post('/api/categories', {'name': ' 地毯专区 '})[0], 409)                                          # 重名
        for bad in ({'name': ''}, {'name': 'x' * 31}, {'name': 'a|b'}, {'name': 'ok', 'prefix': 'toolong1'}):
            self.assertEqual(self.c.post('/api/categories', bad)[0], 400, bad)
        self.assertEqual(self.c.put('/api/categories/%d' % cid, {'name': '地毯', 'icon': '🪢'})[0], 200)
        self.assertEqual(self.c.put('/api/categories/%d' % cid, {'name': '家具'})[0], 409)
        self.new_product(category_id=cid, sku='RG-1')
        st, r = self.c.delete('/api/categories/%d' % cid)
        self.assertEqual(st, 409)
        self.assertIn('1 个产品', r['error'])
        self.c.delete('/api/products/%d' % self.ctx.db.scalar("SELECT id FROM products WHERE sku='RG-1'"), {'confirm': True})
        self.assertEqual(self.c.delete('/api/categories/%d' % cid)[0], 200)
        self.assertEqual(self.c.delete('/api/categories/%d' % cid)[0], 404)

    def test_deleted_builtin_stays_deleted(self):
        db = self.ctx.db
        cid = self.cat_id('other')
        self.assertEqual(self.c.delete('/api/categories/%d' % cid)[0], 200)
        catalog_mod.ensure_seeds(db)                                        # 每次启动都会跑
        self.assertIsNone(db.one("SELECT 1 FROM categories WHERE code='other'"))
        fid = next(f['id'] for f in self.fields(self.cat_id('decor')) if f['key'] == 'material' or f['key'] == 'color') if False else None
        dec = self.cat_id('decor')
        f = self.fields(dec)[-1]
        self.assertEqual(self.c.delete('/api/fields/%d' % f['id'])[0], 200)
        catalog_mod.ensure_seeds(db)
        self.assertNotIn(f['key'], [x['key'] for x in self.fields(dec)])

    def test_field_crud_and_validation(self):
        cid = self.cat('字段测试')
        st, r = self.c.post('/api/categories/%d/fields' % cid, {'label': '克重', 'type': 'number', 'unit': 'g'})
        self.assertEqual(st, 200, r)
        fid = r['id']
        self.assertEqual(self.c.post('/api/categories/%d/fields' % cid, {'label': '克重', 'type': 'text'})[0], 409)
        for bad in ({'label': 'x', 'type': 'bogus'}, {'label': 'x', 'type': 'select'}, {'label': '', 'type': 'text'}, {'label': 'x', 'type': 'select', 'options': ['a|b']}):
            st, _ = self.c.post('/api/categories/%d/fields' % cid, bad)
            self.assertIn(st, (400,), bad)
        sel = self.c.post('/api/categories/%d/fields' % cid, {'label': '工艺', 'type': 'select', 'options': ['手工', '机织', '手工']})[1]['id']
        self.assertEqual(next(f for f in self.fields(cid) if f['id'] == sel)['options_list'], ['手工', '机织'])                   # 去重
        self.assertEqual(self.c.put('/api/fields/%d' % sel, {'label': '制作工艺', 'options': ['手工', '机织', '簇绒']})[0], 200)
        self.assertEqual(self.c.put('/api/fields/%d' % fid, {'options': ['x']})[0], 400)                                           # 数字字段没有选项
        pid = self.new_product(category_id=cid, sku='FLD-1', field_values={str(fid): '350', str(sel): '手工'})
        self.assertEqual(self.c.get('/api/fields/%d/usage' % fid)[1]['products_with_value'], 1)
        self.assertEqual(self.c.delete('/api/fields/%d' % fid)[0], 200)
        self.assertNotIn(str(fid), self.c.get('/api/products/%d' % pid)[1]['product']['field_values'])                            # 值随字段一起删
        sub = next(f for f in self.fields(cid) if f['key'] == 'subcategory')
        self.assertEqual(self.c.delete('/api/fields/%d' % sub['id'])[0], 400)                                                      # 子类字段不能删
        self.assertEqual(self.c.put('/api/fields/%d' % sub['id'], {'options': ['x']})[0], 400)
        order = [f['id'] for f in self.fields(cid)]
        self.c.post('/api/fields/%d/move' % order[-1], {'direction': 'up'})
        self.assertEqual([f['id'] for f in self.fields(cid)], order[:-2] + [order[-1], order[-2]])
        self.c.post('/api/fields/%d/move' % order[0], {'direction': 'up'})                                                         # 已在最上面：无变化
        self.assertEqual(self.fields(cid)[0]['id'], order[0])

    def test_subcategories_and_applicability(self):
        cid = self.cat('子类测试', prefix='ZT')
        self.assertEqual(self.c.post('/api/categories/%d/subcategories' % cid, {'name': '大号', 'prefix': 'DH'})[0], 200)
        self.assertEqual(self.c.post('/api/categories/%d/subcategories' % cid, {'name': '大号'})[0], 409)
        self.assertEqual(self.c.post('/api/categories/%d/subcategories' % cid, {'name': '小号', 'prefix': 'DH'})[0], 400)           # 前缀重复
        self.assertEqual(self.c.post('/api/categories/%d/subcategories' % cid, {'name': '小号', 'prefix': 'XH'})[0], 200)
        subs = {s['name']: s for s in self.c.get('/api/categories/%d/subcategories' % cid)[1]['subcategories']}
        self.assertEqual((subs['大号']['prefix'], subs['小号']['prefix']), ('DH', 'XH'))
        self.assertEqual(self.c.get('/api/categories/%d/next_sku?subcategory=%s' % (cid, '大号'))[1]['sku'], 'ZTDH000001')       # SKU 自动编号立刻可用
        sub = next(f for f in self.fields(cid) if f['key'] == 'subcategory')
        self.assertEqual(sub['options_list'], ['大号', '小号'])
        # 适用子类
        fid = self.c.post('/api/categories/%d/fields' % cid, {'label': '边长', 'type': 'text', 'applies_to': ['大号']})[1]['id']
        self.assertEqual(next(f for f in self.fields(cid) if f['id'] == fid)['applies_list'], ['大号'])
        self.assertEqual(self.c.put('/api/fields/%d' % fid, {'applies_to': ['不存在的子类']})[0], 400)
        self.assertEqual(self.c.put('/api/fields/%d' % fid, {'applies_to': []})[0], 200)                                           # 空 = 全部子类
        self.c.put('/api/fields/%d' % fid, {'applies_to': ['大号', '小号']})
        # 改名：产品上的值、前缀、适用范围一起跟着变
        pid = self.new_product(category_id=cid, sku='SUB-1', field_values={str(sub['id']): '大号'})
        self.assertEqual(self.c.put('/api/categories/%d/subcategories' % cid, {'old': '大号', 'new': '特大号'})[0], 200)
        self.assertEqual(self.c.get('/api/products/%d' % pid)[1]['product']['field_values'][str(sub['id'])], '特大号')
        self.assertEqual(self.c.get('/api/categories/%d/next_sku?subcategory=%s' % (cid, '特大号'))[1]['sku'], 'ZTDH000001')
        self.assertEqual(next(f for f in self.fields(cid) if f['id'] == fid)['applies_list'], ['特大号', '小号'])
        self.assertEqual(self.c.put('/api/categories/%d/subcategories' % cid, {'old': '没有', 'new': 'x'})[0], 404)
        # 删除
        st, r = self.c.post('/api/categories/%d/subcategories/delete' % cid, {'name': '特大号'})
        self.assertEqual((st, r['products_still_using']), (200, 1))                                                                 # 提示还有 1 个产品在用，产品数据保留
        self.assertEqual(next(f for f in self.fields(cid) if f['id'] == fid)['applies_list'], ['小号'])
        self.assertEqual(self.c.post('/api/categories/%d/subcategories/delete' % cid, {'name': '特大号'})[0], 404)

    def test_builtin_subcategory_delete_and_rename_not_resurrected(self):
        dec = self.cat_id('decor')
        subs = [s['name'] for s in self.c.get('/api/categories/%d/subcategories' % dec)[1]['subcategories']]
        self.assertIn('毯子', subs)
        self.assertEqual(self.c.post('/api/categories/%d/subcategories/delete' % dec, {'name': '毯子'})[0], 200)
        self.assertEqual(self.c.put('/api/categories/%d/subcategories' % dec, {'old': '枕头', 'new': '靠枕'})[0], 200)
        catalog_mod.ensure_seeds(self.ctx.db)
        subs = [s['name'] for s in self.c.get('/api/categories/%d/subcategories' % dec)[1]['subcategories']]
        self.assertNotIn('毯子', subs)
        self.assertNotIn('枕头', subs)
        self.assertIn('靠枕', subs)
        self.assertEqual(self.c.post('/api/categories/%d/subcategories' % dec, {'name': '毯子', 'prefix': 'BL'})[0], 200)           # 可以再加回来
        catalog_mod.ensure_seeds(self.ctx.db)
        self.assertIn('毯子', [s['name'] for s in self.c.get('/api/categories/%d/subcategories' % dec)[1]['subcategories']])


class TestVendors(AppTestCase):
    def setUp(self):
        self.vb = self.ctx.vendors
        self.vb.async_ocr = False
        self.vb.ocr_fn = lambda path: ('done', '你好，这款射灯的报价是 12 元，起订量 500 个', '')

    def tearDown(self):
        self.vb.async_ocr = True
        self.vb.ocr_fn = ocr.recognize

    def vendor(self, name, **kw):
        st, r = self.c.post('/api/vendors', {'name': name, **kw})
        self.assertEqual(st, 200, r)
        return r['id']

    def chat(self, vid, **kw):
        body = {'image_base64': data_url(PNG_RED_BOX), **kw}
        st, r = self.c.post('/api/vendors/%d/chats' % vid, body)
        self.assertEqual(st, 200, r)
        return r['id']

    def test_crud_validation_and_rename_sync(self):
        vid = self.vendor('东莞甲厂', wechat='wx_jia', status='candidate', rating=4)
        self.assertEqual(self.c.post('/api/vendors', {'name': '东莞甲厂'})[0], 409)
        self.assertEqual(self.c.post('/api/vendors', {'name': 'dongguan jia'.upper()})[0], 200)
        self.assertEqual(self.c.post('/api/vendors', {'name': 'DONGGUAN JIA'})[0], 409)                                              # 不区分大小写
        for bad in ({'name': ''}, {'name': 'x', 'status': 'bogus'}, {'name': 'y', 'rating': 9}, {'name': 'z', 'rating': 'a'}, {'name': 'w' * 101}):
            self.assertEqual(self.c.post('/api/vendors', bad)[0], 400, bad)
        pid = self.new_product(sku='VQ-1')
        self.c.post('/api/products/%d/suppliers' % pid, {'supplier_name': '东莞甲厂', 'price_cny': 10, 'project': '海岛酒店A'})
        self.assertEqual(self.c.put('/api/vendors/%d' % vid, {'name': '东莞甲灯饰厂', 'status': 'cooperating', 'rating': ''})[0], 200)
        v = self.c.get('/api/vendors/%d' % vid)[1]
        self.assertEqual((v['name'], v['status_label'], v['rating'], v['quotes'][0]['price_cny']), ('东莞甲灯饰厂', '合作中', None, 10.0))
        self.assertEqual(self.ctx.db.scalar('SELECT supplier_name FROM supplier_quotes WHERE supplier_id=?', (vid,)), '东莞甲灯饰厂')   # 比价记录里的名字同步
        self.assertEqual(self.c.get('/api/vendors/999999')[0], 404)

    def test_product_quote_creates_vendor_and_project_view(self):
        p1, p2 = self.new_product(sku='PV-1'), self.new_product(sku='PV-2')
        self.c.post('/api/products/%d/suppliers' % p1, {'supplier_name': '新供应商X', 'price_cny': 12, 'project': 'Hotel Caribe'})
        self.c.post('/api/products/%d/suppliers' % p1, {'supplier_name': '新供应商Y', 'price_cny': 9.5, 'project': 'Hotel Caribe'})
        self.c.post('/api/products/%d/suppliers' % p2, {'supplier_name': '新供应商X', 'price_cny': 30})
        names = self.c.get('/api/vendors/names')[1]['names']
        self.assertIn('新供应商X', names)
        self.assertEqual(sum(1 for n in names if n == '新供应商X'), 1)                                                              # 同名不重复建
        x = self.ctx.db.scalar("SELECT id FROM suppliers WHERE name='新供应商X'")
        self.chat(x, project='Hotel Caribe', note='问了灯具')
        view = self.c.get('/api/vendor_projects/view?name=' + 'Hotel Caribe')[1]['suppliers']
        self.assertEqual(sorted(s['name'] for s in view), ['新供应商X', '新供应商Y'])
        sx = next(s for s in view if s['name'] == '新供应商X')
        self.assertEqual((sx['chat_count'], [q['price_cny'] for q in sx['quotes']]), (1, [12.0]))                                    # 只算这个项目的报价
        self.assertIn('Hotel Caribe', [p['project'] for p in self.c.get('/api/vendor_projects')[1]['projects']])
        self.assertEqual(self.c.get('/api/vendor_projects/view?name=')[0], 400)
        names_in_project = [v['name'] for v in self.c.get('/api/vendors?project=Hotel%20Caribe')[1]['vendors']]
        self.assertEqual(sorted(names_in_project), ['新供应商X', '新供应商Y'])

    def test_chat_with_ocr_search_and_files(self):
        vid = self.vendor('OCR 供应商')
        cid = self.chat(vid, project='项目甲', title='旺旺报价', chat_date='2026-10-01')
        c = self.c.get('/api/vendors/%d' % vid)[1]['chats'][0]
        self.assertEqual((c['ocr_status'], '射灯' in c['ocr_text'], c['project'], c['chat_date']), ('done', True, '项目甲', '2026-10-01'))
        st, raw, h = self.c.call('GET', c['image_url'])
        self.assertEqual((st, h['Content-Type'].startswith('image/'), raw[:4]), (200, True, b'\x89PNG'))
        self.assertEqual(self.c.call('GET', '/supplier_files/..%2f..%2fcrm.db')[0], 404)
        self.assertEqual(self.c.call('GET', '/supplier_files/nothing.png')[0], 404)
        res = self.c.get('/api/vendor_chats/search?q=' + '射灯')[1]['chats']
        self.assertEqual((len(res) >= 1, res[0]['supplier_name'], '射灯' in res[0]['snippet']), (True, 'OCR 供应商', True))
        self.assertEqual(self.c.get('/api/vendor_chats/search?q=zzzz不存在')[1]['chats'], [])
        listed = self.c.get('/api/vendors?search=' + '起订量')[1]['vendors']                                                          # 供应商列表也能按聊天文字搜到
        self.assertIn(vid, [v['id'] for v in listed])
        self.assertNotIn(vid, [v['id'] for v in self.c.get('/api/vendors?search=' + '完全没有的词')[1]['vendors']])
        self.assertEqual(self.c.get('/api/vendors?search=100%25')[0], 200)                                                           # % _ 当普通字符，不当通配符
        files = [c['image_file'], c['thumb_file']]
        self.assertTrue(all(os.path.exists(os.path.join(self.vb.dir, f)) for f in files if f))
        # 手动修改识别文字
        self.assertEqual(self.c.put('/api/vendor_chats/%d' % cid, {'ocr_text': '改成了手写文字 品牌ABC'})[0], 200)
        self.assertIn(vid, [v['id'] for v in self.c.get('/api/vendors?search=品牌ABC')[1]['vendors']])
        self.assertEqual(self.c.put('/api/vendor_chats/%d' % cid, {'chat_date': '2026-13-45'})[0], 400)
        # 删除记录：文件一起删
        self.assertEqual(self.c.delete('/api/vendor_chats/%d' % cid)[0], 200)
        self.assertFalse(any(os.path.exists(os.path.join(self.vb.dir, f)) for f in files if f))

    def test_ocr_unavailable_failed_and_not_overwriting_manual(self):
        vid = self.vendor('OCR 状态')
        self.vb.ocr_fn = lambda p: ('unavailable', '', '本机没有可用的 OCR 引擎')
        c1 = self.chat(vid)
        self.vb.ocr_fn = lambda p: ('failed', '', '语言包没装')
        c2 = self.chat(vid)
        self.vb.ocr_fn = lambda p: (_ for _ in ()).throw(RuntimeError('引擎崩了'))
        c3 = self.chat(vid)
        by_id = {c['id']: c for c in self.c.get('/api/vendors/%d' % vid)[1]['chats']}
        self.assertEqual((by_id[c1]['ocr_status'], by_id[c2]['ocr_status'], by_id[c3]['ocr_status']), ('unavailable', 'failed', 'failed'))
        self.assertIn('语言包', by_id[c2]['ocr_error'])
        self.vb.ocr_fn = lambda p: ('done', '重新识别成功', '')
        self.assertEqual(self.c.post('/api/vendor_chats/%d/ocr' % c2)[0], 200)
        self.assertEqual(self.c.get('/api/vendors/%d' % vid)[1]['chats'][1]['ocr_text'] if False else
                         {c['id']: c for c in self.c.get('/api/vendors/%d' % vid)[1]['chats']}[c2]['ocr_text'], '重新识别成功')
        # 后台识别期间用户已手动填了文字：识别结果不能覆盖
        cid = self.chat(vid)
        self.ctx.db.execute("UPDATE supplier_chats SET ocr_status='pending', ocr_text='' WHERE id=?", (cid,))
        self.c.put('/api/vendor_chats/%d' % cid, {'ocr_text': '手写的'})
        self.vb._do_ocr(cid)
        self.assertEqual({c['id']: c for c in self.c.get('/api/vendors/%d' % vid)[1]['chats']}[cid]['ocr_text'], '手写的')

    def test_chat_validation(self):
        vid = self.vendor('校验供应商')
        self.assertEqual(self.c.post('/api/vendors/%d/chats' % vid, {})[0], 400)                                                     # 既没图也没字
        self.assertEqual(self.c.post('/api/vendors/%d/chats' % vid, {'image_base64': 'data:image/png;base64,@@@'})[0] in (400,), True)
        self.assertEqual(self.c.post('/api/vendors/%d/chats' % vid, {'image_base64': 'data:image/exe;base64,AAAA'})[0], 400)
        self.assertEqual(self.c.post('/api/vendors/%d/chats' % vid, {'note': '只有文字', 'chat_date': 'bad'})[0], 400)
        self.assertEqual(self.c.post('/api/vendors/999999/chats', {'note': 'x'})[0], 404)
        st, r = self.c.post('/api/vendors/%d/chats' % vid, {'note': '电话沟通：交期 25 天'})
        self.assertEqual(st, 200)
        self.assertEqual(self.c.get('/api/vendors/%d' % vid)[1]['chats'][0]['ocr_status'], '')                                       # 纯文字记录不做 OCR

    def test_delete_vendor_cascades_files_keeps_quotes(self):
        vid = self.vendor('要删除的供应商')
        self.chat(vid); self.chat(vid)
        pid = self.new_product(sku='DV-1')
        self.c.post('/api/products/%d/suppliers' % pid, {'supplier_name': '要删除的供应商', 'price_cny': 5})
        files = [f for r in self.ctx.db.query('SELECT image_file, thumb_file FROM supplier_chats WHERE supplier_id=?', (vid,)) for f in (r['image_file'], r['thumb_file']) if f]
        st, r = self.c.delete('/api/vendors/%d' % vid)
        self.assertEqual((st, r['needs_confirm'], r['impact']['chats'], r['impact']['quotes']), (409, True, 2, 1))
        self.assertEqual(self.c.delete('/api/vendors/%d' % vid, {'confirm': True})[0], 200)
        self.assertFalse(any(os.path.exists(os.path.join(self.vb.dir, f)) for f in files))
        q = self.ctx.db.one('SELECT * FROM supplier_quotes WHERE product_id=?', (pid,))
        self.assertEqual((q['supplier_name'], q['supplier_id']), ('要删除的供应商', None))                                          # 比价记录保留

    def test_migration_adopts_existing_names(self):
        db = self.ctx.db
        pid = self.new_product(sku='MG-1')
        db.execute("INSERT INTO supplier_quotes(product_id,supplier_name,price_cny) VALUES(?,?,?)", (pid, '  历史供应商Z  ', 7))
        db.execute("UPDATE products SET supplier='只在产品上写过的厂' WHERE id=?", (pid,))
        migrations.migrate(db)
        migrations.migrate(db)                                                                                                      # 幂等
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM suppliers WHERE name IN ('历史供应商Z','只在产品上写过的厂')"), 2)
        self.assertIsNotNone(db.scalar("SELECT supplier_id FROM supplier_quotes WHERE supplier_name LIKE '%历史供应商Z%'"))


class TestOcrText(unittest.TestCase):
    def test_clean_text_removes_cjk_spaces_keeps_english_spaces(self):
        self.assertEqual(ocr.clean_text('你 好 ， 这 款 灯 12 W\nLED  panel light 很 好'), '你好，这款灯 12 W\nLED  panel light 很好')
        self.assertEqual(ocr.clean_text('\n\n  a b \r\n  \n'), 'a b')

    def test_not_available_off_windows(self):
        import sys
        if sys.platform != 'win32':
            self.assertFalse(ocr.available())
            st, text, err = ocr.recognize('/nonexistent.png')
            self.assertEqual((st, text), ('unavailable', ''))


if __name__ == '__main__':
    unittest.main()
