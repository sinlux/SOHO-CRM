# -*- coding: utf-8 -*-
"""类目、结构化规格字段、SKU 自动编号。

SKU 规则（沿用旧版）：<类目总前缀><子类前缀><6位序号>，如 SL+SP+000001。
"""
import re

from ..core.util import ApiError
from . import seeds

# 内置类目 → 默认总前缀 / 灯饰默认子类前缀（沿用旧版，不新增臆测的前缀）
BUILTIN_CATEGORY_PREFIXES = {'lighting': 'SL', 'furniture': 'GL', 'other': 'OT'}
DEFAULT_LIGHTING_SUB_PREFIXES = [
    ('射灯', 'SP'), ('筒灯', 'DL'), ('球泡', 'BU'), ('线条灯', 'LL'), ('吸顶灯', 'CL'), ('吊灯', 'PD'),
    ('壁灯', 'WL'), ('落地灯', 'FL'), ('台灯', 'TL'), ('轨道灯', 'TR'), ('面板灯', 'PL'), ('泛光灯', 'FD'),
    ('投光灯', 'PF'), ('草坪灯', 'LW'), ('庭院灯', 'GD'),
]
PREFIX_RE = re.compile(r'^[A-Z]{1,4}$')


def ensure_seeds(db):
    """幂等：补齐缺失的内置类目/字段/默认前缀；已有的一律不动（不覆盖用户改过的内容）。返回新增条数。"""
    added = 0
    with db.tx():
        for cat in seeds.DEFAULT_CATEGORIES:
            row = db.one('SELECT id FROM categories WHERE code=?', (cat['code'],))
            if row:
                cat_id = row['id']
            else:
                cat_id = db.execute('INSERT INTO categories(code,name,icon,sort_order,is_builtin) VALUES(?,?,?,?,1)',
                                    (cat['code'], cat['name'], cat['icon'], cat['sort_order'])).lastrowid
                added += 1
            for i, f in enumerate(seeds.DEFAULT_FIELDS_MAP.get(cat['code'], [])):
                if db.one('SELECT id FROM category_fields WHERE category_id=? AND field_key=?', (cat_id, f['key'])):
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
            if cat['code'] == 'lighting':
                for sub, pf in DEFAULT_LIGHTING_SUB_PREFIXES:
                    if not db.one('SELECT 1 FROM subcategory_prefixes WHERE category_id=? AND subcategory_value=?',
                                  (cat_id, sub)):
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
              placeholder, sort_order FROM category_fields WHERE category_id=? ORDER BY sort_order, id""", (category_id,))
        for r in rows:
            r['options_list'] = [o for o in (r['options'] or '').split('|') if o]
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
        self.db.execute('INSERT INTO category_sku_prefixes(category_id,prefix) VALUES(?,?) '
                        "ON CONFLICT(category_id) DO UPDATE SET prefix=excluded.prefix, updated_at=datetime('now')",
                        (category_id, prefix))
        return prefix

    def upsert_sub_prefix(self, category_id, value, prefix):
        self.require_category(category_id)
        value = (value or '').strip()
        if not value:
            raise ApiError('子类名称不能为空')
        prefix = _prefix(prefix)
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
