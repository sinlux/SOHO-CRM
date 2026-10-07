# -*- coding: utf-8 -*-
"""客户模块 API（真实 HTTP 服务）：增删改查、校验、搜索、备注、提醒、智能录入、备份、安全。"""
import base64
import http.client
import io
import json
import os
import time
import unittest
import zipfile

from helpers import AppTestCase

PNG = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'\x00' * 32).decode()


class TestCustomerCrud(AppTestCase):
    def test_create_get_update(self):
        cid = self.new_customer(company='Alpha Lighting', name='Anna', lv='5', stage='已联系',
                                emails='a@alpha.com, a@alpha.com  b@alpha.com', website='www.alpha.com')
        st, d = self.c.get('/api/customers/%d' % cid)
        c = d['customer']
        self.assertEqual((c['lv'], c['stage']), (5, '已联系'))
        self.assertEqual(c['emails'], 'a@alpha.com b@alpha.com')        # 拆分去重
        self.assertEqual((d['notes'], d['reminders'], d['quotes'], d['enrichments']), ([], [], [], []))
        st, r = self.c.put('/api/customers/%d' % cid, {'company': 'Alpha Lighting GmbH', 'lv': None, 'country': 'DE'})
        self.assertEqual(st, 200)
        c = self.c.get('/api/customers/%d' % cid)[1]['customer']
        self.assertEqual((c['company'], c['lv'], c['country'], c['name']), ('Alpha Lighting GmbH', None, 'DE', 'Anna'))
        self.assertGreaterEqual(c['updated_at'], c['created_at'])

    def test_partial_update_keeps_other_fields(self):
        cid = self.new_customer(company='Keep Co', main_business='LED', lv=3)
        self.c.put('/api/customers/%d' % cid, {'address': 'Berlin'})
        c = self.c.get('/api/customers/%d' % cid)[1]['customer']
        self.assertEqual((c['main_business'], c['lv'], c['address']), ('LED', 3, 'Berlin'))

    def test_validation(self):
        bad = [({'company': 'X', 'lv': 7}, 'LV'), ({'company': 'X', 'lv': 0}, 'LV'), ({'company': 'X', 'lv': 'abc'}, 'LV'),
               ({'company': 'X', 'stage': 'nonsense'}, '阶段'), ({}, '至少填一项'), ({'company': '   '}, '至少填一项')]
        for body, kw in bad:
            st, r = self.c.post('/api/customers', body)
            self.assertEqual(st, 400, body)
            self.assertIn(kw, r['error'])
        cid = self.new_customer(company='V')
        st, r = self.c.put('/api/customers/%d' % cid, {'lv': 9})
        self.assertEqual(st, 400)
        st, r = self.c.put('/api/customers/987654', {'company': 'x'})
        self.assertEqual(st, 404)

    def test_legacy_english_stage_key_accepted_and_normalized(self):
        cid = self.new_customer(company='Eng Stage', stage='won')
        self.assertEqual(self.c.get('/api/customers/%d' % cid)[1]['customer']['stage'], '成交')

    def test_unknown_fields_ignored_and_id_not_overwritable(self):
        cid = self.new_customer(company='Safe Co')
        st, r = self.c.put('/api/customers/%d' % cid, {'id': 99999, 'created_at': 'x', 'evil': 1, 'name': 'N'})
        self.assertEqual(st, 200)
        c = self.c.get('/api/customers/%d' % cid)[1]['customer']
        self.assertEqual((c['id'], c['name']), (cid, 'N'))
        self.assertNotEqual(c['created_at'], 'x')

    def test_sql_injection_strings_are_data(self):
        cid = self.new_customer(company="Robert'); DROP TABLE customers;--")
        st, d = self.c.get('/api/customers?search=' + "'); DROP")
        self.assertEqual([c['id'] for c in d['customers']], [cid])
        self.assertEqual(self.c.get('/api/customers/%d' % cid)[0], 200)


class TestSearchFilter(AppTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        mk = lambda **kw: cls.c.post('/api/customers', kw)[1]['id']
        cls.a = mk(company='Berlin Lights', country='Germany', lv=6, stage='成交', emails='x@berlin.de', main_business='LED strip')
        cls.b = mk(company='Paris Deco', country='France', lv=4, stage='潜在', ai_summary='Interior designer in zebra town')
        cls.c_ = mk(company='100% Natural_Wood', country='France', lv=4, stage='潜在')
        cls.d = mk(company='Plain Co', country='USA')
        cls.c.post('/api/customers/%d/notes' % cls.d, {'content': 'Met at Canton Fair, loves oak tables'})

    def ids(self, qs):
        st, d = self.c.get('/api/customers?' + qs)
        self.assertEqual(st, 200)
        return [c['id'] for c in d['customers']]

    def test_filters(self):
        self.assertEqual(set(self.ids('country=France')), {self.b, self.c_})
        self.assertEqual(self.ids('lv=6'), [self.a])
        self.assertEqual(set(self.ids('stage=潜在')), {self.b, self.c_})
        self.assertEqual(self.ids('lv=4&country=France&search=Paris'), [self.b])
        st, r = self.c.get('/api/customers?lv=99')
        self.assertEqual(st, 400)
        st, r = self.c.get('/api/customers?stage=bogus')
        self.assertEqual(st, 400)

    def test_search_fields_including_notes_and_summary(self):
        self.assertEqual(self.ids('search=berlin'), [self.a])          # 公司名(大小写不敏感)、邮箱域
        self.assertEqual(self.ids('search=LED strip'), [self.a])       # 主营
        self.assertEqual(self.ids('search=zebra'), [self.b])           # AI 摘要
        self.assertEqual(self.ids('search=oak tables'), [self.d])      # 备注全文
        self.assertEqual(self.ids('search=Germany'), [self.a])         # 国家

    def test_like_wildcards_are_literal(self):
        self.assertEqual(self.ids('search=100%25'), [self.c_])         # 100%
        self.assertEqual(self.ids('search=Natural_Wood'), [self.c_])
        self.assertEqual(self.ids('search=%25'), [self.c_])            # 单个 % 只匹配含 % 的
        self.assertEqual(self.ids('search=Natural_Woo_'), [])          # _ 不是通配符

    def test_ordering_and_pagination(self):
        for i in range(25):
            self.new_customer(company='Page Co %02d' % i, lv=1, country='Pagia')
        st, d = self.c.get('/api/customers?country=Pagia&limit=10&offset=0')
        self.assertEqual((d['total'], len(d['customers'])), (25, 10))
        st, d2 = self.c.get('/api/customers?country=Pagia&limit=10&offset=20')
        self.assertEqual(len(d2['customers']), 5)
        ids = {c['id'] for c in d['customers']} | {c['id'] for c in d2['customers']}
        self.assertEqual(len(ids), 15)
        st, d = self.c.get('/api/customers?limit=2000')                # 上限被夹住
        self.assertEqual(st, 200)
        st, d = self.c.get('/api/customers?limit=abc')
        self.assertEqual(st, 400)
        full = self.c.get('/api/customers?limit=1000')[1]['customers']
        lvs = [c['lv'] or 0 for c in full]
        self.assertEqual(lvs, sorted(lvs, reverse=True))

    def test_countries_endpoint(self):
        st, d = self.c.get('/api/countries')
        m = {c['country']: c['n'] for c in d['countries']}
        self.assertEqual(m['France'], 2)
        self.assertNotIn('', m)


class TestNotes(AppTestCase):
    def test_text_note_and_system_ordering(self):
        cid = self.new_customer()
        for t in ('first', 'second'):
            self.assertEqual(self.c.post('/api/customers/%d/notes' % cid, {'content': t})[0], 200)
        notes = self.c.get('/api/customers/%d' % cid)[1]['notes']
        self.assertEqual([n['content'] for n in notes], ['second', 'first'])   # 新的在前

    def test_empty_note_rejected(self):
        cid = self.new_customer()
        self.assertEqual(self.c.post('/api/customers/%d/notes' % cid, {'content': '  '})[0], 400)
        self.assertEqual(self.c.post('/api/customers/987654/notes', {'content': 'x'})[0], 404)

    def test_image_note_lifecycle(self):
        cid = self.new_customer()
        st, r = self.c.post('/api/customers/%d/notes' % cid, {'content': 'shot', 'image_base64': 'data:image/png;base64,' + PNG})
        self.assertEqual(st, 200, r)
        n = self.c.get('/api/customers/%d' % cid)[1]['notes'][0]
        self.assertTrue(n['image_path'].endswith('.png'))
        path = os.path.join(self.ctx.images_dir, n['image_path'])
        self.assertTrue(os.path.isfile(path))
        st, raw, h = self.c.call('GET', '/images/' + n['image_path'])
        self.assertEqual((st, h['Content-Type']), (200, 'image/png'))
        self.assertEqual(self.c.delete('/api/notes/%d' % n['id'])[0], 200)
        self.assertFalse(os.path.exists(path))                         # 删备注同时删图片文件
        self.assertEqual(self.c.delete('/api/notes/%d' % n['id'])[0], 404)

    def test_image_only_note_and_bad_images(self):
        cid = self.new_customer()
        self.assertEqual(self.c.post('/api/customers/%d/notes' % cid, {'image_base64': 'data:image/jpeg;base64,' + PNG})[0], 200)
        n = self.c.get('/api/customers/%d' % cid)[1]['notes'][0]
        self.assertTrue(n['image_path'].endswith('.jpg'))
        for bad in ('data:image/svg+xml;base64,' + PNG, 'data:image/php;base64,' + PNG, 'data:image/png;base64,'):
            st, r = self.c.post('/api/customers/%d/notes' % cid, {'content': 'x', 'image_base64': bad})
            self.assertEqual(st, 400, bad)
        big = base64.b64encode(b'0' * (10 * 1024 * 1024 + 10)).decode()
        st, r = self.c.post('/api/customers/%d/notes' % cid, {'image_base64': 'data:image/png;base64,' + big})
        self.assertEqual(st, 400)
        self.assertIn('10MB', r['error'])
        # 被拒绝的图片不应留下文件
        leftovers = [f for f in os.listdir(self.ctx.images_dir) if f.startswith('%d_' % cid)]
        self.assertEqual(len(leftovers), 1)

    def test_image_path_traversal_blocked(self):
        for p in ('/images/..%2f..%2fcrm.db', '/images/%2e%2e/%2e%2e/crm.db', '/images/../crm.db'):
            st, raw, _ = self.c.call('GET', p)
            self.assertEqual(st, 404, p)
            self.assertNotIn(b'SQLite format', raw)


class TestReminders(AppTestCase):
    def test_lifecycle_and_badge(self):
        cid = self.new_customer(company='Remind Co')
        past, future = '2020-01-01', '2999-01-01'
        self.assertEqual(self.c.post('/api/customers/%d/reminders' % cid, {'content': 'old', 'due_date': past})[0], 200)
        r2 = self.c.post('/api/customers/%d/reminders' % cid, {'content': 'later', 'due_date': future})[1]['id']
        d = self.c.get('/api/reminders')[1]
        mine = [r for r in d['reminders'] if r['customer_id'] == cid]
        self.assertEqual([(r['content'], r['overdue']) for r in mine], [('old', True), ('later', False)])
        self.assertEqual(mine[0]['company'], 'Remind Co')
        due0 = self.c.get('/api/stats')[1]['due_reminders']
        self.assertGreaterEqual(due0, 1)
        rid = mine[0]['id']
        self.assertEqual(self.c.post('/api/reminders/%d/done' % rid)[0], 200)
        self.assertEqual(self.c.get('/api/stats')[1]['due_reminders'], due0 - 1)
        self.assertNotIn(rid, [r['id'] for r in self.c.get('/api/reminders')[1]['reminders']])
        self.assertEqual(self.c.delete('/api/reminders/%d' % r2)[0], 200)
        self.assertEqual(self.c.delete('/api/reminders/%d' % r2)[0], 404)
        self.assertEqual(self.c.post('/api/reminders/987654/done')[0], 404)

    def test_date_validation(self):
        cid = self.new_customer()
        for bad in ('', '2026-13-01', '2026-02-30', 'tomorrow', '2026/05/01', None):
            st, r = self.c.post('/api/customers/%d/reminders' % cid, {'content': 'x', 'due_date': bad})
            self.assertEqual(st, 400, bad)
        self.assertEqual(self.c.post('/api/customers/987654/reminders', {'content': 'x', 'due_date': '2030-01-01'})[0], 404)

    def test_done_reminders_shown_on_customer_page_sorted_last(self):
        cid = self.new_customer()
        a = self.c.post('/api/customers/%d/reminders' % cid, {'content': 'a', 'due_date': '2030-01-01'})[1]['id']
        self.c.post('/api/customers/%d/reminders' % cid, {'content': 'b', 'due_date': '2031-01-01'})
        self.c.post('/api/reminders/%d/done' % a)
        rem = self.c.get('/api/customers/%d' % cid)[1]['reminders']
        self.assertEqual([(r['content'], r['done']) for r in rem], [('b', 0), ('a', 1)])


class TestDeleteFlow(AppTestCase):
    def test_delete_customer_without_children(self):
        cid = self.new_customer()
        st, r = self.c.delete('/api/customers/%d' % cid, {})
        self.assertEqual(st, 409)
        self.assertEqual(r['impact'], {'notes': 0, 'reminders': 0, 'enrichments': 0, 'quotes': 0})
        self.assertEqual(self.c.delete('/api/customers/%d' % cid, {'confirm': True})[0], 200)
        self.assertEqual(self.c.get('/api/customers/%d' % cid)[0], 404)

    def test_delete_cascade_via_app_created_data(self):
        cid = self.new_customer()
        self.c.post('/api/customers/%d/notes' % cid, {'content': 'n', 'image_base64': PNG})
        self.c.post('/api/customers/%d/reminders' % cid, {'content': 'r', 'due_date': '2030-01-01'})
        img = os.listdir(self.ctx.images_dir)
        st, imp = self.c.get('/api/customers/%d/impact' % cid)
        self.assertEqual((imp['impact']['notes'], imp['impact']['reminders']), (1, 1))
        self.c.delete('/api/customers/%d' % cid, {'confirm': True})
        for t in ('notes', 'reminders'):
            self.assertEqual(self.ctx.db.scalar('SELECT COUNT(*) FROM %s WHERE customer_id=?' % t, (cid,)), 0)
        self.assertTrue(len(os.listdir(self.ctx.images_dir)) < len(img))


class TestIntake(AppTestCase):
    def parse(self, text):
        st, r = self.c.post('/api/intake/parse', {'input': text})
        self.assertEqual(st, 200, r)
        return r

    def test_classification(self):
        r = self.parse('info@brightlux.com')
        self.assertEqual((r['type'], r['fields']['emails'], r['fields']['website']), ('email', 'info@brightlux.com', 'www.brightlux.com'))
        r = self.parse('john@gmail.com')
        self.assertEqual((r['type'], 'website' in r['fields']), ('email', False))      # 免费邮箱不推网站
        self.assertEqual(self.parse('https://www.linkedin.com/company/acme')['type'], 'linkedin')
        self.assertEqual(self.parse('facebook.com/acme')['type'], 'facebook')
        r = self.parse('https://instagram.com/acme')
        self.assertEqual((r['type'], list(r['fields'])), ('social', ['other_social']))
        self.assertEqual(self.parse('www.example.com')['type'], 'website')
        self.assertEqual(self.parse('Example Lighting Inc.')['fields'], {'company': 'Example Lighting Inc.'})

    def test_duplicates_by_email_domain_company(self):
        cid = self.new_customer(company='Dup Corp', emails='sales@dupcorp.com', website='www.dupcorp.com',
                                linkedin='https://www.linkedin.com/company/dupcorp')
        r = self.parse('sales@dupcorp.com')
        self.assertEqual({d['id'] for d in r['duplicates']}, {cid})
        r = self.parse('other@dupcorp.com')                      # 同域名不同邮箱
        self.assertIn('域名相同: dupcorp.com', [d['why'] for d in r['duplicates']])
        r = self.parse('https://dupcorp.com/about')
        self.assertEqual([d['id'] for d in r['duplicates']], [cid])
        r = self.parse('dup corp')                              # 公司名大小写不敏感的包含匹配
        self.assertEqual([d['id'] for d in r['duplicates']], [cid])
        r = self.parse('https://www.linkedin.com/company/dupcorp/')
        self.assertEqual([d['id'] for d in r['duplicates']], [cid])
        r = self.parse('https://www.linkedin.com/company/someoneelse')
        self.assertEqual(r['duplicates'], [])                    # 社媒域名本身不算重复
        self.assertEqual(self.parse('Totally New Company')['duplicates'], [])

    def test_empty_input_rejected(self):
        self.assertEqual(self.c.post('/api/intake/parse', {'input': '  '})[0], 400)


class TestSettingsBackup(AppTestCase):
    def test_settings_masking_and_update(self):
        s = self.c.get('/api/settings')[1]
        self.assertEqual((s['deepseek_key_set'], s['deepseek_model']), (False, 'deepseek-chat'))
        self.c.put('/api/settings', {'deepseek_key': 'sk-secret-abcdef1234', 'tavily_key': 'tvly-xyz-98765432', 'deepseek_model': 'deepseek-v9'})
        s = self.c.get('/api/settings')[1]
        self.assertEqual((s['deepseek_key_set'], s['deepseek_key_hint'], s['deepseek_model']), (True, '****1234', 'deepseek-v9'))
        self.assertNotIn('secret', json.dumps(s))
        self.c.put('/api/settings', {'deepseek_key': '', 'tavily_key': ''})          # 空 = 不变
        self.assertEqual(self.ctx.db.get_setting('deepseek_key'), 'sk-secret-abcdef1234')
        self.c.put('/api/settings', {'clear_deepseek_key': True})
        self.assertFalse(self.c.get('/api/settings')[1]['deepseek_key_set'])
        self.c.put('/api/settings', {'not_allowed_key': 'x'})
        self.assertEqual(self.ctx.db.get_setting('not_allowed_key'), '')

    def test_connectivity_test_uses_net(self):
        self.net.llm_reply = '{"reply":"OK"}'
        self.net.search_results = [{'url': 'u', 'title': 't', 'content': 'c'}]
        r = self.c.post('/api/settings/test')[1]
        self.assertTrue(r['tavily'].startswith('OK'))
        self.assertTrue(r['deepseek'].startswith('OK'))

    def test_backup_is_consistent_and_complete(self):
        cid = self.new_customer(company='Backup Me')
        self.c.post('/api/customers/%d/notes' % cid, {'content': 'x', 'image_base64': PNG})
        st, r = self.c.post('/api/backup')
        self.assertEqual(st, 200, r)
        st2, r2 = self.c.post('/api/backup')                    # 同一秒内连续备份不能覆盖
        self.assertNotEqual(r['file'], r2['file'])
        lst = self.c.get('/api/backups')[1]['backups']
        self.assertEqual({r['file'], r2['file']} <= {b['file'] for b in lst}, True)
        st, raw, h = self.c.call('GET', '/backups/' + r['file'])
        self.assertEqual((st, h['Content-Type']), (200, 'application/zip'))
        zf = zipfile.ZipFile(io.BytesIO(raw))
        names = zf.namelist()
        self.assertIn('crm.db', names)
        self.assertTrue(any(n.startswith('images/') for n in names))
        out = os.path.join(self.tmp, 'restored.db')
        with open(out, 'wb') as f:
            f.write(zf.read('crm.db'))
        import sqlite3
        c = sqlite3.connect(out)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM customers WHERE company='Backup Me'").fetchone()[0], 1)
        self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        c.close()

    def test_backup_download_name_validated(self):
        for n in ('../crm.db', 'evil.zip', 'backup_1.zip', 'backup_20200101_000000.zip'):
            st, raw, _ = self.c.call('GET', '/backups/' + n)
            self.assertIn(st, (400, 404), n)

    def test_version_and_health(self):
        v = self.c.get('/api/version')[1]
        self.assertTrue(v['version'])
        self.assertTrue(self.c.get('/api/health')[1]['ok'])


class TestHttpSecurityAndRouting(AppTestCase):
    def raw(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.request(method, path, body=body, headers=headers or {})
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, data

    def test_index_and_static(self):
        st, body = self.raw('GET', '/')
        self.assertEqual(st, 200)
        self.assertIn('SINLUX', body.decode())
        for p in ('/js/main.js', '/js/lib.js', '/css/app.css', '/js/pages/list.js'):
            self.assertEqual(self.raw('GET', p)[0], 200, p)
        self.assertEqual(self.raw('GET', '/nope.html')[0], 404)

    def test_static_path_traversal(self):
        for p in ('/../sinlux/app.py', '/%2e%2e/sinlux/app.py', '/js/../../sinlux/app.py', '/..%2fsinlux%2fapp.py',
                  '/js/%2e%2e/%2e%2e/main.py'):
            st, body = self.raw('GET', p)
            self.assertEqual(st, 404, p)
            self.assertNotIn(b'def create_app', body)
        st, body = self.raw('GET', '/css')                         # 目录
        self.assertEqual(st, 404)

    def test_foreign_host_and_origin_rejected(self):
        self.assertEqual(self.raw('GET', '/api/stats', {'Host': 'evil.example.com'})[0], 403)      # DNS 重绑定
        self.assertEqual(self.raw('GET', '/api/stats', {'Host': 'localhost:%d' % self.port})[0], 200)
        hdr = {'Origin': 'http://evil.example.com', 'Content-Type': 'text/plain'}
        st, _ = self.raw('POST', '/api/customers', hdr, '{"company":"CSRF"}')                      # 跨站表单
        self.assertEqual(st, 403)
        self.assertEqual(self.ctx.db.scalar("SELECT COUNT(*) FROM customers WHERE company='CSRF'"), 0)
        ok = {'Origin': 'http://127.0.0.1:%d' % self.port, 'Content-Type': 'application/json'}
        self.assertEqual(self.raw('POST', '/api/customers', ok, '{"company":"SameOrigin"}')[0], 200)

    def test_bad_requests(self):
        st, r = self.c.j('POST', '/api/customers', raw=b'{not json')
        self.assertEqual(st, 400)
        st, r = self.c.j('POST', '/api/customers', raw=b'[1,2]')
        self.assertEqual(st, 400)
        self.assertEqual(self.c.j('GET', '/api/nothing')[0], 404)
        self.assertEqual(self.c.j('PATCH', '/api/customers')[0], 501)
        st, r = self.c.j('POST', '/api/stats', {})
        self.assertEqual(st, 405)
        self.assertEqual(self.c.j('GET', '/api/customers/abc')[0], 404)      # 非数字 id 不会 500

    def test_no_stack_trace_leak_on_500(self):
        orig = self.ctx.customers.stats
        self.ctx.customers.stats = lambda: 1 / 0
        try:
            st, r = self.c.get('/api/stats')
        finally:
            self.ctx.customers.stats = orig
        self.assertEqual(st, 500)
        self.assertNotIn('Traceback', json.dumps(r))

    def test_xss_payloads_stored_verbatim_and_served_as_json(self):
        payload = '<img src=x onerror=alert(1)>'
        cid = self.new_customer(company=payload)
        st, raw, h = self.c.call('GET', '/api/customers/%d' % cid)
        self.assertEqual(h['Content-Type'], 'application/json; charset=utf-8')   # 不会被当 HTML 渲染
        self.assertEqual(h['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(json.loads(raw)['customer']['company'], payload)


if __name__ == '__main__':
    unittest.main()
