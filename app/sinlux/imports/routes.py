# -*- coding: utf-8 -*-
"""产品 Excel 导入向导 + PI 导入 API。"""
import os

from ..core.http import FileResponse
from ..core.util import ApiError
from .pi_import import MAX_PI_UPLOAD
from .docimport import MAX_UPLOAD as DOC_MAX_UPLOAD
from .product_import import MAX_UPLOAD


def register(r):
    P = '/api/import/products'

    @r.post(P + '/upload')
    def upload(ctx, req):
        name = req.upload_filename('products.xlsx')
        sid, path = ctx.product_import.new_path(name)
        try:
            req.save_upload(path, MAX_UPLOAD)
            return ctx.product_import.start(sid)
        except Exception:
            ctx.product_import.discard(sid)
            raise

    @r.post(P + '/{sid}/sheet')
    def sheet(ctx, req):
        b = req.json()
        return ctx.product_import.choose_sheet(req.params['sid'], b.get('sheet'), 1 if b.get('header_row') is None else b.get('header_row'))

    @r.post(P + '/{sid}/category')
    def category(ctx, req):
        try:
            cid = int(req.json().get('category_id'))
        except (TypeError, ValueError):
            raise ApiError('请选择类目')
        return ctx.product_import.choose_category(req.params['sid'], cid)

    @r.post(P + '/{sid}/preview')
    def preview(ctx, req):
        b = req.json()
        return ctx.product_import.preview(req.params['sid'], b.get('mapping'), b.get('currency') or 'CNY',
                                          b.get('profit_rate', 0.25), b.get('extract_images', True) is not False)

    @r.get(P + '/{sid}/rows')
    def rows(ctx, req):
        return ctx.product_import.rows(req.params['sid'], req.arg('offset', 0) or 0, req.arg('limit', 200) or 200)

    @r.get(P + '/{sid}/image/{name}')
    def image(ctx, req):
        return FileResponse(ctx.product_import.image_path(req.params['sid'], req.params['name']))

    @r.post(P + '/{sid}/apply')
    def apply(ctx, req):
        return {'ok': True, **ctx.product_import.apply(req.params['sid'], req.json().get('skip_rows') or [])}

    @r.delete(P + '/{sid}')
    def cancel(ctx, req):
        ctx.product_import.discard(req.params['sid'])
        return {'ok': True}

    # ---------- PI ----------
    @r.post('/api/import/pi/preview')
    def pi_preview(ctx, req):
        name = req.upload_filename('pi.xls')
        path = ctx.pi_import.new_path(name)
        try:
            req.save_upload(path, MAX_PI_UPLOAD)
        except Exception:
            if os.path.exists(path):
                os.remove(path)
            raise
        return ctx.pi_import.preview(path, name)

    @r.post('/api/import/pi/apply')
    def pi_apply(ctx, req):
        return {'ok': True, **ctx.pi_import.apply(req.json())}

    @r.get('/api/import/pi/customers')
    def pi_customers(ctx, req):
        q = req.arg('q').strip()
        rows, _ = ctx.customers.list(q, '', '', '', 20, 0)
        return {'customers': [{'id': c['id'], 'company': c['company'], 'name': c['name'], 'emails': c['emails']} for c in rows]}

    # ---------- 文档导入（PI / 供应商报价单 / 产品清单；xls、xlsx） ----------
    D = '/api/import/doc'

    @r.post(D + '/upload')
    def doc_upload(ctx, req):
        name = req.upload_filename('doc.xlsx')
        sid, path = ctx.doc_import.new_path(name)
        try:
            req.save_upload(path, DOC_MAX_UPLOAD)
            return ctx.doc_import.start(sid)
        except Exception:
            ctx.doc_import.discard(sid)
            raise

    @r.post(D + '/{sid}/reparse')
    def doc_reparse(ctx, req):
        b = req.json()
        return ctx.doc_import.analyze(req.params['sid'], b.get('sheet') or None, b.get('header_row'), b.get('columns') or None, b.get('kind'), b.get('currency'))

    @r.get(D + '/{sid}/image/{name}')
    def doc_image(ctx, req):
        return FileResponse(ctx.doc_import.image_path(req.params['sid'], req.params['name']))

    @r.post(D + '/{sid}/apply')
    def doc_apply(ctx, req):
        return {'ok': True, **ctx.doc_import.apply(req.params['sid'], req.json())}

    @r.delete(D + '/{sid}')
    def doc_cancel(ctx, req):
        ctx.doc_import.discard(req.params['sid'])
        return {'ok': True}
