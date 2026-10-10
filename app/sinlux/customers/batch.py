# -*- coding: utf-8 -*-
"""批量背调：把一批客户排进队列，后台几个线程逐个跑一轮背调，结果都是"待确认"，由用户逐个核对。

安全与成本规则：
- 只搜公开信息，发给搜索/AI 服务的只有 公司名 + 国家（+ 已知官网域名），不发联系人私人资料、不发报价。
- Tavily 额度用完 / Key 无效：整批自动暂停（当前客户放回队列），不会把剩下的客户全标成失败，充值或换 Key 后点"继续"即可。
- 队列存在数据库里；程序关闭再打开，上次没跑完的任务回到队列，但默认【暂停】，由用户点"继续"才会花钱。
"""
import threading

from ..core.util import ApiError
from .enrich import DEPTH_COUNT, DEPTH_LABEL, QuotaError


class BatchRunner:
    WORKERS = 3

    def __init__(self, db, enricher):
        self.db = db
        self.enricher = enricher
        self.paused = True
        self.reason = ''
        self._lock = threading.Lock()
        self._threads = []
        self.db.execute("UPDATE enrich_queue SET state='queued', started_at=NULL WHERE state='running'")   # 上次异常退出时遗留的
        self.paused = bool(self.db.scalar("SELECT COUNT(*) FROM enrich_queue WHERE state='queued'"))
        if self.paused:
            self.reason = '上次还有没跑完的任务，已暂停，确认后点「继续」'

    # ---------- 入队 ----------
    def candidates(self, scope):
        """按范围找出要背调的客户。scope: mode(unresearched|all|ids), lv_min, country, ids, with_website。"""
        scope = scope or {}
        where, params = ["(TRIM(company)<>'' OR TRIM(name)<>'')"], []
        mode = scope.get('mode') or 'unresearched'
        if mode == 'ids':
            ids = [int(i) for i in (scope.get('ids') or [])]
            if not ids:
                raise ApiError('没有选择客户')
            where.append('id IN (%s)' % ','.join('?' * len(ids)))
            params += ids
        elif mode == 'unresearched':
            where.append("id NOT IN (SELECT customer_id FROM enrichments WHERE status IN ('pending','applied'))")
        elif mode != 'all':
            raise ApiError('范围不正确')
        if scope.get('lv_min') not in (None, ''):
            where.append('COALESCE(lv,0)>=?')
            params.append(int(scope['lv_min']))
        if scope.get('country'):
            where.append('country=?')
            params.append(scope['country'])
        where.append("id NOT IN (SELECT customer_id FROM enrich_queue WHERE state IN ('queued','running'))")   # 已在队列里的不重复排
        return self.db.query('SELECT id, company, name, country, website FROM customers WHERE %s '
                             'ORDER BY COALESCE(lv,0) DESC, id' % ' AND '.join(where), params)

    def estimate(self, scope, depth):
        rows = self.candidates(scope)
        per = DEPTH_COUNT.get(depth, 6)
        searches = sum(per if not r['website'] else max(1, per - 1) + 0 for r in rows)
        return {'customers': len(rows), 'searches': searches, 'depth': depth, 'depth_label': DEPTH_LABEL.get(depth, ''),
                'ai_calls': len(rows)}

    def enqueue(self, scope, depth='standard', limit=500):
        if depth not in DEPTH_COUNT:
            raise ApiError('深度不正确')
        rows = self.candidates(scope)[:max(1, min(int(limit or 500), 2000))]
        if not rows:
            raise ApiError('没有符合条件、且不在队列里的客户')
        with self.db.tx():
            for r in rows:
                self.db.execute("INSERT INTO enrich_queue(customer_id,depth,state) VALUES(?,?, 'queued')", (r['id'], depth))
        return {'queued': len(rows)}

    # ---------- 控制 ----------
    def start(self):
        """开始 / 继续。"""
        with self._lock:
            self.paused = False
            self.reason = ''
            self._threads = [t for t in self._threads if t.is_alive()]
            for _ in range(self.WORKERS - len(self._threads)):
                t = threading.Thread(target=self._worker, daemon=True)
                self._threads.append(t)
                t.start()

    def pause(self, reason='已手动暂停'):
        self.paused = True
        self.reason = reason

    def cancel_queued(self):
        n = self.db.execute("UPDATE enrich_queue SET state='cancelled', finished_at=datetime('now','localtime') WHERE state='queued'").rowcount
        self.pause('已清空排队')
        return n

    def retry_failed(self):
        return self.db.execute("UPDATE enrich_queue SET state='queued', error='' WHERE state='failed'").rowcount

    # ---------- 干活 ----------
    def _claim(self):
        with self.db.tx():
            row = self.db.one("SELECT * FROM enrich_queue WHERE state='queued' ORDER BY id LIMIT 1")
            if not row:
                return None
            self.db.execute("UPDATE enrich_queue SET state='running', started_at=datetime('now','localtime') WHERE id=?", (row['id'],))
            return row

    def _do(self, row):
        try:
            r = self.enricher.run(row['customer_id'], row['depth'])
            self.db.execute("UPDATE enrich_queue SET state='done', enrichment_id=?, finished_at=datetime('now','localtime') WHERE id=?",
                            (r['enrichment_id'], row['id']))
        except QuotaError as e:                                   # 额度/Key 问题：放回队列，整批暂停
            self.db.execute("UPDATE enrich_queue SET state='queued', started_at=NULL WHERE id=?", (row['id'],))
            self.pause('%s。处理好后点「继续」，已完成的不会重复花钱。' % e)
        except ApiError as e:
            self.db.execute("UPDATE enrich_queue SET state='failed', error=?, finished_at=datetime('now','localtime') WHERE id=?",
                            (e.message, row['id']))
        except Exception as e:
            self.db.execute("UPDATE enrich_queue SET state='failed', error=?, finished_at=datetime('now','localtime') WHERE id=?",
                            (str(e), row['id']))

    def _worker(self):
        while not self.paused:
            row = self._claim()
            if not row:
                return
            self._do(row)

    def run_pending_sync(self):
        """测试用：在当前线程里把队列跑完（或跑到被暂停）。"""
        self.paused = False
        while not self.paused:
            row = self._claim()
            if not row:
                break
            self._do(row)

    # ---------- 状态 ----------
    def status(self, limit=500, state=''):
        counts = {r['state']: r['n'] for r in self.db.query('SELECT state, COUNT(*) n FROM enrich_queue GROUP BY state')}
        where, params = '', []
        if state:
            where, params = 'WHERE q.state=?', [state]
        items = self.db.query(
            """SELECT q.id, q.customer_id, q.state, q.depth, q.error, q.finished_at, q.enrichment_id,
                      c.company, c.name, c.country, c.lv,
                      e.status enrich_status, e.score, e.round, e.extracted
               FROM enrich_queue q JOIN customers c ON c.id=q.customer_id
               LEFT JOIN enrichments e ON e.id=q.enrichment_id %s
               ORDER BY q.id DESC LIMIT ?""" % where, params + [max(1, min(int(limit), 2000))])
        import json
        for it in items:
            try:
                ex = json.loads(it.pop('extracted') or '{}')
            except ValueError:
                ex = {}
            it['risks'] = len(ex.get('risk_flags') or [])
            it['relevance'] = (ex.get('relevance') or {}).get('value', '')
            it['found'] = sum(len(ex.get(k) or []) for k in ('emails', 'whatsapp', 'phones', 'linkedin', 'facebook', 'instagram'))
        running = counts.get('running', 0)
        return {'paused': self.paused, 'reason': self.reason, 'counts': counts,
                'active': (not self.paused) and (running > 0 or counts.get('queued', 0) > 0), 'items': items}
