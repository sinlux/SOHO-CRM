# -*- coding: utf-8 -*-
"""产品备注截图（+OCR 搜索）、PI 采购价/毛利、以及「升级不动用户数据和设置」。"""
import hashlib
import json
import os
import tempfile
import unittest

from helpers import AppTestCase
from imgutil import PNG_RED_BOX, data_url
from test_imports import make_pi
from test_batch5 import make_zip
from sinlux.core import migrations
from sinlux.core.updater import Updater
from sinlux.products import catalog as catalog_mod


class TestProductShots(AppTestCase):
    def setUp(self):
        self.sh = self.ctx.shots
        self.sh.async_ocr = False
        self.sh.ocr_fn = lambda p: ('done', '这款含税价 35 元 起订量 200 交期 25 天', '')

    def tearDown(self):
        self.sh.async_ocr = True

    def shot(self, pid):
        st, r = self.c.post('/api/products/%d/shots' % pid, {'image_base64': data_url(PNG_RED_BOX)})
        self.assertEqual(st, 200, r)
        return r['shots']

    def test_paste_search_serve_delete(self):
        pid = self.new_product(sku='SH-1', name='Shot lamp', remark='')
        shots = self.shot(pid)
        self.assertEqual((len(shots), shots[0]['ocr_status'], '起订量' in shots[0]['ocr_text']), (1, 'done', True))
        st, raw, h = self.c.call('GET', shots[0]['url'])
        self.assertEqual((st, raw[:4], h['Content-Type'].startswith('image/')), (200, b'\x89PNG', True))
        self.assertEqual(self.c.call('GET', '/product_shots/..%2fcrm.db')[0], 404)
        found = self.c.get('/api/products?search=' + '起订量')[1]['products']
        self.assertIn(pid, [p['id'] for p in found])                                         # 备注截图里的文字能搜到产品
        self.assertNotIn(pid, [p['id'] for p in self.c.get('/api/products?search=' + '完全没有的词')[1]['products']])
        self.assertEqual(self.c.put('/api/product_shots/%d' % shots[0]['id'], {'ocr_text': '手改文字 XYZ789'})[0], 200)
        self.assertIn(pid, [p['id'] for p in self.c.get('/api/products?search=XYZ789')[1]['products']])
        f = [os.path.join(self.sh.dir, n) for n in (shots[0]['file'], shots[0]['thumb_file']) if n]
        self.assertTrue(all(os.path.exists(x) for x in f))
        self.assertEqual(self.c.delete('/api/product_shots/%d' % shots[0]['id'])[0], 200)
        self.assertFalse(any(os.path.exists(x) for x in f))

    def test_validation_delete_product_and_merge(self):
        pid = self.new_product(sku='SH-2')
        self.assertEqual(self.c.post('/api/products/%d/shots' % pid, {'image_base64': 'data:image/exe;base64,AAAA'})[0], 400)
        self.assertEqual(self.c.post('/api/products/%d/shots' % pid, {})[0], 400)
        self.assertEqual(self.c.post('/api/products/999999/shots', {'image_base64': data_url(PNG_RED_BOX)})[0], 404)
        s1 = self.shot(pid)
        files = [os.path.join(self.sh.dir, n) for n in (s1[0]['file'], s1[0]['thumb_file']) if n]
        self.assertEqual(self.c.get('/api/products/%d/impact' % pid)[1]['impact']['note_shots'], 1)
        # 合并：截图并入保留的产品
        keep = self.new_product(sku='SH-KEEP')
        self.assertEqual(self.c.post('/api/products/merge', {'survivor_id': keep, 'merge_ids': [pid]})[0], 200)
        self.assertEqual(len(self.c.get('/api/products/%d/shots' % keep)[1]['shots']), 1)
        self.assertTrue(all(os.path.exists(x) for x in files))
        # 删除产品：截图文件一起清理
        self.assertEqual(self.c.delete('/api/products/%d' % keep, {'confirm': True})[0], 200)
        self.assertFalse(any(os.path.exists(x) for x in files))
        self.assertEqual(self.ctx.db.scalar('SELECT COUNT(*) FROM product_shots WHERE product_id=?', (keep,)), 0)

    def test_ocr_failure_still_keeps_shot(self):
        pid = self.new_product(sku='SH-3')
        self.sh.ocr_fn = lambda p: ('unavailable', '', '本机没有可用的 OCR 引擎')
        s = self.shot(pid)
        self.assertEqual((s[0]['ocr_status'], bool(s[0]['url'])), ('unavailable', True))
        self.sh.ocr_fn = lambda p: (_ for _ in ()).throw(RuntimeError('boom'))
        s = self.shot(pid)
        self.assertEqual(s[1]['ocr_status'], 'failed')


class TestPiMargin(AppTestCase):
    def preview(self, data):
        return self.c.j('POST', '/api/import/pi/preview', raw=data, headers={'X-Filename': 'pi.xls'})

    def test_margin_and_cost_columns(self):
        rate = self.ctx.rates.rate()
        d = self.preview(make_pi(invoice='SL-M1US'))[1]
        a = d['items'][0]                                       # 12.5 USD，rmb 700 / 100 件 = 7 元/件
        self.assertEqual(a['unit_cost_cny'], 7.0)
        self.assertAlmostEqual(a['margin_pct'], round((12.5 - 7 * rate) / 12.5 * 100, 1), places=1)
        self.assertEqual(d['rate'], rate)
        self.assertAlmostEqual(d['profit']['profit_usd'], round(1250 + 400 - (700 + 280) * rate, 2), places=1)
        self.assertNotIn('margin_pct', d['items'][2])           # 运费行没有毛利率

    def test_alt_header_names(self):
        import io
        import xlwt
        wb = xlwt.Workbook(); ws = wb.add_sheet('PI')
        ws.write(1, 0, 'Invoice Number:'); ws.write(1, 2, 'SL-ALT1US'); ws.write(2, 0, 'Issued Date:'); ws.write(2, 2, 46023)
        for c, h in enumerate(['Item No.', 'Product Name', 'Parameters', 'Picture', 'Price/PCS (USD)', 'Quantity', 'Amount', '采购单价']):
            ws.write(11, c, h)
        for c, v in enumerate([1, 'ALT1', 'Code: ALT-1', None, 10, 20, 200, 4.5]):
            if v is not None:
                ws.write(12, c, v)
        ws.write(13, 0, 'Total Payment'); ws.write(13, 6, 200)
        buf = io.BytesIO(); wb.save(buf)
        d = self.preview(buf.getvalue())[1]
        self.assertEqual((d['items'][0]['sku'], d['items'][0]['unit_cost_cny']), ('ALT-1', 4.5))                  # 直接的单件采购价列也认
        wb2 = xlwt.Workbook(); ws2 = wb2.add_sheet('PI')
        ws2.write(1, 0, 'Invoice Number:'); ws2.write(1, 2, 'SL-ALT2US'); ws2.write(2, 0, 'Issued Date:'); ws2.write(2, 2, 46023)
        for c, h in enumerate(['Item No.', 'Product Name', 'Parameters', 'Picture', 'Price/PCS (USD)', 'Quantity', 'Amount', '采购总价']):
            ws2.write(11, c, h)
        for c, v in enumerate([1, 'ALT2', 'Code: ALT-2', None, 10, 20, 200, 90]):
            if v is not None:
                ws2.write(12, c, v)
        ws2.write(13, 0, 'Total Payment'); ws2.write(13, 6, 200)
        buf = io.BytesIO(); wb2.save(buf)
        self.assertEqual(self.preview(buf.getvalue())[1]['items'][0]['unit_cost_cny'], 4.5)                       # 90 ÷ 20


class TestHsDefault(AppTestCase):
    def test_default_url_and_override(self):
        self.assertEqual(self.c.get('/api/settings')[1]['hs_lookup_url'], 'https://www.hsbianma.com/Home/Message')
        self.c.put('/api/settings', {'hs_lookup_url': 'https://x.test/s?q={keyword}'})
        self.assertIn('{keyword}', self.c.get('/api/settings')[1]['hs_lookup_url'])


def tree_hash(root, skip=()):
    out = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in skip]
        for f in files:
            p = os.path.join(d, f)
            with open(p, 'rb') as fh:
                out[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


class TestUpgradeKeepsUserData(AppTestCase):
    """用户最在意的承诺：升级只换程序，不碰 data/（数据、图片、API Key、设置）；数据库迁移只补不改。"""

    def test_update_package_never_touches_data_and_settings(self):
        db = self.ctx.db
        db.set_setting('deepseek_key', 'sk-keep-me-1234'); db.set_setting('tavily_key', 'tvly-keep-me-5678'); db.set_setting('deepseek_model', 'my-model')
        self.c.put('/api/settings/quote', {'company_name': '我的公司', 'bank_swift': 'MYSWIFT1', 'whatsapp_template': 'Hi {customer_name}'})
        self.c.put('/api/rate', {'rate': 0.141})
        pid = self.new_product(sku='KEEP-1', cost=10, cost_currency='CNY', remark='重要备注')
        self.c.post('/api/products/%d/shots' % pid, {'image_base64': data_url(PNG_RED_BOX)})
        for sub in ('images', 'uploads', 'product_shots', 'supplier_files', 'exports'):
            os.makedirs(os.path.join(self.data_dir, sub), exist_ok=True)
            with open(os.path.join(self.data_dir, sub, 'precious.bin'), 'wb') as f:
                f.write(b'USER DATA ' + sub.encode())
        before_settings = {r['key']: r['value'] for r in db.query('SELECT key, value FROM settings')}
        before_app = {r['key']: r['value'] for r in db.query('SELECT key, value FROM app_settings')}
        before_files = tree_hash(self.data_dir, skip=('backups', 'update_tmp', 'import_tmp'))
        before_db = {t: db.scalar('SELECT COUNT(*) FROM %s' % t) for t in ('customers', 'products', 'quotes', 'notes', 'price_history', 'suppliers')}
        # 造一个"程序根目录"，用真实升级流程升一次级（包里连 data/ 都不能碰）
        root = tempfile.mkdtemp(prefix='sinlux_keep_')
        os.makedirs(os.path.join(root, 'app'))
        open(os.path.join(root, 'app', 'main.py'), 'w').write('old')
        open(os.path.join(root, 'app', 'version.txt'), 'w').write('1.0.0')
        real = self.ctx.updater
        self.ctx.updater = Updater(root, self.data_dir, '1.0.0')
        try:
            st, info = self.c.j('POST', '/api/update/upload', raw=make_zip({'app/main.py': 'new', 'app/version.txt': '2.0.0'}, {'version': '2.0.0'}))
            self.assertEqual(st, 200)
            self.assertEqual(self.c.post('/api/update/apply', {'token': info['token']})[0], 200)
            evil = make_zip({'data/crm.db': 'x', 'app/main.py': 'y'})
            self.assertEqual(self.c.j('POST', '/api/update/upload', raw=evil)[0], 400)                    # 想碰 data/ 的包直接拒绝
        finally:
            self.ctx.updater = real
        self.assertEqual(open(os.path.join(root, 'app', 'main.py')).read(), 'new')
        self.assertEqual(before_files, tree_hash(self.data_dir, skip=('backups', 'update_tmp', 'import_tmp')))                # 数据文件一字节没变
        # 新版启动时会重跑迁移和内置种子：设置、数据、API Key 一个都不能变
        migrations.migrate(db)
        catalog_mod.ensure_seeds(db)
        self.assertEqual(before_settings, {r['key']: r['value'] for r in db.query('SELECT key, value FROM settings')})
        self.assertEqual(before_app, {r['key']: r['value'] for r in db.query('SELECT key, value FROM app_settings')})
        self.assertEqual(before_db, {t: db.scalar('SELECT COUNT(*) FROM %s' % t) for t in before_db})
        s = self.c.get('/api/settings')[1]
        self.assertEqual((s['deepseek_key_set'], s['deepseek_key_hint'], s['deepseek_model']), (True, '****1234', 'my-model'))
        q = self.c.get('/api/settings/quote')[1]
        self.assertEqual((q['company_name'], q['bank_swift'], q['whatsapp_template']), ('我的公司', 'MYSWIFT1', 'Hi {customer_name}'))
        self.assertEqual(self.c.get('/api/rate')[1]['rate'], 0.141)
        self.assertEqual(self.c.get('/api/products/%d' % pid)[1]['product']['remark'], '重要备注')


if __name__ == '__main__':
    unittest.main()
