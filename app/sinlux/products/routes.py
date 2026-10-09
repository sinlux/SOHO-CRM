# -*- coding: utf-8 -*-
"""产品库 API。"""
import os
import time
import urllib.parse

from ..core.http import FileResponse
from ..core.util import ApiError
from .media import MAX_DOC_BYTES, MAX_IMAGE_UPLOAD

_id = lambda req: int(req.params['id'])


def register(r):
    # ---------- 类目 / 规格字段 / SKU ----------
    @r.get('/api/categories')
    def categories(ctx, req):
        return {'categories': ctx.catalog.categories()}

    @r.post('/api/categories')
    def create_cat(ctx, req):
        b = req.json()
        return {'ok': True, 'id': ctx.catalog.create_category(b.get('name'), b.get('icon'), b.get('prefix'))}

    @r.put('/api/categories/{id}')
    def update_cat(ctx, req):
        b = req.json()
        ctx.catalog.update_category(_id(req), b.get('name'), b.get('icon'))
        return {'ok': True}

    @r.delete('/api/categories/{id}')
    def delete_cat(ctx, req):
        ctx.catalog.delete_category(_id(req))
        return {'ok': True}

    @r.post('/api/categories/{id}/fields')
    def create_field(ctx, req):
        b = req.json()
        return {'ok': True, 'id': ctx.catalog.create_field(_id(req), b.get('label'), b.get('type'), b.get('unit'), b.get('options'),
                                                          b.get('placeholder'), b.get('applies_to'))}

    @r.put('/api/fields/{id}')
    def update_field(ctx, req):
        b = req.json()
        ctx.catalog.update_field(_id(req), **{k: b[k] for k in ('label', 'unit', 'options', 'placeholder', 'applies_to') if k in b})
        return {'ok': True}

    @r.get('/api/fields/{id}/usage')
    def field_usage(ctx, req):
        return {'products_with_value': ctx.catalog.field_usage(_id(req))}

    @r.delete('/api/fields/{id}')
    def delete_field(ctx, req):
        ctx.catalog.delete_field(_id(req))
        return {'ok': True}

    @r.post('/api/fields/{id}/move')
    def move_field(ctx, req):
        ctx.catalog.move_field(_id(req), req.json().get('direction'))
        return {'ok': True}

    @r.get('/api/categories/{id}/subcategories')
    def subs(ctx, req):
        return {'subcategories': ctx.catalog.subcategories(_id(req))}

    @r.post('/api/categories/{id}/subcategories')
    def add_sub(ctx, req):
        b = req.json()
        ctx.catalog.add_subcategory(_id(req), b.get('name'), b.get('prefix'))
        return {'ok': True}

    @r.put('/api/categories/{id}/subcategories')
    def rename_sub(ctx, req):
        b = req.json()
        ctx.catalog.rename_subcategory(_id(req), b.get('old'), b.get('new'), b.get('prefix'))
        return {'ok': True}

    @r.post('/api/categories/{id}/subcategories/delete')
    def delete_sub(ctx, req):
        return {'ok': True, **ctx.catalog.delete_subcategory(_id(req), req.json().get('name'))}

    @r.get('/api/hs_codes')
    def hs_codes(ctx, req):
        """本库里已经用过的 HS 编码（按使用次数排序，带几个示例产品）：同类产品直接复用，不用再查。"""
        q = req.arg('q').strip()
        where, args = "TRIM(COALESCE(hs_code,''))<>''", []
        if q:
            like = '%' + q.replace('%', r'\%').replace('_', r'\_') + '%'
            where += " AND (hs_code LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\' OR spec_text LIKE ? ESCAPE '\\')"
            args = [like] * 3
        rows = ctx.db.query('SELECT hs_code, COUNT(*) AS n, GROUP_CONCAT(name, \' / \') AS names FROM products WHERE %s GROUP BY hs_code ORDER BY n DESC, hs_code LIMIT 30' % where, args)
        for r in rows:
            r['names'] = ' / '.join((r['names'] or '').split(' / ')[:3])[:120]
        return {'codes': rows}

    @r.get('/api/categories/{id}/fields')
    def cat_fields(ctx, req):
        return {'fields': ctx.catalog.fields(_id(req))}

    @r.get('/api/categories/{id}/next_sku')
    def next_sku(ctx, req):
        return {'sku': ctx.catalog.next_sku(_id(req), req.arg('subcategory'))}

    @r.get('/api/categories/{id}/prefixes')
    def prefixes(ctx, req):
        return ctx.catalog.prefixes(_id(req))

    @r.put('/api/categories/{id}/prefix')
    def set_prefix(ctx, req):
        return {'ok': True, 'prefix': ctx.catalog.set_category_prefix(_id(req), req.json().get('prefix'))}

    @r.post('/api/categories/{id}/sub_prefixes')
    def upsert_sub(ctx, req):
        b = req.json()
        ctx.catalog.upsert_sub_prefix(_id(req), b.get('subcategory_value'), b.get('prefix'))
        return {'ok': True}

    @r.delete('/api/sub_prefixes/{id}')
    def del_sub(ctx, req):
        ctx.catalog.delete_sub_prefix(_id(req))
        return {'ok': True}

    # ---------- 汇率（中国银行·美元现汇买入价） ----------
    @r.get('/api/rate')
    def rate(ctx, req):
        return {**ctx.rates.info(), 'history': ctx.rates.history(30)}

    @r.put('/api/rate')
    def set_rate(ctx, req):
        b = req.json()
        if b.get('boc_buy') not in (None, ''):          # 填中行牌价页上看到的「现汇买入价」（人民币/100美元）
            return {'ok': True, **ctx.rates.set_manual_boc(b['boc_buy'])}
        return {'ok': True, **ctx.rates.set_manual(b.get('rate'))}

    @r.put('/api/rate/mode')
    def set_rate_mode(ctx, req):
        mode = req.json().get('mode')
        info = ctx.rates.set_mode(mode)
        err = ''
        if mode == 'boc':                               # 切回自动：马上抓一次；失败不影响切换
            try:
                info = ctx.rates.refresh()
            except ApiError as e:
                err = e.message
        return {'ok': True, **info, 'refresh_error': err}

    @r.post('/api/rate/refresh')
    def refresh_rate(ctx, req):
        return {'ok': True, **ctx.rates.refresh()}

    r.add('POST', '/api/rate/fetch', refresh_rate)       # 旧名字，保留兼容

    # ---------- 产品 ----------
    @r.get('/api/products')
    def products(ctx, req):
        try:
            limit, offset = int(req.arg('limit', 100) or 100), int(req.arg('offset', 0) or 0)
            cat = int(req.arg('category_id')) if req.arg('category_id') else None
        except ValueError:
            raise ApiError('参数必须是整数')
        rows, total = ctx.products.list(req.arg('search').strip(), cat, req.arg('status'), req.arg('sort') or 'updated',
                                        limit, offset)
        return {'products': rows, 'total': total}

    @r.post('/api/products')
    def create(ctx, req):
        pid = ctx.products.create(req.json())
        return {'ok': True, 'id': pid, 'product': ctx.products.get(pid)}

    @r.post('/api/products/merge')
    def merge(ctx, req):
        b = req.json()
        if not b.get('survivor_id'):
            raise ApiError('请指定保留哪个产品')
        return {'ok': True, **ctx.products.merge(b['survivor_id'], b.get('merge_ids'))}

    @r.post('/api/products/recalc_prices')
    def recalc(ctx, req):
        return {'ok': True, **ctx.products.recalc_suggested(req.json().get('ids'))}

    @r.post('/api/products/normalize_images')
    def normalize_all(ctx, req):
        return {'ok': True, **ctx.media.normalize()}

    @r.get('/api/products/{id}')
    def get(ctx, req):
        return {'product': ctx.products.get(_id(req))}

    @r.put('/api/products/{id}')
    def update(ctx, req):
        pid = _id(req)
        ctx.products.update(pid, req.json())
        return {'ok': True, 'product': ctx.products.get(pid)}

    @r.get('/api/products/{id}/impact')
    def impact(ctx, req):
        return {'impact': ctx.products.impact(_id(req))}

    @r.get('/api/products/{id}/usage')
    def usage(ctx, req):
        return {'quotes': ctx.products.usage(_id(req))}

    @r.post('/api/products/{id}/duplicate')
    def duplicate(ctx, req):
        new_id = ctx.products.duplicate(_id(req), (req.json().get('sku') or '').strip())
        return {'ok': True, 'id': new_id}

    @r.delete('/api/products/{id}')
    def delete(ctx, req):
        pid = _id(req)
        impact = ctx.products.impact(pid)
        if not req.json().get('confirm'):
            raise ApiError('删除产品会同时删除其价格历史、供应商比价、图片和文档，请确认', 409,
                           {'needs_confirm': True, 'impact': impact})
        ctx.products.delete(pid)
        return {'ok': True, 'deleted': impact}

    # ---------- 价格历史 ----------
    @r.get('/api/products/{id}/price_history')
    def history(ctx, req):
        pid = _id(req)
        ctx.products.require(pid)
        return {'history': ctx.history.list(pid)}

    @r.post('/api/products/{id}/price_history')
    def add_history(ctx, req):
        pid = _id(req)
        ctx.products.require(pid)
        recorded = ctx.history.add_manual(pid, req.json())
        return {'ok': True, 'recorded': recorded, 'product': ctx.products.get(pid)}

    @r.delete('/api/price_history/{id}')
    def del_history(ctx, req):
        pid = ctx.history.delete(_id(req))
        return {'ok': True, 'product': ctx.products.get(pid)}

    @r.get('/api/products/{id}/last_price')
    def last_price(ctx, req):
        pid = _id(req)
        ctx.products.require(pid)
        cid = req.arg('customer_id')
        return ctx.history.last_prices(pid, int(cid) if cid.isdigit() else None)

    # ---------- 供应商比价 ----------
    @r.get('/api/products/{id}/suppliers')
    def suppliers(ctx, req):
        return {'suppliers': ctx.suppliers.list(_id(req))}

    @r.post('/api/products/{id}/suppliers')
    def add_supplier(ctx, req):
        pid = _id(req)
        ctx.suppliers.create(pid, req.json())
        return {'ok': True, 'suppliers': ctx.suppliers.list(pid), 'product': ctx.products.get(pid)}

    @r.post('/api/suppliers/{id}/adopt')
    def adopt(ctx, req):
        res = ctx.suppliers.adopt(_id(req))
        return {'ok': True, 'suppliers': ctx.suppliers.list(res['product_id']), 'product': ctx.products.get(res['product_id'])}

    @r.delete('/api/suppliers/{id}')
    def del_supplier(ctx, req):
        ctx.suppliers.delete(_id(req))
        return {'ok': True}

    # ---------- 图片：任意格式/尺寸上传，自动规范化为居中白底高清图 ----------
    @r.post('/api/images/upload')
    def upload_image(ctx, req):
        img = ctx.media.stage_image(req.read_bytes(MAX_IMAGE_UPLOAD))
        return {'ok': True, 'image': img}

    @r.post('/api/products/{id}/images/normalize')
    def normalize_one(ctx, req):
        pid = _id(req)
        ctx.products.require(pid)
        return {'ok': True, **ctx.media.normalize(pid), 'images': ctx.media.list_images(pid)}

    @r.get('/uploads/{name}')
    def upload(ctx, req):
        name = os.path.basename(req.params['name'])
        if name.rsplit('.', 1)[-1].lower() not in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
            raise ApiError('文件不存在', 404)
        return FileResponse(os.path.join(ctx.uploads_dir, name))

    # ---------- 文档附件（认证/规格书/图纸） ----------
    @r.post('/api/products/{id}/files')
    def upload_file(ctx, req):
        pid = _id(req)
        ctx.products.require(pid)
        display, stored, path = ctx.media.new_file_path(req.upload_filename('file'))
        req.save_upload(path, MAX_DOC_BYTES)
        try:
            f = ctx.media.register_file(pid, display, stored, urllib.parse.unquote(req.arg('kind')))
        except Exception:
            os.remove(path)
            raise
        return {'ok': True, 'file': f, 'files': ctx.media.list_files(pid)}

    @r.delete('/api/product_files/{id}')
    def del_file(ctx, req):
        ctx.media.delete_file(_id(req))
        return {'ok': True}

    # ---------- 内部备注截图（Ctrl+V 粘贴，本机 OCR 建搜索索引） ----------
    @r.get('/api/products/{id}/shots')
    def list_shots(ctx, req):
        pid = _id(req)
        ctx.products.require(pid)
        return {'shots': ctx.shots.list(pid)}

    @r.post('/api/products/{id}/shots')
    def add_shot(ctx, req):
        pid = _id(req)
        ctx.shots.add(pid, req.json().get('image_base64'))
        return {'ok': True, 'shots': ctx.shots.list(pid)}

    @r.put('/api/product_shots/{id}')
    def set_shot_text(ctx, req):
        ctx.shots.set_text(_id(req), req.json().get('ocr_text'))
        return {'ok': True}

    @r.delete('/api/product_shots/{id}')
    def del_shot(ctx, req):
        ctx.shots.delete(_id(req))
        return {'ok': True}

    @r.get('/product_shots/{name}')
    def shot_file(ctx, req):
        return FileResponse(ctx.shots.file_path(req.params['name']))

    @r.get('/files/{name}')
    def download_file(ctx, req):
        name = os.path.basename(req.params['name'])
        row = ctx.db.one('SELECT name FROM product_files WHERE file=?', (name,))
        if not row:
            raise ApiError('文件不存在', 404)
        return FileResponse(os.path.join(ctx.files_dir, name), row['name'])
