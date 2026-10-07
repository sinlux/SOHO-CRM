# -*- coding: utf-8 -*-
"""CNY→USD 汇率。

默认模式 boc：自动抓取中国银行外汇牌价的「美元·现汇买入价」（人民币/100美元），
折算 rate = 100 / 现汇买入价，每天（含当天多次发布，取最新）写入 rate_history，并成为当前汇率。
手动模式 manual：用户自己填，自动更新暂停，直到点"恢复自动"。

存储：app_settings（沿用旧键 cny_usd_rate / cny_usd_rate_source，其余为本版新增）+ rate_history 表。
抓取失败绝不覆盖现有汇率，只记录错误并在界面显示。
"""
import html as htmllib
import re
import threading
import time
import urllib.parse
import urllib.request

from ..core.util import ApiError, now

DEFAULT_RATE = 0.1380
MODES = ('boc', 'manual')
# 现汇买入价（人民币/100美元）的合理范围，超出视为抓错了列/页面改版，拒绝入库
BUY_MIN, BUY_MAX = 300.0, 1500.0

BOC_SEARCH = 'https://srh.bankofchina.com/search/whpj/search_cn.jsp'
BOC_PAGES = ['https://www.boc.cn/sourcedb/whpj/'] + ['https://www.boc.cn/sourcedb/whpj/index_%d.html' % i for i in (1, 2, 3)]
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'

_ROW = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S | re.I)
_CELL = re.compile(r'<t[dh][^>]*>(.*?)</t[dh]>', re.S | re.I)
_TAG = re.compile(r'<[^>]+>')
_TIME = re.compile(r'(\d{4})[.\-/](\d{2})[.\-/](\d{2})(?:\s+(\d{2}:\d{2}(?::\d{2})?))?')
_NUM = re.compile(r'^\d+(?:\.\d+)?$')


def parse_boc_usd(page):
    """从中国银行牌价页 HTML 里取出「美元」行。返回 {'buy_spot', 'published'} 或 None。

    不依赖固定列号以外的结构：找第一个单元格含"美元"、后面至少 5 个数字的行；
    列顺序为 货币名称 | 现汇买入价 | 现钞买入价 | 现汇卖出价 | 现钞卖出价 | 中行折算价 | 发布时间。
    先按表头定位"现汇买入价"所在列，找不到表头时退回第 2 列。"""
    col = 1
    for row in _ROW.findall(page or ''):
        cells = [htmllib.unescape(_TAG.sub('', c)).strip() for c in _CELL.findall(row)]
        if cells and '现汇买入价' in cells:
            col = cells.index('现汇买入价')
            continue
        if not cells or '美元' not in cells[0] or '日元' in cells[0]:
            continue
        nums = [c for c in cells[1:] if _NUM.match(c)]
        if len(nums) < 5 or col >= len(cells) or not _NUM.match(cells[col]):
            continue
        tm = None
        for c in reversed(cells):
            m = _TIME.search(c)
            if m:
                tm = '%s-%s-%s %s' % (m.group(1), m.group(2), m.group(3), m.group(4) or '00:00:00')
                if len(tm) == 16:
                    tm += ':00'
                break
        return {'buy_spot': float(cells[col]), 'published': tm}
    return None


def _http(url, data=None, timeout=15):
    req = urllib.request.Request(url, data=data, headers={'User-Agent': UA, 'Accept-Language': 'zh-CN,zh;q=0.9'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', errors='ignore')


def fetch_boc_usd(http=None):
    """抓取中国银行美元现汇买入价。依次尝试：牌价查询接口(按美元)、牌价首页及后续分页。
    http(url, data=None) 可注入，便于测试。失败抛 RuntimeError（附各来源的失败原因）。"""
    http = http or _http
    errors = []
    today = time.strftime('%Y-%m-%d')
    body = urllib.parse.urlencode({'erectDate': today, 'nothing': today, 'pjname': '美元'}).encode('utf-8')
    try:
        got = parse_boc_usd(http(BOC_SEARCH, body))
        if got:
            return got
        errors.append('查询接口没有返回美元行')
    except Exception as e:
        errors.append('查询接口: %s' % e)
    for url in BOC_PAGES:
        try:
            got = parse_boc_usd(http(url))
            if got:
                return got
            errors.append('%s 没有美元行' % url)
        except Exception as e:
            errors.append('%s: %s' % (url, e))
    raise RuntimeError('无法从中国银行页面取得美元现汇买入价（页面格式可能已变化，或网络不通）。' + '；'.join(errors[:3]))


class RateService:
    def __init__(self, db, fetcher=None):
        self.db = db
        self.fetcher = fetcher or fetch_boc_usd
        self._lock = threading.Lock()      # 防止定时线程和手动"立即更新"同时抓取

    # ---------- app_settings 读写 ----------
    def _get(self, key, default=None):
        r = self.db.one('SELECT value FROM app_settings WHERE key=?', (key,))
        return r['value'] if r and r['value'] is not None else default

    def _set(self, key, value):
        self.db.execute("INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,datetime('now','localtime')) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                        (key, str(value)))

    def mode(self):
        m = self._get('rate_mode', 'boc')
        return m if m in MODES else 'boc'

    def info(self):
        try:
            rate = float(self._get('cny_usd_rate', DEFAULT_RATE))
        except (TypeError, ValueError):
            rate = DEFAULT_RATE
        if not 0 < rate <= 10:
            rate = DEFAULT_RATE
        row = self.db.one("SELECT updated_at FROM app_settings WHERE key='cny_usd_rate'")
        buy = self._get('rate_boc_buy')
        return {'rate': rate, 'updated_at': row['updated_at'] if row else None,
                'source': self._get('cny_usd_rate_source', 'default'), 'mode': self.mode(),
                'boc_buy': float(buy) if buy else None, 'boc_published': self._get('rate_boc_published'),
                'last_attempt': self._get('rate_last_attempt'), 'last_success': self._get('rate_last_success'),
                'last_error': self._get('rate_last_error') or ''}

    def rate(self):
        return self.info()['rate']

    # ---------- 手动 ----------
    @staticmethod
    def _check_rate(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ApiError('汇率必须是数字')
        if not 0 < v <= 10:
            raise ApiError('汇率取值范围 (0, 10]，例如 1 CNY = 0.14 USD 应填 0.14')
        return v

    def set_manual(self, v):
        """直接填 CNY→USD 汇率（如 0.14）。切换为手动模式。"""
        v = self._check_rate(v)
        with self.db.tx():
            self._set('cny_usd_rate', v)
            self._set('cny_usd_rate_source', 'manual')
            self._set('rate_mode', 'manual')
        return self.info()

    def set_manual_boc(self, buy_spot):
        """填中国银行牌价页上看到的「现汇买入价」（人民币/100美元），系统折算。切换为手动模式。"""
        try:
            buy = float(buy_spot)
        except (TypeError, ValueError):
            raise ApiError('现汇买入价必须是数字（人民币/100美元，如 712.34）')
        if not BUY_MIN <= buy <= BUY_MAX:
            raise ApiError('现汇买入价应在 %d–%d（人民币/100美元）之间，请核对' % (BUY_MIN, BUY_MAX))
        with self.db.tx():
            self._set('cny_usd_rate', round(100.0 / buy, 6))
            self._set('cny_usd_rate_source', 'manual')
            self._set('rate_boc_buy', buy)
            self._set('rate_mode', 'manual')
        return self.info()

    def set_mode(self, mode):
        if mode not in MODES:
            raise ApiError('汇率模式必须是 boc（自动，中国银行）或 manual（手动）')
        self._set('rate_mode', mode)
        return self.info()

    # ---------- 自动：中国银行 ----------
    def refresh(self, force=True):
        """抓取并入库。force=False 时仅在 boc 模式下执行（定时线程用）。
        成功：写 rate_history（同一发布日期只保留最新一条），boc 模式下更新当前汇率。
        失败：不动现有汇率，记录错误；抛 ApiError(502)。"""
        if not force and self.mode() != 'boc':
            return None
        with self._lock:
            self._set('rate_last_attempt', now())
            try:
                got = self.fetcher()
                buy = float(got['buy_spot'])
                if not BUY_MIN <= buy <= BUY_MAX:
                    raise ValueError('抓到的现汇买入价 %s 不在合理范围，已拒绝' % buy)
                published = got.get('published') or now()
            except Exception as e:
                msg = str(e) or e.__class__.__name__
                self._set('rate_last_error', msg)
                raise ApiError('更新中国银行汇率失败：%s（当前汇率保持不变，可稍后重试或手动填写）' % msg, 502)
            rate = round(100.0 / buy, 6)
            day = published[:10]
            with self.db.tx():
                self.db.execute("""INSERT INTO rate_history(day, buy_spot, rate, published_at, fetched_at, source)
                    VALUES(?,?,?,?,?,'boc') ON CONFLICT(day) DO UPDATE SET buy_spot=excluded.buy_spot,
                    rate=excluded.rate, published_at=excluded.published_at, fetched_at=excluded.fetched_at
                    WHERE excluded.published_at >= rate_history.published_at""",
                                (day, buy, rate, published, now()))
                self._set('rate_last_success', now())
                self._set('rate_last_error', '')
                if self.mode() == 'boc':
                    self._set('cny_usd_rate', rate)
                    self._set('cny_usd_rate_source', 'boc')
                    self._set('rate_boc_buy', buy)
                    self._set('rate_boc_published', published)
            return self.info()

    def history(self, limit=60):
        return self.db.query('SELECT day, buy_spot, rate, published_at, fetched_at, source FROM rate_history '
                             'ORDER BY day DESC LIMIT ?', (int(limit),))


class RateScheduler(threading.Thread):
    """后台线程：启动后稍等即抓一次，之后每隔 interval 秒再抓（BOC 当天多次发布，取最新）。
    失败后 retry 秒再试。仅 boc 模式生效；程序退出即停。"""

    def __init__(self, rates, first_delay=15, interval=2 * 3600, retry=1800):
        super().__init__(daemon=True, name='rate-scheduler')
        self.rates, self.first_delay, self.interval, self.retry = rates, first_delay, interval, retry
        self._stop_evt = threading.Event()

    def run(self):
        wait = self.first_delay
        while not self._stop_evt.wait(wait):
            try:
                self.rates.refresh(force=False)
                wait = self.interval
            except Exception:
                wait = self.retry

    def stop(self):
        self._stop_evt.set()
