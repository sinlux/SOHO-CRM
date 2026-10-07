# -*- coding: utf-8 -*-
"""按 v4.4 的旧表结构造一个"真实使用过"的测试库（全部是假数据）。

DDL 逐字取自 v4.4 的 db.py：无外键约束（旧版没开 PRAGMA foreign_keys）、
notes/reminders/enrichments 无外键，price_history 无外键。
故意埋入旧版真实会产生的脏数据：英文/中文混写的 stage、已被删客户遗留的报价/备注/提醒。
"""
import os
import sqlite3

from imgutil import png_bytes

OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS customers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lv INTEGER, country TEXT DEFAULT '', name TEXT DEFAULT '', company TEXT DEFAULT '',
  website TEXT DEFAULT '', emails TEXT DEFAULT '', whatsapp TEXT DEFAULT '', linkedin TEXT DEFAULT '',
  facebook TEXT DEFAULT '', other_social TEXT DEFAULT '', main_business TEXT DEFAULT '',
  address TEXT DEFAULT '', company_size TEXT DEFAULT '', founded_year TEXT DEFAULT '',
  customer_type TEXT DEFAULT '', certifications TEXT DEFAULT '', ai_summary TEXT DEFAULT '',
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS notes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL,
  content TEXT DEFAULT '', image_path TEXT DEFAULT '', created_at TEXT);
CREATE TABLE IF NOT EXISTS enrichments(
  id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL,
  status TEXT DEFAULT 'pending', sources TEXT DEFAULT '[]', extracted TEXT DEFAULT '{}',
  error TEXT DEFAULT '', created_at TEXT);
CREATE TABLE IF NOT EXISTS reminders(
  id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL,
  content TEXT DEFAULT '', due_date TEXT, done INTEGER DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""

OLD_QM_SCHEMA = """
CREATE TABLE IF NOT EXISTS categories(
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  icon TEXT, sort_order INTEGER DEFAULT 0, is_builtin INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS category_fields(
  id INTEGER PRIMARY KEY AUTOINCREMENT, category_id INTEGER NOT NULL,
  field_key TEXT NOT NULL, field_label TEXT NOT NULL, field_type TEXT NOT NULL,
  is_required INTEGER DEFAULT 0, options TEXT, unit TEXT, placeholder TEXT,
  sort_order INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE,
  UNIQUE (category_id, field_key));
CREATE TABLE IF NOT EXISTS products(
  id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  category_id INTEGER NOT NULL, image_path TEXT, cost REAL, cost_currency TEXT DEFAULT 'USD',
  profit_rate REAL DEFAULT 0.25, suggested_price REAL, moq INTEGER, lead_time INTEGER,
  supplier TEXT, remark TEXT, created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (category_id) REFERENCES categories(id));
CREATE TABLE IF NOT EXISTS product_field_values(
  id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL, field_id INTEGER NOT NULL, value TEXT,
  FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
  FOREIGN KEY (field_id) REFERENCES category_fields(id) ON DELETE CASCADE,
  UNIQUE (product_id, field_id));
CREATE TABLE IF NOT EXISTS quotes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, quote_no TEXT NOT NULL UNIQUE, customer_id INTEGER NOT NULL,
  currency TEXT DEFAULT 'USD', status TEXT DEFAULT 'sent', valid_days INTEGER DEFAULT 30,
  lead_time TEXT DEFAULT '', payment_terms TEXT DEFAULT '', shipping_terms TEXT DEFAULT '',
  notes TEXT DEFAULT '', total REAL DEFAULT 0, customer_feedback TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS quote_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT, quote_id INTEGER NOT NULL, product_id INTEGER,
  sku TEXT DEFAULT '', name TEXT DEFAULT '', spec TEXT DEFAULT '', quantity REAL DEFAULT 1,
  unit TEXT DEFAULT 'pcs', unit_price REAL DEFAULT 0, remark TEXT DEFAULT '', sort_order INTEGER DEFAULT 0,
  FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS supplier_quotes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL, supplier_name TEXT NOT NULL,
  price_cny REAL, quote_date TEXT, screenshot_path TEXT, remark TEXT, is_adopted INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
"""

OLD_PRICE_HISTORY = """CREATE TABLE IF NOT EXISTS price_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL, price_type TEXT NOT NULL,
  price REAL NOT NULL, currency TEXT DEFAULT 'CNY', customer_id INTEGER, effective_date TEXT,
  source TEXT DEFAULT '', note TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')))"""

# 旧版真实混写过的阶段值（前端写中文，pi_import/quote_service 写英文 key）
STAGE_CYCLE = ['潜在', 'won', '已联系', 'quoted', '已报价', 'repeat', '已寄样', 'dormant', '成交', '', '复购', '沉睡',
               'potential', 'contacted', 'sampled']

TABLES = ['customers', 'notes', 'reminders', 'enrichments', 'settings', 'categories', 'category_fields',
          'products', 'product_field_values', 'quotes', 'quote_items', 'price_history', 'supplier_quotes',
          'app_settings']


def build_legacy_db(data_dir, n_customers=30, very_old=False):
    """在 data_dir/crm.db 建旧库并灌入假数据。very_old=True 模拟 v4.0 之前（无 stage / spec_text 列）。
    返回摘要 dict，供测试断言。"""
    os.makedirs(os.path.join(data_dir, 'images'), exist_ok=True)
    path = os.path.join(data_dir, 'crm.db')
    if os.path.exists(path):
        os.remove(path)
    c = sqlite3.connect(path)          # 默认不开外键，和旧版一致
    c.executescript(OLD_SCHEMA)
    c.executescript(OLD_QM_SCHEMA)
    if not very_old:
        c.execute("ALTER TABLE customers ADD COLUMN stage TEXT DEFAULT ''")
        c.execute("ALTER TABLE products ADD COLUMN spec_text TEXT DEFAULT ''")
        c.execute(OLD_PRICE_HISTORY)

    countries = ['Germany', 'USA', 'Vietnam', 'France', 'UAE']
    for i in range(1, n_customers + 1):
        cols = dict(lv=(i % 6) + 1 if i % 7 else None, country=countries[i % 5], name='Contact %d' % i,
                    company='Fake Company %d Ltd' % i, website='www.fake%d.com' % i,
                    emails='buyer%d@fake%d.com sales%d@fake%d.com' % (i, i, i, i),
                    whatsapp='+49 170 000%04d' % i, main_business='LED lighting %d' % i,
                    created_at='2026-01-%02d 10:00:00' % (i % 28 + 1), updated_at='2026-02-%02d 10:00:00' % (i % 28 + 1))
        if not very_old:
            cols['stage'] = STAGE_CYCLE[i % len(STAGE_CYCLE)]
        keys = list(cols)
        c.execute('INSERT INTO customers(%s) VALUES(%s)' % (','.join(keys), ','.join('?' * len(keys))),
                  [cols[k] for k in keys])
    # 备注（含一条带图）、提醒、背调
    for i in range(1, n_customers + 1):
        img = ''
        if i == 3:
            img = '3_1700000000000.png'
            with open(os.path.join(data_dir, 'images', img), 'wb') as f:
                f.write(b'\x89PNG\r\n\x1a\nfake')
        c.execute('INSERT INTO notes(customer_id,content,image_path,created_at) VALUES(?,?,?,?)',
                  (i, '备注 %d 含关键词 zebra%d' % (i, i), img, '2026-03-01 09:00:00'))
        c.execute('INSERT INTO reminders(customer_id,content,due_date,done,created_at) VALUES(?,?,?,?,?)',
                  (i, '再联系 %d' % i, '2026-04-%02d' % (i % 28 + 1), i % 3 == 0, '2026-03-01 09:00:00'))
    c.execute("INSERT INTO enrichments(customer_id,status,sources,extracted,created_at) VALUES(1,'applied','[]','{}','2026-03-02')")
    c.execute("INSERT INTO enrichments(customer_id,status,created_at) VALUES(2,'running','2026-03-02')")  # 上次崩溃遗留
    c.execute("INSERT INTO settings(key,value) VALUES('deepseek_key','sk-fake-legacy-key-1234'),('tavily_key','tvly-fake-9999')")
    # 孤儿数据：旧版删了客户 998/999，但没删报价/备注/提醒
    c.execute("INSERT INTO notes(customer_id,content,created_at) VALUES(998,'孤儿备注','2026-03-05')")
    c.execute("INSERT INTO reminders(customer_id,content,due_date,created_at) VALUES(998,'孤儿提醒','2026-05-01','2026-03-05')")

    # 产品库
    c.execute("INSERT INTO categories(code,name,icon,sort_order,is_builtin) VALUES('lighting','灯饰','💡',1,1),('furniture','家具','🪟',2,1)")
    c.execute("INSERT INTO category_fields(category_id,field_key,field_label,field_type,is_required) VALUES(1,'wattage','瓦数','number',1)")
    prods = [('SL-001', 'Downlight 10W', 1, 12.5, 'CNY'), ('SL-002', 'Track Light 20W', 1, 30.0, 'CNY'),
             ('SF-001', 'Oak Chair', 2, 55.0, 'USD')]
    for sku, name, cat, cost, cur in prods:
        if very_old:
            c.execute('INSERT INTO products(sku,name,category_id,cost,cost_currency,suggested_price) VALUES(?,?,?,?,?,?)',
                      (sku, name, cat, cost, cur, cost * 1.3))
        else:
            c.execute('INSERT INTO products(sku,name,category_id,cost,cost_currency,suggested_price,spec_text) VALUES(?,?,?,?,?,?,?)',
                      (sku, name, cat, cost, cur, cost * 1.3, '规格描述 ' + sku))
    os.makedirs(os.path.join(data_dir, 'uploads'), exist_ok=True)
    with open(os.path.join(data_dir, 'uploads', 'legacyimg1.png'), 'wb') as f:        # 旧版：原样保存的上传图
        f.write(png_bytes(300, 200, box=(100, 60, 200, 140)))
    c.execute("UPDATE products SET image_path='uploads/legacyimg1.png' WHERE id=1")
    c.execute("INSERT INTO product_field_values(product_id,field_id,value) VALUES(1,1,'10')")
    c.execute("INSERT INTO supplier_quotes(product_id,supplier_name,price_cny,is_adopted) VALUES(1,'供应商甲',12.5,1),(1,'供应商乙',13.0,0)")
    if not very_old:
        # 产品1、2 已有历史；产品3 没有 -> 迁移要补初始记录
        c.execute("INSERT INTO price_history(product_id,price_type,price,currency,customer_id,effective_date,source) VALUES"
                  "(1,'cost',12.5,'CNY',NULL,'2026-01-05','初始建档'),"
                  "(1,'sell',2.2,'USD',4,'2026-02-10','PI-001'),"
                  "(2,'cost',30,'CNY',NULL,'2026-01-06','初始建档')")
    # 报价单：客户 4 的 2 张 + 孤儿客户 999 的 1 张
    for no, cid, total in (('SLQ-20260301-001', 4, 220.0), ('SLQ-20260302-001', 4, 99.5), ('OLD-0001', 999, 500.0)):
        cur = c.execute("INSERT INTO quotes(quote_no,customer_id,status,total) VALUES(?,?,?,?)", (no, cid, 'accepted', total))
        c.execute("INSERT INTO quote_items(quote_id,product_id,sku,name,quantity,unit_price) VALUES(?,?,?,?,?,?)",
                  (cur.lastrowid, 1, 'SL-001', 'Downlight 10W', 10, total / 10))
    c.commit()
    summary = {t: c.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0] for t in TABLES if t != 'price_history' or not very_old}
    c.close()
    return summary
