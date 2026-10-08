# -*- coding: utf-8 -*-
"""生成完整安装包（开发者用）：把 git 里的程序文件打成 SINLUX-CRM-v<版本>.zip，不含 data/、tests/、tools/。
用法：python tools/make_release.py [git ref，默认 HEAD] [输出目录，默认当前目录] [--python python.3.12.10.nupkg]
--python：把官方 Python 3.12（PSF 发布在 nuget.org 的 python 包，
  https://api.nuget.org/v3-flatcontainer/python/3.12.10/python.3.12.10.nupkg）精简后放进安装包的 python/ 文件夹，
  用户电脑不用装 Python，开箱即用。启动.bat 会优先用它（app/libs 里的 Pillow 等是按 Python 3.12 编译的）。
之后的功能更新请用 tools/make_update.py 生成小升级包。"""
import os
import subprocess
import sys
import zipfile


def git(*a, raw=False):
    out = subprocess.run(['git', '-c', 'core.quotepath=off', *a], capture_output=True, check=True).stdout
    return out if raw else out.decode('utf-8')


_DROP = ('tools/include/', 'tools/libs/', 'tools/tcl/', 'tools/Lib/test/', 'tools/Lib/idlelib/', 'tools/Lib/turtledemo/', 'tools/Lib/lib2to3/',
         'tools/Lib/tkinter/', 'tools/Lib/ensurepip/', 'tools/Lib/site-packages/', 'tools/Scripts/', 'tools/DLLs/_tkinter', 'tools/DLLs/tcl', 'tools/DLLs/tk')


def add_python(z, nupkg):
    """把 nupkg 里的 tools/ 精简后放到 SINLUX-CRM/python/ 下。"""
    n = 0
    with zipfile.ZipFile(nupkg) as src:
        for i in src.infolist():
            if i.is_dir() or not i.filename.startswith('tools/') or i.filename.startswith(_DROP) or '__pycache__' in i.filename:
                continue
            z.writestr('SINLUX-CRM/python/' + i.filename[len('tools/'):], src.read(i))
            n += 1
    return n


def main():
    args = sys.argv[1:]
    nupkg = None
    if '--python' in args:
        i = args.index('--python')
        nupkg = args[i + 1]
        del args[i:i + 2]
    ref = args[0] if len(args) > 0 else 'HEAD'
    outdir = args[1] if len(args) > 1 else '.'
    version = git('show', '%s:app/version.txt' % ref).strip()
    files = [f for f in git('ls-tree', '-r', '--name-only', ref).splitlines()
             if not f.startswith(('tests/', 'tools/', 'data/', 'versions/', '.git')) and f != 'REWRITE_NOTES.md']
    out = os.path.join(outdir, 'SINLUX-CRM-v%s%s.zip' % (version, '-win64-with-python' if nupkg else ''))
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.writestr('SINLUX-CRM/' + f, git('show', '%s:%s' % (ref, f), raw=True))
        extra = add_python(z, nupkg) if nupkg else 0
    if nupkg:
        print('已内置 Python：%d 个文件' % extra)
    print('已生成 %s（%d 个文件，版本 %s）' % (out, len(files), version))


if __name__ == '__main__':
    main()
