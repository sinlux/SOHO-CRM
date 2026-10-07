# -*- coding: utf-8 -*-
"""汇率：中国银行「美元·现汇买入价」解析、自动更新、每日历史、手动覆盖、失败保护、定时线程。"""
import time
import unittest

from helpers import AppTestCase
from sinlux.products import rates as R

HEAD = '<tr class="odd"><th>货币名称</th><th>现汇买入价</th><th>现钞买入价</th><th>现汇卖出价</th><th>现钞卖出价</th><th>中行折算价</th><th>发布时间</th></tr>'
ROW_JPY = '<tr><td>日元</td><td>4.7512</td><td>4.6034</td><td>4.7852</td><td>4.7852</td><td>4.7601</td><td>2026.10.07 10:30:21</td></tr>'
ROW_USD = '<tr class="odd"><td>美元</td><td>712.34</td><td>706.52</td><td>715.30</td><td>715.30</td><td>713.43</td><td>2026.10.07 10:30:21</td></tr>'
class _Page:
    def __mod__(self, body):
        return '<div class="BOC_main"><table cellpadding="0" cellspacing="0" width="100%">' + body + '</table></div>'


PAGE = _Page()


class TestParse(unittest.TestCase):
    def test_standard_page(self):
        got = R.parse_boc_usd(PAGE % (HEAD + ROW_JPY + ROW_USD))
        self.assertEqual(got, {'buy_spot': 712.34, 'published': '2026-10-07 10:30:21'})

    def test_uses_spot_buy_column_not_cash_buy(self):
        got = R.parse_boc_usd(PAGE % (HEAD + ROW_USD))
        self.assertNotEqual(got['buy_spot'], 706.52)        # 现钞买入价
        self.assertNotEqual(got['buy_spot'], 715.30)        # 卖出价

    def test_header_with_different_column_order(self):
        head = '<tr><th>货币名称</th><th>现钞买入价</th><th>现汇买入价</th><th>现汇卖出价</th><th>现钞卖出价</th><th>中行折算价</th><th>发布时间</th></tr>'
        row = '<tr><td>美元</td><td>706.52</td><td>712.34</td><td>715.30</td><td>715.30</td><td>713.43</td><td>2026.10.07 10:30:21</td></tr>'
        self.assertEqual(R.parse_boc_usd(PAGE % (head + row))['buy_spot'], 712.34)

    def test_whitespace_entities_nested_tags_and_split_date_time(self):
        row = ('<tr>\n <td> <a href="#">美元</a> </td>\n<td>&nbsp;712.34 </td><td>706.52</td><td>715.30</td><td>715.30</td>'
               '<td>713.43</td><td>2026-10-07</td><td>09:05:00</td>\n</tr>')
        got = R.parse_boc_usd(PAGE % (HEAD + row))
        self.assertEqual(got['buy_spot'], 712.34)
        self.assertEqual(got['published'][:10], '2026-10-07')

    def test_no_usd_row_or_garbage(self):
        self.assertIsNone(R.parse_boc_usd(PAGE % (HEAD + ROW_JPY)))
        self.assertIsNone(R.parse_boc_usd(''))
        self.assertIsNone(R.parse_boc_usd(None))
        self.assertIsNone(R.parse_boc_usd('<html>维护中</html>'))
        bad = '<tr><td>美元</td><td>--</td><td>--</td><td>--</td><td>--</td><td>--</td><td>2026.10.07 10:30:21</td></tr>'
        self.assertIsNone(R.parse_boc_usd(PAGE % (HEAD + bad)))          # 暂无报价的占位行不能当数字

    def test_does_not_confuse_other_currencies(self):
        row = '<tr><td>美元兑日元</td><td>1</td><td>2</td><td>3</td><td>4</td><td>5</td><td>2026.10.07 10:30:21</td></tr>'
        self.assertIsNone(R.parse_boc_usd(PAGE % (HEAD + row)))          # 名称里带"美元"的交叉盘不能当成美元行


class TestFetch(unittest.TestCase):
    def test_query_interface_first_and_form_fields(self):
        calls = []

        def http(url, data=None):
            calls.append((url, data))
            return PAGE % (HEAD + ROW_USD)
        got = R.fetch_boc_usd(http)
        self.assertEqual(got['buy_spot'], 712.34)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], R.BOC_SEARCH)
        body = calls[0][1].decode()
        self.assertIn('pjname=%E7%BE%8E%E5%85%83', body)            # 美元
        self.assertIn('erectDate=' + time.strftime('%Y-%m-%d'), body)

    def test_falls_back_to_index_pages(self):
        seen = []

        def http(url, data=None):
            seen.append(url)
            if url == R.BOC_SEARCH:
                raise OSError('timeout')
            if url == R.BOC_PAGES[0]:
                return PAGE % (HEAD + ROW_JPY)                     # 第一页没有美元（BOC 首页只列前 20 种货币）
            if url == R.BOC_PAGES[1]:
                return PAGE % (HEAD + ROW_USD)
            raise AssertionError('不该走到这里')
        self.assertEqual(R.fetch_boc_usd(http)['buy_spot'], 712.34)
        self.assertEqual(seen, [R.BOC_SEARCH, R.BOC_PAGES[0], R.BOC_PAGES[1]])

    def test_all_sources_fail_message(self):
        def http(url, data=None):
            raise OSError('network down')
        with self.assertRaises(RuntimeError) as cm:
            R.fetch_boc_usd(http)
        self.assertIn('无法从中国银行页面取得美元现汇买入价', str(cm.exception))
        self.assertIn('network down', str(cm.exception))


class TestRateService(AppTestCase):
    def setUp(self):
        type(self).rate_result = 714.0
        self.ctx.db.execute("DELETE FROM app_settings WHERE key LIKE 'rate_%' OR key LIKE 'cny_usd%'")
        self.ctx.db.execute('DELETE FROM rate_history')

    def test_defaults(self):
        d = self.c.get('/api/rate')[1]
        self.assertEqual((d['rate'], d['source'], d['mode'], d['boc_buy'], d['history']), (0.138, 'default', 'boc', None, []))

    def test_refresh_sets_rate_from_spot_buy_and_logs_history(self):
        st, d = self.c.post('/api/rate/refresh')
        self.assertEqual(st, 200, d)
        self.assertEqual((d['rate'], d['source'], d['boc_buy'], d['boc_published']), (round(100 / 714.0, 6), 'boc', 714.0, '2026-10-07 10:30:00'))
        h = self.c.get('/api/rate')[1]['history']
        self.assertEqual([(x['day'], x['buy_spot'], x['rate']) for x in h], [('2026-10-07', 714.0, round(100 / 714.0, 6))])
        pid = self.new_product(cost=100, cost_currency='CNY', profit_rate=0)
        self.assertEqual(self.c.get('/api/products/%d' % pid)[1]['product']['suggested_price'], round(100 * round(100 / 714.0, 6), 2))   # 14.01
        self.assertEqual(self.c.post('/api/rate/fetch')[0], 200)                        # 旧接口名仍可用

    def test_same_day_keeps_latest_published(self):
        type(self).rate_result = 714.0
        self.c.post('/api/rate/refresh')
        orig = self.ctx.rates.fetcher
        self.ctx.rates.fetcher = lambda: {'buy_spot': 700.0, 'published': '2026-10-07 09:00:00'}      # 更早发布的数据晚到
        try:
            self.c.post('/api/rate/refresh')
            self.assertEqual(self.c.get('/api/rate')[1]['history'][0]['buy_spot'], 714.0)
            self.ctx.rates.fetcher = lambda: {'buy_spot': 716.0, 'published': '2026-10-07 15:00:00'}
            self.c.post('/api/rate/refresh')
            h = self.c.get('/api/rate')[1]['history']
            self.assertEqual([(x['day'], x['buy_spot']) for x in h], [('2026-10-07', 716.0)])        # 同一天只留最新一条
            self.ctx.rates.fetcher = lambda: {'buy_spot': 710.0, 'published': '2026-10-08 10:00:00'}
            self.c.post('/api/rate/refresh')
            self.assertEqual([x['day'] for x in self.c.get('/api/rate')[1]['history']], ['2026-10-08', '2026-10-07'])
        finally:
            self.ctx.rates.fetcher = orig

    def test_failure_keeps_old_rate_and_records_error(self):
        self.c.post('/api/rate/refresh')
        type(self).rate_result = RuntimeError('页面改版了')
        st, r = self.c.post('/api/rate/refresh')
        self.assertEqual(st, 502)
        self.assertIn('页面改版了', r['error'])
        self.assertIn('当前汇率保持不变', r['error'])
        d = self.c.get('/api/rate')[1]
        self.assertEqual((d['rate'], d['source']), (round(100 / 714.0, 6), 'boc'))
        self.assertIn('页面改版了', d['last_error'])
        type(self).rate_result = 714.0
        self.c.post('/api/rate/refresh')
        self.assertEqual(self.c.get('/api/rate')[1]['last_error'], '')            # 成功后清除错误

    def test_implausible_values_rejected(self):
        for bad in (7.14, 71.4, 7140.0, 0, -5, 'abc'):
            type(self).rate_result = bad
            st, r = self.c.post('/api/rate/refresh')
            self.assertEqual(st, 502, bad)
        self.assertEqual(self.c.get('/api/rate')[1]['rate'], 0.138)
        self.assertEqual(self.c.get('/api/rate')[1]['history'], [])

    def test_manual_overrides_and_pauses_auto(self):
        self.c.post('/api/rate/refresh')
        d = self.c.put('/api/rate', {'rate': '0.15'})[1]
        self.assertEqual((d['rate'], d['source'], d['mode']), (0.15, 'manual', 'manual'))
        self.assertIsNone(self.ctx.rates.refresh(force=False))                    # 定时任务在手动模式下什么都不做
        self.c.post('/api/rate/refresh')                                          # 用户点"立即更新"：记历史但不覆盖手动汇率
        d = self.c.get('/api/rate')[1]
        self.assertEqual((d['rate'], d['mode']), (0.15, 'manual'))
        d = self.c.put('/api/rate/mode', {'mode': 'boc'})[1]                      # 恢复自动：立即更新
        self.assertEqual((d['mode'], d['source'], d['rate'], d['refresh_error']), ('boc', 'boc', round(100 / 714.0, 6), ''))

    def test_manual_from_boc_page_value(self):
        d = self.c.put('/api/rate', {'boc_buy': '712.34'})[1]
        self.assertEqual((d['rate'], d['boc_buy'], d['mode']), (round(100 / 712.34, 6), 712.34, 'manual'))
        for bad in ('70', '99999', 'x'):
            self.assertEqual(self.c.put('/api/rate', {'boc_buy': bad})[0], 400, bad)

    def test_validation(self):
        for bad in ('abc', 0, -1, 11, None, ''):
            self.assertEqual(self.c.put('/api/rate', {'rate': bad})[0], 400, bad)
        self.assertEqual(self.c.put('/api/rate/mode', {'mode': 'weird'})[0], 400)

    def test_switch_to_auto_with_network_down_still_switches(self):
        self.c.put('/api/rate', {'rate': '0.15'})
        type(self).rate_result = OSError('offline')
        st, d = self.c.put('/api/rate/mode', {'mode': 'boc'})
        self.assertEqual(st, 200)
        self.assertEqual((d['mode'], d['rate']), ('boc', 0.15))
        self.assertIn('offline', d['refresh_error'])

    def test_scheduler_runs_repeatedly_and_retries_after_failure_and_stops(self):
        calls = []
        outcomes = iter([OSError('x'), None, None, None, None, None, None, None])

        def fetcher():
            calls.append(time.time())
            o = next(outcomes)
            if o:
                raise o
            return {'buy_spot': 713.0, 'published': '2026-10-09 10:00:00'}
        orig = self.ctx.rates.fetcher
        self.ctx.rates.fetcher = fetcher
        sch = R.RateScheduler(self.ctx.rates, first_delay=0.01, interval=0.05, retry=0.05)
        sch.start()
        time.sleep(0.5)
        sch.stop()
        sch.join(2)
        self.ctx.rates.fetcher = orig
        self.assertFalse(sch.is_alive())
        self.assertGreaterEqual(len(calls), 3)                                    # 失败后重试，成功后继续按间隔更新
        self.assertEqual(self.ctx.rates.info()['source'], 'boc')
        n = len(calls)
        time.sleep(0.2)
        self.assertEqual(len(calls), n)                                           # 停止后不再抓取


if __name__ == '__main__':
    unittest.main()
