# -*- coding: utf-8 -*-
"""供应商档案 / 沟通记录 API。路径用 /api/vendors，因为 /api/suppliers/{id} 早已被「产品页的供应商报价」占用。"""
from ..core.http import FileResponse
from ..core.util import ApiError


def _id(req):
    return int(req.params['id'])


def register(r):
    @r.get('/api/vendors')
    def vendors(ctx, req):
        try:
            limit, offset = int(req.arg('limit', 200) or 200), int(req.arg('offset', 0) or 0)
        except ValueError:
            raise ApiError('limit/offset 必须是整数')
        rows, total = ctx.vendors.list(req.arg('search'), req.arg('status'), req.arg('project'), limit, offset)
        return {'vendors': rows, 'total': total}

    @r.post('/api/vendors')
    def create_vendor(ctx, req):
        return {'ok': True, 'id': ctx.vendors.create(req.json())}

    @r.get('/api/vendors/names')
    def vendor_names(ctx, req):
        """产品页选供应商用：只要名字。"""
        return {'names': [x['name'] for x in ctx.db.query('SELECT name FROM suppliers ORDER BY name')]}

    @r.get('/api/vendors/{id}')
    def vendor(ctx, req):
        return ctx.vendors.get(_id(req))

    @r.put('/api/vendors/{id}')
    def update_vendor(ctx, req):
        ctx.vendors.update(_id(req), req.json())
        return {'ok': True}

    @r.get('/api/vendors/{id}/impact')
    def vendor_impact(ctx, req):
        return {'impact': ctx.vendors.impact(_id(req))}

    @r.delete('/api/vendors/{id}')
    def delete_vendor(ctx, req):
        sid = _id(req)
        impact = ctx.vendors.impact(sid)
        if not req.json().get('confirm'):
            raise ApiError('删除供应商会同时删除它的全部沟通记录和截图，请确认', 409, {'needs_confirm': True, 'impact': impact})
        ctx.vendors.delete(sid)
        return {'ok': True, 'deleted': impact}

    @r.post('/api/vendors/{id}/chats')
    def add_chat(ctx, req):
        return {'ok': True, 'id': ctx.vendors.add_chat(_id(req), req.json())}

    @r.put('/api/vendor_chats/{id}')
    def update_chat(ctx, req):
        ctx.vendors.update_chat(_id(req), req.json())
        return {'ok': True}

    @r.delete('/api/vendor_chats/{id}')
    def delete_chat(ctx, req):
        ctx.vendors.delete_chat(_id(req))
        return {'ok': True}

    @r.post('/api/vendor_chats/{id}/ocr')
    def rerun_ocr(ctx, req):
        ctx.vendors.rerun_ocr(_id(req))
        return {'ok': True}

    @r.get('/api/vendor_chats/search')
    def search_chats(ctx, req):
        return {'chats': ctx.vendors.search_chats(req.arg('q'), req.arg('project'))}

    @r.get('/api/vendor_projects')
    def projects(ctx, req):
        return {'projects': ctx.vendors.projects()}

    @r.get('/api/vendor_projects/view')
    def project_view(ctx, req):
        return {'project': req.arg('name'), 'suppliers': ctx.vendors.project_view(req.arg('name'))}

    @r.get('/api/ocr/status')
    def ocr_status(ctx, req):
        from . import ocr
        return {'available': ocr.available()}

    @r.get('/supplier_files/{name}')
    def supplier_file(ctx, req):
        return FileResponse(ctx.vendors.file_path(req.params['name']))
