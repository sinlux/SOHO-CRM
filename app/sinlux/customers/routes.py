# -*- coding: utf-8 -*-
"""客户模块 API。"""
import os

from ..core.http import FileResponse
from ..core.util import ApiError
from . import intake, xlsx_io
from .xlsx_io import MAX_UPLOAD


def _id(req, name='id'):
    return int(req.params[name])


def register(r):
    # ---------- 客户 ----------
    @r.get('/api/customers')
    def list_customers(ctx, req):
        try:
            limit, offset = int(req.arg('limit', 100) or 100), int(req.arg('offset', 0) or 0)
        except ValueError:
            raise ApiError('limit/offset 必须是整数')
        rows, total = ctx.customers.list(req.arg('search').strip(), req.arg('lv'), req.arg('country'),
                                         req.arg('stage'), limit, offset)
        return {'customers': rows, 'total': total}

    @r.post('/api/customers')
    def create_customer(ctx, req):
        cid = ctx.customers.create(req.json())
        return {'ok': True, 'id': cid}

    @r.get('/api/customers/{id}')
    def get_customer(ctx, req):
        return ctx.customers.get(_id(req))

    @r.put('/api/customers/{id}')
    def update_customer(ctx, req):
        ctx.customers.update(_id(req), req.json())
        return {'ok': True}

    @r.get('/api/customers/{id}/impact')
    def customer_impact(ctx, req):
        return {'impact': ctx.customers.impact(_id(req))}

    @r.delete('/api/customers/{id}')
    def delete_customer(ctx, req):
        cid = _id(req)
        impact = ctx.customers.impact(cid)
        if not req.json().get('confirm'):
            # 有报价单时删除会连带删掉成交记录，必须让用户看清楚后明确确认
            raise ApiError('删除客户会同时删除其全部备注、提醒、背调记录和报价单，请确认', 409,
                           {'needs_confirm': True, 'impact': impact})
        ctx.customers.delete(cid)
        return {'ok': True, 'deleted': impact}

    @r.get('/api/countries')
    def countries(ctx, req):
        return {'countries': ctx.customers.countries()}

    @r.get('/api/stats')
    def stats(ctx, req):
        return ctx.customers.stats()

    @r.get('/api/stages')
    def stages(ctx, req):
        from ..core.schema import STAGES
        return {'stages': STAGES}

    # ---------- 备注 ----------
    @r.post('/api/customers/{id}/notes')
    def add_note(ctx, req):
        b = req.json()
        nid = ctx.customers.add_note(_id(req), b.get('content', ''), b.get('image_base64', ''))
        return {'ok': True, 'id': nid}

    @r.delete('/api/notes/{id}')
    def delete_note(ctx, req):
        ctx.customers.delete_note(_id(req))
        return {'ok': True}

    @r.get('/images/{name}')
    def image(ctx, req):
        name = os.path.basename(req.params['name'])
        return FileResponse(os.path.join(ctx.images_dir, name))

    # ---------- 提醒 ----------
    @r.get('/api/reminders')
    def reminders(ctx, req):
        rows, today = ctx.customers.pending_reminders()
        return {'reminders': rows, 'today': today}

    @r.post('/api/customers/{id}/reminders')
    def add_reminder(ctx, req):
        b = req.json()
        rid = ctx.customers.add_reminder(_id(req), b.get('content', ''), b.get('due_date', ''))
        return {'ok': True, 'id': rid}

    @r.post('/api/reminders/{id}/done')
    def reminder_done(ctx, req):
        ctx.customers.mark_reminder_done(_id(req))
        return {'ok': True}

    @r.delete('/api/reminders/{id}')
    def reminder_delete(ctx, req):
        ctx.customers.delete_reminder(_id(req))
        return {'ok': True}

    # ---------- 智能录入 ----------
    @r.post('/api/intake/parse')
    def parse(ctx, req):
        text = (req.json().get('input') or '').strip()
        if not text:
            raise ApiError('请输入内容')
        return intake.parse_input(ctx.db, text)

    # ---------- Excel 导入 / 导出 ----------
    @r.post('/api/import/customers/upload')
    def import_upload(ctx, req):
        name = req.upload_filename('customers.xlsx')
        if not name.lower().endswith('.xlsx'):
            raise ApiError('只支持 .xlsx 文件（Excel 另存为 xlsx 即可）')
        sid, path, _ = ctx.importer.new_session_path(name)
        try:
            req.save_upload(path, MAX_UPLOAD)
            return ctx.importer.preview(sid, req.arg('remap_lv') in ('1', 'true'))
        except Exception:
            import shutil
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)
            raise

    @r.post('/api/import/customers/preview')
    def import_preview(ctx, req):
        b = req.json()
        return ctx.importer.preview(b.get('session_id', ''), bool(b.get('remap_lv')))

    @r.post('/api/import/customers/apply')
    def import_apply(ctx, req):
        b = req.json()
        res = ctx.importer.apply(b.get('session_id', ''), bool(b.get('remap_lv')))
        return {'ok': True, **res}

    @r.post('/api/customers/export')
    def export(ctx, req):
        name = xlsx_io.export_customers(ctx.db, ctx.exports_dir)
        return {'ok': True, 'url': '/exports/' + name, 'filename': name}

    @r.get('/exports/{name}')
    def download_export(ctx, req):
        name = os.path.basename(req.params['name'])
        if not name.endswith('.xlsx'):
            raise ApiError('文件不存在', 404)
        return FileResponse(os.path.join(ctx.exports_dir, name), name)

    # ---------- AI 背调 ----------
    @r.post('/api/customers/{id}/enrich')
    def enrich(ctx, req):
        return {'ok': True, **ctx.enricher.run(_id(req))}

    @r.get('/api/enrichments/{id}')
    def get_enrichment(ctx, req):
        return ctx.enricher.get(_id(req))

    @r.post('/api/enrichments/{id}/apply')
    def apply_enrichment(ctx, req):
        return {'ok': True, **ctx.enricher.apply(_id(req), req.json())}
