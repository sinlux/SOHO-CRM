# -*- coding: utf-8 -*-
"""生成完整安装包（开发者用）：把 git 里的程序文件打成 SINLUX-CRM-v<版本>.zip，不含 data/、tests/、tools/。
用法：python tools/make_release.py [git ref，默认 HEAD] [输出目录，默认当前目录]
之后的功能更新请用 tools/make_update.py 生成小升级包。"""
import os
import subprocess
import sys
import zipfile


def git(*a, raw=False):
    out = subprocess.run(['git', '-c', 'core.quotepath=off', *a], capture_output=True, check=True).stdout
    return out if raw else out.decode('utf-8')


def main():
    ref = sys.argv[1] if len(sys.argv) > 1 else 'HEAD'
    outdir = sys.argv[2] if len(sys.argv) > 2 else '.'
    version = git('show', '%s:app/version.txt' % ref).strip()
    files = [f for f in git('ls-tree', '-r', '--name-only', ref).splitlines()
             if not f.startswith(('tests/', 'tools/', 'data/', 'versions/', '.git')) and f != 'REWRITE_NOTES.md']
    out = os.path.join(outdir, 'SINLUX-CRM-v%s.zip' % version)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.writestr('SINLUX-CRM/' + f, git('show', '%s:%s' % (ref, f), raw=True))
    print('已生成 %s（%d 个文件，版本 %s）' % (out, len(files), version))


if __name__ == '__main__':
    main()
