# -*- coding: utf-8 -*-
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(ROOT, 'app', 'libs'))   # 内置库放最后：优先用系统已装的 Pillow/openpyxl
sys.path.insert(0, os.path.join(ROOT, 'app'))

from sinlux.app import create_app  # noqa: E402


class FakeNet:
    """替换真实网络：测试里不访问任何外部服务。"""

    def __init__(self):
        self.pages = {}          # url -> text
        self.search_results = []
        self.llm_reply = '{}'
        self.calls = []
        self.fail_search = False

    def fetch_page(self, url, timeout=12):
        self.calls.append(('fetch', url))
        return self.pages.get(url)

    def tavily_search(self, key, query, max_results=5):
        self.calls.append(('search', query))
        if self.fail_search:
            raise RuntimeError('boom')
        return list(self.search_results)

    def deepseek_chat(self, key, model, prompt):
        self.calls.append(('llm', prompt))
        return self.llm_reply


class Client:
    def __init__(self, base):
        self.base = base

    def call(self, method, path, body=None, headers=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode('utf-8') if body is not None else None)
        h = dict(headers or {})
        if body is not None and raw is None:
            h.setdefault('Content-Type', 'application/json')
        path = urllib.parse.quote(path, safe="/?&=%:+,;@")
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def j(self, method, path, body=None, **kw):
        st, raw, _ = self.call(method, path, body, **kw)
        try:
            return st, json.loads(raw.decode('utf-8'))
        except ValueError:
            return st, raw

    def get(self, path): return self.j('GET', path)
    def post(self, path, body=None): return self.j('POST', path, {} if body is None else body)
    def put(self, path, body): return self.j('PUT', path, body)
    def delete(self, path, body=None): return self.j('DELETE', path, {} if body is None else body)


class AppTestCase(unittest.TestCase):
    """每个测试类起一个真实 HTTP 服务（随机端口 + 临时数据目录）。"""
    legacy = None          # 子类可设为 callable(data_dir)，在启动前造旧库

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='sinlux_test_')
        cls.data_dir = os.path.join(cls.tmp, 'data')
        os.makedirs(cls.data_dir)
        if cls.legacy:
            cls.legacy_summary = cls.legacy(cls.data_dir)
        cls.net = FakeNet()
        cls.rate_result = 714.0          # 测试里"中国银行美元现汇买入价"（人民币/100美元）；设成异常实例则模拟网络/页面失败

        def fake_rate():
            if isinstance(cls.rate_result, Exception):
                raise cls.rate_result
            return {'buy_spot': cls.rate_result, 'published': '2026-10-07 10:30:00'}
        cls.ctx, cls.server = create_app(cls.data_dir, port=0, net=cls.net, rate_fetcher=fake_rate)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]
        cls.c = Client('http://127.0.0.1:%d' % cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.ctx.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def new_customer(self, **kw):
        kw.setdefault('company', 'Test Co %d' % id(kw))
        st, r = self.c.post('/api/customers', kw)
        self.assertEqual(st, 200, r)
        return r['id']

    def new_product(self, **kw):
        cats = {c['code']: c['id'] for c in self.c.get('/api/categories')[1]['categories']}
        kw.setdefault('category_id', cats['lighting'])
        kw.setdefault('sku', 'T-%d' % self.ctx.db.scalar('SELECT COALESCE(MAX(id),0)+1 FROM products'))
        kw.setdefault('name', 'Test product')
        st, r = self.c.post('/api/products', kw)
        self.assertEqual(st, 200, r)
        return r['id']

    def cat_id(self, code):
        return {c['code']: c['id'] for c in self.c.get('/api/categories')[1]['categories']}[code]
