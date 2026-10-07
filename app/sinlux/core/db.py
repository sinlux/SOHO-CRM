# -*- coding: utf-8 -*-
"""SQLite 访问层。

- 一个连接 + 可重入锁（本地单用户程序，ThreadingHTTPServer 的多线程串行访问数据库）。
- 外键约束始终开启（旧版没开，导致删客户后报价单变孤儿）。
- 自动提交模式 + tx() 显式事务：多步写入要么全成功要么全回滚。
"""
import os
import sqlite3
import threading
from contextlib import contextmanager


class Database:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.RLock()
        self._depth = 0
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA busy_timeout=5000')
        self.conn.execute('PRAGMA foreign_keys=ON')

    # ---------- 查询 ----------
    def query(self, sql, params=()):
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql, params=()):
        with self._lock:
            row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else None

    # ---------- 写入 ----------
    def execute(self, sql, params=()):
        """执行一条写语句，返回 cursor（可取 lastrowid / rowcount）。"""
        with self._lock:
            return self.conn.execute(sql, params)

    def executescript(self, script):
        with self._lock:
            if self._depth:
                raise RuntimeError('executescript 不能在事务内调用')
            self.conn.executescript(script)

    @contextmanager
    def tx(self):
        """事务。可嵌套：只有最外层负责 BEGIN/COMMIT/ROLLBACK。"""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self.conn.execute('BEGIN IMMEDIATE')
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                if outer:
                    self.conn.execute('ROLLBACK')
                raise
            else:
                self._depth -= 1
                if outer:
                    self.conn.execute('COMMIT')

    def set_foreign_keys(self, on):
        """PRAGMA foreign_keys 不能在事务内改，仅供迁移重建表时使用。"""
        with self._lock:
            if self._depth:
                raise RuntimeError('事务内不能切换外键开关')
            self.conn.execute('PRAGMA foreign_keys=%s' % ('ON' if on else 'OFF'))

    def column_exists(self, table, col):
        return any(r['name'] == col for r in self.query('PRAGMA table_info(%s)' % table))

    def table_exists(self, table):
        return bool(self.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)))

    def has_fk(self, table, parent):
        return any(r['table'] == parent for r in self.query('PRAGMA foreign_key_list(%s)' % table))

    # ---------- 设置（settings 表，沿用旧版 key/value） ----------
    def get_setting(self, key, default=''):
        r = self.one('SELECT value FROM settings WHERE key=?', (key,))
        return r['value'] if r and r['value'] is not None else default

    def set_setting(self, key, value):
        self.execute('INSERT INTO settings(key,value) VALUES(?,?) '
                     'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))

    def backup_to(self, dest_path):
        """SQLite 在线备份 API：拿到一致性快照，即使别的线程在写。"""
        with self._lock:
            dest = sqlite3.connect(dest_path)
            try:
                self.conn.backup(dest)
            finally:
                dest.close()

    def close(self):
        with self._lock:
            self.conn.close()
