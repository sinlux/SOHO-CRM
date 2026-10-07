# -*- coding: utf-8 -*-
"""从旧 QuoteMaster 迁移数据（双击「迁移旧数据.bat」运行）。
需要：旧项目的 quotes.db + 数据库密码 + 本机已装 sqlcipher3-wheels（旧项目装过就有）。
迁移前会先自动备份当前数据；可重复运行，不会产生重复数据。请先关闭 SINLUX CRM 再运行。"""
import getpass
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if sys.platform == 'win32':
    sys.path.insert(0, os.path.join(APP_DIR, 'libs'))
else:
    sys.path.append(os.path.join(APP_DIR, 'libs'))
sys.path.insert(0, APP_DIR)


def _exit(msg=''):
    if msg:
        print(msg)
    input('按回车退出...')


def main():
    print('=' * 50)
    print(' SINLUX CRM 旧数据迁移工具 (QuoteMaster -> CRM)')
    print('=' * 50)
    try:
        from sqlcipher3 import dbapi2 as sqlcipher
    except ImportError:
        return _exit('\n[错误] 没有找到 sqlcipher3 模块。请先运行：\n  pip install sqlcipher3-wheels')
    from sinlux.app import Context, default_data_dir
    from sinlux.core import backup
    from sinlux.migrate.quotemaster import migrate

    default = os.path.abspath(os.path.join(APP_DIR, '..', '..', 'QuoteMaster', 'data', 'quotes.db'))
    old_path = input('\n旧数据库路径（直接回车 = %s）\n路径: ' % default).strip().strip('"') or default
    if not os.path.exists(old_path):
        return _exit('[错误] 文件不存在: %s' % old_path)
    pwd = getpass.getpass('旧数据库密码（输入时不显示）: ')
    conn = sqlcipher.connect(old_path)
    conn.execute("PRAGMA key = '%s'" % pwd.replace("'", "''"))
    try:
        conn.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()
    except Exception:
        return _exit('[错误] 密码不对或数据库损坏，解密失败。')
    print('解密成功。先备份当前数据，再开始迁移...')
    ctx = Context(default_data_dir())
    try:
        b = backup.make_backup(ctx.db, ctx.data_dir, ctx.backups_dir)
        print('已备份：data/backups/%s（%sMB）' % (b['file'], b['size_mb']))
        res = migrate(ctx, conn, os.path.join(os.path.dirname(old_path), 'uploads'))
    finally:
        ctx.close()
        conn.close()
    print('\n迁移完成：')
    for k, v in res['stats'].items():
        print('  %s: %s' % (k, v))
    for w in res['warnings']:
        print('  [提示] ' + w)
    print('\n重新打开 SINLUX CRM 即可看到数据。')
    _exit()


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        _exit('\n[迁移中断] %s（数据库未被改动）' % e)
