# -*- coding: utf-8 -*-
"""类目、结构化规格字段、SKU 自动编号。

SKU 规则（沿用旧版）：<类目总前缀><子类前缀><6位序号>，如 SL+SP+000001。
"""
import json
import re
import uuid

from ..core.util import ApiError
from . import seeds

# SKU 格式（沿用旧版并铺满新业务）：<类目前缀><子类前缀><6位序号>，如 SL + SP + 000001 = SLSP000001。
# 内置类目 → 默认总前缀。首饰旧版没有预置，仍不预置（需要时在「SKU前缀」里设置）。
BUILTIN_CATEGORY_PREFIXES = {'lighting': 'SL', 'furniture': 'GL', 'decor': 'DC', 'other': 'OT'}
# 各类目默认子类前缀（子类名必须与规格字段「子类」的选项一致，否则选不到）。类目内前缀不得重复。
DEFAULT_SUB_PREFIXES = {
    'lighting': [  # LED 灯饰
        ('射灯', 'SP'), ('筒灯', 'DL'), ('球泡', 'BU'), ('线条灯', 'LL'), ('吸顶灯', 'CL'), ('吊灯', 'PD'),
        ('壁灯', 'WL'), ('落地灯', 'FL'), ('台灯', 'TL'), ('轨道灯', 'TR'), ('面板灯', 'PL'), ('泛光灯', 'FD'),
        ('投光灯', 'PF'), ('草坪灯', 'LW'), ('庭院灯', 'GD'), ('灯带', 'LS'), ('工矿灯', 'HB'), ('路灯', 'ST'),
        ('装饰灯', 'DE'), ('其他', 'OT')],
    'furniture': [  # 家具（含酒店 FF&E 常见品类）
        ('卫浴柜', 'BV'), ('办公椅', 'OC'), ('办公桌', 'OD'), ('沙发', 'SF'), ('床架', 'BD'), ('餐桌', 'DT'),
        ('餐椅', 'DC'), ('柜子', 'CB'), ('茶几', 'CT'), ('床头柜', 'NS'), ('书架', 'BS'), ('鞋柜', 'SR'),
        ('床垫', 'MT'), ('床头板', 'HB'), ('衣柜', 'WD'), ('电视柜', 'TV'), ('行李架', 'LR'), ('梳妆台', 'DR'),
        ('扶手椅', 'AC'), ('边几', 'SD'), ('吧椅', 'BC'), ('凳子', 'ST'), ('户外家具', 'OF'), ('躺椅', 'LG'),
        ('其他', 'OT')],
    'decor': [  # 装饰材料
        ('毯子', 'BL'), ('枕头', 'PW'), ('窗帘', 'CU'), ('地毯', 'RG'), ('玻璃', 'GS'), ('镜子', 'MR'),
        ('花瓶', 'VS'), ('摆件', 'OR'), ('挂画', 'AT'), ('布草', 'LN'), ('墙纸', 'WP'), ('餐具', 'TW'),
        ('香薰蜡烛', 'CD'), ('花艺', 'FR'), ('其他', 'OT')],
}
PREFIX_RE = re.compile(r'^[A-Z]{1,4}$')
FIELD_TYPES = ('text', 'number', 'select', 'multi', 'textarea')
REMOVED_KEY = 'seed_removed'


def _removed(db):
    """用户删掉的内置项（类目/字段/子类）。ensure_seeds 每次启动都会补缺失的内置项，不记下来的话删掉的东西会"复活"。"""
    try:
        d = json.loads(db.get_setting(REMOVED_KEY) or '{}')
    except ValueError:
        d = {}
    return {k: set(d.get(k, [])) for k in ('cats', 'fields', 'subs')}


def _mark_removed(db, kind, ident):
    d = _removed(db)
    d[kind].add(ident)
    db.set_setting(REMOVED_KEY, json.dumps({k: sorted(v) for k, v in d.items()}, ensure_ascii=False))


def _unmark_removed(db, kind, ident):
    d = _removed(db)
    if ident in d[kind]:
        d[kind].discard(ident)
        db.set_setting(REMOVED_KEY, json.dumps({k: sorted(v) for k, v in d.items()}, ensure_ascii=False))



def ensure_seeds(db):
    """幂等：补齐缺失的内置类目/字段/默认前缀；已有的一律不动（不覆盖用户改过的内容）。返回新增条数。"""
    added = 0
    gone = _removed(db)
    with db.tx():
        for cat in seeds.DEFAULT_CATEGORIES:
            if cat['code'] in gone['cats']:
                continue
            row = db.one('SELECT id FROM categories WHERE code=?', (cat['code'],))
            if row:
                cat_id = row['id']
            else:
                cat_id = db.execute('INSERT INTO categories(code,name,icon,sort_order,is_builtin) VALUES(?,?,?,?,1)',
                                    (cat['code'], cat['name'], cat['icon'], cat['sort_order'])).lastrowid
                added += 1
            for i, f in enumerate(seeds.DEFAULT_FIELDS_MAP.get(cat['code'], [])):
                if '%s:%s' % (cat['code'], f['key']) in gone['fields']:
                    continue
                ex = db.one('SELECT id, options FROM category_fields WHERE category_id=? AND field_key=?', (cat_id, f['key']))
                if ex:
                    # 已有字段：只把种子里新增的选项并进去（不删、不重排用户已有的选项）
                    if f.get('options') and f['type'] in ('select', 'multi'):
                        have = [o for o in (ex['options'] or '').split('|') if o]
                        new = [o for o in f['options'].split('|') if o and o not in have
                               and not (f['key'] == 'subcategory' and '%s:%s' % (cat['code'], o) in gone['subs'])]
                        if new:
                            db.execute('UPDATE category_fields SET options=? WHERE id=?', ('|'.join(have + new), ex['id']))
                            added += len(new)
                    continue
                db.execute("""INSERT INTO category_fields
                    (category_id,field_key,field_label,field_type,is_required,options,unit,placeholder,sort_order)
                    VALUES(?,?,?,?,0,?,?,?,?)""",
                           (cat_id, f['key'], f['label'], f['type'], f.get('options'), f.get('unit'),
                            f.get('placeholder'), i))
                added += 1
            pre = BUILTIN_CATEGORY_PREFIXES.get(cat['code'])
            if pre and not db.one('SELECT 1 FROM category_sku_prefixes WHERE category_id=?', (cat_id,)):
                db.execute('INSERT INTO category_sku_prefixes(category_id,prefix) VALUES(?,?)', (cat_id, pre))
                added += 1
            for sub, pf in DEFAULT_SUB_PREFIXES.get(cat['code'], []):
                if '%s:%s' % (cat['code'], sub) in gone['subs']:
                    continue
                if db.one('SELECT 1 FROM subcategory_prefixes WHERE category_id=? AND subcategory_value=?', (cat_id, sub)):
                    continue
                # 用户在该类目里已把这个前缀给了别的子类，就不再补默认的，避免两个子类共用一个编号段
                if db.one('SELECT 1 FROM subcategory_prefixes WHERE category_id=? AND prefix=?', (cat_id, pf)):
                    continue
                db.execute('INSERT INTO subcategory_prefixes(category_id,subcategory_value,prefix) VALUES(?,?,?)',
                           (cat_id, sub, pf))
                added += 1
    return added


def _prefix(s):
    s = (s or '').strip().upper()
    if not PREFIX_RE.match(s):
        raise ApiError('前缀必须为 1-4 位英文字母（如 SP、LIGH）')
    return s


class CatalogService:
    def __init__(self, db):
        self.db = db

    def categories(self):
        return self.db.query("""SELECT c.id, c.code, c.name, c.icon, c.sort_order, c.is_builtin,
              (SELECT COUNT(*) FROM category_fields f WHERE f.category_id=c.id) AS field_count,
              (SELECT COUNT(*) FROM products p WHERE p.category_id=c.id) AS product_count
            FROM categories c ORDER BY c.sort_order, c.id""")

    def require_category(self, cid):
        c = self.db.one('SELECT * FROM categories WHERE id=?', (cid,))
        if not c:
            raise ApiError('类目不存在', 404)
        return c

    def fields(self, category_id):
        self.require_category(category_id)
        rows = self.db.query("""SELECT id, field_key AS key, field_label AS label, field_type AS type, options, unit,
              placeholder, sort_order, applies_to FROM category_fields WHERE category_id=? ORDER BY sort_order, id""", (category_id,))
        for r in rows:
            r['options_list'] = [o for o in (r['options'] or '').split('|') if o]
            r['applies_list'] = [o for o in (r['applies_to'] or '').split('|') if o]
        return rows

    # ---------- SKU 前缀 ----------
    def prefixes(self, category_id):
        self.require_category(category_id)
        return {'category_prefix': (self.db.one('SELECT prefix FROM category_sku_prefixes WHERE category_id=?',
                                                (category_id,)) or {}).get('prefix'),
                'subcategories': self.db.query('SELECT id, subcategory_value, prefix FROM subcategory_prefixes '
                                               'WHERE category_id=? ORDER BY subcategory_value', (category_id,))}

    def set_category_prefix(self, category_id, prefix):
        self.require_category(category_id)
        prefix = _prefix(prefix)
        for r in self.db.query('SELECT sp.prefix AS sp, sp.subcategory_value AS sv FROM subcategory_prefixes sp WHERE sp.category_id=?', (category_id,)):
            self._check_no_collision(category_id, prefix, r['sp'], r['sv'])
        self.db.execute('INSERT INTO category_sku_prefixes(category_id,prefix) VALUES(?,?) '
                        "ON CONFLICT(category_id) DO UPDATE SET prefix=excluded.prefix, updated_at=datetime('now')",
                        (category_id, prefix))
        return prefix

    def _check_no_collision(self, category_id, cat_prefix, sub_prefix, sub_value=None):
        """同类目内子类前缀不能重复；跨类目"类目前缀+子类前缀"拼出的完整前缀也不能相同，否则两个品类会抢同一段编号。"""
        full = cat_prefix + sub_prefix
        dup = self.db.one('SELECT subcategory_value FROM subcategory_prefixes WHERE category_id=? AND prefix=? '
                          'AND subcategory_value<>?', (category_id, sub_prefix, sub_value or ''))
        if dup:
            raise ApiError('子类前缀「%s」已被本类目的「%s」使用' % (sub_prefix, dup['subcategory_value']))
        for r in self.db.query("""SELECT c.name, cp.prefix AS cp, sp.prefix AS sp, sp.subcategory_value AS sv
            FROM subcategory_prefixes sp JOIN category_sku_prefixes cp ON cp.category_id=sp.category_id
            JOIN categories c ON c.id=sp.category_id WHERE sp.category_id<>?""", (category_id,)):
            if r['cp'] + r['sp'] == full:
                raise ApiError('完整前缀「%s」已被类目「%s」的「%s」使用' % (full, r['name'], r['sv']))

    def upsert_sub_prefix(self, category_id, value, prefix):
        self.require_category(category_id)
        value = (value or '').strip()
        if not value:
            raise ApiError('子类名称不能为空')
        prefix = _prefix(prefix)
        cp = (self.db.one('SELECT prefix FROM category_sku_prefixes WHERE category_id=?', (category_id,)) or {}).get('prefix')
        if cp:
            self._check_no_collision(category_id, cp, prefix, value)
        self.db.execute('INSERT INTO subcategory_prefixes(category_id,subcategory_value,prefix) VALUES(?,?,?) '
                        "ON CONFLICT(category_id,subcategory_value) DO UPDATE SET prefix=excluded.prefix, updated_at=datetime('now')",
                        (category_id, value, prefix))

    def delete_sub_prefix(self, prefix_id):
        if not self.db.execute('DELETE FROM subcategory_prefixes WHERE id=?', (prefix_id,)).rowcount:
            raise ApiError('前缀不存在', 404)

    def next_sku(self, category_id, subcategory):
        cat = self.require_category(category_id)
        cp = (self.db.one('SELECT prefix FROM category_sku_prefixes WHERE category_id=?', (category_id,)) or {}).get('prefix')
        if not cp:
            raise ApiError('类目「%s」尚未设置 SKU 总前缀，请在「SKU 前缀」里设置（1-4 位字母，如 SL）' % cat['name'])
        subcategory = (subcategory or '').strip()
        if not subcategory:
            raise ApiError('请先选择/填写产品的「子类」（用于决定 SKU 子类前缀）')
        sp = (self.db.one('SELECT prefix FROM subcategory_prefixes WHERE category_id=? AND subcategory_value=?',
                          (category_id, subcategory)) or {}).get('prefix')
        if not sp:
            raise ApiError('子类「%s」尚未配置前缀，请在「SKU 前缀」里添加' % subcategory)
        full = cp + sp
        mx = 0
        for r in self.db.query('SELECT sku FROM products WHERE sku LIKE ?', (full + '%',)):
            tail = r['sku'][len(full):]
            if len(tail) == 6 and tail.isdigit():
                mx = max(mx, int(tail))
        if mx + 1 > 999999:
            raise ApiError('前缀「%s」的序号已用尽（6 位上限）' % full)
        return '%s%06d' % (full, mx + 1)

    # ================= 类目 / 字段 / 子类的管理（用户自己增删改） =================
    @staticmethod
    def _clean_name(v, what, limit=40):
        v = (v or '').strip()
        if not v:
            raise ApiError('%s不能为空' % what)
        if len(v) > limit:
            raise ApiError('%s最长 %d 个字' % (what, limit))
        if '|' in v:
            raise ApiError('%s里不能包含竖线 |' % what)
        return v

    def create_category(self, name, icon='📦', prefix=None):
        name = self._clean_name(name, '类目名称', 30)
        if self.db.one('SELECT 1 FROM categories WHERE name=? COLLATE NOCASE', (name,)):
            raise ApiError('已经有叫「%s」的类目' % name, 409)
        icon = (icon or '📦').strip()[:4] or '📦'
        if prefix:
            prefix = _prefix(prefix)
        with self.db.tx():
            order = (self.db.scalar('SELECT MAX(sort_order) FROM categories') or 0) + 1
            cid = self.db.execute('INSERT INTO categories(code,name,icon,sort_order,is_builtin) VALUES(?,?,?,?,0)',
                                  ('c_' + uuid.uuid4().hex[:8], name, icon, order)).lastrowid
            # 新类目给一套通用起步字段，之后在「字段」里随便增删
            for i, (key, label, typ) in enumerate([('subcategory', '子类', 'select'), ('dimensions', '尺寸', 'text'), ('material', '材质', 'text'),
                                                   ('color', '颜色', 'text'), ('remark', '备注', 'textarea')]):
                self.db.execute('INSERT INTO category_fields(category_id,field_key,field_label,field_type,is_required,sort_order) VALUES(?,?,?,?,0,?)',
                                (cid, key, label, typ, i))
            if prefix:
                self.set_category_prefix(cid, prefix)
        return cid

    def update_category(self, cid, name=None, icon=None):
        cat = self.require_category(cid)
        sets, args = [], []
        if name is not None:
            name = self._clean_name(name, '类目名称', 30)
            if self.db.one('SELECT 1 FROM categories WHERE name=? COLLATE NOCASE AND id<>?', (name, cid)):
                raise ApiError('已经有叫「%s」的类目' % name, 409)
            sets.append('name=?'); args.append(name)
        if icon is not None:
            sets.append('icon=?'); args.append((icon or '').strip()[:4] or cat['icon'])
        if sets:
            self.db.execute('UPDATE categories SET %s WHERE id=?' % ','.join(sets), args + [cid])

    def delete_category(self, cid):
        cat = self.require_category(cid)
        n = self.db.scalar('SELECT COUNT(*) FROM products WHERE category_id=?', (cid,))
        if n:
            raise ApiError('类目「%s」下还有 %d 个产品，请先把它们移到别的类目（在产品页改「类目」）再删除' % (cat['name'], n), 409)
        with self.db.tx():
            self.db.execute('DELETE FROM categories WHERE id=?', (cid,))
            if cat['is_builtin']:
                _mark_removed(self.db, 'cats', cat['code'])

    # ---- 字段 ----
    def _field(self, fid):
        f = self.db.one('SELECT f.*, c.code AS cat_code FROM category_fields f JOIN categories c ON c.id=f.category_id WHERE f.id=?', (fid,))
        if not f:
            raise ApiError('字段不存在', 404)
        return f

    @staticmethod
    def _opts(v):
        items = v.split('|') if isinstance(v, str) else (v or [])
        out = []
        for o in items:
            o = str(o).strip()
            if o and '|' not in o and o not in out:
                if len(o) > 60:
                    raise ApiError('选项「%s…」太长（最多 60 个字）' % o[:10])
                out.append(o)
        return out

    def _applies(self, cid, v):
        """适用子类：必须是本类目已有的子类；空列表 = 所有子类。"""
        names = {s['name'] for s in self.subcategories(cid)}
        out = []
        for a in (v if isinstance(v, list) else [x for x in (v or '').split('|') if x]):
            a = str(a).strip()
            if a not in names:
                raise ApiError('子类「%s」不存在，无法设为适用范围' % a)
            if a not in out:
                out.append(a)
        return '|'.join(out)

    def create_field(self, cid, label, ftype, unit='', options=None, placeholder='', applies_to=None):
        self.require_category(cid)
        label = self._clean_name(label, '字段名称', 30)
        if ftype not in FIELD_TYPES:
            raise ApiError('字段类型无效，可选：' + '/'.join(FIELD_TYPES))
        if self.db.one('SELECT 1 FROM category_fields WHERE category_id=? AND field_label=?', (cid, label)):
            raise ApiError('本类目已经有叫「%s」的字段' % label, 409)
        opts = self._opts(options) if ftype in ('select', 'multi') else []
        if ftype in ('select', 'multi') and not opts:
            raise ApiError('下拉/多选字段至少要有一个选项')
        order = (self.db.scalar('SELECT MAX(sort_order) FROM category_fields WHERE category_id=?', (cid,)) or 0) + 1
        return self.db.execute("""INSERT INTO category_fields(category_id,field_key,field_label,field_type,is_required,options,unit,placeholder,sort_order,applies_to)
            VALUES(?,?,?,?,0,?,?,?,?,?)""", (cid, 'f_' + uuid.uuid4().hex[:8], label, ftype, '|'.join(opts) or None, (unit or '').strip()[:10] or None,
                                             (placeholder or '').strip()[:80] or None, order, self._applies(cid, applies_to))).lastrowid

    def update_field(self, fid, label=None, unit=None, options=None, placeholder=None, applies_to=None):
        f = self._field(fid)
        sets, args = [], []
        if label is not None:
            label = self._clean_name(label, '字段名称', 30)
            if self.db.one('SELECT 1 FROM category_fields WHERE category_id=? AND field_label=? AND id<>?', (f['category_id'], label, fid)):
                raise ApiError('本类目已经有叫「%s」的字段' % label, 409)
            sets.append('field_label=?'); args.append(label)
        if unit is not None:
            sets.append('unit=?'); args.append((unit or '').strip()[:10] or None)
        if placeholder is not None:
            sets.append('placeholder=?'); args.append((placeholder or '').strip()[:80] or None)
        if options is not None:
            if f['field_key'] == 'subcategory':
                raise ApiError('「子类」的选项请用「子类管理」修改（它同时决定 SKU 编号前缀）')
            if f['field_type'] not in ('select', 'multi'):
                raise ApiError('只有下拉/多选字段有选项')
            opts = self._opts(options)
            if not opts:
                raise ApiError('下拉/多选字段至少要有一个选项')
            sets.append('options=?'); args.append('|'.join(opts))
        if applies_to is not None:
            if f['field_key'] == 'subcategory':
                raise ApiError('「子类」字段本身对所有子类都显示')
            sets.append('applies_to=?'); args.append(self._applies(f['category_id'], applies_to))
        if sets:
            self.db.execute('UPDATE category_fields SET %s WHERE id=?' % ','.join(sets), args + [fid])

    def field_usage(self, fid):
        self._field(fid)
        return self.db.scalar("SELECT COUNT(*) FROM product_field_values WHERE field_id=? AND TRIM(COALESCE(value,''))<>''", (fid,))

    def delete_field(self, fid):
        f = self._field(fid)
        if f['field_key'] == 'subcategory':
            raise ApiError('「子类」字段不能删除（SKU 自动编号依赖它）；可以在「子类管理」里增删具体的子类')
        with self.db.tx():
            self.db.execute('DELETE FROM category_fields WHERE id=?', (fid,))       # 产品上已填的值随之删除（外键级联）
            if f['cat_code'] in {c['code'] for c in seeds.DEFAULT_CATEGORIES} and any(
                    x['key'] == f['field_key'] for x in seeds.DEFAULT_FIELDS_MAP.get(f['cat_code'], [])):
                _mark_removed(self.db, 'fields', '%s:%s' % (f['cat_code'], f['field_key']))

    def move_field(self, fid, direction):
        f = self._field(fid)
        rows = self.db.query('SELECT id, sort_order FROM category_fields WHERE category_id=? ORDER BY sort_order, id', (f['category_id'],))
        ids = [r['id'] for r in rows]
        i = ids.index(fid)
        j = i - 1 if direction == 'up' else i + 1
        if direction not in ('up', 'down') or not 0 <= j < len(ids):
            return
        ids[i], ids[j] = ids[j], ids[i]
        for n, x in enumerate(ids):                                                    # 整体重排，避免 sort_order 相同导致交换无效
            self.db.execute('UPDATE category_fields SET sort_order=? WHERE id=?', (n, x))

    # ---- 子类（= 「子类」字段的选项 + 对应的 SKU 前缀，两边一起改） ----
    def _sub_field(self, cid, create=False):
        f = self.db.one("SELECT * FROM category_fields WHERE category_id=? AND field_key='subcategory'", (cid,))
        if not f and create:
            order = (self.db.scalar('SELECT MAX(sort_order) FROM category_fields WHERE category_id=?', (cid,)) or 0) + 1
            fid = self.db.execute("""INSERT INTO category_fields(category_id,field_key,field_label,field_type,is_required,sort_order)
                VALUES(?,'subcategory','子类','select',0,?)""", (cid, order)).lastrowid
            f = self.db.one('SELECT * FROM category_fields WHERE id=?', (fid,))
        return f

    def subcategories(self, cid):
        self.require_category(cid)
        f = self._sub_field(cid)
        names = [o for o in ((f['options'] if f else '') or '').split('|') if o]
        prefixes = {r['subcategory_value']: r for r in self.db.query('SELECT id, subcategory_value, prefix FROM subcategory_prefixes WHERE category_id=?', (cid,))}
        for extra in prefixes:
            if extra not in names:
                names.append(extra)
        counts = {}
        if f:
            counts = {r['value']: r['n'] for r in self.db.query('SELECT value, COUNT(*) n FROM product_field_values WHERE field_id=? GROUP BY value', (f['id'],))}
        return [{'name': n, 'prefix': (prefixes.get(n) or {}).get('prefix'), 'prefix_id': (prefixes.get(n) or {}).get('id'), 'product_count': counts.get(n, 0)} for n in names]

    def add_subcategory(self, cid, name, prefix=None):
        cat = self.require_category(cid)
        name = self._clean_name(name, '子类名称', 30)
        if prefix:
            prefix = _prefix(prefix)
        with self.db.tx():
            f = self._sub_field(cid, create=True)
            have = [o for o in (f['options'] or '').split('|') if o]
            if name in have:
                raise ApiError('子类「%s」已经存在' % name, 409)
            self.db.execute('UPDATE category_fields SET options=? WHERE id=?', ('|'.join(have + [name]), f['id']))
            _unmark_removed(self.db, 'subs', '%s:%s' % (cat['code'], name))
            if prefix:
                self.upsert_sub_prefix(cid, name, prefix)

    def rename_subcategory(self, cid, old, new, prefix=None):
        cat = self.require_category(cid)
        new = self._clean_name(new, '子类名称', 30)
        if prefix:
            prefix = _prefix(prefix)
        with self.db.tx():
            f = self._sub_field(cid)
            have = [o for o in ((f['options'] if f else '') or '').split('|') if o]
            if old not in have and not self.db.one('SELECT 1 FROM subcategory_prefixes WHERE category_id=? AND subcategory_value=?', (cid, old)):
                raise ApiError('子类「%s」不存在' % old, 404)
            if new != old and new in have:
                raise ApiError('子类「%s」已经存在' % new, 409)
            if f:
                self.db.execute('UPDATE category_fields SET options=? WHERE id=?', ('|'.join(new if o == old else o for o in have), f['id']))
                self.db.execute('UPDATE product_field_values SET value=? WHERE field_id=? AND value=?', (new, f['id'], old))
            self.db.execute('UPDATE subcategory_prefixes SET subcategory_value=? WHERE category_id=? AND subcategory_value=?', (new, cid, old))
            for r in self.db.query("SELECT id, applies_to FROM category_fields WHERE category_id=? AND applies_to<>''", (cid,)):
                self.db.execute('UPDATE category_fields SET applies_to=? WHERE id=?',
                                ('|'.join(new if o == old else o for o in r['applies_to'].split('|')), r['id']))
            if new != old and cat['code'] in {c['code'] for c in seeds.DEFAULT_CATEGORIES} and any(s == old for s, _ in DEFAULT_SUB_PREFIXES.get(cat['code'], [])):
                _mark_removed(self.db, 'subs', '%s:%s' % (cat['code'], old))        # 改名后别让内置的旧名字又长回来
            if prefix:
                self.upsert_sub_prefix(cid, new, prefix)

    def delete_subcategory(self, cid, name):
        cat = self.require_category(cid)
        with self.db.tx():
            f = self._sub_field(cid)
            have = [o for o in ((f['options'] if f else '') or '').split('|') if o]
            had_prefix = self.db.execute('DELETE FROM subcategory_prefixes WHERE category_id=? AND subcategory_value=?', (cid, name)).rowcount
            if name not in have and not had_prefix:
                raise ApiError('子类「%s」不存在' % name, 404)
            n = 0
            if f:
                self.db.execute('UPDATE category_fields SET options=? WHERE id=?', ('|'.join(o for o in have if o != name) or None, f['id']))
                n = self.db.scalar('SELECT COUNT(*) FROM product_field_values WHERE field_id=? AND value=?', (f['id'], name))
            for r in self.db.query("SELECT id, applies_to FROM category_fields WHERE category_id=? AND applies_to<>''", (cid,)):
                self.db.execute('UPDATE category_fields SET applies_to=? WHERE id=?', ('|'.join(o for o in r['applies_to'].split('|') if o != name), r['id']))
            if any(s == name for s, _ in DEFAULT_SUB_PREFIXES.get(cat['code'], [])):
                _mark_removed(self.db, 'subs', '%s:%s' % (cat['code'], name))
        return {'products_still_using': n}
