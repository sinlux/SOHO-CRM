# -*- coding: utf-8 -*-
"""智能录入：粘贴任意信息，识别类型并查重。"""
import re
import urllib.parse

from ..core.util import like

EMAIL_RE = re.compile(r'^[\w.+-]+@[\w-]+(\.[\w-]+)+$')
URL_RE = re.compile(r'^(https?://)?([\w-]+\.)+[a-z]{2,}(:\d+)?(/\S*)?$', re.I)
FREE_MAIL = {'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'qq.com', '163.com', '126.com',
             'yandex.ru', 'icloud.com', 'aol.com', 'protonmail.com', 'live.com', 'msn.com', 'sina.com',
             'foxmail.com', 'gmx.com', 'mail.ru', 'yahoo.co.uk', 'googlemail.com'}


def domain_of(url):
    u = url if '://' in url else 'http://' + url
    try:
        d = urllib.parse.urlparse(u).netloc.lower().split('@')[-1].split(':')[0]
    except ValueError:
        return ''
    return d[4:] if d.startswith('www.') else d


def classify(text):
    """返回 (type, fields)。"""
    low = text.lower()
    if EMAIL_RE.match(text):
        fields = {'emails': text}
        dom = text.split('@')[1].lower()
        if dom not in FREE_MAIL:           # 企业邮箱推出公司网站
            fields['website'] = 'www.' + dom
        return 'email', fields
    if 'linkedin.com' in low:
        return 'linkedin', {'linkedin': text}
    if 'facebook.com' in low or 'fb.com' in low:
        return 'facebook', {'facebook': text}
    if 'instagram.com' in low:
        return 'social', {'other_social': text}
    if URL_RE.match(text) and ' ' not in text:
        return 'website', {'website': text}
    return 'company', {'company': text}


def parse_input(db, text):
    text = (text or '').strip()
    typ, fields = classify(text)
    dups, seen = [], set()

    def hit(rows, why):
        for r in rows:
            if r['id'] not in seen:
                seen.add(r['id'])
                dups.append({'id': r['id'], 'company': r['company'], 'name': r['name'],
                             'emails': r['emails'], 'why': why})

    if typ == 'email':
        hit(db.query("SELECT * FROM customers WHERE emails LIKE ? ESCAPE '\\'", (like(text),)), '邮箱相同')
    dom = ''
    if typ in ('website', 'linkedin', 'facebook', 'social'):
        dom = domain_of(text)
    elif fields.get('website'):
        dom = domain_of(fields['website'])
    # 社媒域名（linkedin.com 等）不能当作"公司域名"去查重，否则会把所有人都判为重复
    if dom and dom not in ('linkedin.com', 'facebook.com', 'fb.com', 'instagram.com'):
        hit(db.query("SELECT * FROM customers WHERE website LIKE ? ESCAPE '\\' OR emails LIKE ? ESCAPE '\\'",
                     (like(dom), like('@' + dom))), '域名相同: ' + dom)
    if typ in ('linkedin', 'facebook', 'social'):
        col = {'linkedin': 'linkedin', 'facebook': 'facebook', 'social': 'other_social'}[typ]
        hit(db.query("SELECT * FROM customers WHERE %s LIKE ? ESCAPE '\\'" % col, (like(text.rstrip('/')),)),
            '社媒链接相同')
    if typ == 'company' and len(text) >= 3:
        hit(db.query("SELECT * FROM customers WHERE company LIKE ? ESCAPE '\\'", (like(text),)), '公司名相似')
    return {'input': text, 'type': typ, 'fields': fields, 'duplicates': dups[:8]}
