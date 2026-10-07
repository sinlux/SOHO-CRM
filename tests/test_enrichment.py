# -*- coding: utf-8 -*-
"""AI 背调：来源强制校验、确认后才写入、不能写入结果之外的内容、全程用假网络。"""
import json
import unittest

from helpers import AppTestCase
from sinlux.customers.enrich import sanitize

SITE = 'https://www.zeta-lights.com'
PAGES = {SITE + '/': 'Zeta Lights GmbH home page. ' * 10, SITE + '/contact': 'Contact us: info@zeta-lights.com ' * 10}
RESULT = {'url': 'https://www.linkedin.com/company/zeta-lights', 'title': 'Zeta Lights | LinkedIn',
          'content': 'Zeta Lights, 51-200 employees, founded 2009, lighting wholesaler in Hamburg'}

GOOD = {
    'emails': [{'value': 'info@zeta-lights.com', 'source': SITE + '/contact'},
               {'value': 'ceo@made-up.com', 'source': 'https://not-fetched.example.com/x'}],   # 来源无效 -> 必须丢弃
    'whatsapp': [{'value': '+49 40 123 456', 'source': SITE + '/contact'}],
    'phones': [{'value': '+49 40 999 000', 'source': SITE + '/contact'}],
    'linkedin': [{'value': RESULT['url'], 'source': RESULT['url']}],
    'facebook': [], 'instagram': [{'value': 'https://instagram.com/zeta', 'source': SITE + '/'}], 'other_social': [],
    'key_people': [{'name': 'Anna Zeta', 'title': 'CEO', 'source': RESULT['url']},
                   {'name': 'Ghost', 'title': 'CTO', 'source': 'https://nowhere.example.com'}],
    'company_size': {'value': '51-200', 'source': RESULT['url']},
    'founded_year': {'value': '2009', 'source': RESULT['url']},
    'customer_type': {'value': '批发商', 'source': 'https://invented.example.com'},              # 来源无效 -> null
    'main_products': {'value': 'LED lighting', 'source': SITE + '/'},
    'certifications': None,
    'summary': '位于汉堡的灯具批发商。',
}


class TestEnrichment(AppTestCase):
    def setUp(self):
        self.ctx.db.set_setting('deepseek_key', 'sk-test')
        self.ctx.db.set_setting('tavily_key', 'tvly-test')
        self.net.pages = dict(PAGES)
        self.net.search_results = [dict(RESULT)]
        self.net.llm_reply = json.dumps(GOOD)
        self.net.fail_search = False
        self.net.calls.clear()
        self.cid = self.new_customer(company='Zeta Lights', website='www.zeta-lights.com', country='Germany',
                                     emails='old@zeta-lights.com', whatsapp='+49 40 123 456')

    def run_enrich(self):
        st, r = self.c.post('/api/customers/%d/enrich' % self.cid)
        self.assertEqual(st, 200, r)
        return r

    def test_run_drops_items_without_valid_source(self):
        r = self.run_enrich()
        ex = r['extracted']
        self.assertEqual([e['value'] for e in ex['emails']], ['info@zeta-lights.com'])      # made-up.com 被丢
        self.assertEqual([p['name'] for p in ex['key_people']], ['Anna Zeta'])
        self.assertIsNone(ex['customer_type'])
        self.assertEqual(ex['company_size']['value'], '51-200')
        self.assertTrue(any('已丢弃' in w for w in r['warnings']))
        # 每一条保留下来的信息，来源都在实际抓取的材料里
        urls = {s['url'] for s in r['sources']}
        for k in ('emails', 'whatsapp', 'phones', 'linkedin', 'instagram'):
            for it in ex[k]:
                self.assertIn(it['source'], urls)
        # 客户档案在确认前不变
        c = self.c.get('/api/customers/%d' % self.cid)[1]['customer']
        self.assertEqual((c['emails'], c['ai_summary']), ('old@zeta-lights.com', ''))
        en = self.c.get('/api/enrichments/%d' % r['enrichment_id'])[1]
        self.assertEqual(en['status'], 'pending')
        self.assertTrue(len(en['sources']) >= 3)                                              # 原始材料留档

    def test_prompt_contains_anti_fabrication_rules_and_sources(self):
        self.run_enrich()
        prompt = [c for c in self.net.calls if c[0] == 'llm'][0][1]
        self.assertIn('禁止推测', prompt)
        self.assertIn('必须是下面片段里出现过的URL', prompt)
        self.assertIn(SITE + '/contact', prompt)
        self.assertIn('Zeta Lights', prompt)

    def test_apply_merges_without_overwriting_and_dedups(self):
        r = self.run_enrich()
        sel = {'emails': ['info@zeta-lights.com', 'OLD@zeta-lights.com'],     # 后者与已有重复(大小写不同)
               'whatsapp': ['+49 40 123 456', '+49 40 999 000'],              # 前者已有（仅空格差异不影响）
               'linkedin': [RESULT['url']], 'other_social': ['https://instagram.com/zeta'],
               'company_size': '51-200', 'main_products': 'LED lighting', 'key_people': ['Anna Zeta(CEO)'],
               'summary': GOOD['summary']}
        st, r2 = self.c.post('/api/enrichments/%d/apply' % r['enrichment_id'], sel)
        self.assertEqual(st, 400, r2)                                          # OLD@... 不在提取结果里 -> 拒绝
        sel['emails'] = ['info@zeta-lights.com']
        st, r2 = self.c.post('/api/enrichments/%d/apply' % r['enrichment_id'], sel)
        self.assertEqual(st, 200, r2)
        d = self.c.get('/api/customers/%d' % self.cid)[1]
        c = d['customer']
        self.assertEqual(c['emails'], 'old@zeta-lights.com info@zeta-lights.com')       # 追加，原有保留
        self.assertEqual(c['whatsapp'], '+49 40 123 456; +49 40 999 000')               # 同号码不重复
        self.assertEqual(c['linkedin'], RESULT['url'])
        self.assertEqual((c['company_size'], c['ai_summary']), ('51-200', GOOD['summary']))
        self.assertEqual(c['founded_year'], '')                                         # 没勾选 -> 不写
        self.assertTrue(any('AI背调已确认写入' in n['content'] and 'Anna Zeta(CEO)' in n['content'] for n in d['notes']))
        self.assertEqual(d['enrichments'][0]['status'], 'applied')

    def test_apply_cannot_inject_values_outside_results(self):
        r = self.run_enrich()
        eid = r['enrichment_id']
        for bad in ({'emails': ['ceo@made-up.com']}, {'emails': ['evil@evil.com']}, {'company_size': '10000+'},
                    {'summary': '伪造的摘要'}, {'key_people': ['Ghost(CTO)']}, {'customer_type': '批发商'},
                    {'main_products': 'weapons'}, {'whatsapp': ['+1 555 0000']}):
            st, r2 = self.c.post('/api/enrichments/%d/apply' % eid, bad)
            self.assertEqual(st, 400, bad)
        c = self.c.get('/api/customers/%d' % self.cid)[1]['customer']
        self.assertEqual((c['emails'], c['company_size'], c['ai_summary']), ('old@zeta-lights.com', '', ''))
        self.assertEqual(self.c.get('/api/enrichments/%d' % eid)[1]['status'], 'pending')   # 被拒绝后仍可再确认

    def test_apply_only_once(self):
        r = self.run_enrich()
        eid = r['enrichment_id']
        self.assertEqual(self.c.post('/api/enrichments/%d/apply' % eid, {'founded_year': '2009'})[0], 200)
        st, r2 = self.c.post('/api/enrichments/%d/apply' % eid, {'founded_year': '2009'})
        self.assertEqual(st, 409)
        self.assertEqual(self.c.post('/api/enrichments/987654/apply', {})[0], 404)

    def test_missing_keys_and_missing_company(self):
        self.ctx.db.set_setting('tavily_key', '')
        st, r = self.c.post('/api/customers/%d/enrich' % self.cid)
        self.assertEqual(st, 400)
        self.assertIn('API Key', r['error'])
        self.assertEqual(self.ctx.db.scalar('SELECT COUNT(*) FROM enrichments WHERE customer_id=?', (self.cid,)), 0)
        self.ctx.db.set_setting('tavily_key', 'tvly')
        anon = self.c.post('/api/customers', {'emails': 'x@y.com'})[1]['id']
        st, r = self.c.post('/api/customers/%d/enrich' % anon)
        self.assertEqual(st, 400)
        self.assertEqual(self.c.post('/api/customers/987654/enrich')[0], 404)

    def test_failures_are_recorded_not_applied(self):
        self.net.llm_reply = 'sorry I cannot do JSON'
        st, r = self.c.post('/api/customers/%d/enrich' % self.cid)
        self.assertEqual(st, 502)
        en = self.c.get('/api/enrichments/%d' % r['enrichment_id'])[1]
        self.assertEqual(en['status'], 'failed')
        self.assertIn('JSON', en['error'])
        self.assertTrue(en['sources'])                                         # 失败也留档材料
        self.net.llm_reply = '[1,2,3]'
        self.assertEqual(self.c.post('/api/customers/%d/enrich' % self.cid)[0], 502)
        # 一个搜索都没搜到
        self.net.pages, self.net.search_results, self.net.llm_reply = {}, [], json.dumps(GOOD)
        st, r = self.c.post('/api/customers/%d/enrich' % self.cid)
        self.assertEqual(st, 502)
        self.assertIn('没有搜到', r['error'])

    def test_search_failure_tolerated_when_site_has_content(self):
        self.net.fail_search = True
        r = self.run_enrich()
        self.assertTrue(any('Tavily搜索失败' in w for w in r['warnings']))
        self.assertTrue(r['extracted']['emails'])

    def test_markdown_fenced_json_accepted_and_no_site_adds_official_query(self):
        self.net.llm_reply = '```json\n' + json.dumps(GOOD) + '\n```'
        cid = self.new_customer(company='No Site Co')
        r = self.c.post('/api/customers/%d/enrich' % cid)[1]
        self.assertTrue(r['ok'])
        self.assertTrue(any(c[0] == 'search' and 'official website' in c[1] for c in self.net.calls))

    def test_delete_customer_removes_enrichments(self):
        r = self.run_enrich()
        self.c.delete('/api/customers/%d' % self.cid, {'confirm': True})
        self.assertEqual(self.c.get('/api/enrichments/%d' % r['enrichment_id'])[0], 404)


class TestSanitizeUnit(unittest.TestCase):
    def test_garbage_input_is_safe(self):
        out, dropped = sanitize({'emails': 'not a list', 'key_people': [None, 5, {'name': ''}],
                                 'company_size': 'str', 'summary': 123}, {'u'})
        self.assertEqual(out['emails'], [])
        self.assertEqual(out['key_people'], [])
        self.assertIsNone(out['company_size'])
        self.assertEqual(out['summary'], '')

    def test_source_must_match_exactly(self):
        out, dropped = sanitize({'emails': [{'value': 'a@b.com', 'source': 'https://x.com/a'},
                                            {'value': 'c@d.com', 'source': 'https://x.com/a/'}]}, {'https://x.com/a'})
        self.assertEqual([e['value'] for e in out['emails']], ['a@b.com'])
        self.assertEqual(len(dropped), 1)


if __name__ == '__main__':
    unittest.main()
