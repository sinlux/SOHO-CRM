# -*- coding: utf-8 -*-
"""第 5 批 rc.5：多源背调计划、多轮（排除已否掉的信息）、批量队列（暂停/额度/重启）。全程假网络。"""
import json
import unittest

from helpers import AppTestCase
from sinlux.customers.enrich import plan_queries, score_of

URL = 'https://www.linkedin.com/company/acme'
SITE = 'https://acme.mx'


def reply(**kw):
    base = {'emails': [], 'whatsapp': [], 'phones': [], 'linkedin': [], 'facebook': [], 'instagram': [], 'other_social': [],
            'key_people': [], 'import_signals': [], 'risk_flags': [], 'company_size': None, 'founded_year': None,
            'customer_type': None, 'main_products': None, 'certifications': None, 'relevance': None, 'summary': 'x'}
    base.update(kw)
    return json.dumps(base)


class TestPlan(unittest.TestCase):
    def test_plan_covers_many_sources_by_depth(self):
        q = plan_queries('Acme SA de CV', 'Mexico', '', 'deep', spanish=True)
        dims = [d for d, _, _ in q]
        self.assertEqual(len(q), 10)
        for d in ('official', 'contact', 'linkedin', 'social', 'directory', 'local', 'trade', 'news', 'b2b'):
            self.assertIn(d, dims)
        self.assertIn(['linkedin.com'], [dom for _, _, dom in q])
        self.assertEqual(len(plan_queries('Acme', 'Peru', 'acme.pe', 'quick')), 3)
        self.assertNotIn('official', [d for d, _, _ in plan_queries('Acme', 'Peru', 'acme.pe', 'deep')])

    def test_round2_prioritises_missing_info(self):
        q = plan_queries('Acme', 'Peru', 'acme.pe', 'quick', missing=['import_signals'], round_no=2)
        self.assertEqual(q[0][0], 'trade')

    def test_score(self):
        self.assertEqual(score_of({}), 0)
        self.assertEqual(score_of({'emails': [1], 'phones': [1], 'linkedin': [1]}), 50)


class TestRounds(AppTestCase):
    def setUp(self):
        self.ctx.db.set_setting('deepseek_key', 'sk')
        self.ctx.db.set_setting('tavily_key', 'tv')
        self.net.pages = {}
        self.net.search_results = [{'url': URL, 'title': 'Acme | LinkedIn', 'content': 'Acme SA mexico lighting importer'}]
        self.net.calls.clear(); self.net.domains.clear(); self.net.n_search = 0; self.net.quota_after = None
        self.cid = self.new_customer(company='Acme SA', country='Mexico')

    def run_enrich(self, **kw):
        self.net.llm_reply = reply(**kw)
        st, r = self.c.post('/api/customers/%d/enrich' % self.cid, {'depth': 'quick'})
        self.assertEqual(st, 200, r)
        return r

    def test_multi_source_search_uses_domain_filters_and_records_plan(self):
        r = self.run_enrich()
        self.assertIn(['linkedin.com'], self.net.domains)
        self.assertEqual(len(r['plan']), 3)
        self.assertEqual(r['round'], 1)

    def test_new_fields_need_valid_source(self):
        r = self.run_enrich(import_signals=[{'value': '2024年进口LED灯12票', 'source': URL}, {'value': '编的', 'source': 'https://x.example'}],
                            risk_flags=[{'value': '被投诉', 'source': 'https://nope.example'}],
                            relevance={'value': '高', 'reason': '做酒店', 'source': URL})
        ex = r['extracted']
        self.assertEqual(len(ex['import_signals']), 1)
        self.assertEqual(ex['risk_flags'], [])
        self.assertEqual(ex['relevance']['value'], '高')
        # 无来源的相关度 -> 未知(None)
        r2 = self.run_enrich(relevance={'value': '高', 'reason': 'x', 'source': 'https://nope.example'})
        self.assertIsNone(r2['extracted']['relevance'])

    def test_continue_round_remembers_rejects_and_filters_model_output(self):
        r = self.run_enrich(emails=[{'value': 'a@acme.mx', 'source': URL}, {'value': 'bad@other.com', 'source': URL}])
        eid = r['enrichment_id']
        # 第 2 轮：模型"不听话"又把 bad@other.com 提取回来 —— 代码层必须挡掉
        self.net.llm_reply = reply(emails=[{'value': 'BAD@other.com', 'source': URL}, {'value': 'new@acme.mx', 'source': URL}])
        st, r2 = self.c.post('/api/enrichments/%d/continue' % eid,
                             {'select': {'emails': ['a@acme.mx']}, 'rejected': {'emails': ['bad@other.com']}, 'depth': 'quick'})
        self.assertEqual(st, 200, r2)
        self.assertEqual([e['value'] for e in r2['extracted']['emails']], ['new@acme.mx'])
        self.assertEqual(r2['round'], 2)
        self.assertTrue(any('自动排除' in w for w in r2['warnings']))
        prompt = [c for c in self.net.calls if c[0] == 'llm'][-1][1]
        self.assertIn('bad@other.com', prompt)                           # 提示词里告诉了模型哪些已确认无效
        c = self.c.get('/api/customers/%d' % self.cid)[1]['customer']
        self.assertIn('a@acme.mx', c['emails'])                          # 第 1 轮确认的已写入
        self.assertNotIn('bad@other.com', c['emails'])
        en1 = self.c.get('/api/enrichments/%d' % eid)[1]
        self.assertEqual(en1['status'], 'applied')
        st, _ = self.c.post('/api/enrichments/%d/continue' % eid, {})    # 同一份结果不能重复继续
        self.assertEqual(st, 409)

    def test_reject_ignores_values_not_in_result(self):
        r = self.run_enrich(emails=[{'value': 'a@acme.mx', 'source': URL}])
        st, out = self.c.post('/api/enrichments/%d/reject' % r['enrichment_id'], {'rejected': {'emails': ['x@y.com', 'a@acme.mx'], 'hacker': ['z']}})
        self.assertEqual(out['rejected'], 1)

    def test_quota_error_is_clear_429_not_500(self):
        self.net.quota_after = 0
        st, r = self.c.post('/api/customers/%d/enrich' % self.cid, {'depth': 'quick'})
        self.assertEqual(st, 429, r)
        self.assertIn('额度', r['error'])


class TestBatch(AppTestCase):
    def setUp(self):
        self.ctx.db.set_setting('deepseek_key', 'sk')
        self.ctx.db.set_setting('tavily_key', 'tv')
        self.net.pages = {}
        self.net.search_results = [{'url': URL, 'title': 't', 'content': 'c ' * 50}]
        self.net.llm_reply = reply(emails=[{'value': 'a@b.com', 'source': URL}], risk_flags=[{'value': '投诉', 'source': URL}])
        self.net.calls.clear(); self.net.n_search = 0; self.net.quota_after = None
        self.ctx.db.execute('DELETE FROM customers'); self.ctx.db.execute('DELETE FROM enrich_queue')
        self.ids = [self.new_customer(company='Co%d' % i, country='Mexico', lv=3 - i % 3) for i in range(5)]
        self.b = self.ctx.enrich_batch
        self.b.paused = True

    def enqueue(self, **scope):
        st, r = self.c.post('/api/enrich/batch', {'scope': scope, 'depth': 'quick', 'start': False})
        self.assertEqual(st, 200, r)
        return r

    def test_estimate_and_enqueue_skips_already_queued(self):
        st, e = self.c.post('/api/enrich/batch/estimate', {'scope': {'mode': 'unresearched'}, 'depth': 'quick'})
        self.assertEqual((e['customers'], e['searches']), (5, 15))
        self.assertEqual(self.enqueue(mode='unresearched')['queued'], 5)
        st, r = self.c.post('/api/enrich/batch', {'scope': {'mode': 'unresearched'}, 'depth': 'quick', 'start': False})
        self.assertEqual(st, 400)                                        # 都已在队列，没有可排的

    def test_run_produces_pending_results_and_status(self):
        self.enqueue(mode='all', lv_min=2)
        n = self.ctx.db.scalar("SELECT COUNT(*) FROM enrich_queue")
        self.b.run_pending_sync()
        s = self.c.get('/api/enrich/batch')[1]
        self.assertEqual(s['counts'].get('done'), n)
        it = s['items'][0]
        self.assertEqual((it['enrich_status'], it['risks'], it['found']), ('pending', 1, 1))
        # 批量背调不会改动客户档案，必须逐个确认
        self.assertEqual(self.c.get('/api/customers/%d' % self.ids[0])[1]['customer']['emails'], '')
        # 已有待确认结果的客户，不会被"未背调"范围再选中
        st, e = self.c.post('/api/enrich/batch/estimate', {'scope': {'mode': 'unresearched', 'lv_min': 2}, 'depth': 'quick'})
        self.assertEqual(e['customers'], 0)

    def test_quota_pauses_batch_and_requeues(self):
        self.enqueue(mode='all')
        self.net.quota_after = 3                                        # 第 1 个客户用掉 3 次搜索，第 2 个客户一开始额度就没了
        self.b.run_pending_sync()
        s = self.c.get('/api/enrich/batch')[1]
        self.assertTrue(s['paused'])
        self.assertIn('额度', s['reason'])
        self.assertEqual(s['counts'].get('done'), 1)
        self.assertEqual(s['counts'].get('queued'), 4)                  # 被打断的放回队列，没有被标成失败
        self.assertFalse(s.get('failed'))
        self.assertIsNone(s['counts'].get('failed'))

    def test_failed_item_does_not_block_others_and_can_retry(self):
        self.enqueue(mode='all')
        self.ctx.db.execute("UPDATE customers SET company='', name='' WHERE id=?", (self.ids[0],))     # 运行时发现没名字 -> 失败
        self.b.run_pending_sync()
        s = self.c.get('/api/enrich/batch')[1]
        self.assertEqual(s['counts'].get('failed'), 1)
        self.assertEqual(s['counts'].get('done'), 4)
        self.assertEqual(self.c.post('/api/enrich/batch/retry')[1]['requeued'], 1)

    def test_restart_resets_running_and_starts_paused(self):
        self.enqueue(mode='all')
        self.ctx.db.execute("UPDATE enrich_queue SET state='running' WHERE id=(SELECT MIN(id) FROM enrich_queue)")
        from sinlux.customers.batch import BatchRunner
        b2 = BatchRunner(self.ctx.db, self.ctx.enricher)
        self.assertTrue(b2.paused)
        self.assertEqual(self.ctx.db.scalar("SELECT COUNT(*) FROM enrich_queue WHERE state='running'"), 0)

    def test_clear_and_missing_keys(self):
        self.enqueue(mode='all')
        self.assertEqual(self.c.post('/api/enrich/batch/clear')[1]['cancelled'], 5)
        self.enqueue(mode='ids', ids=[self.ids[0]])
        self.ctx.db.set_setting('tavily_key', '')
        self.b.run_pending_sync()
        s = self.c.get('/api/enrich/batch')[1]
        self.assertEqual(s['counts'].get('failed'), 1)
        self.assertIn('API Key', s['items'][0]['error'])


if __name__ == '__main__':
    unittest.main()
