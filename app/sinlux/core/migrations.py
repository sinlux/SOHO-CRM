# -*- coding: utf-8 -*-
"""启动时的增量迁移。每一步都幂等，可重复运行；对 v4.4 及更早的旧库同样有效。

原则：只补、只规范，不丢用户数据。需要"处理"的脏数据（孤儿记录）会被保全并写进报告。
"""
from . import schema
from .util import now

ORPHAN_COMPANY = '【已删除客户 #%d】'


def migrate(db):
    """返回 report: {'actions': [...], 'orphans_adopted': n, 'stages_normalized': n}"""
    report = {'actions': [], 'orphans_adopted': 0, 'stages_normalized': 0}

    def did(msg):
        report['actions'].append(msg)

    # 1. 基础表（IF NOT EXISTS，旧表不动）
    db.executescript(schema.SCHEMA)

    # 2. 旧库缺的列（v4.0 之前的库没有 stage / spec_text）
    if not db.column_exists('customers', 'stage'):
        db.execute("ALTER TABLE customers ADD COLUMN stage TEXT DEFAULT ''")
        did('customers 补列 stage')
    if not db.column_exists('products', 'spec_text'):
        db.execute("ALTER TABLE products ADD COLUMN spec_text TEXT DEFAULT ''")
        did('products 补列 spec_text')

    for col, ddl in schema.PRODUCT_NEW_COLUMNS:
        if not db.column_exists('products', col):
            db.execute('ALTER TABLE products ADD COLUMN %s %s' % (col, ddl))
            did('products 补列 %s' % col)

    for table, col, ddl in schema.LATER_COLUMNS:
        if db.table_exists(table) and not db.column_exists(table, col):
            db.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, col, ddl))
            did('%s 补列 %s' % (table, col))

    # 2b. 供应商档案：把旧版散落在报价 / 产品里的供应商名字收拢成档案（只补不改）
    n = _adopt_suppliers(db)
    if n:
        did('供应商档案：从历史报价/产品里整理出 %d 家' % n)

    # 3. 客户子表：新库直接建；旧库（无外键）保全孤儿后重建
    _ensure_child_tables(db, report, did)

    for table, col, ddl in schema.LATER_COLUMNS:                 # 子表（enrichments 等）此时才确定存在，再补一遍
        if db.table_exists(table) and not db.column_exists(table, col):
            db.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, col, ddl))
            did('%s 补列 %s' % (table, col))

    # 4. 阶段值统一成中文（旧版混写过 won/repeat/quoted 等英文 key）
    n = 0
    for key, label in schema.STAGE_KEY_TO_LABEL.items():
        n += db.execute('UPDATE customers SET stage=? WHERE stage=?', (label, key)).rowcount
    n += db.execute("UPDATE customers SET stage='' WHERE stage IS NULL").rowcount
    if n:
        did('阶段值规范化 %d 条' % n)
    report['stages_normalized'] = n

    # 5. 旧版遗留：结构化规格字段一律选填
    db.execute('UPDATE category_fields SET is_required=0 WHERE is_required<>0')

    # 6. 已有成本价但无价格历史的产品，补一条初始记录（v4.1 逻辑）
    rows = db.query("""SELECT p.id, p.cost, p.cost_currency, p.created_at FROM products p
        WHERE p.cost IS NOT NULL AND p.id NOT IN
          (SELECT DISTINCT product_id FROM price_history WHERE price_type='cost')""")
    for r in rows:
        db.execute("""INSERT INTO price_history(product_id,price_type,price,currency,effective_date,source)
            VALUES(?,?,?,?,?,?)""", (r['id'], 'cost', r['cost'], r['cost_currency'] or 'CNY',
                                     (r['created_at'] or now())[:10], '初始建档'))
    if rows:
        did('补初始成本历史 %d 条' % len(rows))

    # 6b. 旧版每个产品只有 products.image_path 一张图：补成相册第一张（文件原样保留，标记未规范化）
    legacy = db.query("""SELECT id, image_path FROM products WHERE COALESCE(image_path,'')<>''
        AND id NOT IN (SELECT product_id FROM product_images)""")
    for r in legacy:
        db.execute('INSERT INTO product_images(product_id,file,sort_order,normalized) VALUES(?,?,0,0)',
                   (r['id'], r['image_path'].split('/')[-1]))
    if legacy:
        did('产品旧图并入相册 %d 张' % len(legacy))

    # 7. 索引（放在补列之后）
    db.executescript(schema.INDEXES)

    # 8. 上次异常退出时停在"进行中"的背调，标记为失败（否则界面上永远转圈）
    n = db.execute("UPDATE enrichments SET status='failed', error='程序中断，请重新背调' "
                   "WHERE status='running'").rowcount
    if n:
        did('清理中断的背调 %d 条' % n)
    return report


def _ensure_child_tables(db, report, did):
    todo = [t for t in schema.CUSTOMER_CHILD_TABLES
            if not db.table_exists(t) or not db.has_fk(t, 'customers')]
    if not todo:
        return
    # 旧库：先给孤儿记录（客户已被删但子记录还在）建占位客户，保留原 id，数据不丢
    existing_tables = [t for t in todo if db.table_exists(t)]
    adopt_sources = existing_tables + ([] if not db.table_exists('quotes') else ['quotes'])
    orphan_ids = set()
    for t in adopt_sources:
        for r in db.query('SELECT DISTINCT customer_id FROM %s WHERE customer_id IS NOT NULL '
                          'AND customer_id NOT IN (SELECT id FROM customers)' % t):
            orphan_ids.add(r['customer_id'])
    with db.tx():
        for cid in sorted(orphan_ids):
            ts = now()
            db.execute('INSERT INTO customers(id,lv,company,stage,main_business,created_at,updated_at) '
                       'VALUES(?,?,?,?,?,?,?)',
                       (cid, None, ORPHAN_COMPANY % cid, '',
                        '迁移时发现：该客户已被旧版删除，但名下仍有备注/提醒/报价，已保留供核查。',
                        ts, ts))
        report['orphans_adopted'] = len(orphan_ids)
    if orphan_ids:
        did('为 %d 个已删除客户的遗留数据建了占位客户' % len(orphan_ids))

    # 重建带外键的表（SQLite 无法给已有表加外键）
    db.set_foreign_keys(False)
    try:
        with db.tx():
            for t in todo:
                ddl = schema.CUSTOMER_CHILD_TABLES[t]
                if db.table_exists(t):
                    db.execute('ALTER TABLE %s RENAME TO %s_old' % (t, t))
                    db.execute(ddl.format(name=t))
                    cols = [r['name'] for r in db.query('PRAGMA table_info(%s)' % t)]
                    old_cols = {r['name'] for r in db.query('PRAGMA table_info(%s_old)' % t)}
                    use = [c for c in cols if c in old_cols]
                    db.execute('INSERT INTO %s(%s) SELECT %s FROM %s_old'
                               % (t, ','.join(use), ','.join(use), t))
                    db.execute('DROP TABLE %s_old' % t)
                    did('%s 重建为带外键的表' % t)
                else:
                    db.execute(ddl.format(name=t))
                    did('%s 新建' % t)
            for t in todo:  # 只检查本次重建的表，别的表的历史问题不应阻止启动
                bad = db.query('PRAGMA foreign_key_check(%s)' % t)
                if bad:
                    raise RuntimeError('迁移后外键检查失败(%s): %s' % (t, bad[:3]))
    finally:
        db.set_foreign_keys(True)


def _adopt_suppliers(db):
    """旧数据里供应商只是文字：为每个不同的名字建一份档案，并把比价记录挂上去。重复运行不会重复建。"""
    made = 0
    names = [r['n'] for r in db.query("""SELECT TRIM(supplier_name) AS n FROM supplier_quotes WHERE TRIM(COALESCE(supplier_name,''))<>''
        UNION SELECT TRIM(supplier) FROM products WHERE TRIM(COALESCE(supplier,''))<>''""")]
    for name in names:
        if not db.one('SELECT 1 FROM suppliers WHERE name=?', (name,)):
            db.execute("INSERT INTO suppliers(name, status) VALUES(?, 'candidate')", (name,))
            made += 1
    db.execute("""UPDATE supplier_quotes SET supplier_id=(SELECT id FROM suppliers s WHERE s.name=TRIM(supplier_quotes.supplier_name))
        WHERE supplier_id IS NULL AND TRIM(COALESCE(supplier_name,''))<>''""")
    return made
