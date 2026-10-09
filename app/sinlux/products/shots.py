# -*- coding: utf-8 -*-
"""产品「内部备注」里的截图：在备注框里 Ctrl+V 直接贴聊天/报价截图，不用自己整理文字。
截图落盘在 data/product_shots，文字在本机 OCR 识别后进搜索索引（产品列表搜索能搜到）。只有你自己看得到，不会进报价单。"""
import os
import threading
import uuid

from ..core import images as rawimg
from ..core.util import ApiError
from ..suppliers import ocr as ocrmod

MAX_PER_PRODUCT = 30


class ProductShots:
    def __init__(self, db, dir_, ocr_fn=None, async_ocr=True):
        self.db, self.dir = db, dir_
        os.makedirs(dir_, exist_ok=True)
        self.ocr_fn = ocr_fn or ocrmod.recognize
        self.async_ocr = async_ocr

    @staticmethod
    def _row(r):
        r['url'] = '/product_shots/' + r['file']
        r['thumb_url'] = '/product_shots/' + (r['thumb_file'] or r['file'])
        return r

    def list(self, pid):
        return [self._row(r) for r in self.db.query('SELECT * FROM product_shots WHERE product_id=? ORDER BY id', (pid,))]

    def add(self, pid, raw):
        if not self.db.one('SELECT 1 FROM products WHERE id=?', (pid,)):
            raise ApiError('产品不存在', 404)
        if self.db.scalar('SELECT COUNT(*) FROM product_shots WHERE product_id=?', (pid,)) >= MAX_PER_PRODUCT:
            raise ApiError('每个产品最多 %d 张备注截图' % MAX_PER_PRODUCT)
        ext, data = rawimg.decode_data_url(raw)
        stem = uuid.uuid4().hex
        main, thumb = '%s.%s' % (stem, ext), ''
        with open(os.path.join(self.dir, main), 'wb') as f:
            f.write(data)
        try:
            from PIL import Image
            with Image.open(os.path.join(self.dir, main)) as im:
                im.load()
                im = im.convert('RGB')
                im.thumbnail((320, 320))
                thumb = stem + '_s.jpg'
                im.save(os.path.join(self.dir, thumb), 'JPEG', quality=80)
        except Exception:
            thumb = ''
        sid = self.db.execute("INSERT INTO product_shots(product_id,file,thumb_file,ocr_status) VALUES(?,?,?,'pending')", (pid, main, thumb)).lastrowid
        if self.async_ocr:
            threading.Thread(target=self._ocr, args=(sid,), daemon=True).start()
        else:
            self._ocr(sid)
        return sid

    def _ocr(self, sid):
        try:
            r = self.db.one('SELECT file FROM product_shots WHERE id=?', (sid,))
            if not r:
                return
            status, text, err = self.ocr_fn(os.path.join(self.dir, r['file']))
            cur = self.db.one('SELECT ocr_status FROM product_shots WHERE id=?', (sid,))
            if not cur or cur['ocr_status'] != 'pending':                       # 期间被删，或用户手改过文字：不覆盖
                return
            self.db.execute('UPDATE product_shots SET ocr_status=?, ocr_text=?, ocr_error=? WHERE id=?',
                            (status, text if status == 'done' else '', err or '', sid))
        except Exception as e:
            try:
                self.db.execute("UPDATE product_shots SET ocr_status='failed', ocr_error=? WHERE id=?", (str(e)[:300], sid))
            except Exception:
                pass

    def set_text(self, sid, text):
        if not self.db.one('SELECT 1 FROM product_shots WHERE id=?', (sid,)):
            raise ApiError('截图不存在', 404)
        text = str(text or '')
        if len(text) > 60000:
            raise ApiError('文字太长')
        self.db.execute("UPDATE product_shots SET ocr_text=?, ocr_status='done', ocr_error='' WHERE id=?", (text, sid))

    def _rm(self, names):
        for n in names:
            if n:
                try:
                    os.remove(os.path.join(self.dir, os.path.basename(n)))
                except OSError:
                    pass

    def delete(self, sid):
        r = self.db.one('SELECT * FROM product_shots WHERE id=?', (sid,))
        if not r:
            raise ApiError('截图不存在', 404)
        self.db.execute('DELETE FROM product_shots WHERE id=?', (sid,))
        self._rm([r['file'], r['thumb_file']])

    def delete_all(self, pid):
        """删除产品时调用：先删记录，返回要在事务提交后删除的文件名。"""
        names = [n for r in self.db.query('SELECT file, thumb_file FROM product_shots WHERE product_id=?', (pid,)) for n in (r['file'], r['thumb_file'])]
        self.db.execute('DELETE FROM product_shots WHERE product_id=?', (pid,))
        return [n for n in names if n]

    def move(self, src, dst):
        self.db.execute('UPDATE product_shots SET product_id=? WHERE product_id=?', (dst, src))

    def file_path(self, name):
        p = os.path.join(self.dir, os.path.basename(name))
        if not os.path.isfile(p):
            raise ApiError('文件不存在', 404)
        return p
