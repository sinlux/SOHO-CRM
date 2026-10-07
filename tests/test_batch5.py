# -*- coding: utf-8 -*-
"""第五批：数据看板、升级包机制、旧 QuoteMaster 迁移。"""
import io
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
import zipfile

from helpers import AppTestCase
from imgutil import PNG_RED_BOX
from sinlux.core.updater import Updater, safe_rel
from sinlux.core.util import ApiError


# ---------------------------------------------------------------- 看板
class TestDashboard(AppTestCase):
    def setUp(self):
        for t in ('quotes', 'customers', 'products'):                      # 每个用例从空库开始（外键级联清掉明细/备注）
            self.ctx.db.execute('DELETE FROM %s' % t)

    def q(self, cid, status, cur='USD', price=10, qty=1, pid=None, sku='X-1', name='Item'):
        st, r = self.c.post('/api/quotes', {'customer_id': cid, 'currency': cur, 'status': status,
                                            'items': [{'product_id': pid, 'sku': sku, 'name': name, 'quantity': qty, 'unit_price': price}]})
        self.assertEqual(st, 200, r)
        return r['id']

    def test_empty_database(self):
        d = self.c.get('/api/dashboard')[1]
        self.assertEqual((d['customers'], d['products'], d['quotes'], d['won'], d['conversion'], d['won_amount_usd']), (0, 0, 0, 0, 0.0, 0))
        self.assertEqual((d['top_customers'], d['top_products'], d['dormant']), ([], [], []))

    def test_numbers_and_rankings(self):
        db = self.ctx.db
        a = self.new_customer(company='Alpha', lv=5, stage='潜在')
        b = self.new_customer(company='Beta', lv=2)
        c = self.new_customer(company='Gamma', lv=6)
        p = self.new_product(sku='DB-1', name='Lamp', cost=10, cost_currency='USD')
        self.q(a, 'accepted', 'USD', 100, 10, p, 'DB-1', 'Lamp')                   # 1000 USD
        self.q(a, 'sent', 'USD', 50, 2, p, 'DB-1', 'Lamp')                         # 100
        self.q(b, 'accepted', 'CNY', 1000, 1, None, 'FRT', 'Freight')              # 1000 CNY ≈ 138 USD
        self.q(b, 'rejected', 'USD', 5, 1, None, 'FRT', 'Freight')
        self.q(c, 'draft', 'USD', 999, 1, None, 'DR', 'Draft only')                # 草稿不计
        d = self.c.get('/api/dashboard')[1]
        self.assertEqual((d['customers'], d['products'], d['quotes'], d['drafts'], d['won']), (3, 1, 4, 1, 2))
        self.assertEqual(d['conversion'], 50.0)                                       # 2 / 4（草稿不进分母）
        self.assertEqual(d['won_amount_by_currency'], {'USD': 1000.0, 'CNY': 1000.0})
        self.assertAlmostEqual(d['won_amount_usd'], 1000 + 1000 * d['rate'], places=2)
        self.assertEqual(d['quotes_30d'], 4)
        tc = d['top_customers']
        self.assertEqual([x['company'] for x in tc], ['Alpha', 'Beta'])               # Gamma 只有草稿，不上榜
        self.assertEqual((tc[0]['won_usd'], tc[0]['quoted_usd'], tc[0]['quote_count']), (1000.0, 1100.0, 2))
        tp = d['top_products']
        self.assertEqual((tp[0]['sku'], tp[0]['amount_usd'], tp[0]['quote_count']), ('DB-1', 1100.0, 2))
        self.assertEqual({s['status']: s['n'] for s in d['status_dist']}, {'draft': 1, 'sent': 1, 'accepted': 2, 'rejected': 1, 'expired': 0})
        self.assertEqual(sum(x['n'] for x in d['stage_dist']), 3)
        self.assertEqual({x['stage']: x['n'] for x in d['stage_dist']}['成交'], 2)    # 成交单自动推进了阶段

    def test_dormant_rules(self):
        db = self.ctx.db
        old = '2025-01-01 10:00:00'
        sleepy = self.new_customer(company='Sleepy LV5', lv=5)
        low = self.new_customer(company='Sleepy LV3', lv=3)
        active_note = self.new_customer(company='Active Note', lv=6)
        active_quote = self.new_customer(company='Active Quote', lv=4)
        for cid in (sleepy, low, active_note, active_quote):
            db.execute('UPDATE notes SET created_at=? WHERE customer_id=?', (old, cid))
        self.c.post('/api/customers/%d/notes' % active_note, {'content': 'called'})
        self.q(active_quote, 'sent')
        db.execute("UPDATE notes SET created_at=? WHERE customer_id=?", (old, active_quote))      # 报价单自动备注也改成很久以前，只看报价日期
        d = self.c.get('/api/dashboard')[1]
        names = [x['company'] for x in d['dormant']]
        self.assertIn('Sleepy LV5', names)
        for n in ('Sleepy LV3', 'Active Note', 'Active Quote'):
            self.assertNotIn(n, names)
        self.assertEqual(d['dormant_total'], len(d['dormant']) if d['dormant_total'] < 20 else d['dormant_total'])

    def test_legacy_other_currency_not_ranked(self):
        cid = self.new_customer(company='Euro Buyer')
        qid = self.q(cid, 'sent', 'USD', 10)
        self.ctx.db.execute("UPDATE quotes SET currency='EUR' WHERE id=?", (qid,))
        d = self.c.get('/api/dashboard')[1]
        self.assertEqual(d['other_currency_quotes'], 1)
        self.assertNotIn('Euro Buyer', [x['company'] for x in d['top_customers'] if x['quoted_usd']])


# ---------------------------------------------------------------- 升级包
def make_zip(files, manifest=None, extra_entries=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        if manifest is not False:
            z.writestr('manifest.json', json.dumps(manifest if manifest is not None else {'version': '9.9.9', 'changelog': 'test'}))
        for n, c in files.items():
            z.writestr(n, c)
        for zi, data in (extra_entries or []):
            z.writestr(zi, data)
    return buf.getvalue()


class UpdaterBase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='sinlux_upd_')
        os.makedirs(os.path.join(self.root, 'app', 'sub'))
        os.makedirs(os.path.join(self.root, 'app', 'libs', 'pkg'))
        os.makedirs(os.path.join(self.root, 'data'))
        for rel, c in {'app/main.py': 'old main', 'app/version.txt': '1.0.0', 'app/sub/mod.py': 'old mod', 'app/libs/pkg/x.py': 'lib old',
                       'start.bat': '@echo old', 'readme.txt': 'old readme', 'data/crm.db': 'PRECIOUS DATA'}.items():
            with open(os.path.join(self.root, *rel.split('/')), 'w', encoding='utf-8') as f:
                f.write(c)
        self.u = Updater(self.root, os.path.join(self.root, 'data'), '1.0.0')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def read(self, rel):
        with open(os.path.join(self.root, *rel.split('/')), encoding='utf-8') as f:
            return f.read()

    def pkg(self, data, name='p.zip'):
        p = os.path.join(self.root, name)
        with open(p, 'wb') as f:
            f.write(data)
        return p


class TestUpdater(UpdaterBase):
    def test_safe_rel(self):
        ok = ['app/main.py', 'app/static/js/a.js', 'start.bat', '启动.bat', 'notes.txt']
        bad = ['data/crm.db', 'versions/x', '../app/x', 'app/../data/x', '/etc/passwd', 'C:/x.bat', 'app\\x.py', 'app', 'app/', 'sub/x.bat',
               'x.py', 'app//x.py', 'app/./x', '', 'app/__pycache__/x.pyc', 'x.bat\x00']
        for r in ok:
            self.assertTrue(safe_rel(r), r)
        for r in bad:
            self.assertFalse(safe_rel(r), r)

    def test_apply_updates_adds_deletes_and_snapshots(self):
        z = make_zip({'app/main.py': 'new main', 'app/version.txt': '2.0.0', 'app/new_mod.py': 'brand new', 'readme.txt': 'new readme', 'app/libs/pkg/x.py': 'lib new'},
                     {'version': '2.0.0', 'changelog': 'big', 'delete': ['app/sub/mod.py']})
        info = self.u.inspect(self.pkg(z))
        self.assertEqual((info['version'], info['changelog'], info['same_version'], sorted(info['delete'])), ('2.0.0', 'big', False, ['app/sub/mod.py']))
        self.assertEqual(self.read('app/main.py'), 'old main')                         # inspect 不改任何文件
        res = self.u.apply(self.pkg(z))
        self.assertEqual((res['applied_version'], res['files_updated'], res['files_deleted'], res['need_restart']), ('2.0.0', 5, 1, True))
        self.assertEqual((self.read('app/main.py'), self.read('app/new_mod.py'), self.read('readme.txt'), self.read('app/libs/pkg/x.py')),
                         ('new main', 'brand new', 'new readme', 'lib new'))
        self.assertFalse(os.path.exists(os.path.join(self.root, 'app', 'sub', 'mod.py')))
        self.assertEqual(self.read('data/crm.db'), 'PRECIOUS DATA')
        snap = os.path.join(self.root, 'versions', res['backup'])
        self.assertEqual(open(os.path.join(snap, 'app', 'main.py'), encoding='utf-8').read(), 'old main')
        self.assertEqual(open(os.path.join(snap, 'app', 'libs', 'pkg', 'x.py'), encoding='utf-8').read(), 'lib old')    # 被动到的 libs 文件进了快照
        self.assertFalse(os.path.exists(os.path.join(snap, 'data')))
        v = self.u.list_versions()
        self.assertEqual((len(v), v[0]['version']), (1, '1.0.0'))

    def test_rollback_restores_everything_and_removes_new_files(self):
        z = make_zip({'app/main.py': 'new main', 'app/new_mod.py': 'brand new', 'app/libs/pkg/x.py': 'lib new'}, {'version': '2.0.0', 'delete': ['app/sub/mod.py']})
        res = self.u.apply(self.pkg(z))
        self.u.current_version = '2.0.0'
        time.sleep(1.1)
        out = self.u.rollback(res['backup'])
        self.assertTrue(out['need_restart'])
        self.assertEqual((self.read('app/main.py'), self.read('app/sub/mod.py'), self.read('app/libs/pkg/x.py')), ('old main', 'old mod', 'lib old'))
        self.assertFalse(os.path.exists(os.path.join(self.root, 'app', 'new_mod.py')))           # 升级新增的文件也被撤掉
        self.assertEqual(self.read('data/crm.db'), 'PRECIOUS DATA')
        names = [v['name'] for v in self.u.list_versions()]
        self.assertIn(out['pre_rollback_backup'], names)                                          # 回退前快照了当前状态，能再回去
        self.u.rollback(out['pre_rollback_backup'])
        self.assertEqual(self.read('app/main.py'), 'new main')

    def test_rejects_dangerous_packages(self):
        cases = {
            'touch data': make_zip({'data/crm.db': 'x'}),
            'traversal': make_zip({}, extra_entries=[('app/../../evil.py', 'x')]),
            'absolute': make_zip({}, extra_entries=[('/etc/cron.d/x', 'x')]),
            'backslash': make_zip({}, extra_entries=[('app\\x.py', 'x')]),
            'drive': make_zip({}, extra_entries=[('C:/x.bat', 'x')]),
            'root py': make_zip({'evil.py': 'x'}),
            'versions dir': make_zip({'versions/x.txt': 'x'}),
            'delete data': make_zip({}, {'version': '2', 'delete': ['data/crm.db']}),
            'delete traversal': make_zip({}, {'version': '2', 'delete': ['app/../data/crm.db']}),
            'no manifest': make_zip({'app/x.py': 'x'}, manifest=False),
            'bad manifest json': make_zip({}, manifest=None, extra_entries=[]).replace(b'"version"', b'"v"') if False else b'x',
            'manifest not dict': make_zip({}, manifest=['a']),
            'bad version': make_zip({}, {'version': '../../x'}),
            'version number': make_zip({}, {'version': 2}),
            'delete not list': make_zip({}, {'version': '2', 'delete': 'app/x'}),
            'changelog not str': make_zip({}, {'version': '2', 'changelog': {'a': 1}}),
            'not a zip': b'PK\x03\x04garbage',
        }
        before = {r: self.read(r) for r in ('app/main.py', 'data/crm.db')}
        for name, data in cases.items():
            with self.assertRaises(ApiError, msg=name):
                self.u.apply(self.pkg(data))
        self.assertEqual(before, {r: self.read(r) for r in before})
        self.assertFalse(os.path.exists(os.path.join(self.root, '..', 'evil.py')))
        self.assertEqual(self.u.list_versions(), [])                                              # 被拒绝的包连快照都不会产生

    def test_symlink_and_duplicate_entries_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('manifest.json', json.dumps({'version': '2.0.0'}))
            zi = zipfile.ZipInfo('app/link.py')
            zi.external_attr = (0o120777 << 16)
            z.writestr(zi, '/etc/passwd')
        with self.assertRaises(ApiError):
            self.u.inspect(self.pkg(buf.getvalue()))
        import warnings
        buf = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with zipfile.ZipFile(buf, 'w') as z:
                z.writestr('manifest.json', json.dumps({'version': '2.0.0'}))
                z.writestr('app/a.py', '1'); z.writestr('app/a.py', '2')
        with self.assertRaises(ApiError):
            self.u.inspect(self.pkg(buf.getvalue()))

    def test_rollback_validation(self):
        for bad in ('../x', 'nope', '', None, '20260101_000000_v1/../..'):
            with self.assertRaises(ApiError, msg=bad):
                self.u.rollback(bad)
        with self.assertRaises(ApiError):
            self.u.rollback('20260101_000000_v1.0.0')                                              # 格式对但不存在

    def test_failure_midway_restores(self):
        z = make_zip({'app/main.py': 'new main', 'app/sub/mod.py': 'new mod'}, {'version': '2.0.0'})
        os.remove(os.path.join(self.root, 'app', 'sub', 'mod.py'))
        os.rmdir(os.path.join(self.root, 'app', 'sub'))
        with open(os.path.join(self.root, 'app', 'sub'), 'w') as f:                                  # 'app/sub' 是个文件 → 写 app/sub/mod.py 必然失败
            f.write('i am a file')
        with self.assertRaises(ApiError) as cm:
            self.u.apply(self.pkg(z))
        self.assertIn('自动还原', cm.exception.message)
        self.assertEqual(self.read('app/main.py'), 'old main')


class TestUpdateApi(AppTestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='sinlux_updapi_')
        os.makedirs(os.path.join(self.root, 'app'))
        open(os.path.join(self.root, 'app', 'main.py'), 'w').write('old')
        open(os.path.join(self.root, 'app', 'version.txt'), 'w').write('1.0.0')
        self.real = self.ctx.updater
        self.ctx.updater = Updater(self.root, self.data_dir, '1.0.0')

    def tearDown(self):
        self.ctx.updater = self.real
        shutil.rmtree(self.root, ignore_errors=True)

    def test_upload_apply_rollback_flow(self):
        z = make_zip({'app/main.py': 'new', 'app/version.txt': '2.0.0'}, {'version': '2.0.0', 'changelog': 'hello'})
        st, info = self.c.j('POST', '/api/update/upload', raw=z)
        self.assertEqual((st, info['version'], info['changelog']), (200, '2.0.0', 'hello'))
        self.assertEqual(open(os.path.join(self.root, 'app', 'main.py')).read(), 'old')            # 上传只做校验
        st, r = self.c.post('/api/update/apply', {'token': info['token']})
        self.assertEqual((st, r['applied_version'], r['need_restart']), (200, '2.0.0', True))
        self.assertTrue(os.path.exists(os.path.join(self.ctx.backups_dir, r['data_backup'])))      # 升级前自动备份了数据
        self.assertEqual(open(os.path.join(self.root, 'app', 'main.py')).read(), 'new')
        self.assertEqual(self.c.post('/api/update/apply', {'token': info['token']})[0], 404)       # 包用完即删
        vs = self.c.get('/api/update/versions')[1]
        self.assertEqual(len(vs['versions']), 1)
        st, r = self.c.post('/api/update/rollback', {'name': vs['versions'][0]['name']})
        self.assertEqual(st, 200, r)
        self.assertEqual(open(os.path.join(self.root, 'app', 'main.py')).read(), 'old')

    def test_bad_inputs(self):
        self.assertEqual(self.c.j('POST', '/api/update/upload', raw=b'junk')[0], 400)
        self.assertEqual(os.listdir(os.path.join(self.data_dir, 'update_tmp')) if os.path.isdir(os.path.join(self.data_dir, 'update_tmp')) else [], [])
        for tok in ('../../etc/passwd', 'update_1.zip/../x', '', None, 'x.zip', '/abs/update_1.zip'):
            self.assertEqual(self.c.post('/api/update/apply', {'token': tok})[0], 400, tok)
        self.assertEqual(self.c.post('/api/update/rollback', {'name': '../x'})[0], 400)
        evil = make_zip({'data/crm.db': 'x'})
        self.assertEqual(self.c.j('POST', '/api/update/upload', raw=evil)[0], 400)


# ---------------------------------------------------------------- 旧 QuoteMaster 迁移
QM_SCHEMA = """
CREATE TABLE categories(id INTEGER PRIMARY KEY, code TEXT, name TEXT, icon TEXT, sort_order INTEGER, is_builtin INTEGER);
CREATE TABLE category_fields(id INTEGER PRIMARY KEY, category_id INTEGER, field_key TEXT, field_label TEXT, field_type TEXT, is_required INTEGER,
  options TEXT, unit TEXT, placeholder TEXT, sort_order INTEGER);
CREATE TABLE products(id INTEGER PRIMARY KEY, sku TEXT, name TEXT, category_id INTEGER, image_path TEXT, cost REAL, cost_currency TEXT, profit_rate REAL,
  suggested_price REAL, moq INTEGER, lead_time INTEGER, supplier TEXT, remark TEXT, created_at TEXT);
CREATE TABLE product_field_values(id INTEGER PRIMARY KEY, product_id INTEGER, field_id INTEGER, value TEXT);
CREATE TABLE supplier_quotes(id INTEGER PRIMARY KEY, product_id INTEGER, supplier_name TEXT, price_cny REAL, quote_date TEXT, screenshot_path TEXT,
  remark TEXT, is_adopted INTEGER, created_at TEXT);
CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT, company TEXT, email TEXT, whatsapp TEXT, country TEXT, address TEXT, customer_level INTEGER,
  remark TEXT, website TEXT);
CREATE TABLE quotes(id INTEGER PRIMARY KEY, customer_id INTEGER, product_id INTEGER, price REAL, quantity REAL, currency TEXT, status TEXT, valid_days INTEGER,
  lead_time TEXT, message_text TEXT, customer_feedback TEXT, created_at TEXT, updated_at TEXT);
"""


def build_qm(path, uploads):
    os.makedirs(uploads, exist_ok=True)
    with open(os.path.join(uploads, 'p1.png'), 'wb') as f:
        f.write(PNG_RED_BOX)
    with open(os.path.join(uploads, 'shot.png'), 'wb') as f:
        f.write(PNG_RED_BOX)
    c = sqlite3.connect(path)
    c.executescript(QM_SCHEMA)
    c.executescript("""
    INSERT INTO categories VALUES(1,'lighting','灯饰','💡',1,1),(2,'qm_special','旧版专属类目','⭐',9,0);
    INSERT INTO category_fields VALUES(1,1,'wattage','瓦数','number',1,NULL,'W',NULL,1),(2,2,'color','颜色','text',0,NULL,NULL,NULL,1),
      (3,1,'qm_only','旧版独有字段','text',0,NULL,NULL,NULL,5);
    INSERT INTO products VALUES(1,'QM-001','Old Downlight',1,'/uploads/p1.png',12.5,'CNY',0.3,5.2,100,30,'甲厂','old remark','2024-05-01 10:00:00'),
      (2,'QM-002','Old Special',2,'/uploads/missing.png',3.0,'USD',0.25,3.9,NULL,NULL,NULL,NULL,'2024-06-01 10:00:00'),
      (3,'qm-001','Case dup of QM-001',1,NULL,9,'CNY',0.2,NULL,NULL,NULL,NULL,NULL,'2024-06-02 10:00:00');
    INSERT INTO product_field_values VALUES(1,1,1,'10'),(2,2,2,'red'),(3,1,3,'only-old');
    INSERT INTO supplier_quotes VALUES(1,1,'乙厂',11.2,'2024-05-02','/uploads/shot.png','cheaper',1,'2024-05-02'),(2,99,'ghost',1,NULL,NULL,NULL,0,'2024-05-02');
    INSERT INTO customers VALUES(1,'Old Anna','Old Co One','anna@oldco.com; sales@oldco.com','+1 555 1','USA','1 Old St',4,'VIP customer','www.oldco.com'),
      (2,'Mia','Existing Co','mia@existing.com','+44 7','UK','Moved Addr',3,'old remark text',''),
      (3,'Nobody','','','','','',NULL,'',''),
      (4,'','','','','','',9,'',''),
      (5,'Ben','Same Name Co','ben@other.com','','','',2,'','');
    INSERT INTO quotes VALUES(1,1,1,15.5,100,'USD','accepted',30,'30 days','msg one','great','2024-07-01 09:00:00','2024-07-02 09:00:00'),
      (2,1,2,3.333,3,'USD','weird_status',15,NULL,NULL,NULL,'2024-07-05 09:00:00',NULL),
      (3,2,1,16.0,10,'EUR','sent',30,'20','',NULL,'2024-07-06 09:00:00','2024-07-06 09:00:00'),
      (4,77,1,1,1,'USD','sent',30,'','',NULL,'2024-07-06 09:00:00','2024-07-06 09:00:00');
    """)
    c.commit()
    return c


class TestMigrateQuoteMaster(AppTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.old_dir = tempfile.mkdtemp(prefix='sinlux_qm_')
        cls.old = build_qm(os.path.join(cls.old_dir, 'quotes.db'), os.path.join(cls.old_dir, 'uploads'))

    @classmethod
    def tearDownClass(cls):
        cls.old.close()
        shutil.rmtree(cls.old_dir, ignore_errors=True)
        super().tearDownClass()

    def run_migration(self):
        from sinlux.migrate.quotemaster import migrate
        return migrate(self.ctx, self.old, os.path.join(self.old_dir, 'uploads'))

    def test_01_migrate_everything(self):
        existing = self.new_customer(company='Existing Co', emails='mia@existing.com', address='')
        same_name = self.new_customer(company='same name co', emails='x@x.com')
        res = self.run_migration()
        st, db = res['stats'], self.ctx.db
        self.assertEqual((st['产品(新迁入)'], st['客户(新迁入)'], st['客户(信息合并)'], st['报价历史'], st['供应商比价'], st['产品图']), (2, 2, 1, 3, 1, 1))
        # 类目/字段：同 code 合并，没有的新建
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM categories WHERE code='lighting'"), 1)
        self.assertEqual(db.one("SELECT name FROM categories WHERE code='qm_special'")['name'], '旧版专属类目')
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM category_fields f JOIN categories c ON c.id=f.category_id WHERE c.code='lighting' AND f.field_key='qm_only'"), 1)
        # 产品：大小写不同的 SKU 视为已存在，不重复
        p = db.one("SELECT * FROM products WHERE sku='QM-001'")
        self.assertEqual((p['name'], p['cost'], p['cost_currency'], p['profit_rate'], p['moq'], p['supplier']), ('Old Downlight', 12.5, 'CNY', 0.3, 100, '甲厂'))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM products WHERE sku='qm-001' COLLATE NOCASE"), 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM price_history WHERE product_id=? AND price_type='cost'", (p['id'],)), 1)
        self.assertEqual(db.scalar("SELECT value FROM product_field_values v JOIN category_fields f ON f.id=v.field_id WHERE v.product_id=? AND f.field_key='wattage'", (p['id'],)), '10')
        self.assertEqual(db.scalar('SELECT COUNT(*) FROM product_images WHERE product_id=? AND normalized=1', (p['id'],)), 1)       # 图片进相册并规范化
        self.assertTrue(any('missing.png' in w for w in res['warnings']))                                                               # 找不到的图给提示，不中断
        # 供应商比价（含截图），幽灵产品的比价被忽略
        sq = db.one('SELECT * FROM supplier_quotes WHERE product_id=?', (p['id'],))
        self.assertEqual((sq['supplier_name'], sq['price_cny'], sq['is_adopted']), ('乙厂', 11.2, 1))
        self.assertTrue(os.path.exists(os.path.join(self.ctx.data_dir, sq['screenshot_path'])))
        # 客户：按邮箱合并 + 补空字段 + 备注；公司名不区分大小写合并；邮箱含分号也能拆
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM customers WHERE LOWER(company)='same name co'"), 1)
        mia = db.one('SELECT * FROM customers WHERE id=?', (existing,))
        self.assertEqual((mia['address'], mia['whatsapp']), ('Moved Addr', '+44 7'))
        self.assertIn('旧QuoteMaster备注', db.scalar('SELECT group_concat(content) FROM notes WHERE customer_id=?', (existing,)))
        anna = db.one("SELECT * FROM customers WHERE company='Old Co One'")
        self.assertEqual((anna['lv'], 'anna@oldco.com' in anna['emails'], 'sales@oldco.com' in anna['emails']), (4, True, True))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM customers WHERE name='Nobody'"), 1)                                           # 只有姓名也迁
        self.assertTrue(any('#4' in w for w in res['warnings']))                                                                       # 全空客户无法迁入，有提示
        # 报价：OLD-xxxx，金额精确到分，未知状态按已发送，EUR 保留原币种，没有客户的跳过
        q1 = db.one("SELECT * FROM quotes WHERE quote_no='OLD-0001'")
        self.assertEqual((q1['status'], q1['total'], q1['currency'], q1['customer_id'] == anna['id'], q1['created_at']), ('accepted', 1550.0, 'USD', True, '2024-07-01 09:00:00'))
        q2 = db.one("SELECT * FROM quotes WHERE quote_no='OLD-0002'")
        self.assertEqual((q2['status'], q2['total']), ('sent', 10.0))                                                                   # 3.333×3=9.999 → 10.00
        self.assertEqual(db.one("SELECT currency FROM quotes WHERE quote_no='OLD-0003'")['currency'], 'EUR')
        self.assertIsNone(db.one("SELECT 1 FROM quotes WHERE quote_no='OLD-0004'"))
        self.assertTrue(any('#4' in w and '报价' in w for w in res['warnings']))
        it = db.one('SELECT * FROM quote_items WHERE quote_id=?', (q1['id'],))
        self.assertEqual((it['sku'], it['quantity'], it['unit_price'], it['product_id']), ('QM-001', 100.0, 15.5, p['id']))
        sell = db.one("SELECT * FROM price_history WHERE price_type='sell' AND source='OLD-0001'")
        self.assertEqual((sell['price'], sell['customer_id'], sell['effective_date']), (15.5, anna['id'], '2024-07-01'))              # 旧成交价进售价历史
        self.assertEqual(self.c.get('/api/quotes/%d' % q1['id'])[0], 200)                                                               # 新版能正常打开旧报价

    def test_02_rerun_is_idempotent(self):
        db = self.ctx.db
        counts = lambda: {t: db.scalar('SELECT COUNT(*) FROM %s' % t) for t in ('products', 'customers', 'quotes', 'quote_items', 'supplier_quotes', 'notes',
                                                                              'price_history', 'category_fields', 'categories', 'product_images', 'product_field_values')}
        before = counts()
        res = self.run_migration()
        self.assertEqual(before, counts())
        self.assertEqual((res['stats'].get('产品(新迁入)', 0), res['stats'].get('客户(新迁入)', 0), res['stats']['报价历史'], res['stats']['供应商比价']), (0, 0, 0, 0))

    def test_03_failure_rolls_back_everything(self):
        db = self.ctx.db
        n = db.scalar('SELECT COUNT(*) FROM products')
        c = sqlite3.connect(os.path.join(self.old_dir, 'quotes.db'))
        c.execute("INSERT INTO products VALUES(50,'QM-NEW','will be rolled back',1,NULL,1,'USD',0.2,NULL,NULL,NULL,NULL,NULL,'2024-01-01 00:00:00')")
        c.commit()
        from sinlux.migrate import quotemaster as qm
        orig = qm.Migrator._quotes
        qm.Migrator._quotes = lambda self, *a: (_ for _ in ()).throw(RuntimeError('boom'))
        try:
            with self.assertRaises(RuntimeError):
                qm.migrate(self.ctx, c, os.path.join(self.old_dir, 'uploads'))
        finally:
            qm.Migrator._quotes = orig
            c.close()
        self.assertEqual(db.scalar('SELECT COUNT(*) FROM products'), n)
        self.assertIsNone(db.one("SELECT 1 FROM products WHERE sku='QM-NEW'"))


if __name__ == '__main__':
    unittest.main()
