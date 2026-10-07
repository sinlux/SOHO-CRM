# -*- coding: utf-8 -*-
"""应用装配：数据目录、数据库、服务、路由。测试和 main.py 都从这里创建应用。"""
import os

from .core import db as dbmod, migrations
from .core.updater import Updater
from .dashboard import Dashboard
from .core.http import Router, make_server
from .customers.service import CustomerService
from .customers.xlsx_io import CustomerImporter
from .customers.enrich import Enricher
from .products import catalog as catalog_mod
from .products.catalog import CatalogService
from .products.pricing import PriceHistory
from .products.media import MediaStore
from .products.rates import RateService, RateScheduler
from .products.service import ProductService, SupplierService
from .imports import routes as import_routes
from .imports.pi_import import PiImporter
from .imports.product_import import ProductImporter
from .quotes.exporter import QuoteExporter
from .quotes.service import QuoteService
from .quotes.settings import QuoteSettings

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
STATIC_DIR = os.path.join(APP_DIR, 'static')


def default_data_dir():
    return os.environ.get('SINLUX_DATA_DIR') or os.path.join(os.path.dirname(APP_DIR), 'data')


def read_version():
    try:
        with open(os.path.join(APP_DIR, 'version.txt'), encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return 'unknown'


class Context:
    """路由处理函数拿到的共享上下文。"""

    def __init__(self, data_dir, net=None, rate_fetcher=None, start_scheduler=False):
        self.data_dir = os.path.abspath(data_dir)
        self.images_dir = os.path.join(self.data_dir, 'images')
        self.uploads_dir = os.path.join(self.data_dir, 'uploads')
        self.exports_dir = os.path.join(self.data_dir, 'exports')
        self.backups_dir = os.path.join(self.data_dir, 'backups')
        self.import_tmp = os.path.join(self.data_dir, 'import_tmp')
        self.files_dir = os.path.join(self.data_dir, 'product_files')
        for d in (self.images_dir, self.uploads_dir, self.exports_dir):
            os.makedirs(d, exist_ok=True)
        self.db = dbmod.Database(os.path.join(self.data_dir, 'crm.db'))
        self.migration_report = migrations.migrate(self.db)
        catalog_mod.ensure_seeds(self.db)
        self.catalog = CatalogService(self.db)
        self.rates = RateService(self.db, rate_fetcher)
        self.history = PriceHistory(self.db, self.rates)
        self.media = MediaStore(self.db, self.uploads_dir, self.files_dir)
        self.media.cleanup_staged()
        self.products = ProductService(self.db, self.rates, self.history, self.catalog, self.media)
        self.suppliers = SupplierService(self.db, self.uploads_dir, self.products, self.history)
        self.customers = CustomerService(self.db, self.images_dir)
        self.quotes = QuoteService(self.db, self.customers, self.history)
        self.quote_settings = QuoteSettings(self.db)
        self.quote_export = QuoteExporter(self.db, self.quote_settings, self.exports_dir, self.uploads_dir)
        self.importer = CustomerImporter(self.db, self.customers, os.path.join(self.import_tmp, 'customers'))
        self.importer.cleanup_old()
        self.product_import = ProductImporter(self.db, self.products, self.catalog, self.media, os.path.join(self.import_tmp, 'products'))
        self.pi_import = PiImporter(self.db, self.products, self.quotes, self.customers, self.history, self.catalog, os.path.join(self.import_tmp, 'pi'))
        self.enricher = Enricher(self.db, self.customers, net)
        self.dashboard = Dashboard(self.db, self.rates)
        self.version = read_version()
        self.updater = Updater(os.path.dirname(APP_DIR), self.data_dir, self.version)
        self.scheduler = None
        if start_scheduler:                       # 每日自动更新中国银行汇率（仅 main.py 启动；测试里不开）
            self.scheduler = RateScheduler(self.rates)
            self.scheduler.start()

    def close(self):
        if self.scheduler:
            self.scheduler.stop()
        self.db.close()


def build_router():
    from .customers import routes as customer_routes
    from .products import routes as product_routes
    from .quotes import routes as quote_routes
    from .core import routes as core_routes
    r = Router()
    core_routes.register(r)
    customer_routes.register(r)
    product_routes.register(r)
    quote_routes.register(r)
    import_routes.register(r)
    return r


def create_app(data_dir=None, port=8123, net=None, rate_fetcher=None, start_scheduler=False):
    ctx = Context(data_dir or default_data_dir(), net, rate_fetcher, start_scheduler)
    server = make_server(ctx, build_router(), STATIC_DIR, port)
    return ctx, server
