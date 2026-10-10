# -*- coding: utf-8 -*-
"""AI 背调：官网抓取 + Tavily 搜索 → DeepSeek 提取 → 用户逐条确认后才写入。

防编造规则（业务规则 1，代码层面强制，而不只靠提示词）：
1. 提示词禁止推测；找不到的字段留空。
2. 模型返回的每条信息必须带 source，且 source 必须是我们实际抓到的材料 URL，否则丢弃。
3. 结果先是 pending；写入时只接受"出现在提取结果里"的值，用户/接口都不能塞新内容。
4. 多值字段追加去重；单值字段只在被勾选时覆盖。
5. 原始材料(sources)全部留档。

多轮背调（第 2 轮起）：用户在核对页里勾掉的无效信息记入 enrich_rejects（按客户永久记住）；下一轮的提示词会告诉模型"这些已确认无效，不要再提取"，
并且代码层面再过滤一遍（模型不听话也进不来）；下一轮只搜还缺的信息。
信息源（Tavily 按域名定向搜索，不登录任何平台、不抓取需要登录的内容）：官网、LinkedIn、Facebook/Instagram、企业黄页（Europages/Kompass/D&B…）、
进口贸易记录（ImportYeti/Panjiva/Volza）、B2B 平台、新闻/评价/投诉、当地语言（西语）搜索。
"""
import html
import json
import re
import ssl
import urllib.error
import urllib.request

from ..core.util import ApiError, merge_multi, merge_phones
from . import service as cs

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/124.0 Safari/537.36')
LIST_KEYS = ['emails', 'whatsapp', 'phones', 'linkedin', 'facebook', 'instagram', 'other_social']
EXTRA_LIST_KEYS = ['import_signals', 'risk_flags']          # 进口线索 / 风险提示：同样每条必须带有效来源
SINGLE_KEYS = ['company_size', 'founded_year', 'customer_type', 'main_products', 'certifications']

PROMPT = """你是外贸客户背调助手。下面提供了关于公司「{company}」的若干网页内容片段，每段前有编号和来源URL。

任务：只根据这些片段中【明确出现】的信息，提取该公司资料。严格规则：
1. 禁止推测、禁止根据常识补全、禁止编造。片段中没有的信息一律留空（空数组/null）。
2. 每条提取的信息必须带 source 字段，值为该信息所在片段的来源URL（必须是下面片段里出现过的URL）。
3. 注意区分：目标公司是「{company}」，片段里可能混入其他公司的信息，无关信息不要提取。
4. 电话号码若明确标注为WhatsApp则放入whatsapp，否则放入phones。
5. import_signals：片段里明确出现的"该公司进口过什么 / 进口记录 / 采购需求"（例如进口记录里的品类、数量、日期），原文概括，带来源。
6. risk_flags：片段里明确出现的负面信息（投诉、诈骗警告、破产/注销、地址与公司不符等），带来源；没有就留空，不要为了凑数而写。
7. relevance：该公司与我们业务（酒店家具/灯具/软装/FF&E 采购）的相关度。value 只能是 高/中/低/未知；没有依据就写"未知"，必须带 source 和 reason。
{known}
只输出JSON，不要任何其他文字，格式：
{{"emails":[{{"value":"","source":""}}],
"whatsapp":[{{"value":"","source":""}}],
"phones":[{{"value":"","source":""}}],
"linkedin":[{{"value":"","source":""}}],
"facebook":[{{"value":"","source":""}}],
"instagram":[{{"value":"","source":""}}],
"other_social":[{{"value":"","source":""}}],
"key_people":[{{"name":"","title":"","source":""}}],
"import_signals":[{{"value":"","source":""}}],
"risk_flags":[{{"value":"","source":""}}],
"company_size":{{"value":"","source":""}},
"founded_year":{{"value":"","source":""}},
"customer_type":{{"value":"","source":""}},
"main_products":{{"value":"","source":""}},
"certifications":{{"value":"","source":""}},
"relevance":{{"value":"","reason":"","source":""}},
"summary":"用中文2-4句概括该公司（只基于片段内容）"}}

company_size/founded_year/customer_type/main_products/certifications/relevance 若片段中没有对应信息，整个对象设为 null。
customer_type 从以下选项中选（有依据才选）：批发商/零售商/工程商/制造商/进出口商/其他。

网页片段：
{chunks}"""


DIRECTORY_DOMAINS = ['europages.com', 'kompass.com', 'dnb.com', 'opencorporates.com', 'yellowpages.com', 'thomasnet.com', 'zoominfo.com', 'crunchbase.com']
TRADE_DOMAINS = ['importyeti.com', 'panjiva.com', 'volza.com', 'import-genius.com']
B2B_DOMAINS = ['alibaba.com', 'made-in-china.com', 'globalsources.com', 'tradekey.com', 'ec21.com']
SPANISH = {'MX', 'GT', 'SV', 'HN', 'NI', 'CR', 'PA', 'CO', 'VE', 'EC', 'PE', 'BO', 'CL', 'AR', 'UY', 'PY', 'DO', 'CU', 'PR', 'ES',
           'MEXICO', 'GUATEMALA', 'EL SALVADOR', 'HONDURAS', 'NICARAGUA', 'COSTA RICA', 'PANAMA', 'COLOMBIA', 'VENEZUELA', 'ECUADOR', 'PERU', 'BOLIVIA',
           'CHILE', 'ARGENTINA', 'URUGUAY', 'PARAGUAY', 'DOMINICAN REPUBLIC', 'CUBA', 'PUERTO RICO', 'SPAIN'}
DEPTH_COUNT = {'quick': 3, 'standard': 6, 'deep': 10}
DEPTH_LABEL = {'quick': '快速（约 3 次搜索）', 'standard': '标准（约 6 次搜索）', 'deep': '深度（约 10 次搜索）'}
# 第 2 轮：缺哪类信息 → 优先搜哪些维度
MISSING_TO_DIMS = {'emails': ['contact', 'official'], 'phones': ['contact'], 'whatsapp': ['contact', 'social'], 'linkedin': ['linkedin'],
                   'facebook': ['social'], 'instagram': ['social'], 'company_size': ['directory', 'linkedin'], 'founded_year': ['directory', 'linkedin'],
                   'customer_type': ['products', 'b2b', 'directory'], 'main_products': ['products', 'b2b'], 'key_people': ['linkedin', 'official'],
                   'import_signals': ['trade'], 'risk_flags': ['news']}


class QuotaError(Exception):
    """搜索额度用完 / 被限流：批量背调遇到它要暂停，而不是把剩下的客户全标成失败。"""


def source_type(url):
    u = (url or '').lower()
    for key, label in (('linkedin.com', 'LinkedIn'), ('facebook.com', 'Facebook'), ('instagram.com', 'Instagram'), ('importyeti.com', '进口记录'),
                       ('panjiva.com', '进口记录'), ('volza.com', '进口记录'), ('europages', '企业黄页'), ('kompass', '企业黄页'), ('dnb.com', '企业黄页'),
                       ('opencorporates', '工商登记'), ('alibaba.com', 'B2B平台'), ('made-in-china', 'B2B平台'), ('globalsources', 'B2B平台'),
                       ('yellowpages', '企业黄页'), ('crunchbase', '企业库'), ('zoominfo', '企业库')):
        if key in u:
            return label
    return '网页'


def plan_queries(company, country, site, depth, missing=None, spanish=False, round_no=1):
    """返回 [(维度, 查询, 限定域名 或 None)]，按优先级排好并截到该深度的数量。missing 是还缺的字段（第 2 轮用来把相关维度提前）。"""
    c = (company or '').strip()
    short = re.sub(r'[,.]?\s*(s\.?a\.?( de c\.?v\.?)?|s\.?r\.?l\.?|l\.?l\.?c\.?|inc\.?|ltd\.?|gmbh|co\.?|corp\.?|sas|s\.?l\.?)$', '', c, flags=re.I).strip() or c
    qs = []
    if not site:
        qs.append(('official', '"%s" %s official website' % (c, country), None))
    qs += [('contact', '"%s" %s contact email phone' % (c, country), None),
           ('linkedin', '%s %s' % (short, country), ['linkedin.com']),
           ('social', '%s %s' % (short, country), ['facebook.com', 'instagram.com']),
           ('directory', '"%s" %s company profile' % (c, country), DIRECTORY_DOMAINS)]
    if spanish:
        qs.append(('local', '"%s" %s empresa contacto teléfono correo distribuidor' % (c, country), None))
    qs += [('trade', '"%s" %s importer imports shipments' % (short, country), TRADE_DOMAINS),
           ('news', '"%s" %s reviews news complaints scam' % (c, country), None),
           ('products', '"%s" %s products lighting furniture catalog' % (short, country), None),
           ('b2b', '%s %s' % (short, country), B2B_DOMAINS)]
    if round_no > 1:                                                        # 第 2 轮：换一种问法（去掉引号、去掉公司后缀），避免搜到一模一样的结果
        qs = [(d, q.replace('"', '') if d in ('contact', 'products', 'news') else q, dom) for d, q, dom in qs]
    if missing:
        want = []
        for m in missing:
            for d in MISSING_TO_DIMS.get(m, []):
                if d not in want:
                    want.append(d)
        qs.sort(key=lambda x: (0 if x[0] in want else 1))
    return qs[:DEPTH_COUNT.get(depth, 6)]


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

    def tavily_search(self, key, query, max_results=5, include_domains=None):
        payload = {'query': query, 'max_results': max_results, 'include_raw_content': False}
        if include_domains:
            payload['include_domains'] = list(include_domains)
        try:
            data = self.http_json('https://api.tavily.com/search', payload, {'Authorization': 'Bearer ' + key})
        except urllib.error.HTTPError as e:
            if e.code in (429, 432, 433):
                raise QuotaError('Tavily 额度用完或被限流（HTTP %d）' % e.code)
            if e.code in (401, 403):
                raise QuotaError('Tavily API Key 无效或没有权限（HTTP %d）' % e.code)
            raise
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
    for k in LIST_KEYS + EXTRA_LIST_KEYS:
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
    rel = extracted.get('relevance')                                      # 相关度：没有有效来源一律当"未知"，不让模型凭印象打分
    if isinstance(rel, dict) and str(rel.get('value') or '').strip() in ('高', '中', '低') and ok(rel.get('source')):
        out['relevance'] = {'value': str(rel['value']).strip(), 'reason': str(rel.get('reason') or '').strip()[:200], 'source': rel['source']}
    else:
        out['relevance'] = None
    s = extracted.get('summary')
    out['summary'] = s.strip() if isinstance(s, str) else ''
    return out, dropped


def _norm(field, v):
    v = str(v).strip().lower()
    if field in ('whatsapp', 'phones'):
        return re.sub(r'\D', '', v)
    return v.rstrip('/')


def score_of(ex):
    """资料完整度 0~100：联系方式 40（邮箱25 + 电话/WhatsApp15）、官方社媒 20、公司画像 30、人物 10。"""
    sc = 0
    sc += 25 if ex.get('emails') else 0
    sc += 15 if (ex.get('whatsapp') or ex.get('phones')) else 0
    sc += 10 if ex.get('linkedin') else 0
    sc += 10 if (ex.get('facebook') or ex.get('instagram') or ex.get('other_social')) else 0
    sc += sum(10 for k in ('company_size', 'customer_type', 'main_products') if ex.get(k))
    sc += 10 if ex.get('key_people') else 0
    return sc


def missing_of(ex):
    m = []
    if not ex.get('emails'): m.append('emails')
    if not (ex.get('whatsapp') or ex.get('phones')): m.append('phones')
    if not ex.get('linkedin'): m.append('linkedin')
    if not (ex.get('facebook') or ex.get('instagram')): m.append('facebook')
    for k in ('company_size', 'customer_type', 'main_products'):
        if not ex.get(k): m.append(k)
    if not ex.get('key_people'): m.append('key_people')
    if not ex.get('import_signals'): m.append('import_signals')
    return m


class Enricher:
    def __init__(self, db, customers, net=None):
        self.db = db
        self.customers = customers
        self.net = net or Net()

    # ---------- 被用户否掉的信息 ----------
    def rejects(self, cid):
        out = {}
        for r in self.db.query('SELECT field,value FROM enrich_rejects WHERE customer_id=?', (cid,)):
            out.setdefault(r['field'], []).append(r['value'])
        return out

    def _filter_rejected(self, cid, ex):
        """代码层面再挡一次：已否掉的值，模型再提取也不让进来。返回被挡掉的条数。"""
        rej = self.rejects(cid)
        if not rej:
            return 0
        n = 0
        def bad(field, v):
            return any(_norm(field, v) == _norm(field, r) for r in rej.get(field, []))
        for k in LIST_KEYS + EXTRA_LIST_KEYS:
            keep = [i for i in ex.get(k, []) if not bad(k, i['value'])]
            n += len(ex.get(k, [])) - len(keep)
            ex[k] = keep
        keep = [p for p in ex.get('key_people', []) if not bad('key_people', p['name'])]
        n += len(ex.get('key_people', [])) - len(keep)
        ex['key_people'] = keep
        for k in SINGLE_KEYS:
            if ex.get(k) and bad(k, ex[k]['value']):
                ex[k] = None
                n += 1
        return n

    # ---------- 一轮背调 ----------
    def run(self, cid, depth='standard', round_no=1, parent_id=None, missing=None):
        cust = self.customers.require(cid)
        tavily_key = self.db.get_setting('tavily_key')
        ds_key = self.db.get_setting('deepseek_key')
        if not tavily_key or not ds_key:
            raise ApiError('请先在「设置」里填写 Tavily 和 DeepSeek 的 API Key')
        company = cust['company'] or cust['name']
        if not company:
            raise ApiError('该客户没有公司名也没有联系人姓名，无法搜索')
        if depth not in DEPTH_COUNT:
            depth = 'standard'
        eid = self.db.execute("INSERT INTO enrichments(customer_id,status,created_at,round,parent_id,depth) "
                              "VALUES(?,'running',datetime('now','localtime'),?,?,?)", (cid, round_no, parent_id, depth)).lastrowid
        sources, errors, plan = [], [], []
        try:
            site = (cust['website'] or '').split()[0] if cust['website'] else ''
            if site:
                base = (site if site.startswith('http') else 'https://' + site).rstrip('/')
                got = 0
                for path in ['/', '/contact', '/contact-us', '/about', '/about-us']:
                    t = self.net.fetch_page(base + path)
                    if t:
                        sources.append({'url': base + path, 'title': '官网直接抓取', 'content': t, 'type': '官网'})
                        got += 1
                    if got >= 3:
                        break
            country = cust['country'] or ''
            spanish = (country or '').strip().upper() in SPANISH
            quota_hit = None
            for dim, qy, doms in plan_queries(company, country, site, depth, missing, spanish, round_no):
                try:
                    res = self.net.tavily_search(tavily_key, qy, 5, doms) if doms else self.net.tavily_search(tavily_key, qy, 5)
                    for r in res:
                        r['type'] = source_type(r.get('url'))
                    sources.extend(res)
                    plan.append({'dim': dim, 'query': qy, 'domains': doms or [], 'hits': len(res)})
                except QuotaError as e:
                    quota_hit = e
                    errors.append(str(e))
                    break
                except Exception as e:
                    errors.append('Tavily搜索失败(%s): %s' % (qy, e))
                    plan.append({'dim': dim, 'query': qy, 'domains': doms or [], 'hits': 0})
            seen, uniq = set(), []
            for s in sources:
                if s['url'] and s['url'] not in seen and s['content']:
                    seen.add(s['url'])
                    uniq.append(s)
            sources = uniq
            if not sources:
                if quota_hit:
                    raise quota_hit
                raise RuntimeError('没有搜到任何可用的网页内容。' + '；'.join(errors))
            raw = self.net.deepseek_chat(ds_key, self.db.get_setting('deepseek_model', 'deepseek-chat'),
                                         self._prompt(company, sources, self.rejects(cid)))
            try:
                parsed = json.loads(re.sub(r'^```(json)?|```$', '', raw.strip(), flags=re.M).strip())
                if not isinstance(parsed, dict):
                    raise ValueError
            except ValueError:
                raise RuntimeError('模型返回的不是有效 JSON，请重试')
            extracted, dropped = sanitize(parsed, {s['url'] for s in sources})
            if dropped:
                errors.append('已丢弃 %d 条来源无效的信息: %s' % (len(dropped), '；'.join(dropped[:5])))
            blocked = self._filter_rejected(cid, extracted)
            if blocked:
                errors.append('已自动排除 %d 条你之前确认无效的信息' % blocked)
            score = score_of(extracted)
            self.db.execute("UPDATE enrichments SET status='pending', sources=?, extracted=?, error=?, score=?, plan=? WHERE id=?",
                            (json.dumps(sources, ensure_ascii=False), json.dumps(extracted, ensure_ascii=False),
                             '；'.join(errors), score, json.dumps(plan, ensure_ascii=False), eid))
            return {'enrichment_id': eid, 'extracted': extracted, 'sources': sources, 'warnings': errors, 'score': score,
                    'round': round_no, 'plan': plan, 'missing': missing_of(extracted)}
        except Exception as e:
            self.db.execute("UPDATE enrichments SET status='failed', sources=?, error=?, plan=? WHERE id=?",
                            (json.dumps(sources, ensure_ascii=False), str(e), json.dumps(plan, ensure_ascii=False), eid))
            if isinstance(e, QuotaError):
                raise
            raise ApiError('背调失败: %s' % e, 502, {'enrichment_id': eid})

    @staticmethod
    def _prompt(company, sources, rejects=None):
        chunks, budget = [], 26000
        for i, s in enumerate(sources):
            c = '[片段%d] 来源URL: %s\n标题: %s\n内容: %s\n' % (i + 1, s['url'], s.get('title', ''), s['content'])
            if budget - len(c) < 0:
                break
            budget -= len(c)
            chunks.append(c)
        known = ''
        if rejects:
            lines = ['  - %s: %s' % (k, '、'.join(v[:20])) for k, v in rejects.items()]
            known = '8. 以下信息用户已核对确认【无效】，不要再提取（即使片段里出现）：\n' + '\n'.join(lines) + '\n'
        return PROMPT.format(company=company, chunks='\n'.join(chunks), known=known)

    def get(self, eid):
        en = self.db.one('SELECT * FROM enrichments WHERE id=?', (eid,))
        if not en:
            raise ApiError('背调记录不存在', 404)
        en['sources'] = json.loads(en['sources'] or '[]')
        en['extracted'] = json.loads(en['extracted'] or '{}')
        en['plan'] = json.loads(en['plan'] or '[]')
        en['missing'] = missing_of(en['extracted'])
        return en

    def list_for_customer(self, cid):
        return self.db.query("SELECT id,status,round,depth,score,created_at,error FROM enrichments WHERE customer_id=? ORDER BY id DESC", (cid,))

    # ---------- 继续下一轮 ----------
    def continue_round(self, eid, sel, rejected=None, depth='standard'):
        """sel: 用户确认有效、要写入档案的项（同 apply；可为空）。rejected: {字段: [值]} 用户判定无效的项。
        写入 + 记住被否掉的 + 针对还缺的信息再来一轮。"""
        en = self.get(eid)
        if en['status'] != 'pending':
            raise ApiError('该背调结果%s，不能继续' % ('已处理' if en['status'] == 'applied' else '状态为 ' + en['status']), 409)
        self.reject(eid, rejected or {})
        if any(sel.get(k) for k in sel):
            self.apply(eid, sel)
        else:
            self.db.execute("UPDATE enrichments SET status='applied' WHERE id=?", (eid,))
        cid = en['customer_id']
        # 缺口 = 本轮没找到、且档案里也还没有的信息
        cust = self.customers.require(cid)
        miss = [m for m in missing_of(en['extracted'])
                if not (m == 'emails' and cust['emails']) and not (m == 'linkedin' and cust['linkedin'])
                and not (m == 'phones' and cust['whatsapp']) and not (m == 'facebook' and cust['facebook'])]
        return self.run(cid, depth, en['round'] + 1, eid, miss)

    def reject(self, eid, rejected):
        """记住用户判定无效的值。只接受确实出现在该次提取结果里的值（接口不能随便塞）。"""
        en = self.get(eid)
        ex = en['extracted']
        n = 0
        for field, vals in (rejected or {}).items():
            if field == 'key_people':
                have = [p['name'] for p in ex.get('key_people', [])]
            elif field in SINGLE_KEYS:
                have = [ex[field]['value']] if ex.get(field) else []
            elif field in LIST_KEYS + EXTRA_LIST_KEYS:
                have = [i['value'] for i in ex.get(field, [])]
            else:
                continue
            for v in vals or []:
                if str(v).strip() in have:
                    self.db.execute('INSERT OR IGNORE INTO enrich_rejects(customer_id,field,value) VALUES(?,?,?)',
                                    (en['customer_id'], field, str(v).strip()))
                    n += 1
        return n

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
        for k, label in (('import_signals', '进口线索'), ('risk_flags', '风险提示')):
            vals = pick(k, k)
            if vals:
                notes.append('%s: %s' % (label, '；'.join(vals)))
        if sel.get('relevance'):
            rel = ex.get('relevance')
            if not rel or rel['value'] != str(sel['relevance']).strip():
                raise ApiError('所选内容不在背调结果中（relevance）')
            notes.append('与我们业务相关度: %s（%s）' % (rel['value'], rel['reason']))
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
