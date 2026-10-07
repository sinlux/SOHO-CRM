# -*- coding: utf-8 -*-
import os
import sqlite3
import tempfile
import threading
import unittest

from helpers import AppTestCase
from sinlux.core import util
from sinlux.core.db import Database
from sinlux.customers import intake


class TestUtil(unittest.TestCase):
    def test_valid_date(self):
        for ok in ('2026-02-28', '2024-02-29'):
            self.assertTrue(util.valid_date(ok), ok)
        for bad in ('2026-02-29', '2026-1-1', '', None, 20260101, '2026-01-01 10:00', '２０２６-01-01'):
            self.assertFalse(util.valid_date(bad), bad)

    def test_clean_and_merge_multi(self):
        self.assertEqual(util.clean_multi('A@x.com, a@X.com;b@x.com\nc@x.com'), 'A@x.com b@x.com c@x.com')
        self.assertEqual(util.clean_multi(None), '')
        self.assertEqual(util.merge_multi('a@x.com', ['A@x.com', 'b@x.com b@x.com']), 'a@x.com b@x.com')

    def test_phones(self):
        self.assertEqual(util.split_phones('+86 138 0000; +49 1,\n+1 2'), ['+86 138 0000', '+49 1', '+1 2'])
        self.assertEqual(util.merge_phones('+86 138 0000', ['+86-138-0000', '+49 1']), '+86 138 0000; +49 1')

    def test_like_escape(self):
        self.assertEqual(util.like('50%_\\'), '%50\\%\\_\\\\%')


class TestIntakeUnit(unittest.TestCase):
    def test_classify_edge_cases(self):
        self.assertEqual(intake.classify('a@b.co')[0], 'email')
        self.assertEqual(intake.classify('not an email@x')[0], 'company')
        self.assertEqual(intake.classify('Acme Inc. www.acme.com')[0], 'company')      # 含空格不当网址
        self.assertEqual(intake.classify('http://acme.com:8080/x?y=1')[0], 'website')
        self.assertEqual(intake.domain_of('https://www.Acme.com:8080/x'), 'acme.com')
        self.assertEqual(intake.domain_of('user@acme.com'), 'acme.com')
        self.assertEqual(intake.domain_of('::::'), '')


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.d, 't.db'))
        self.db.execute('CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)')

    def tearDown(self):
        self.db.close()

    def test_tx_commit_rollback_nested(self):
        with self.db.tx():
            self.db.execute("INSERT INTO t(v) VALUES('a')")
        with self.assertRaises(ValueError):
            with self.db.tx():
                self.db.execute("INSERT INTO t(v) VALUES('b')")
                with self.db.tx():                       # 内层不单独提交
                    self.db.execute("INSERT INTO t(v) VALUES('c')")
                raise ValueError
        self.assertEqual([r['v'] for r in self.db.query('SELECT v FROM t')], ['a'])
        with self.db.tx():
            with self.db.tx():
                self.db.execute("INSERT INTO t(v) VALUES('d')")
        self.assertEqual(self.db.scalar('SELECT COUNT(*) FROM t'), 2)
        self.db.execute("INSERT INTO t(v) VALUES('e')")  # 回到自动提交，且事务状态没被污染
        self.assertEqual(self.db._depth, 0)

    def test_concurrent_writers_do_not_lose_rows(self):
        def work(n):
            for i in range(50):
                with self.db.tx():
                    self.db.execute('INSERT INTO t(v) VALUES(?)', ('%d-%d' % (n, i),))
        ts = [threading.Thread(target=work, args=(n,)) for n in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(self.db.scalar('SELECT COUNT(*) FROM t'), 400)


class TestConcurrentHttp(AppTestCase):
    def test_parallel_requests_consistent(self):
        errors = []

        def work(n):
            try:
                for i in range(15):
                    st, r = self.c.post('/api/customers', {'company': 'Conc %d-%d' % (n, i), 'lv': (i % 6) + 1})
                    if st != 200:
                        errors.append((st, r))
                    self.c.get('/api/customers?search=Conc')
                    self.c.get('/api/stats')
            except Exception as e:      # noqa
                errors.append(repr(e))
        ts = [threading.Thread(target=work, args=(n,)) for n in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errors, [])
        self.assertEqual(self.ctx.db.scalar("SELECT COUNT(*) FROM customers WHERE company LIKE 'Conc %'"), 90)


if __name__ == '__main__':
    unittest.main()
