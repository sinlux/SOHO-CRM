# -*- coding: utf-8 -*-
"""系统级 API：版本、设置、备份。"""
import os
import re

from . import backup
from .http import FileResponse
from .util import ApiError

# 设置页可读写的键；密钥类只返回"是否已设置+末4位"，不回传明文
SECRET_KEYS = ('deepseek_key', 'tavily_key')
PLAIN_KEYS = ('deepseek_model',)


def _hint(v):
    return ('****' + v[-4:]) if len(v) > 8 else ('****' if v else '')


def register(r):
    @r.get('/api/version')
    def version(ctx, req):
        log = ''
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'changelog.txt')
        try:
            with open(p, encoding='utf-8') as f:
                log = f.read()
        except OSError:
            pass
        return {'version': ctx.version, 'changelog': log}

    @r.get('/api/settings')
    def get_settings(ctx, req):
        out = {k: ctx.db.get_setting(k) for k in PLAIN_KEYS}
        out['deepseek_model'] = out['deepseek_model'] or 'deepseek-chat'
        for k in SECRET_KEYS:
            v = ctx.db.get_setting(k)
            out[k + '_set'] = bool(v)
            out[k + '_hint'] = _hint(v)
        return out

    @r.put('/api/settings')
    def put_settings(ctx, req):
        b = req.json()
        for k in SECRET_KEYS + PLAIN_KEYS:
            if k in b and b[k] is not None:
                v = str(b[k]).strip()
                if k in SECRET_KEYS and not v:
                    continue          # 空值 = 保持原样；要清除请用 clear_<key>
                ctx.db.set_setting(k, v)
        for k in SECRET_KEYS:
            if b.get('clear_' + k):
                ctx.db.set_setting(k, '')
        return {'ok': True}

    @r.post('/api/settings/test')
    def test_keys(ctx, req):
        out, net = {}, ctx.enricher.net
        try:
            res = net.tavily_search(ctx.db.get_setting('tavily_key'), 'test', 1)
            out['tavily'] = 'OK（返回%d条结果）' % len(res)
        except Exception as e:
            out['tavily'] = '失败: %s' % e
        try:
            txt = net.deepseek_chat(ctx.db.get_setting('deepseek_key'), ctx.db.get_setting('deepseek_model', 'deepseek-chat'),
                                    '请只输出JSON：{"reply":"OK"}')
            out['deepseek'] = 'OK（模型回复: %s）' % txt[:30]
        except Exception as e:
            out['deepseek'] = '失败: %s' % e
        return out

    @r.post('/api/backup')
    def make_backup(ctx, req):
        return {'ok': True, **backup.make_backup(ctx.db, ctx.data_dir, ctx.backups_dir)}

    @r.get('/api/backups')
    def backups(ctx, req):
        return {'backups': backup.list_backups(ctx.backups_dir)}

    @r.get('/backups/{name}')
    def download_backup(ctx, req):
        return FileResponse(backup.backup_path(ctx.backups_dir, req.params['name']), req.params['name'])

    # ---------- 升级包 / 回退 ----------
    @r.post('/api/update/upload')
    def update_upload(ctx, req):
        path = ctx.updater.new_upload_path()
        try:
            req.save_upload(path, 300 * 1024 * 1024)
            info = ctx.updater.inspect(path)
        except Exception:
            if os.path.exists(path):
                os.remove(path)
            raise
        return {**info, 'token': os.path.basename(path)}

    @r.post('/api/update/apply')
    def update_apply(ctx, req):
        token = str(req.json().get('token') or '')
        if not re.fullmatch(r'update_\d+\.zip', token):           # 只接受刚上传的包，不接受任意路径
            raise ApiError('升级包标识无效，请重新上传')
        path = os.path.join(ctx.updater.tmp, token)
        if not os.path.isfile(path):
            raise ApiError('升级包已过期，请重新上传', 404)
        db_backup = backup.make_backup(ctx.db, ctx.data_dir, ctx.backups_dir)      # 升级前先备份数据：万一新版改了数据库结构，回退代码时还能退回数据
        res = ctx.updater.apply(path)
        os.remove(path)
        return {'ok': True, **res, 'data_backup': db_backup['file']}

    @r.get('/api/update/versions')
    def update_versions(ctx, req):
        return {'versions': ctx.updater.list_versions(), 'current': ctx.version}

    @r.post('/api/update/rollback')
    def update_rollback(ctx, req):
        name = req.json().get('name')
        db_backup = backup.make_backup(ctx.db, ctx.data_dir, ctx.backups_dir)
        return {'ok': True, **ctx.updater.rollback(name), 'data_backup': db_backup['file']}

    @r.get('/api/dashboard')
    def dashboard(ctx, req):
        return ctx.dashboard.overview()

    @r.get('/api/health')
    def health(ctx, req):
        return {'ok': True, 'version': ctx.version, 'migration': ctx.migration_report}
