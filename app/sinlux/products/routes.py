# -*- coding: utf-8 -*-
"""产品库 API。"""
import os

from ..core.http import FileResponse
from ..core.util import ApiError


def register(r):
    # ---------- 类目 / 规格字段 / SKU ----------
    @r.get('/api/categories')
    def categories(ctx, req):
        return {'categories': ctx.catalog.categories()}

    @r.get('/api/categories/{id}/fields')
    def cat_fields(ctx, req):
        return {'fields': ctx.catalog.fields(int(req.params['id']))}

    @r.get('/api/categories/{id}/next_sku')
    def next_sku(ctx, req):
        return {'sku': ctx.catalog.next_sku(int(req.params['id']), req.arg('subcategory'))}

    @r.get('/api/categories/{id}/prefixes')
    def prefixes(ctx, req):
        return ctx.catalog.prefixes(int(req.params['id']))

    @r.put('/api/categories/{id}/prefix')
    def set_prefix(ctx, req):
        return {'ok': True, 'prefix': ctx.catalog.set_category_prefix(int(req.params['id']), req.json().get('prefix'))}

    @r.post('/api/categories/{id}/sub_prefixes')
    def upsert_sub(ctx, req):
        b = req.json()
        ctx.catalog.upsert_sub_prefix(int(req.params['id']), b.get('subcategory_value'), b.get('prefix'))
        return {'ok': True}

    @r.delete('/api/sub_prefixes/{id}')
    def del_sub(ctx, req):
        ctx.catalog.delete_sub_prefix(int(req.params['id']))
        return {'ok': True}

    # ---------- 汇率 ----------
    @r.get('/api/rate')
    def rate(ctx, req):
        return ctx.rates.info()

    @r.put('/api/rate')
    def set_rate(ctx, req):
        return {'ok': True, **ctx.rates.set_manual(req.json().get('rate'))}

    @r.post('/api/rate/fetch')
    def fetch_rate(ctx, req):
        return {'ok': True, **ctx.rates.fetch()}

    # ---------- 产品 ----------
    @r.get('/api/products')
    def products(ctx, req):
        try:
            limit, offset = int(req.arg('limit', 100) or 100), int(req.arg('offset', 0) or 0)
            cat = int(req.arg('category_id')) if req.arg('category_id') else None
        except ValueError:
            raise ApiError('参数必须是整数')
        rows, total = ctx.products.list(req.arg('search').strip(), cat, limit, offset)
        return {'products': rows, 'total': total}

    @r.post('/api/products')
    def create(ctx, req):
        pid = ctx.products.create(req.json())
        return {'ok': True, 'id': pid, 'product': ctx.products.get(pid)}

    # 注意：/api/products/merge 必须在 {id} 路由之前匹配 —— {id} 只匹配数字，所以不冲突
    @r.post('/api/products/merge')
    def merge(ctx, req):
        b = req.json()
        if not b.get('survivor_id'):
            raise ApiError('请指定保留哪个产品')
        return {'ok': True, **ctx.products.merge(b['survivor_id'], b.get('merge_ids'))}

    @r.get('/api/products/{id}')
    def get(ctx, req):
        return {'product': ctx.products.get(int(req.params['id']))}

    @r.put('/api/products/{id}')
    def update(ctx, req):
        pid = int(req.params['id'])
        ctx.products.update(pid, req.json())
        return {'ok': True, 'product': ctx.products.get(pid)}

    @r.get('/api/products/{id}/impact')
    def impact(ctx, req):
        return {'impact': ctx.products.impact(int(req.params['id']))}

    @r.delete('/api/products/{id}')
    def delete(ctx, req):
        pid = int(req.params['id'])
        impact = ctx.products.impact(pid)
        if not req.json().get('confirm'):
            raise ApiError('删除产品会同时删除其价格历史和供应商比价，请确认', 409,
                           {'needs_confirm': True, 'impact': impact})
        ctx.products.delete(pid)
        return {'ok': True, 'deleted': impact}

    # ---------- 价格历史 ----------
    @r.get('/api/products/{id}/price_history')
    def history(ctx, req):
        pid = int(req.params['id'])
        ctx.products.require(pid)
        return {'history': ctx.history.list(pid)}

    @r.post('/api/products/{id}/price_history')
    def add_history(ctx, req):
        pid = int(req.params['id'])
        ctx.products.require(pid)
        recorded = ctx.history.add_manual(pid, req.json())
        return {'ok': True, 'recorded': recorded, 'product': ctx.products.get(pid)}

    @r.delete('/api/price_history/{id}')
    def del_history(ctx, req):
        pid = ctx.history.delete(int(req.params['id']))
        return {'ok': True, 'product': ctx.products.get(pid)}

    @r.get('/api/products/{id}/last_price')
    def last_price(ctx, req):
        pid = int(req.params['id'])
        ctx.products.require(pid)
        cid = req.arg('customer_id')
        return ctx.history.last_prices(pid, int(cid) if cid.isdigit() else None)

    # ---------- 供应商比价 ----------
    @r.get('/api/products/{id}/suppliers')
    def suppliers(ctx, req):
        return {'suppliers': ctx.suppliers.list(int(req.params['id']))}

    @r.post('/api/products/{id}/suppliers')
    def add_supplier(ctx, req):
        pid = int(req.params['id'])
        ctx.suppliers.create(pid, req.json())
        return {'ok': True, 'suppliers': ctx.suppliers.list(pid), 'product': ctx.products.get(pid)}

    @r.post('/api/suppliers/{id}/adopt')
    def adopt(ctx, req):
        res = ctx.suppliers.adopt(int(req.params['id']))
        return {'ok': True, 'suppliers': ctx.suppliers.list(res['product_id']), 'product': ctx.products.get(res['product_id'])}

    @r.delete('/api/suppliers/{id}')
    def del_supplier(ctx, req):
        ctx.suppliers.delete(int(req.params['id']))
        return {'ok': True}

    # ---------- 产品图片 ----------
    @r.get('/uploads/{name}')
    def upload(ctx, req):
        name = os.path.basename(req.params['name'])
        if name.rsplit('.', 1)[-1].lower() not in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
            raise ApiError('文件不存在', 404)
        return FileResponse(os.path.join(ctx.uploads_dir, name))
