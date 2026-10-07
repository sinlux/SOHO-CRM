# -*- coding: utf-8 -*-
"""一键备份：SQLite 在线备份 API 取一致性快照，连同图片打包成 zip。"""
import os
import re
import tempfile
import time
import zipfile

from .util import ApiError

NAME_RE = re.compile(r'^backup_\d{8}_\d{6}(_\d+)?\.zip$')


def make_backup(db, data_dir, backup_dir):
    os.makedirs(backup_dir, exist_ok=True)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    zip_path, n = os.path.join(backup_dir, 'backup_%s.zip' % stamp), 1
    while os.path.exists(zip_path):          # 同一秒内多次备份不覆盖
        zip_path = os.path.join(backup_dir, 'backup_%s_%d.zip' % (stamp, n))
        n += 1
    fd, tmp_db = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    try:
        db.backup_to(tmp_db)
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(tmp_db, 'crm.db')
            for sub in ('images', 'uploads'):
                d = os.path.join(data_dir, sub)
                if os.path.isdir(d):
                    for f in sorted(os.listdir(d)):
                        p = os.path.join(d, f)
                        if os.path.isfile(p):
                            zf.write(p, '%s/%s' % (sub, f))
    finally:
        os.remove(tmp_db)
    return {'file': os.path.basename(zip_path), 'size_mb': round(os.path.getsize(zip_path) / 1048576, 2)}


def list_backups(backup_dir):
    if not os.path.isdir(backup_dir):
        return []
    out = []
    for f in sorted(os.listdir(backup_dir), reverse=True):
        if NAME_RE.match(f):
            p = os.path.join(backup_dir, f)
            out.append({'file': f, 'size_mb': round(os.path.getsize(p) / 1048576, 2),
                        'time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(p)))})
    return out


def backup_path(backup_dir, name):
    if not NAME_RE.match(name or ''):
        raise ApiError('备份文件名无效', 400)
    p = os.path.join(backup_dir, name)
    if not os.path.isfile(p):
        raise ApiError('备份文件不存在', 404)
    return p
