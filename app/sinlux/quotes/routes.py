# -*- coding: utf-8 -*-
"""报价单 API。"""
import re

from ..core.http import FileResponse
from ..core.util import ApiError

_id = lambda req: int(req.params['id'])


def wa_number(raw):
    """客户 WhatsApp 字段里的第一个号码 → wa.me 用的纯数字（含国家码）。号码不像样返回空串。"""
    first = re.split(r'[;；,，\n/]', raw or '')[0]
    digits = re.sub(r'\D', '', first)
    return digits if 8 <= len(digits) <= 15 else ''


def register(r):
    @r.get('/api/quotes')
    def list_quotes(ctx, req):
        try:
            cid = int(req.arg('customer_id')) if req.arg('customer_id') else None
            limit, offset = int(req.arg('limit', 100) or 100), int(req.arg('offset', 0) or 0)
        except ValueError:
            raise ApiError('参数必须是整数')
        rows, total = ctx.quotes.list(cid, req.arg('status') or None, req.arg('search').strip(), limit, offset)
        return {'quotes': rows, 'total': total}

    @r.post('/api/quotes')
    def create(ctx, req):
        return {'ok': True, **ctx.quotes.create(req.json())}

    @r.get('/api/quotes/{id}')
    def get(ctx, req):
        q = ctx.quotes.get(_id(req))
        q['wa_number'] = wa_number(q['whatsapp'])
        return {'quote': q}

    @r.put('/api/quotes/{id}')
    def update(ctx, req):
        ctx.quotes.update(_id(req), req.json())
        return {'ok': True, 'quote': ctx.quotes.get(_id(req))}

    @r.delete('/api/quotes/{id}')
    def delete(ctx, req):
        qid = _id(req)
        q = ctx.quotes.get(qid)
        if q['status'] == 'accepted' and not req.json().get('confirm'):
            raise ApiError('该报价单已成交，删除会同时撤销它写入产品售价历史的成交价，请确认', 409, {'needs_confirm': True})
        ctx.quotes.delete(qid)
        return {'ok': True}

    @r.post('/api/quotes/{id}/status')
    def status(ctx, req):
        b = req.json()
        ctx.quotes.set_status(_id(req), b.get('status'), b.get('feedback'))
        return {'ok': True, 'quote': ctx.quotes.get(_id(req))}

    @r.post('/api/quotes/{id}/duplicate')
    def duplicate(ctx, req):
        return {'ok': True, **ctx.quotes.duplicate(_id(req))}

    @r.post('/api/quotes/{id}/pdf')
    def pdf(ctx, req):
        name = ctx.quote_export.pdf(ctx.quotes.get(_id(req)))
        return {'ok': True, 'url': '/exports/' + name, 'filename': name}

    @r.post('/api/quotes/{id}/excel')
    def excel(ctx, req):
        name = ctx.quote_export.excel(ctx.quotes.get(_id(req)))
        return {'ok': True, 'url': '/exports/' + name, 'filename': name}

    @r.get('/api/quotes/{id}/whatsapp')
    def whatsapp(ctx, req):
        q = ctx.quotes.get(_id(req))
        return {'text': ctx.quote_export.whatsapp(q), 'wa_number': wa_number(q['whatsapp'])}

    # ---------- 报价单抬头 / WhatsApp 模板 ----------
    @r.get('/api/settings/quote')
    def get_settings(ctx, req):
        return ctx.quote_settings.get()

    @r.put('/api/settings/quote')
    def put_settings(ctx, req):
        return {'ok': True, **ctx.quote_settings.update(req.json())}
