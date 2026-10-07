# -*- coding: utf-8 -*-
"""报价单抬头（公司信息）与 WhatsApp 文案模板。存在 settings 表，键名沿用 v4.4。"""
import os
import re
import uuid

from ..core.util import ApiError

KEYS = ('company_name', 'contact_name', 'company_email', 'company_phone', 'company_address', 'bank_account_number', 'bank_name',
        'bank_swift', 'bank_account_name', 'bank_address', 'show_bank', 'whatsapp_template')
LIMITS = {'company_name': 100, 'contact_name': 60, 'company_email': 120, 'company_phone': 60, 'company_address': 300,
          'bank_account_number': 60, 'bank_name': 120, 'bank_swift': 30, 'bank_account_name': 120, 'bank_address': 300,
          'show_bank': 1, 'whatsapp_template': 4000}
# 首次使用的默认值（来自用户提供的资料）。只在该键从未保存过时生效；用户清空后保存即为空，不会再被默认值顶回来。
DEFAULTS = {'company_name': 'SINLUX', 'contact_name': 'jun', 'company_email': 'jun@sinluxlight.com', 'company_phone': '+86-18938260518',
            'company_address': 'No.42 Zhan Qian Road,Chashan Town, Dongguan City, China',
            'bank_account_number': '559000017172230', 'bank_name': 'BANK OF DONGGUAN CO..LTD', 'bank_swift': 'DGCBCN22',
            'bank_account_name': 'DGDL SINLUX MYSH',
            'bank_address': 'BANK OF DONGGUAN BUILDING,NO.21TIYUROAD, GUANCHENG DIST,DONGGUAN CITY,GUANGDONGPROVINCE,CHINA',
            'show_bank': '1'}
LOGO_KEY = 'company_logo'
BUILTIN_LOGO = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'assets', 'logo.png')

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

    def _val(self, k):
        r = self.db.one('SELECT value FROM settings WHERE key=?', (k,))
        if r is None or r['value'] is None:
            return DEFAULTS.get(k, '')
        return r['value']

    def get(self):
        out = {k: self._val(k) for k in KEYS}
        out['logo_custom'] = bool(self.db.get_setting(LOGO_KEY))
        out['company_name_effective'] = out['company_name'] or 'SINLUX'
        out['whatsapp_template_effective'] = out['whatsapp_template'] or DEFAULT_WA_TEMPLATE
        out['variables'] = VARIABLES
        return out

    def update(self, data):
        clean = {}
        for k in KEYS:
            if k in data and data[k] is not None:
                if k == 'show_bank':
                    clean[k] = '1' if data[k] in (True, 1, '1', 'true') else '0'
                    continue
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
        v = self._val
        return {'name': v('company_name') or 'SINLUX', 'contact': v('contact_name'), 'address': v('company_address'),
                'email': v('company_email'), 'phone': v('company_phone'),
                'bank': [(l, v(k)) for l, k in (('Account Number', 'bank_account_number'), ('Bank Name', 'bank_name'), ('SWIFT Code', 'bank_swift'),
                                                ('Account Name', 'bank_account_name'), ('Bank Address', 'bank_address')) if v(k)]
                if v('show_bank') == '1' else []}

    def template(self):
        return self.db.get_setting('whatsapp_template') or DEFAULT_WA_TEMPLATE

    # ---------- LOGO ----------
    def set_logo(self, uploads_dir, raw):
        from ..core import imaging
        from ..core.images import decode_data_url
        _, data = decode_data_url(raw)
        try:
            png = imaging.logo_png(data)
        except Exception:
            raise ApiError('无法识别这张图片，请换 PNG/JPG')
        os.makedirs(uploads_dir, exist_ok=True)
        name = 'company_logo_%s.png' % uuid.uuid4().hex[:8]
        with open(os.path.join(uploads_dir, name), 'wb') as f:
            f.write(png)
        old = self.db.get_setting(LOGO_KEY)
        self.db.set_setting(LOGO_KEY, name)
        if old:
            try:
                os.remove(os.path.join(uploads_dir, os.path.basename(old)))
            except OSError:
                pass

    def reset_logo(self, uploads_dir):
        old = self.db.get_setting(LOGO_KEY)
        self.db.set_setting(LOGO_KEY, '')
        if old:
            try:
                os.remove(os.path.join(uploads_dir, os.path.basename(old)))
            except OSError:
                pass

    def logo_path(self, uploads_dir):
        """自定义 LOGO（若文件还在）> 内置 LOGO。"""
        n = self.db.get_setting(LOGO_KEY)
        if n:
            p = os.path.join(uploads_dir, os.path.basename(n))
            if os.path.exists(p):
                return p
        return BUILTIN_LOGO
