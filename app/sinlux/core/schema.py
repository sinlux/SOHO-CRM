# -*- coding: utf-8 -*-
"""全库表结构。

与 v4.4 的 crm.db 保持列级兼容（同名同列），所以旧库可以被新版直接打开。
和旧版的差别只有一处：notes / reminders / enrichments 现在带外键（级联删除），
旧库里这三张表在 migrations.py 里重建。
产品、报价相关的表本批只负责"建出来"，业务逻辑在后续批次实现。
"""

STAGES = ['潜在', '已联系', '已报价', '已寄样', '成交', '复购', '沉睡']

# 旧版里 pi_import / quote_service 曾把英文 key 写进 stage 列，迁移时统一成中文
STAGE_KEY_TO_LABEL = {
    'potential': '潜在', 'contacted': '已联系', 'quoted': '已报价',
    'sampled': '已寄样', 'won': '成交', 'repeat': '复购', 'dormant': '沉睡',
}

CUSTOMER_FIELDS = [
    'lv', 'stage', 'country', 'name', 'company', 'website', 'emails', 'whatsapp',
    'linkedin', 'facebook', 'other_social', 'main_business', 'address',
    'company_size', 'founded_year', 'customer_type', 'certifications', 'ai_summary',
]

# 客户三张子表（带外键）。迁移时也用这份定义重建旧表。
CUSTOMER_CHILD_TABLES = {
    'notes': """CREATE TABLE {name}(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER NOT NULL,
  content TEXT DEFAULT '',
  image_path TEXT DEFAULT '',
  created_at TEXT,
  FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE)""",
    'reminders': """CREATE TABLE {name}(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER NOT NULL,
  content TEXT DEFAULT '',
  due_date TEXT,
  done INTEGER DEFAULT 0,
  created_at TEXT,
  FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE)""",
    'enrichments': """CREATE TABLE {name}(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER NOT NULL,
  status TEXT DEFAULT 'pending',
  sources TEXT DEFAULT '[]',
  extracted TEXT DEFAULT '{{}}',
  error TEXT DEFAULT '',
  created_at TEXT,
  FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE)""",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lv INTEGER,
  stage TEXT DEFAULT '',
  country TEXT DEFAULT '',
  name TEXT DEFAULT '',
  company TEXT DEFAULT '',
  website TEXT DEFAULT '',
  emails TEXT DEFAULT '',
  whatsapp TEXT DEFAULT '',
  linkedin TEXT DEFAULT '',
  facebook TEXT DEFAULT '',
  other_social TEXT DEFAULT '',
  main_business TEXT DEFAULT '',
  address TEXT DEFAULT '',
  company_size TEXT DEFAULT '',
  founded_year TEXT DEFAULT '',
  customer_type TEXT DEFAULT '',
  certifications TEXT DEFAULT '',
  ai_summary TEXT DEFAULT '',
  created_at TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS categories(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  icon TEXT, sort_order INTEGER DEFAULT 0,
  is_builtin INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS category_fields(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category_id INTEGER NOT NULL,
  field_key TEXT NOT NULL, field_label TEXT NOT NULL,
  field_type TEXT NOT NULL, is_required INTEGER DEFAULT 0,
  options TEXT, unit TEXT, placeholder TEXT,
  sort_order INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE,
  UNIQUE (category_id, field_key));
CREATE TABLE IF NOT EXISTS products(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sku TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  category_id INTEGER NOT NULL, image_path TEXT,
  cost REAL, cost_currency TEXT DEFAULT 'USD',
  profit_rate REAL DEFAULT 0.25, suggested_price REAL,
  moq INTEGER, lead_time INTEGER, supplier TEXT, remark TEXT,
  spec_text TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (category_id) REFERENCES categories(id));
CREATE TABLE IF NOT EXISTS product_field_values(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL, field_id INTEGER NOT NULL, value TEXT,
  FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
  FOREIGN KEY (field_id) REFERENCES category_fields(id) ON DELETE CASCADE,
  UNIQUE (product_id, field_id));
CREATE TABLE IF NOT EXISTS quotes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  quote_no TEXT NOT NULL UNIQUE,
  customer_id INTEGER NOT NULL,
  currency TEXT DEFAULT 'USD',
  status TEXT DEFAULT 'sent',
  valid_days INTEGER DEFAULT 30,
  lead_time TEXT DEFAULT '',
  payment_terms TEXT DEFAULT '',
  shipping_terms TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  total REAL DEFAULT 0,
  customer_feedback TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS quote_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  quote_id INTEGER NOT NULL,
  product_id INTEGER,
  sku TEXT DEFAULT '', name TEXT DEFAULT '',
  spec TEXT DEFAULT '',
  quantity REAL DEFAULT 1,
  unit TEXT DEFAULT 'pcs',
  unit_price REAL DEFAULT 0,
  remark TEXT DEFAULT '',
  sort_order INTEGER DEFAULT 0,
  FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS price_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL,
  price_type TEXT NOT NULL,
  price REAL NOT NULL,
  currency TEXT DEFAULT 'CNY',
  customer_id INTEGER,
  effective_date TEXT,
  source TEXT DEFAULT '',
  note TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
  FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS supplier_quotes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL,
  supplier_name TEXT NOT NULL,
  price_cny REAL,
  quote_date TEXT,
  screenshot_path TEXT,
  remark TEXT,
  is_adopted INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_supplier_quotes_product ON supplier_quotes(product_id);
CREATE TABLE IF NOT EXISTS app_settings(
  key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
"""

# 子表和常用查询列的索引（旧库没有）
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_notes_customer ON notes(customer_id);
CREATE INDEX IF NOT EXISTS idx_reminders_customer ON reminders(customer_id);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(done, due_date);
CREATE INDEX IF NOT EXISTS idx_enrichments_customer ON enrichments(customer_id);
CREATE INDEX IF NOT EXISTS idx_customers_lv ON customers(lv);
CREATE INDEX IF NOT EXISTS idx_quotes_customer ON quotes(customer_id);
"""
