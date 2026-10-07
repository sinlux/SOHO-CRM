# -*- coding: utf-8 -*-
"""SINLUX CRM 入口。纯标准库 HTTP 服务器，启动后自动打开浏览器。"""
import os
import sys
import threading
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'libs'))   # 内置 openpyxl 等
sys.path.insert(0, HERE)

from sinlux.app import create_app  # noqa: E402

PORT = int(os.environ.get('SINLUX_PORT', '8123'))


def main():
    ctx, server = create_app(port=PORT)
    rep = ctx.migration_report
    if rep['actions']:
        print('数据库已自动升级:')
        for a in rep['actions']:
            print('  -', a)
    if os.environ.get('SINLUX_NO_BROWSER') != '1':
        threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open('http://127.0.0.1:%d' % PORT)),
                         daemon=True).start()
    print('SINLUX CRM %s 已启动: http://127.0.0.1:%d' % (ctx.version, PORT))
    print('关闭此窗口即退出程序。数据保存在 data 文件夹。')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        ctx.close()


if __name__ == '__main__':
    main()
