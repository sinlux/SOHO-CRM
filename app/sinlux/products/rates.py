# -*- coding: utf-8 -*-
"""CNY→USD 汇率（存在 app_settings，键名与旧版一致）。"""
import json
import urllib.error
import urllib.request

from ..core.util import ApiError

DEFAULT_RATE = 0.1380
ONLINE_API = 'https://open.er-api.com/v6/latest/CNY'


def fetch_online(timeout=8):
    """返回 float。失败抛 Exception（调用方转成中文提示）。"""
    req = urllib.request.Request(ONLINE_API, headers={'User-Agent': 'SINLUX-CRM/5'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    if data.get('result') != 'success':
        raise RuntimeError('API 返回错误：%s' % data.get('error-type', 'unknown'))
    usd = (data.get('rates') or {}).get('USD')
    if not usd:
        raise RuntimeError('API 响应缺少 USD 字段')
    return float(usd)


class RateService:
    def __init__(self, db, fetcher=None):
        self.db = db
        self.fetcher = fetcher or fetch_online

    def _get(self, key):
        r = self.db.one('SELECT value, updated_at FROM app_settings WHERE key=?', (key,))
        return r

    def _set(self, key, value):
        self.db.execute('INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,datetime(\'now\',\'localtime\')) '
                        'ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at',
                        (key, str(value)))

    def info(self):
        r = self._get('cny_usd_rate')
        try:
            rate = float(r['value']) if r else DEFAULT_RATE
        except (TypeError, ValueError):
            rate = DEFAULT_RATE
        if not 0 < rate <= 10:
            rate = DEFAULT_RATE
        src = self._get('cny_usd_rate_source')
        return {'rate': rate, 'updated_at': r['updated_at'] if r else None, 'source': src['value'] if src else 'default'}

    def rate(self):
        return self.info()['rate']

    @staticmethod
    def _check(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ApiError('汇率必须是数字')
        if not 0 < v <= 10:
            raise ApiError('汇率取值范围 (0, 10]，例如 1 CNY = 0.138 USD 应填 0.138')
        return v

    def set_manual(self, v):
        v = self._check(v)
        with self.db.tx():
            self._set('cny_usd_rate', v)
            self._set('cny_usd_rate_source', 'manual')
        return self.info()

    def fetch(self):
        try:
            v = self._check(self.fetcher())
        except ApiError as e:
            raise ApiError('在线汇率异常：%s' % e.message, 502)
        except Exception as e:
            raise ApiError('在线获取汇率失败：%s（可手动填写）' % e, 502)
        with self.db.tx():
            self._set('cny_usd_rate', v)
            self._set('cny_usd_rate_source', 'online')
        return self.info()
