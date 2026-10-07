# -*- coding: utf-8 -*-
"""新版直接打开 v4.4 旧库：数据不丢、脏数据被保全、外键生效、可重复迁移。"""
import os
import shutil
import sqlite3
import unittest

from helpers import AppTestCase
from legacy_db import build_legacy_db, TABLES
from sinlux.core import migrations, schema


def legacy_with_snapshot(very_old=False, n=30):
    def build(data_dir):
        s = build_legacy_db(data_dir, n_customers=n, very_old=very_old)
        shutil.copy(os.path.join(data_dir, 'crm.db'), os.path.join(os.path.dirname(data_dir), 'before.db'))
        return s
    return build


def rows(path, sql):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(sql).fetchall()]
    finally:
        c.close()


class TestLegacyOpen(AppTestCase):
    legacy = staticmethod(legacy_with_snapshot())

    @property
    def before(self):
        return os.path.join(self.tmp, 'before.db')

    @property
    def after(self):
        return os.path.join(self.data_dir, 'crm.db')

    def test_row_counts_preserved(self):
        expect_extra = {'customers': 2,          # 孤儿 998 / 999 的占位客户
                        'price_history': 1}      # 产品3 缺成本历史，补初始记录
        for t in TABLES:
            old = self.legacy_summary[t]
            new = self.ctx.db.scalar('SELECT COUNT(*) FROM %s' % t)
            self.assertEqual(new, old + expect_extra.get(t, 0), '表 %s 行数变了' % t)

    def test_untouched_tables_identical(self):
        for t in ('notes', 'reminders', 'products', 'quotes', 'quote_items', 'supplier_quotes', 'product_field_values',
                  'categories', 'category_fields', 'settings'):
            b = rows(self.before, 'SELECT * FROM %s ORDER BY 1' % t)
            a = rows(self.after, 'SELECT * FROM %s ORDER BY 1' % t)
            if t == 'category_fields':   # is_required 被规范为 0（业务规则：规格字段全部选填）
                for r in b:
                    r['is_required'] = 0
            if t == 'products':          # 迁移只允许补列，不改旧值
                pass
            self.assertEqual(a, b, '表 %s 内容被改动' % t)

    def test_customers_identical_except_stage(self):
        b = rows(self.before, 'SELECT * FROM customers ORDER BY id')
        a = {r['id']: r for r in rows(self.after, 'SELECT * FROM customers ORDER BY id')}
        for r in b:
            n = a[r['id']]
            for k, v in r.items():
                if k == 'stage':
                    continue
                self.assertEqual(n[k], v, '客户 %s 字段 %s 变了' % (r['id'], k))

    def test_stage_normalized_to_chinese(self):
        stages = {r['stage'] for r in self.ctx.db.query('SELECT stage FROM customers')}
        self.assertTrue(stages <= set(schema.STAGES) | {''}, stages)
        # 'won'(英文) 和 '成交'(中文) 合并后，原来 stage 为 won 的客户现在是成交
        before = rows(self.before, "SELECT id FROM customers WHERE stage IN ('won','成交')")
        after = self.ctx.db.query("SELECT id FROM customers WHERE stage='成交' AND id<=30")
        self.assertEqual({r['id'] for r in before}, {r['id'] for r in after})

    def test_orphans_preserved_not_dropped(self):
        for cid in (998, 999):
            c = self.ctx.db.one('SELECT * FROM customers WHERE id=?', (cid,))
            self.assertIn('已删除客户', c['company'])
        st, d = self.c.get('/api/customers/999')
        self.assertEqual(st, 200)
        self.assertEqual([q['quote_no'] for q in d['quotes']], ['OLD-0001'])
        self.assertEqual(d['quotes'][0]['item_count'], 1)
        st, d = self.c.get('/api/customers/998')
        self.assertEqual(len(d['notes']), 1)
        self.assertEqual(len(d['reminders']), 1)

    def test_foreign_keys_enforced(self):
        self.assertEqual(self.ctx.db.scalar('PRAGMA foreign_keys'), 1)
        for t in ('notes', 'reminders', 'enrichments'):
            self.assertTrue(self.ctx.db.has_fk(t, 'customers'), t)
            self.assertEqual(self.ctx.db.query('PRAGMA foreign_key_check(%s)' % t), [])
        with self.assertRaises(sqlite3.IntegrityError):
            self.ctx.db.execute("INSERT INTO notes(customer_id,content) VALUES(424242,'x')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.ctx.db.execute("INSERT INTO quotes(quote_no,customer_id) VALUES('X',424242)")

    def test_migration_idempotent(self):
        before = {t: self.ctx.db.scalar('SELECT COUNT(*) FROM %s' % t) for t in TABLES}
        for _ in range(2):
            rep = migrations.migrate(self.ctx.db)
            self.assertEqual(rep['actions'], [])
        after = {t: self.ctx.db.scalar('SELECT COUNT(*) FROM %s' % t) for t in TABLES}
        self.assertEqual(before, after)

    def test_api_reads_legacy_data(self):
        st, d = self.c.get('/api/customers?limit=1000')
        self.assertEqual(d['total'], 32)
        # 备注全文搜索：zebra5 只命中客户 5
        st, d = self.c.get('/api/customers?search=zebra5')
        self.assertEqual([c['id'] for c in d['customers']], [5])
        st, d = self.c.get('/api/customers?stage=成交')
        self.assertTrue(d['total'] > 0 and all(c['stage'] == '成交' for c in d['customers']))
        # 排序：LV 从高到低
        st, d = self.c.get('/api/customers?limit=1000')
        lvs = [c['lv'] or 0 for c in d['customers']]
        self.assertEqual(lvs, sorted(lvs, reverse=True))
        # 带图备注 + 图片可访问
        st, d = self.c.get('/api/customers/3')
        img = [n['image_path'] for n in d['notes'] if n['image_path']]
        self.assertEqual(img, ['3_1700000000000.png'])
        st, raw, hdr = self.c.call('GET', '/images/3_1700000000000.png')
        self.assertEqual(st, 200)
        self.assertTrue(raw.startswith(b'\x89PNG'))
        self.assertEqual(hdr['Content-Type'], 'image/png')
        # 客户 4 的报价历史
        st, d = self.c.get('/api/customers/4')
        self.assertEqual(sorted(q['quote_no'] for q in d['quotes']), ['SLQ-20260301-001', 'SLQ-20260302-001'])
        self.assertEqual(d['quotes'][0]['status_label'], '成交')

    def test_legacy_settings_kept_and_masked(self):
        self.assertEqual(self.ctx.db.get_setting('deepseek_key'), 'sk-fake-legacy-key-1234')
        st, s = self.c.get('/api/settings')
        self.assertTrue(s['deepseek_key_set'])
        self.assertEqual(s['deepseek_key_hint'], '****1234')
        self.assertNotIn('sk-fake', str(s))

    def test_interrupted_enrichment_cleaned(self):
        st, d = self.c.get('/api/customers/2')
        self.assertEqual([e['status'] for e in d['enrichments']], ['failed'])

    def test_initial_cost_history_backfilled(self):
        # 产品 3 原本没有价格历史
        ph = self.ctx.db.query("SELECT * FROM price_history WHERE product_id=3")
        self.assertEqual(len(ph), 1)
        self.assertEqual((ph[0]['price_type'], ph[0]['price'], ph[0]['currency']), ('cost', 55.0, 'USD'))
        # 已有历史的产品不重复补
        self.assertEqual(self.ctx.db.scalar("SELECT COUNT(*) FROM price_history WHERE product_id=1 AND price_type='cost'"), 1)


class TestLegacyDelete(AppTestCase):
    """删除客户：旧版会留下孤儿报价，新版必须干净级联。"""
    legacy = staticmethod(legacy_with_snapshot())

    def test_delete_cascades_everything(self):
        # 先不带 confirm：必须被拒绝并告知影响范围，数据不动
        st, r = self.c.delete('/api/customers/4', {})
        self.assertEqual(st, 409)
        self.assertTrue(r['needs_confirm'])
        self.assertEqual(r['impact']['quotes'], 2)
        self.assertEqual(self.ctx.db.scalar('SELECT COUNT(*) FROM customers WHERE id=4'), 1)
        st, r = self.c.delete('/api/customers/4', {'confirm': True})
        self.assertEqual(st, 200, r)
        db = self.ctx.db
        self.assertEqual(db.scalar('SELECT COUNT(*) FROM customers WHERE id=4'), 0)
        for t in ('notes', 'reminders', 'enrichments', 'quotes'):
            self.assertEqual(db.scalar('SELECT COUNT(*) FROM %s WHERE customer_id=4' % t), 0, t)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM quote_items WHERE quote_id IN "
                                   "(SELECT id FROM quotes WHERE quote_no LIKE 'SLQ-%')"), 0)
        # 无孤儿报价明细
        self.assertEqual(db.query('PRAGMA foreign_key_check'), [])
        # 价格历史保留，只是客户置空
        ph = db.one("SELECT * FROM price_history WHERE source='PI-001'")
        self.assertIsNotNone(ph)
        self.assertIsNone(ph['customer_id'])
        # 其他客户的报价不受影响
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM quotes WHERE quote_no='OLD-0001'"), 1)

    def test_delete_removes_note_image_file(self):
        p = os.path.join(self.data_dir, 'images', '3_1700000000000.png')
        self.assertTrue(os.path.exists(p))
        st, r = self.c.delete('/api/customers/3', {'confirm': True})
        self.assertEqual(st, 200)
        self.assertFalse(os.path.exists(p))

    def test_delete_placeholder_customer_removes_its_orphan_quote(self):
        st, r = self.c.delete('/api/customers/999', {'confirm': True})
        self.assertEqual(st, 200)
        self.assertEqual(self.ctx.db.scalar("SELECT COUNT(*) FROM quotes WHERE quote_no='OLD-0001'"), 0)

    def test_delete_missing_customer_404(self):
        st, r = self.c.delete('/api/customers/987654', {'confirm': True})
        self.assertEqual(st, 404)


class TestVeryOldDb(AppTestCase):
    """v4.0 之前的库：没有 stage / spec_text 列，也没有 price_history 表。"""
    legacy = staticmethod(legacy_with_snapshot(very_old=True, n=10))

    def test_columns_added_and_data_intact(self):
        db = self.ctx.db
        self.assertTrue(db.column_exists('customers', 'stage'))
        self.assertTrue(db.column_exists('products', 'spec_text'))
        self.assertEqual(db.scalar('SELECT COUNT(*) FROM customers WHERE id<=10'), 10)
        self.assertEqual(db.scalar('SELECT COUNT(*) FROM products'), 3)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM price_history WHERE price_type='cost'"), 3)
        st, d = self.c.get('/api/customers?limit=100&search=Fake Company')
        self.assertEqual(d['total'], 10)
        self.assertTrue(all(c['stage'] == '' for c in d['customers']))
        self.assertEqual(self.ctx.db.scalar("SELECT COUNT(*) FROM customers WHERE company LIKE '%已删除客户%'"), 2)

    def test_can_write_after_migration(self):
        cid = self.new_customer(company='After Migration Co')
        st, r = self.c.put('/api/customers/%d' % cid, {'stage': '成交', 'lv': 6})
        self.assertEqual(st, 200)
        st, d = self.c.get('/api/customers/%d' % cid)
        self.assertEqual((d['customer']['stage'], d['customer']['lv']), ('成交', 6))


class TestFreshDb(AppTestCase):
    def test_empty_db_has_full_schema(self):
        names = {r['name'] for r in self.ctx.db.query("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in TABLES:
            self.assertIn(t, names)
        self.assertEqual(self.ctx.db.scalar('PRAGMA foreign_keys'), 1)
        st, d = self.c.get('/api/customers')
        self.assertEqual((st, d['total'], d['customers']), (200, 0, []))

    def test_fresh_db_fk_cascade_declared(self):
        for t in ('notes', 'reminders', 'enrichments', 'quotes'):
            self.assertTrue(self.ctx.db.has_fk(t, 'customers'), t)


if __name__ == '__main__':
    unittest.main()
