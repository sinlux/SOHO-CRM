# -*- coding: utf-8 -*-
"""全库表结构。

与 v4.4 的 crm.db 保持列级兼容（同名同列），所以旧库可以被新版直接打开。
和旧版的差别只有一处：notes / reminders / enrichments 现在带外键（级联删除），
旧库里这三张表在 migrations.py 里重建。
产品、报价相关的表本批只负责"建出来"，业务逻辑在后续批次实现。
"""

# 第二批之后给 products 追加的列（旧库迁移时按此补）。(列名, 类型定义)
PRODUCT_NEW_COLUMNS = [
    ('status', "TEXT DEFAULT 'active'"), ('unit', "TEXT DEFAULT 'pcs'"), ('brand', "TEXT DEFAULT ''"),
    ('series', "TEXT DEFAULT ''"), ('hs_code', "TEXT DEFAULT ''"), ('origin', "TEXT DEFAULT ''"),
    ('pcs_per_carton', 'INTEGER'), ('carton_l', 'REAL'), ('carton_w', 'REAL'), ('carton_h', 'REAL'),
    ('gross_weight', 'REAL'), ('net_weight', 'REAL'),
]

# 第五批之后追加的列（旧库迁移时按此补）：(表, 列名, 类型定义)
LATER_COLUMNS = [
    ('category_fields', 'applies_to', "TEXT DEFAULT ''"),           # 该字段适用的子类（| 分隔）；空 = 所有子类都显示
    ('supplier_quotes', 'supplier_id', 'INTEGER REFERENCES suppliers(id) ON DELETE SET NULL'),
    ('supplier_quotes', 'project', "TEXT DEFAULT ''"),               # 询价项目（自由文字）：一个项目问几十家，最后选一家
]

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
  status TEXT DEFAULT 'active',
  unit TEXT DEFAULT 'pcs',
  brand TEXT DEFAULT '',
  series TEXT DEFAULT '',
  hs_code TEXT DEFAULT '',
  origin TEXT DEFAULT '',
  pcs_per_carton INTEGER,
  carton_l REAL, carton_w REAL, carton_h REAL,
  gross_weight REAL, net_weight REAL,
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
CREATE TABLE IF NOT EXISTS suppliers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  contact TEXT DEFAULT '', phone TEXT DEFAULT '', wechat TEXT DEFAULT '', email TEXT DEFAULT '',
  platform TEXT DEFAULT '', link TEXT DEFAULT '', location TEXT DEFAULT '',
  main_products TEXT DEFAULT '', status TEXT DEFAULT 'inquiring', rating INTEGER,
  notes TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS supplier_chats(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  supplier_id INTEGER NOT NULL,
  project TEXT DEFAULT '', title TEXT DEFAULT '', note TEXT DEFAULT '',
  image_file TEXT DEFAULT '', thumb_file TEXT DEFAULT '',
  ocr_text TEXT DEFAULT '', ocr_status TEXT DEFAULT '', ocr_error TEXT DEFAULT '',
  chat_date TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (supplier_id) REFERENCES suppliers(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_supplier_chats_supplier ON supplier_chats(supplier_id);
CREATE TABLE IF NOT EXISTS category_sku_prefixes(
  category_id INTEGER PRIMARY KEY,
  prefix TEXT NOT NULL,
  updated_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS subcategory_prefixes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category_id INTEGER NOT NULL,
  subcategory_value TEXT NOT NULL,
  prefix TEXT NOT NULL,
  updated_at TEXT DEFAULT (datetime('now')),
  UNIQUE(category_id, subcategory_value),
  FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS product_images(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER,                      -- NULL = 刚上传、尚未保存到任何产品的暂存图
  file TEXT NOT NULL,
  thumb TEXT DEFAULT '',
  width INTEGER, height INTEGER,
  normalized INTEGER DEFAULT 0,
  low_res INTEGER DEFAULT 0,
  sort_order INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now','localtime')),
  FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS product_files(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  kind TEXT DEFAULT '其他',
  file TEXT NOT NULL,
  size INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now','localtime')),
  FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS rate_history(
  day TEXT PRIMARY KEY,
  buy_spot REAL NOT NULL,
  rate REAL NOT NULL,
  published_at TEXT,
  fetched_at TEXT,
  source TEXT DEFAULT 'boc');
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
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_product_images_product ON product_images(product_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_product_files_product ON product_files(product_id);
CREATE INDEX IF NOT EXISTS idx_price_history_product ON price_history(product_id, price_type, effective_date);
CREATE INDEX IF NOT EXISTS idx_quote_items_product ON quote_items(product_id);
"""
