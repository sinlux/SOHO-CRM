# -*- coding: utf-8 -*-
"""产品媒体：相册（多图，自动规范化为居中白底高清图）与文档附件（认证/规格书/图纸…）。

相册模型：product_images(product_id, file, thumb, sort_order)，sort_order 最小的是主图，
同时镜像写入 products.image_path（旧版/报价 PDF 等沿用该字段）。
新上传的图片先「暂存」（product_id 为空），保存产品时按 ids 顺序挂到产品上；超过 24 小时没人认领的暂存图会被清理。
"""
import os
import re
import shutil
import time
import uuid

from ..core import imaging, images as rawimg
from ..core.util import ApiError, now

DOC_EXTS = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'csv', 'zip', 'rar', '7z',
            'png', 'jpg', 'jpeg', 'webp', 'gif', 'dwg', 'dxf', 'step', 'stp', 'skp', 'ai', 'psd'}
DOC_KINDS = ('规格书', '认证', '图纸', '报价/合同', '其他')
MAX_DOC_BYTES = 50 * 1024 * 1024
MAX_IMAGE_UPLOAD = 40 * 1024 * 1024
MAX_IMAGES_PER_PRODUCT = 20
IMG_PREFIX = 'uploads/'
_SAFE = re.compile(r'[^\w\-. 一-鿿()（）\[\]]+')


def safe_name(name, default='file'):
    name = os.path.basename(name or '')
    name = _SAFE.sub('_', name).strip(' .')
    return name[:120] or default


class MediaStore:
    def __init__(self, db, uploads_dir, files_dir):
        self.db = db
        self.uploads = uploads_dir
        self.files_dir = files_dir
        os.makedirs(uploads_dir, exist_ok=True)
        os.makedirs(files_dir, exist_ok=True)

    # ---------- 图片 ----------
    def _decorate(self, r):
        r['url'] = '/uploads/' + r['file']
        r['thumb_url'] = '/uploads/' + (r['thumb'] or r['file'])
        r['normalized'] = bool(r['normalized'])
        r['low_res'] = bool(r['low_res'])
        return r

    def list_images(self, pid):
        rows = self.db.query('SELECT * FROM product_images WHERE product_id=? ORDER BY sort_order, id', (pid,))
        for i, r in enumerate(rows):
            self._decorate(r)
            r['is_primary'] = i == 0
        return rows

    def _write_new(self, data):
        """把一张上传图处理并落盘。返回要写进 product_images 的字段。"""
        if imaging.available():
            out = imaging.process(data)
            stem = uuid.uuid4().hex
            main, thumb = stem + '.jpg', stem + '_s.jpg'
            with open(os.path.join(self.uploads, main), 'wb') as f:
                f.write(out['main'])
            with open(os.path.join(self.uploads, thumb), 'wb') as f:
                f.write(out['thumb'])
            return dict(file=main, thumb=thumb, width=out['width'], height=out['height'], normalized=1,
                        low_res=1 if out['low_res'] else 0)
        ext = imaging.sniff(data)               # 没有 Pillow：只能原样保存，页面用 CSS 居中显示
        if not ext:
            raise ApiError('无法识别这个文件，请上传 JPG / PNG / WebP / GIF 格式的图片')
        name = '%s.%s' % (uuid.uuid4().hex, ext)
        with open(os.path.join(self.uploads, name), 'wb') as f:
            f.write(data)
        return dict(file=name, thumb='', width=None, height=None, normalized=0, low_res=0)

    def stage_image(self, data):
        """上传一张图（尚未属于任何产品）。返回图片 dict（含 id）。"""
        if not data:
            raise ApiError('上传内容为空')
        info = self._write_new(data)
        rid = self.db.execute("""INSERT INTO product_images(product_id,file,thumb,width,height,normalized,low_res,sort_order)
            VALUES(NULL,?,?,?,?,?,?,0)""", (info['file'], info['thumb'], info['width'], info['height'],
                                           info['normalized'], info['low_res'])).lastrowid
        return self._decorate(self.db.one('SELECT * FROM product_images WHERE id=?', (rid,)))

    def apply_images(self, pid, ids):
        """按 ids 的顺序设置产品相册：已属于本产品的保留并排序，暂存的认领，没在列表里的删除。
        返回 (要在事务提交后删除的文件名列表)。主图同步到 products.image_path。"""
        if not isinstance(ids, list):
            raise ApiError('images 必须是图片编号列表')
        try:
            ids = [int(i) for i in ids]
        except (TypeError, ValueError):
            raise ApiError('图片编号无效')
        if len(set(ids)) != len(ids):
            raise ApiError('图片编号重复')
        if len(ids) > MAX_IMAGES_PER_PRODUCT:
            raise ApiError('每个产品最多 %d 张图片' % MAX_IMAGES_PER_PRODUCT)
        for i in ids:
            r = self.db.one('SELECT product_id FROM product_images WHERE id=?', (i,))
            if not r:
                raise ApiError('图片 %d 不存在（可能已过期，请重新上传）' % i, 404)
            if r['product_id'] not in (None, pid):
                raise ApiError('图片 %d 属于其他产品' % i)
        gone = []
        for r in self.db.query('SELECT id, file, thumb FROM product_images WHERE product_id=?', (pid,)):
            if r['id'] not in ids:
                gone += [r['file'], r['thumb']]
                self.db.execute('DELETE FROM product_images WHERE id=?', (r['id'],))
        for order, i in enumerate(ids):
            self.db.execute('UPDATE product_images SET product_id=?, sort_order=? WHERE id=?', (pid, order, i))
        self.sync_primary(pid)
        return [g for g in gone if g]

    def sync_primary(self, pid):
        r = self.db.one('SELECT file FROM product_images WHERE product_id=? ORDER BY sort_order, id LIMIT 1', (pid,))
        self.db.execute('UPDATE products SET image_path=? WHERE id=?', (IMG_PREFIX + r['file'] if r else None, pid))

    def remove_files(self, names):
        for n in names:
            rawimg.remove_file(self.uploads, n)

    def delete_all_images(self, pid):
        gone = []
        for r in self.db.query('SELECT file, thumb FROM product_images WHERE product_id=?', (pid,)):
            gone += [r['file'], r['thumb']]
        self.db.execute('DELETE FROM product_images WHERE product_id=?', (pid,))
        return [g for g in gone if g]

    def move_images(self, src, dst):
        """合并同类项：把 src 的图片追加到 dst 相册末尾。"""
        base = self.db.scalar('SELECT COALESCE(MAX(sort_order), -1) + 1 FROM product_images WHERE product_id=?', (dst,))
        for i, r in enumerate(self.db.query('SELECT id FROM product_images WHERE product_id=? ORDER BY sort_order, id', (src,))):
            self.db.execute('UPDATE product_images SET product_id=?, sort_order=? WHERE id=?', (dst, base + i, r['id']))
        self.sync_primary(dst)

    def copy_images(self, src, dst):
        """复制产品：相册文件逐个复制，互不影响。"""
        for i, r in enumerate(self.db.query('SELECT * FROM product_images WHERE product_id=? ORDER BY sort_order, id', (src,))):
            names = {}
            for key in ('file', 'thumb'):
                if r[key]:
                    ext = r[key].rsplit('.', 1)[-1]
                    new = uuid.uuid4().hex + ('_s' if key == 'thumb' else '') + '.' + ext
                    try:
                        shutil.copyfile(os.path.join(self.uploads, r[key]), os.path.join(self.uploads, new))
                    except OSError:
                        new = ''
                    names[key] = new
                else:
                    names[key] = ''
            if names['file']:
                self.db.execute("""INSERT INTO product_images(product_id,file,thumb,width,height,normalized,low_res,sort_order)
                    VALUES(?,?,?,?,?,?,?,?)""", (dst, names['file'], names['thumb'], r['width'], r['height'],
                                                r['normalized'], r['low_res'], i))
        self.sync_primary(dst)

    def normalize(self, pid=None):
        """把旧版遗留的、没规范化的图片统一成标准产品图。返回 {'done', 'failed': [...], 'no_pillow'}。"""
        if not imaging.available():
            return {'done': 0, 'failed': [], 'no_pillow': True}
        sql = 'SELECT * FROM product_images WHERE normalized=0 AND product_id IS NOT NULL'
        rows = self.db.query(sql + (' AND product_id=?' if pid else ''), (pid,) if pid else ())
        done, failed = 0, []
        for r in rows:
            try:
                with open(os.path.join(self.uploads, r['file']), 'rb') as f:
                    info = self._write_new(f.read())
            except (OSError, ApiError) as e:
                failed.append({'id': r['id'], 'error': getattr(e, 'message', str(e))})
                continue
            with self.db.tx():
                self.db.execute("""UPDATE product_images SET file=?,thumb=?,width=?,height=?,normalized=1,low_res=?
                    WHERE id=?""", (info['file'], info['thumb'], info['width'], info['height'], info['low_res'], r['id']))
                self.sync_primary(r['product_id'])
            self.remove_files([r['file']])
            done += 1
        return {'done': done, 'failed': failed, 'no_pillow': False}

    def cleanup_staged(self, max_age_hours=24):
        cutoff = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() - max_age_hours * 3600))
        rows = self.db.query('SELECT id, file, thumb FROM product_images WHERE product_id IS NULL AND created_at < ?', (cutoff,))
        for r in rows:
            self.db.execute('DELETE FROM product_images WHERE id=?', (r['id'],))
            self.remove_files([r['file'], r['thumb']])
        return len(rows)

    # ---------- 文档附件 ----------
    def list_files(self, pid):
        rows = self.db.query('SELECT * FROM product_files WHERE product_id=? ORDER BY id DESC', (pid,))
        for r in rows:
            r['url'] = '/files/' + r['file']
        return rows

    def new_file_path(self, filename):
        """上传前：校验扩展名并给出落盘路径（文件名带 uuid，原名只作显示）。返回 (display_name, stored_name, path)。"""
        display = safe_name(filename, 'file')
        ext = display.rsplit('.', 1)[-1].lower() if '.' in display else ''
        if ext not in DOC_EXTS:
            raise ApiError('不支持的文件类型 .%s（支持：%s）' % (ext, ' '.join(sorted(DOC_EXTS))))
        stored = '%s.%s' % (uuid.uuid4().hex, ext)
        return display, stored, os.path.join(self.files_dir, stored)

    def register_file(self, pid, display, stored, kind):
        kind = kind if kind in DOC_KINDS else '其他'
        size = os.path.getsize(os.path.join(self.files_dir, stored))
        rid = self.db.execute('INSERT INTO product_files(product_id,name,kind,file,size) VALUES(?,?,?,?,?)',
                              (pid, display, kind, stored, size)).lastrowid
        r = self.db.one('SELECT * FROM product_files WHERE id=?', (rid,))
        r['url'] = '/files/' + r['file']
        return r

    def delete_file(self, fid):
        r = self.db.one('SELECT * FROM product_files WHERE id=?', (fid,))
        if not r:
            raise ApiError('文件不存在', 404)
        self.db.execute('DELETE FROM product_files WHERE id=?', (fid,))
        self._rm_doc(r['file'])

    def _rm_doc(self, stored):
        if stored and stored == os.path.basename(stored):
            try:
                os.remove(os.path.join(self.files_dir, stored))
            except OSError:
                pass

    def delete_all_files(self, pid):
        names = [r['file'] for r in self.db.query('SELECT file FROM product_files WHERE product_id=?', (pid,))]
        self.db.execute('DELETE FROM product_files WHERE product_id=?', (pid,))
        return names

    def remove_docs(self, names):
        for n in names:
            self._rm_doc(n)
