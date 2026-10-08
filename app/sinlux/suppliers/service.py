# -*- coding: utf-8 -*-
"""供应商档案 + 沟通记录（聊天截图）。

背景：一个项目可能同时问几十家供应商，最后只选一家；很多时候只有阿里旺旺 / 微信聊天记录，并没有深入了解对方。
所以：每家供应商一份轻量档案（先只记名字也行）；聊天截图可以随手 Ctrl+V 归档，自动在本机识别文字（OCR）建立搜索索引，
之后能按文字搜到是哪家供应商、哪个项目，并看到当时的截图原图。
"""
import os
import threading
import uuid

from ..core import images as rawimg
from ..core.util import ApiError, now, valid_date
from . import ocr as ocrmod

STATUSES = {'inquiring': '询价中', 'candidate': '备选', 'cooperating': '合作中', 'rejected': '淘汰'}
PLATFORMS = ['阿里巴巴', '1688', '微信', '旺旺', '展会', '朋友介绍', '其他']
TEXT_FIELDS = {'contact': 60, 'phone': 60, 'wechat': 60, 'email': 120, 'platform': 30, 'link': 300, 'location': 80, 'main_products': 300, 'notes': 4000}


def _txt(v, limit, what):
    v = '' if v is None else str(v).strip()
    if len(v) > limit:
        raise ApiError('%s最长 %d 个字符' % (what, limit))
    return v


class SupplierBook:
    def __init__(self, db, files_dir, ocr_fn=None, async_ocr=True):
        self.db = db
        self.dir = files_dir
        os.makedirs(files_dir, exist_ok=True)
        self.ocr_fn = ocr_fn or ocrmod.recognize
        self.async_ocr = async_ocr

    # ---------- 供应商 ----------
    def require(self, sid):
        r = self.db.one('SELECT * FROM suppliers WHERE id=?', (sid,))
        if not r:
            raise ApiError('供应商不存在', 404)
        return r

    def ensure(self, name, status='candidate'):
        """按名字找供应商（不区分大小写），没有就建一份最简档案。返回 id。"""
        name = _txt(name, 100, '供应商名称')
        if not name:
            raise ApiError('供应商名称不能为空')
        r = self.db.one('SELECT id FROM suppliers WHERE name=?', (name,))
        if r:
            return r['id']
        return self.db.execute('INSERT INTO suppliers(name,status) VALUES(?,?)', (name, status)).lastrowid

    def _clean(self, d, partial):
        out = {}
        if 'name' in d or not partial:
            out['name'] = _txt(d.get('name'), 100, '供应商名称')
            if not out['name']:
                raise ApiError('供应商名称不能为空')
        for k, lim in TEXT_FIELDS.items():
            if k in d:
                out[k] = _txt(d[k], lim, k)
        if 'status' in d:
            if d['status'] not in STATUSES:
                raise ApiError('状态无效，可选：' + '/'.join(STATUSES))
            out['status'] = d['status']
        if 'rating' in d:
            r = d['rating']
            if r in (None, '', 0, '0'):
                out['rating'] = None
            else:
                try:
                    r = int(r)
                except (TypeError, ValueError):
                    raise ApiError('评分必须是 1-5')
                if not 1 <= r <= 5:
                    raise ApiError('评分必须是 1-5')
                out['rating'] = r
        return out

    def create(self, d):
        c = self._clean(d, partial=False)
        if self.db.one('SELECT 1 FROM suppliers WHERE name=?', (c['name'],)):
            raise ApiError('已经有叫「%s」的供应商' % c['name'], 409)
        c.setdefault('status', 'inquiring')
        return self.db.execute('INSERT INTO suppliers(%s) VALUES(%s)' % (','.join(c), ','.join('?' * len(c))), list(c.values())).lastrowid

    def update(self, sid, d):
        old = self.require(sid)
        c = self._clean(d, partial=True)
        if 'name' in c and c['name'].lower() != old['name'].lower() and self.db.one('SELECT 1 FROM suppliers WHERE name=? AND id<>?', (c['name'], sid)):
            raise ApiError('已经有叫「%s」的供应商' % c['name'], 409)
        if not c:
            return
        with self.db.tx():
            self.db.execute('UPDATE suppliers SET %s,updated_at=? WHERE id=?' % ','.join(k + '=?' for k in c), list(c.values()) + [now(), sid])
            if 'name' in c and c['name'] != old['name']:                      # 比价记录里冗余存了名字，同步改
                self.db.execute('UPDATE supplier_quotes SET supplier_name=? WHERE supplier_id=?', (c['name'], sid))
                self.db.execute('UPDATE products SET supplier=? WHERE supplier=?', (c['name'], old['name']))

    def impact(self, sid):
        self.require(sid)
        return {'chats': self.db.scalar('SELECT COUNT(*) FROM supplier_chats WHERE supplier_id=?', (sid,)),
                'quotes': self.db.scalar('SELECT COUNT(*) FROM supplier_quotes WHERE supplier_id=?', (sid,)),
                'adopted': self.db.scalar('SELECT COUNT(*) FROM supplier_quotes WHERE supplier_id=? AND is_adopted=1', (sid,))}

    def delete(self, sid):
        self.require(sid)
        files = [f for r in self.db.query('SELECT image_file, thumb_file FROM supplier_chats WHERE supplier_id=?', (sid,)) for f in (r['image_file'], r['thumb_file'])]
        self.db.execute('DELETE FROM suppliers WHERE id=?', (sid,))        # 聊天记录级联删除；比价记录保留（名字还在，supplier_id 置空）
        self._rm(files)

    def list(self, search='', status='', project='', limit=200, offset=0):
        where, args = ['1=1'], []
        if status:
            where.append('s.status=?'); args.append(status)
        if project:
            where.append("(EXISTS(SELECT 1 FROM supplier_chats c WHERE c.supplier_id=s.id AND c.project=?) OR EXISTS(SELECT 1 FROM supplier_quotes q WHERE q.supplier_id=s.id AND q.project=?))")
            args += [project, project]
        kw = (search or '').strip()
        if kw:
            like = '%' + kw.replace('%', r'\%').replace('_', r'\_') + '%'
            cols = ' OR '.join("s.%s LIKE ? ESCAPE '\\'" % c for c in ('name', 'contact', 'phone', 'wechat', 'email', 'main_products', 'notes', 'location', 'platform'))
            where.append('(%s OR EXISTS(SELECT 1 FROM supplier_chats c WHERE c.supplier_id=s.id AND (c.ocr_text LIKE ? ESCAPE \'\\\' OR c.note LIKE ? ESCAPE \'\\\' '
                         'OR c.title LIKE ? ESCAPE \'\\\' OR c.project LIKE ? ESCAPE \'\\\')) OR EXISTS(SELECT 1 FROM supplier_quotes q WHERE q.supplier_id=s.id AND (q.remark LIKE ? ESCAPE \'\\\' OR q.project LIKE ? ESCAPE \'\\\')))' % cols)
            args += [like] * 9 + [like] * 6
        where_sql = ' AND '.join(where)
        total = self.db.scalar('SELECT COUNT(*) FROM suppliers s WHERE ' + where_sql, args)
        rows = self.db.query("""SELECT s.*, (SELECT COUNT(*) FROM supplier_chats c WHERE c.supplier_id=s.id) AS chat_count,
              (SELECT COUNT(*) FROM supplier_quotes q WHERE q.supplier_id=s.id) AS quote_count,
              (SELECT COUNT(*) FROM supplier_quotes q WHERE q.supplier_id=s.id AND q.is_adopted=1) AS adopted_count,
              (SELECT MAX(COALESCE(NULLIF(c.chat_date,''), substr(c.created_at,1,10))) FROM supplier_chats c WHERE c.supplier_id=s.id) AS last_chat
            FROM suppliers s WHERE %s ORDER BY s.status='cooperating' DESC, s.updated_at DESC, s.id DESC LIMIT ? OFFSET ?""" % where_sql,
                             args + [max(1, min(int(limit), 500)), max(0, int(offset))])
        for r in rows:
            r['status_label'] = STATUSES.get(r['status'], r['status'])
        return rows, total

    def get(self, sid):
        s = self.require(sid)
        s['status_label'] = STATUSES.get(s['status'], s['status'])
        s['chats'] = [self._chat_row(c) for c in self.db.query(
            'SELECT * FROM supplier_chats WHERE supplier_id=? ORDER BY COALESCE(NULLIF(chat_date,\'\'), substr(created_at,1,10)) DESC, id DESC', (sid,))]
        s['quotes'] = self.db.query("""SELECT q.id, q.product_id, q.price_cny, q.quote_date, q.remark, q.is_adopted, q.project, q.screenshot_path,
              p.sku, p.name AS product_name FROM supplier_quotes q LEFT JOIN products p ON p.id=q.product_id
            WHERE q.supplier_id=? ORDER BY q.is_adopted DESC, q.quote_date DESC, q.id DESC""", (sid,))
        for q in s['quotes']:
            q['screenshot_url'] = '/' + q['screenshot_path'] if q['screenshot_path'] else ''
        s['projects'] = sorted({c['project'] for c in s['chats'] if c['project']} | {q['project'] for q in s['quotes'] if q['project']})
        return s

    # ---------- 沟通记录 ----------
    @staticmethod
    def _chat_row(c):
        c['image_url'] = '/supplier_files/' + c['image_file'] if c['image_file'] else ''
        c['thumb_url'] = '/supplier_files/' + (c['thumb_file'] or c['image_file']) if c['image_file'] else ''
        return c

    def _rm(self, names):
        for n in names:
            if n:
                try:
                    os.remove(os.path.join(self.dir, os.path.basename(n)))
                except OSError:
                    pass

    def file_path(self, name):
        p = os.path.join(self.dir, os.path.basename(name))
        if not os.path.isfile(p):
            raise ApiError('文件不存在', 404)
        return p

    def _save_image(self, raw):
        ext, data = rawimg.decode_data_url(raw)
        stem = uuid.uuid4().hex
        main = '%s.%s' % (stem, ext)
        with open(os.path.join(self.dir, main), 'wb') as f:
            f.write(data)
        thumb = ''
        try:                                                    # 缩略图方便列表浏览；没有 Pillow 就直接用原图
            from PIL import Image
            with Image.open(os.path.join(self.dir, main)) as im:
                im.load()
                im = im.convert('RGB')
                im.thumbnail((360, 360))
                thumb = stem + '_s.jpg'
                im.save(os.path.join(self.dir, thumb), 'JPEG', quality=80)
        except Exception:
            thumb = ''
        return main, thumb

    def add_chat(self, sid, d):
        self.require(sid)
        raw = d.get('image_base64')
        note = _txt(d.get('note'), 4000, '备注')
        if not raw and not note:
            raise ApiError('请粘贴/上传聊天截图，或至少写一点文字备注')
        date = (d.get('chat_date') or '').strip()
        if date and not valid_date(date):
            raise ApiError('日期格式应为 YYYY-MM-DD')
        main = thumb = ''
        if raw:
            main, thumb = self._save_image(raw)
        try:
            cid = self.db.execute("""INSERT INTO supplier_chats(supplier_id,project,title,note,image_file,thumb_file,ocr_status,chat_date)
                VALUES(?,?,?,?,?,?,?,?)""", (sid, _txt(d.get('project'), 80, '项目'), _txt(d.get('title'), 100, '标题'), note, main, thumb,
                                             'pending' if main else '', date)).lastrowid
            self.db.execute('UPDATE suppliers SET updated_at=? WHERE id=?', (now(), sid))
        except Exception:
            self._rm([main, thumb])
            raise
        if main:
            self._start_ocr(cid)
        return cid

    def _chat(self, cid):
        c = self.db.one('SELECT * FROM supplier_chats WHERE id=?', (cid,))
        if not c:
            raise ApiError('沟通记录不存在', 404)
        return c

    def update_chat(self, cid, d):
        self._chat(cid)
        sets, args = [], []
        for k, lim, what in (('project', 80, '项目'), ('title', 100, '标题'), ('note', 4000, '备注'), ('ocr_text', 60000, '识别文字')):
            if k in d:
                sets.append(k + '=?'); args.append(_txt(d[k], lim, what))
        if 'chat_date' in d:
            dt = (d['chat_date'] or '').strip()
            if dt and not valid_date(dt):
                raise ApiError('日期格式应为 YYYY-MM-DD')
            sets.append('chat_date=?'); args.append(dt)
        if 'ocr_text' in d:                                      # 手动改过文字：标记为已处理，别再被后台识别覆盖
            sets.append("ocr_status='done'"); sets.append("ocr_error=''")
        if sets:
            self.db.execute('UPDATE supplier_chats SET %s WHERE id=?' % ','.join(sets), args + [cid])

    def delete_chat(self, cid):
        c = self._chat(cid)
        self.db.execute('DELETE FROM supplier_chats WHERE id=?', (cid,))
        self._rm([c['image_file'], c['thumb_file']])

    def rerun_ocr(self, cid):
        c = self._chat(cid)
        if not c['image_file']:
            raise ApiError('这条记录没有截图')
        self.db.execute("UPDATE supplier_chats SET ocr_status='pending', ocr_error='' WHERE id=?", (cid,))
        self._start_ocr(cid)

    def _start_ocr(self, cid):
        if self.async_ocr:
            threading.Thread(target=self._do_ocr, args=(cid,), daemon=True).start()
        else:
            self._do_ocr(cid)

    def _do_ocr(self, cid):
        try:
            c = self.db.one('SELECT image_file FROM supplier_chats WHERE id=?', (cid,))
            if not c:
                return
            status, text, err = self.ocr_fn(os.path.join(self.dir, c['image_file']))
            if status not in ('done', 'unavailable', 'failed'):
                status, err = 'failed', 'OCR 引擎返回了未知状态'
            cur = self.db.one('SELECT ocr_status FROM supplier_chats WHERE id=?', (cid,))
            if not cur or cur['ocr_status'] != 'pending':        # 这期间被删除或手动改过文字：不覆盖
                return
            self.db.execute('UPDATE supplier_chats SET ocr_status=?, ocr_text=?, ocr_error=? WHERE id=?', (status, text if status == 'done' else '', err or '', cid))
        except Exception as e:                                   # 后台线程不能抛异常出去
            try:
                self.db.execute("UPDATE supplier_chats SET ocr_status='failed', ocr_error=? WHERE id=?", (str(e)[:300], cid))
            except Exception:
                pass

    def search_chats(self, q, project='', limit=50):
        q = (q or '').strip()
        if not q and not project:
            return []
        where, args = ['1=1'], []
        if q:
            like = '%' + q.replace('%', r'\%').replace('_', r'\_') + '%'
            where.append("(c.ocr_text LIKE ? ESCAPE '\\' OR c.note LIKE ? ESCAPE '\\' OR c.title LIKE ? ESCAPE '\\' OR c.project LIKE ? ESCAPE '\\')")
            args += [like] * 4
        if project:
            where.append('c.project=?'); args.append(project)
        rows = self.db.query("""SELECT c.*, s.name AS supplier_name FROM supplier_chats c JOIN suppliers s ON s.id=c.supplier_id
            WHERE %s ORDER BY c.id DESC LIMIT ?""" % ' AND '.join(where), args + [max(1, min(int(limit), 200))])
        for r in rows:
            self._chat_row(r)
            r['snippet'] = self._snippet(r['ocr_text'] or r['note'], q)
        return rows

    @staticmethod
    def _snippet(text, q, width=60):
        text = ' '.join((text or '').split())
        if not q:
            return text[:width * 2]
        i = text.lower().find(q.lower())
        if i < 0:
            return text[:width * 2]
        a = max(0, i - width)
        return ('…' if a else '') + text[a:i + len(q) + width] + ('…' if i + len(q) + width < len(text) else '')

    # ---------- 项目 ----------
    def projects(self):
        return self.db.query("""SELECT project, COUNT(DISTINCT supplier_id) AS suppliers FROM (
              SELECT project, supplier_id FROM supplier_chats WHERE project<>''
              UNION ALL SELECT project, supplier_id FROM supplier_quotes WHERE project<>'' AND supplier_id IS NOT NULL)
            GROUP BY project ORDER BY project""")

    def project_view(self, project):
        """一个项目问过哪些供应商：对比各家的沟通量、报价、状态，方便最后选一家。"""
        project = (project or '').strip()
        if not project:
            raise ApiError('请指定项目名称')
        rows = self.db.query("""SELECT s.id, s.name, s.status, s.rating, s.contact, s.wechat, s.phone,
              (SELECT COUNT(*) FROM supplier_chats c WHERE c.supplier_id=s.id AND c.project=?) AS chat_count,
              (SELECT MAX(COALESCE(NULLIF(c.chat_date,''), substr(c.created_at,1,10))) FROM supplier_chats c WHERE c.supplier_id=s.id AND c.project=?) AS last_chat
            FROM suppliers s WHERE EXISTS(SELECT 1 FROM supplier_chats c WHERE c.supplier_id=s.id AND c.project=?)
              OR EXISTS(SELECT 1 FROM supplier_quotes q WHERE q.supplier_id=s.id AND q.project=?) ORDER BY s.status='cooperating' DESC, s.name""",
                             (project,) * 4)
        for r in rows:
            r['status_label'] = STATUSES.get(r['status'], r['status'])
            r['quotes'] = self.db.query("""SELECT q.id, q.price_cny, q.quote_date, q.is_adopted, p.sku, p.name AS product_name
                FROM supplier_quotes q LEFT JOIN products p ON p.id=q.product_id WHERE q.supplier_id=? AND q.project=? ORDER BY q.is_adopted DESC, q.price_cny""", (r['id'], project))
        return rows
