# -*- coding: utf-8 -*-
"""报价单抬头（公司信息）与 WhatsApp 文案模板。存在 settings 表，键名沿用 v4.4。"""
import re

from ..core.util import ApiError

KEYS = ('company_name', 'company_email', 'company_phone', 'company_address', 'whatsapp_template')
LIMITS = {'company_name': 100, 'company_email': 120, 'company_phone': 60, 'company_address': 300, 'whatsapp_template': 4000}

DEFAULT_WA_TEMPLATE = (
    "Hi {customer_name},\n\n"
    "Please find our quotation {quote_no}:\n"
    "{items}\n"
    "Total: {currency} {total}\n"
    "Lead time: {lead_time}\n"
    "Payment: {payment_terms}\n"
    "Validity: {valid_days} days\n\n"
    "Best regards,\n{sender}"
)
VARIABLES = ['customer_name', 'company', 'quote_no', 'items', 'currency', 'total', 'lead_time', 'payment_terms',
             'valid_days', 'valid_until', 'shipping_terms', 'notes', 'sender']
_VAR = re.compile(r'\{(\w+)\}')


def render_template(tpl, values):
    """只替换已知变量 {name}；未知的 {xxx}、单个花括号一律原样保留。
    不用 str.format：模板由用户填写，format 的 {0.__class__} 之类语法会读取对象内部信息，也容易因多一个花括号整体报错。"""
    return _VAR.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), tpl)


class QuoteSettings:
    def __init__(self, db):
        self.db = db

    def get(self):
        out = {k: self.db.get_setting(k) for k in KEYS}
        out['company_name_effective'] = out['company_name'] or 'SINLUX'
        out['whatsapp_template_effective'] = out['whatsapp_template'] or DEFAULT_WA_TEMPLATE
        out['variables'] = VARIABLES
        return out

    def update(self, data):
        clean = {}
        for k in KEYS:
            if k in data and data[k] is not None:
                v = str(data[k]).strip() if k != 'whatsapp_template' else str(data[k]).replace('\r\n', '\n')
                if len(v) > LIMITS[k]:
                    raise ApiError('%s过长（最多 %d 个字符）' % (k, LIMITS[k]))
                clean[k] = v
        em = clean.get('company_email')
        if em and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', em):
            raise ApiError('公司邮箱格式不正确')
        with self.db.tx():
            for k, v in clean.items():
                self.db.set_setting(k, v)
        return self.get()

    def company(self):
        return {'name': self.db.get_setting('company_name') or 'SINLUX', 'address': self.db.get_setting('company_address'),
                'email': self.db.get_setting('company_email'), 'phone': self.db.get_setting('company_phone')}

    def template(self):
        return self.db.get_setting('whatsapp_template') or DEFAULT_WA_TEMPLATE
