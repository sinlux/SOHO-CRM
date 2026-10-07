# -*- coding: utf-8 -*-
"""AI 背调：官网抓取 + Tavily 搜索 → DeepSeek 提取 → 用户逐条确认后才写入。

防编造规则（业务规则 1，代码层面强制，而不只靠提示词）：
1. 提示词禁止推测；找不到的字段留空。
2. 模型返回的每条信息必须带 source，且 source 必须是我们实际抓到的材料 URL，否则丢弃。
3. 结果先是 pending；写入时只接受"出现在提取结果里"的值，用户/接口都不能塞新内容。
4. 多值字段追加去重；单值字段只在被勾选时覆盖。
5. 原始材料(sources)全部留档。
"""
import html
import json
import re
import ssl
import urllib.request

from ..core.util import ApiError, merge_multi, merge_phones
from . import service as cs

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/124.0 Safari/537.36')
LIST_KEYS = ['emails', 'whatsapp', 'phones', 'linkedin', 'facebook', 'instagram', 'other_social']
SINGLE_KEYS = ['company_size', 'founded_year', 'customer_type', 'main_products', 'certifications']

PROMPT = """你是外贸客户背调助手。下面提供了关于公司「{company}」的若干网页内容片段，每段前有编号和来源URL。

任务：只根据这些片段中【明确出现】的信息，提取该公司资料。严格规则：
1. 禁止推测、禁止根据常识补全、禁止编造。片段中没有的信息一律留空（空数组/null）。
2. 每条提取的信息必须带 source 字段，值为该信息所在片段的来源URL（必须是下面片段里出现过的URL）。
3. 注意区分：目标公司是「{company}」，片段里可能混入其他公司的信息，无关信息不要提取。
4. 电话号码若明确标注为WhatsApp则放入whatsapp，否则放入phones。

只输出JSON，不要任何其他文字，格式：
{{"emails":[{{"value":"","source":""}}],
"whatsapp":[{{"value":"","source":""}}],
"phones":[{{"value":"","source":""}}],
"linkedin":[{{"value":"","source":""}}],
"facebook":[{{"value":"","source":""}}],
"instagram":[{{"value":"","source":""}}],
"other_social":[{{"value":"","source":""}}],
"key_people":[{{"name":"","title":"","source":""}}],
"company_size":{{"value":"","source":""}},
"founded_year":{{"value":"","source":""}},
"customer_type":{{"value":"","source":""}},
"main_products":{{"value":"","source":""}},
"certifications":{{"value":"","source":""}},
"summary":"用中文2-4句概括该公司（只基于片段内容）"}}

company_size/founded_year/customer_type/main_products/certifications 若片段中没有对应信息，整个对象设为 null。
customer_type 从以下选项中选（有依据才选）：批发商/零售商/工程商/制造商/进出口商/其他。

网页片段：
{chunks}"""


class Net:
    """所有外部网络调用集中在这里，测试时整体替换。"""

    def __init__(self):
        self.ctx = ssl.create_default_context()

    def http_json(self, url, payload, headers, timeout=60):
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),
                                     headers={'Content-Type': 'application/json', **headers}, method='POST')
        with urllib.request.urlopen(req, timeout=timeout, context=self.ctx) as r:
            return json.loads(r.read().decode('utf-8'))

    def fetch_page(self, url, timeout=12):
        """抓网页并粗提取正文。失败返回 None。"""
        try:
            if not url.startswith('http'):
                url = 'https://' + url
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=timeout, context=self.ctx) as r:
                raw = r.read(500000).decode('utf-8', errors='ignore')
            text = re.sub(r'(?is)<(script|style|noscript).*?</\1>', ' ', raw)
            text = re.sub(r'(?s)<[^>]+>', ' ', text)
            text = html.unescape(re.sub(r'\s+', ' ', text)).strip()
            return text[:6000] if len(text) > 100 else None
        except Exception:
            return None

    def tavily_search(self, key, query, max_results=5):
        data = self.http_json('https://api.tavily.com/search',
                              {'query': query, 'max_results': max_results, 'include_raw_content': False},
                              {'Authorization': 'Bearer ' + key})
        return [{'url': r.get('url', ''), 'title': r.get('title', ''), 'content': (r.get('content') or '')[:4000]}
                for r in data.get('results', [])]

    def deepseek_chat(self, key, model, prompt):
        data = self.http_json('https://api.deepseek.com/chat/completions',
                              {'model': model or 'deepseek-chat',
                               'messages': [{'role': 'user', 'content': prompt}],
                               'temperature': 0.1, 'response_format': {'type': 'json_object'}},
                              {'Authorization': 'Bearer ' + key}, timeout=180)
        return data['choices'][0]['message']['content']


def sanitize(extracted, source_urls):
    """丢弃没有合法来源的条目。返回 (干净结果, 被丢弃条目说明列表)。"""
    dropped, out = [], {}
    ok = lambda s: isinstance(s, str) and s in source_urls
    for k in LIST_KEYS:
        keep = []
        for it in (extracted.get(k) or []):
            if isinstance(it, dict) and str(it.get('value') or '').strip():
                if ok(it.get('source')):
                    keep.append({'value': str(it['value']).strip(), 'source': it['source']})
                else:
                    dropped.append('%s: %s（来源无效）' % (k, it['value']))
        out[k] = keep
    people = []
    for it in (extracted.get('key_people') or []):
        if isinstance(it, dict) and str(it.get('name') or '').strip():
            if ok(it.get('source')):
                people.append({'name': str(it['name']).strip(), 'title': str(it.get('title') or '').strip(),
                               'source': it['source']})
            else:
                dropped.append('key_people: %s（来源无效）' % it['name'])
    out['key_people'] = people
    for k in SINGLE_KEYS:
        it = extracted.get(k)
        if isinstance(it, dict) and str(it.get('value') or '').strip():
            if ok(it.get('source')):
                out[k] = {'value': str(it['value']).strip(), 'source': it['source']}
            else:
                dropped.append('%s: %s（来源无效）' % (k, it['value']))
                out[k] = None
        else:
            out[k] = None
    s = extracted.get('summary')
    out['summary'] = s.strip() if isinstance(s, str) else ''
    return out, dropped


class Enricher:
    def __init__(self, db, customers, net=None):
        self.db = db
        self.customers = customers
        self.net = net or Net()

    def run(self, cid):
        cust = self.customers.require(cid)
        tavily_key = self.db.get_setting('tavily_key')
        ds_key = self.db.get_setting('deepseek_key')
        if not tavily_key or not ds_key:
            raise ApiError('请先在「设置」里填写 Tavily 和 DeepSeek 的 API Key')
        company = cust['company'] or cust['name']
        if not company:
            raise ApiError('该客户没有公司名也没有联系人姓名，无法搜索')
        eid = self.db.execute("INSERT INTO enrichments(customer_id,status,created_at) VALUES(?,'running',datetime('now','localtime'))",
                              (cid,)).lastrowid
        sources, errors = [], []
        try:
            site = (cust['website'] or '').split()[0] if cust['website'] else ''
            if site:
                base = (site if site.startswith('http') else 'https://' + site).rstrip('/')
                got = 0
                for path in ['/', '/contact', '/contact-us', '/about', '/about-us']:
                    t = self.net.fetch_page(base + path)
                    if t:
                        sources.append({'url': base + path, 'title': '官网直接抓取', 'content': t})
                        got += 1
                    if got >= 3:
                        break
            country = cust['country'] or ''
            queries = ['"%s" %s contact email' % (company, country),
                       '"%s" %s linkedin company profile' % (company, country)]
            if not site:
                queries.insert(0, '"%s" %s official website' % (company, country))
            for qy in queries:
                try:
                    sources.extend(self.net.tavily_search(tavily_key, qy, 5))
                except Exception as e:
                    errors.append('Tavily搜索失败(%s): %s' % (qy, e))
            seen, uniq = set(), []
            for s in sources:
                if s['url'] and s['url'] not in seen and s['content']:
                    seen.add(s['url'])
                    uniq.append(s)
            sources = uniq
            if not sources:
                raise RuntimeError('没有搜到任何可用的网页内容。' + '；'.join(errors))
            raw = self.net.deepseek_chat(ds_key, self.db.get_setting('deepseek_model', 'deepseek-chat'),
                                         self._prompt(company, sources))
            try:
                parsed = json.loads(re.sub(r'^```(json)?|```$', '', raw.strip(), flags=re.M).strip())
                if not isinstance(parsed, dict):
                    raise ValueError
            except ValueError:
                raise RuntimeError('模型返回的不是有效 JSON，请重试')
            extracted, dropped = sanitize(parsed, {s['url'] for s in sources})
            if dropped:
                errors.append('已丢弃 %d 条来源无效的信息: %s' % (len(dropped), '；'.join(dropped[:5])))
            self.db.execute("UPDATE enrichments SET status='pending', sources=?, extracted=?, error=? WHERE id=?",
                            (json.dumps(sources, ensure_ascii=False), json.dumps(extracted, ensure_ascii=False),
                             '；'.join(errors), eid))
            return {'enrichment_id': eid, 'extracted': extracted, 'sources': sources, 'warnings': errors}
        except Exception as e:
            self.db.execute("UPDATE enrichments SET status='failed', sources=?, error=? WHERE id=?",
                            (json.dumps(sources, ensure_ascii=False), str(e), eid))
            raise ApiError('背调失败: %s' % e, 502, {'enrichment_id': eid})

    @staticmethod
    def _prompt(company, sources):
        chunks, budget = [], 26000
        for i, s in enumerate(sources):
            c = '[片段%d] 来源URL: %s\n标题: %s\n内容: %s\n' % (i + 1, s['url'], s.get('title', ''), s['content'])
            if budget - len(c) < 0:
                break
            budget -= len(c)
            chunks.append(c)
        return PROMPT.format(company=company, chunks='\n'.join(chunks))

    def get(self, eid):
        en = self.db.one('SELECT * FROM enrichments WHERE id=?', (eid,))
        if not en:
            raise ApiError('背调记录不存在', 404)
        en['sources'] = json.loads(en['sources'] or '[]')
        en['extracted'] = json.loads(en['extracted'] or '{}')
        return en

    def apply(self, eid, sel):
        """sel: 用户勾选结果。所有值必须来自该次背调的提取结果。"""
        en = self.get(eid)
        if en['status'] != 'pending':
            raise ApiError('该背调结果%s，不能重复写入' % ('已写入' if en['status'] == 'applied' else '状态为 ' + en['status']), 409)
        ex = en['extracted']
        cust = self.customers.require(en['customer_id'])

        def allowed(*keys):
            return {i['value'].strip().lower() for k in keys for i in (ex.get(k) or [])}

        def pick(field, *keys):
            vals = [str(v).strip() for v in (sel.get(field) or []) if str(v).strip()]
            bad = [v for v in vals if v.lower() not in allowed(*keys)]
            if bad:
                raise ApiError('所选内容不在背调结果中（%s）：%s' % (field, bad[0]))
            return vals

        upd = {}
        for field, keys in (('emails', ('emails',)), ('linkedin', ('linkedin',)), ('facebook', ('facebook',)),
                            ('other_social', ('other_social', 'instagram'))):
            vals = pick(field, *keys)
            if vals:
                upd[field] = merge_multi(cust[field], vals)
        wa = pick('whatsapp', 'whatsapp', 'phones')
        if wa:
            upd['whatsapp'] = merge_phones(cust['whatsapp'], wa)
        for f in ('company_size', 'founded_year', 'customer_type', 'certifications'):
            v = sel.get(f)
            if v:
                item = ex.get(f)
                if not item or item['value'] != str(v).strip():
                    raise ApiError('所选内容不在背调结果中（%s）' % f)
                upd[f] = item['value']
        notes = ['【AI背调已确认写入】']
        mp = sel.get('main_products')
        if mp:
            if not ex.get('main_products') or ex['main_products']['value'] != str(mp).strip():
                raise ApiError('所选内容不在背调结果中（main_products）')
            notes.append('主营: ' + ex['main_products']['value'])
        people = [str(p).strip() for p in (sel.get('key_people') or '').split('、') if str(p).strip()] \
            if isinstance(sel.get('key_people'), str) else [str(p).strip() for p in (sel.get('key_people') or [])]
        if people:
            valid = {('%s(%s)' % (p['name'], p['title']) if p['title'] else p['name']) for p in ex.get('key_people') or []}
            for p in people:
                if p not in valid:
                    raise ApiError('所选内容不在背调结果中（key_people）：%s' % p)
            notes.append('关键人物: ' + '、'.join(people))
        if sel.get('summary'):
            if str(sel['summary']).strip() != ex.get('summary', ''):
                raise ApiError('所选内容不在背调结果中（summary）')
            upd['ai_summary'] = ex['summary']
            notes.append('摘要: ' + ex['summary'])
        with self.db.tx():
            if upd:
                self.customers.update(en['customer_id'], upd)
            self.customers.add_system_note(en['customer_id'], '\n'.join(notes))
            self.db.execute("UPDATE enrichments SET status='applied' WHERE id=?", (eid,))
        return {'written': sorted(upd)}
