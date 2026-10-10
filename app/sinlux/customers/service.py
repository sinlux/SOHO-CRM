# -*- coding: utf-8 -*-
"""客户：增删改查、搜索筛选、备注、提醒。纯业务逻辑，不依赖 HTTP。"""
import base64
import os
import re
import time

from ..core import schema
from ..core.util import ApiError, now, today, valid_date, like, clean_multi

TEXT_FIELDS = [f for f in schema.CUSTOMER_FIELDS if f not in ('lv', 'stage')]
MULTI_FIELDS = ('website', 'emails', 'linkedin', 'facebook', 'other_social')  # 空白分隔的多值字段
IMAGE_EXTS = {'png': 'png', 'jpg': 'jpg', 'jpeg': 'jpg', 'gif': 'gif', 'webp': 'webp'}
MAX_IMAGE_BYTES = 10 * 1024 * 1024

QUOTE_STATUS_LABELS = {'draft': '草稿', 'sent': '已发送', 'accepted': '成交',
                       'rejected': '未成交', 'expired': '过期'}


def normalize_stage(v):
    """接受中文标签或旧版英文 key，返回中文标签；空串表示未设置。"""
    if v in (None, ''):
        return ''
    v = str(v).strip()
    v = schema.STAGE_KEY_TO_LABEL.get(v, v)
    if v not in schema.STAGES:
        raise ApiError('阶段无效: %s（可选: %s）' % (v, '/'.join(schema.STAGES)))
    return v


def normalize_lv(v):
    if v in (None, ''):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ApiError('LV 必须是 1-6 的整数')
    if not 1 <= n <= 6:
        raise ApiError('LV 必须在 1-6 之间（6=长期合作，1=不确定）')
    return n


def _clean(data, partial):
    """校验并规整客户字段。partial=True 时只处理出现的字段。"""
    out = {}
    if 'lv' in data or not partial:
        out['lv'] = normalize_lv(data.get('lv'))
    if 'stage' in data or not partial:
        out['stage'] = normalize_stage(data.get('stage'))
    for f in TEXT_FIELDS:
        if f in data or not partial:
            v = data.get(f)
            v = '' if v is None else str(v).strip()
            if f in MULTI_FIELDS:
                v = clean_multi(v)
            out[f] = v
    return out


class CustomerService:
    def __init__(self, db, images_dir):
        self.db = db
        self.images_dir = images_dir
        os.makedirs(images_dir, exist_ok=True)

    # ---------- 客户 ----------
    def create(self, data):
        d = _clean(data, partial=False)
        if not (d['company'] or d['name'] or d['emails'] or d['website']):
            raise ApiError('至少填一项：公司名/联系人/邮箱/网站')
        ts = now()
        cols = list(d)
        cur = self.db.execute(
            'INSERT INTO customers(%s,created_at,updated_at) VALUES(%s)'
            % (','.join(cols), ','.join('?' * (len(cols) + 2))),
            [d[c] for c in cols] + [ts, ts])
        return cur.lastrowid

    def update(self, cid, data):
        self.require(cid)
        d = _clean(data, partial=True)
        if not d:
            return
        self.db.execute('UPDATE customers SET %s,updated_at=? WHERE id=?'
                        % ','.join(c + '=?' for c in d), list(d.values()) + [now(), cid])

    def require(self, cid):
        c = self.db.one('SELECT * FROM customers WHERE id=?', (cid,))
        if not c:
            raise ApiError('客户不存在', 404)
        return c

    def get(self, cid):
        c = self.require(cid)
        quotes = self.db.query(
            """SELECT q.id, q.quote_no, q.status, q.currency, q.total, q.created_at,
                      (SELECT COUNT(*) FROM quote_items i WHERE i.quote_id=q.id) AS item_count
               FROM quotes q WHERE q.customer_id=? ORDER BY q.created_at DESC, q.id DESC""", (cid,))
        for q in quotes:
            q['status_label'] = QUOTE_STATUS_LABELS.get(q['status'], q['status'])
        return {
            'customer': c,
            'notes': self.db.query('SELECT * FROM notes WHERE customer_id=? ORDER BY id DESC', (cid,)),
            'reminders': self.db.query('SELECT * FROM reminders WHERE customer_id=? ORDER BY done, due_date', (cid,)),
            'enrichments': self.db.query('SELECT id,status,error,created_at,round,score FROM enrichments '
                                         'WHERE customer_id=? ORDER BY id DESC', (cid,)),
            'quotes': quotes,
        }

    def impact(self, cid):
        """删除前给用户看的影响范围。"""
        self.require(cid)
        n = lambda sql: self.db.scalar(sql, (cid,))
        return {
            'notes': n('SELECT COUNT(*) FROM notes WHERE customer_id=?'),
            'reminders': n('SELECT COUNT(*) FROM reminders WHERE customer_id=?'),
            'enrichments': n('SELECT COUNT(*) FROM enrichments WHERE customer_id=?'),
            'quotes': n('SELECT COUNT(*) FROM quotes WHERE customer_id=?'),
        }

    def delete(self, cid):
        """级联删除备注/提醒/背调/报价单(含明细)；价格历史里的客户置空（价格本身保留）。"""
        self.require(cid)
        images = [r['image_path'] for r in self.db.query(
            "SELECT image_path FROM notes WHERE customer_id=? AND image_path<>''", (cid,))]
        with self.db.tx():
            self.db.execute('UPDATE price_history SET customer_id=NULL WHERE customer_id=?', (cid,))
            self.db.execute('DELETE FROM customers WHERE id=?', (cid,))
        for name in images:
            self._remove_image(name)

    def list(self, search='', lv='', country='', stage='', limit=100, offset=0):
        where, params = ['1=1'], []
        if lv != '':
            where.append('lv=?'); params.append(normalize_lv(lv))
        if country:
            where.append('country=?'); params.append(country)
        if stage:
            where.append('stage=?'); params.append(normalize_stage(stage))
        if search:
            k = like(search)
            where.append("""(company LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\' OR emails LIKE ? ESCAPE '\\'
                OR website LIKE ? ESCAPE '\\' OR main_business LIKE ? ESCAPE '\\' OR address LIKE ? ESCAPE '\\'
                OR ai_summary LIKE ? ESCAPE '\\' OR country LIKE ? ESCAPE '\\'
                OR id IN (SELECT customer_id FROM notes WHERE content LIKE ? ESCAPE '\\'))""")
            params += [k] * 9
        w = ' AND '.join(where)
        total = self.db.scalar('SELECT COUNT(*) FROM customers WHERE ' + w, params)
        limit = max(1, min(int(limit), 1000))
        rows = self.db.query('SELECT * FROM customers WHERE %s ORDER BY COALESCE(lv,0) DESC, updated_at DESC, id DESC '
                             'LIMIT ? OFFSET ?' % w, params + [limit, max(0, int(offset))])
        return rows, total

    def countries(self):
        return self.db.query("SELECT country, COUNT(*) n FROM customers WHERE country<>'' "
                             "GROUP BY country ORDER BY n DESC, country")

    def stats(self):
        return {
            'total': self.db.scalar('SELECT COUNT(*) FROM customers'),
            'due_reminders': self.db.scalar('SELECT COUNT(*) FROM reminders WHERE done=0 AND due_date<=?', (today(),)),
        }

    # ---------- 备注 ----------
    def add_note(self, cid, content='', image_base64=''):
        self.require(cid)
        content = (content or '').strip()
        image = self._save_image(cid, image_base64) if image_base64 else ''
        if not content and not image:
            raise ApiError('备注为空')
        cur = self.db.execute('INSERT INTO notes(customer_id,content,image_path,created_at) VALUES(?,?,?,?)',
                              (cid, content, image, now()))
        return cur.lastrowid

    def add_system_note(self, cid, content):
        """系统留痕（导入、背调等）。"""
        return self.db.execute('INSERT INTO notes(customer_id,content,image_path,created_at) VALUES(?,?,?,?)',
                               (cid, content, '', now())).lastrowid

    def delete_note(self, nid):
        n = self.db.one('SELECT * FROM notes WHERE id=?', (nid,))
        if not n:
            raise ApiError('备注不存在', 404)
        self.db.execute('DELETE FROM notes WHERE id=?', (nid,))
        if n['image_path']:
            self._remove_image(n['image_path'])

    def _save_image(self, cid, raw):
        m = re.match(r'^data:image/(\w+);base64,(.*)$', raw, re.S)
        ext, b64 = (m.group(1).lower(), m.group(2)) if m else ('png', raw)
        if ext not in IMAGE_EXTS:
            raise ApiError('不支持的图片格式: %s' % ext)
        try:
            data = base64.b64decode(b64, validate=False)
        except Exception:
            raise ApiError('图片数据无效')
        if not data:
            raise ApiError('图片数据无效')
        if len(data) > MAX_IMAGE_BYTES:
            raise ApiError('图片超过 10MB')
        name = '%d_%d.%s' % (cid, int(time.time() * 1000), IMAGE_EXTS[ext])
        with open(os.path.join(self.images_dir, name), 'wb') as f:
            f.write(data)
        return name

    def _remove_image(self, name):
        # 只删 images 目录下的普通文件名，且没有别的备注还引用它
        if name != os.path.basename(name):
            return
        if self.db.scalar('SELECT COUNT(*) FROM notes WHERE image_path=?', (name,)):
            return
        try:
            os.remove(os.path.join(self.images_dir, name))
        except OSError:
            pass

    # ---------- 提醒 ----------
    def add_reminder(self, cid, content, due_date):
        self.require(cid)
        if not valid_date(due_date):
            raise ApiError('提醒日期格式应为 YYYY-MM-DD')
        return self.db.execute('INSERT INTO reminders(customer_id,content,due_date,created_at) VALUES(?,?,?,?)',
                               (cid, (content or '').strip(), due_date, now())).lastrowid

    def mark_reminder_done(self, rid):
        if not self.db.execute('UPDATE reminders SET done=1 WHERE id=?', (rid,)).rowcount:
            raise ApiError('提醒不存在', 404)

    def delete_reminder(self, rid):
        if not self.db.execute('DELETE FROM reminders WHERE id=?', (rid,)).rowcount:
            raise ApiError('提醒不存在', 404)

    def pending_reminders(self):
        t = today()
        rows = self.db.query("""SELECT r.*, c.company, c.name FROM reminders r
            JOIN customers c ON c.id=r.customer_id WHERE r.done=0 ORDER BY r.due_date, r.id""")
        for r in rows:
            r['overdue'] = bool(r['due_date']) and r['due_date'] <= t
        return rows, t
