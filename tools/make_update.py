# -*- coding: utf-8 -*-
"""生成升级包（开发者用，不随程序发布）：把两个 git 版本之间 app/ 与根目录 .bat/.txt 的变化打成 zip。
用法：python tools/make_update.py <旧版本ref> <新版本ref> "更新说明" [输出.zip]
例：  python tools/make_update.py v5.0.0 HEAD "修复报价单分页" update_5.0.1.zip
版本号取自新版本里的 app/version.txt。生成的包可在「设置 → 版本与升级」里上传。"""
import json
import subprocess
import sys
import zipfile

sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..', 'app'))
from sinlux.core.updater import safe_rel  # noqa: E402


def git(*a, raw=False):
    out = subprocess.run(['git', *a], capture_output=True, check=True).stdout
    return out if raw else out.decode('utf-8')


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    old, new, note = sys.argv[1:4]
    out = sys.argv[4] if len(sys.argv) > 4 else 'update.zip'
    version = git('show', '%s:app/version.txt' % new).strip()
    changed, deleted = [], []
    for line in git('-c', 'core.quotepath=off', 'diff', '--name-status', old, new).splitlines():
        status, *paths = line.split('\t')
        path = paths[-1]
        if not safe_rel(path) or path.startswith('app/libs/') and status.startswith('D'):
            continue
        if status.startswith('D'):
            deleted.append(path)
        else:
            changed.append(path)
        if status.startswith('R'):
            deleted.append(paths[0]) if safe_rel(paths[0]) else None
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json', json.dumps({'version': version, 'changelog': note, 'delete': sorted(set(deleted))}, ensure_ascii=False, indent=1))
        for p in changed:
            z.writestr(p, git('show', '%s:%s' % (new, p), raw=True))
    print('已生成 %s：版本 %s，更新 %d 个文件，删除 %d 个' % (out, version, len(changed), len(set(deleted))))


if __name__ == '__main__':
    main()
