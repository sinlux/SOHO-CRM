# -*- coding: utf-8 -*-
"""本地升级包机制：用小升级包代替全量包，永远不碰 data/ 目录。

升级包 = 一个 zip：manifest.json {"version": "5.0.1", "changelog": "...", "delete": ["app/old.py"]} + 变更文件（路径相对程序根目录）。

安全规则（一个坏包不能毁掉系统，也不能写到程序目录之外）：
1. 只允许改动 app/ 目录，以及根目录下的 .bat / .txt 文件；data/、versions/、带 .. 或绝对路径/盘符/反斜杠的一律拒绝。
2. 拒绝符号链接条目、重复条目、过大（解压后 > 300MB / > 5000 个文件）的包。
3. 应用前先把当前 app/（不含 libs 和 __pycache__）+ 根目录 bat/txt 快照到 versions/<时间>_v<旧版本>/；
   包里会覆盖/删除的 app/libs 文件也逐个进快照。
4. 先把整个包解到临时目录并校验，再覆盖文件；覆盖中途出错会用快照自动还原。
5. 回退 = 还原某个快照（回退前也会先快照当前状态，回退错了还能回来）。
升级/回退后需要重启程序才生效。数据库结构变化靠启动时的幂等迁移，不在这里处理。"""
import json
import os
import re
import shutil
import tempfile
import time
import zipfile

from .util import ApiError

MAX_UNPACKED = 300 * 1024 * 1024
MAX_FILES = 5000
SNAP_RE = re.compile(r'^\d{8}_\d{6}_[\w.\-]+$')
_IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc', 'libs')


def safe_rel(rel):
    """相对程序根目录的路径是否允许被升级包改动。"""
    if not isinstance(rel, str) or not rel or '\\' in rel or '\x00' in rel or rel.startswith('/') or re.match(r'^[A-Za-z]:', rel):
        return False
    parts = rel.split('/')
    if any(p in ('', '.', '..') for p in parts):
        return False
    if parts[0] == 'app':
        return len(parts) > 1 and '__pycache__' not in parts
    return len(parts) == 1 and rel.lower().endswith(('.bat', '.txt'))


class Updater:
    def __init__(self, root, data_dir, current_version):
        self.root = os.path.abspath(root)
        self.versions = os.path.join(self.root, 'versions')
        self.tmp = os.path.join(os.path.abspath(data_dir), 'update_tmp')
        self.current_version = current_version

    # ---------- 校验 ----------
    def inspect(self, zip_path):
        try:
            zf = zipfile.ZipFile(zip_path)
        except zipfile.BadZipFile:
            raise ApiError('不是有效的 zip 升级包')
        with zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            names = [i.filename for i in infos]
            if 'manifest.json' not in names:
                raise ApiError('不是有效的升级包：缺少 manifest.json')
            if len(set(names)) != len(names):
                raise ApiError('升级包里有重复的文件条目，已拒绝')
            if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_UNPACKED:
                raise ApiError('升级包过大（文件数或解压后体积超限），已拒绝')
            for i in infos:
                if (i.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ApiError('升级包含有符号链接，已拒绝')
            try:
                m = json.loads(zf.read('manifest.json').decode('utf-8'))
            except Exception:
                raise ApiError('manifest.json 不是有效的 JSON')
            if not isinstance(m, dict):
                raise ApiError('manifest.json 格式不对')
            ver = m.get('version')
            if not isinstance(ver, str) or not re.fullmatch(r'[\w.\-+]{1,40}', ver):
                raise ApiError('manifest.json 里的 version 无效')
            if not isinstance(m.get('changelog', ''), str):
                raise ApiError('manifest.json 里的 changelog 必须是文字')
            dels = m.get('delete', [])
            if not isinstance(dels, list):
                raise ApiError('manifest.json 里的 delete 必须是列表')
            bad = [n for n in names if n != 'manifest.json' and not safe_rel(n)]
            if bad:
                raise ApiError('升级包包含不允许的路径（只能改 app/ 和根目录 .bat/.txt，不能碰 data/）：%s' % ', '.join(bad[:5]))
            baddel = [d for d in dels if not safe_rel(d)]
            if baddel:
                raise ApiError('升级包要求删除不允许的路径：%s' % ', '.join(map(str, baddel[:5])))
            bad_zip = zf.testzip()
            if bad_zip:
                raise ApiError('升级包已损坏（%s 校验失败）' % bad_zip)
        return {'version': ver, 'changelog': m.get('changelog', ''), 'files': [n for n in names if n != 'manifest.json'],
                'delete': dels, 'current_version': self.current_version, 'same_version': ver == self.current_version}

    # ---------- 快照 ----------
    def _snapshot(self, tag, extra_files=()):
        os.makedirs(self.versions, exist_ok=True)
        name, n = '%s_%s' % (time.strftime('%Y%m%d_%H%M%S'), re.sub(r'[^\w.\-]', '_', tag)), 1
        dest = os.path.join(self.versions, name)
        while os.path.exists(dest):
            dest = os.path.join(self.versions, '%s_%d' % (name, n))
            n += 1
        os.makedirs(dest)
        app = os.path.join(self.root, 'app')
        if os.path.isdir(app):
            shutil.copytree(app, os.path.join(dest, 'app'), ignore=_IGNORE)
        for f in os.listdir(self.root):
            p = os.path.join(self.root, f)
            if os.path.isfile(p) and f.lower().endswith(('.bat', '.txt')):
                shutil.copy2(p, os.path.join(dest, f))
        for rel in extra_files:                                      # 会被动到的 libs 文件
            src = os.path.join(self.root, *rel.split('/'))
            if os.path.isfile(src):
                os.makedirs(os.path.dirname(os.path.join(dest, *rel.split('/'))), exist_ok=True)
                shutil.copy2(src, os.path.join(dest, *rel.split('/')))
        return os.path.basename(dest)

    # ---------- 应用 ----------
    def apply(self, zip_path):
        info = self.inspect(zip_path)
        touched_libs = [n for n in info['files'] + info['delete'] if n.startswith('app/libs/')]
        snap = self._snapshot('v' + self.current_version, touched_libs)
        stage = tempfile.mkdtemp(prefix='stage_', dir=self._tmp_dir())
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for n in info['files']:                              # 先全部解到临时目录，解不开就还没动任何文件
                    p = os.path.join(stage, *n.split('/'))
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    with zf.open(n) as src, open(p, 'wb') as out:
                        shutil.copyfileobj(src, out)
            try:
                for n in info['files']:
                    dest = os.path.join(self.root, *n.split('/'))
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.copyfile(os.path.join(stage, *n.split('/')), dest)
                for d in info['delete']:
                    p = os.path.join(self.root, *d.split('/'))
                    if os.path.isfile(p):
                        os.remove(p)
            except Exception as e:
                self._restore(os.path.join(self.versions, snap), full=False)
                raise ApiError('应用升级包时出错，已自动还原到升级前：%s' % e, 500)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return {'applied_version': info['version'], 'backup': snap, 'files_updated': len(info['files']),
                'files_deleted': len(info['delete']), 'need_restart': True}

    def _tmp_dir(self):
        os.makedirs(self.tmp, exist_ok=True)
        return self.tmp

    def new_upload_path(self):
        return os.path.join(self._tmp_dir(), 'update_%d.zip' % int(time.time() * 1000))

    # ---------- 版本列表 / 回退 ----------
    def list_versions(self):
        if not os.path.isdir(self.versions):
            return []
        out = []
        for name in sorted(os.listdir(self.versions), reverse=True):
            p = os.path.join(self.versions, name)
            if os.path.isdir(p) and SNAP_RE.match(name):
                vf = os.path.join(p, 'app', 'version.txt')
                try:
                    ver = open(vf, encoding='utf-8').read().strip()
                except OSError:
                    ver = '?'
                out.append({'name': name, 'version': ver, 'time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(p)))})
        return out

    def _restore(self, snap_dir, full=True):
        """把快照还原到程序目录。full=True（回退）：app/ 里快照没有的文件也删掉（libs 不动）。"""
        src_app = os.path.join(snap_dir, 'app')
        keep = set()
        for root, dirs, files in os.walk(snap_dir):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), snap_dir).replace(os.sep, '/')
                keep.add(rel)
                dest = os.path.join(self.root, *rel.split('/'))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(os.path.join(root, f), dest)
        if full and os.path.isdir(src_app):
            app = os.path.join(self.root, 'app')
            for root, dirs, files in os.walk(app):
                dirs[:] = [d for d in dirs if d not in ('__pycache__', 'libs')]
                for f in files:
                    rel = os.path.relpath(os.path.join(root, f), self.root).replace(os.sep, '/')
                    if rel not in keep and not f.endswith('.pyc'):
                        os.remove(os.path.join(root, f))
            for f in os.listdir(self.root):                                  # 根目录多出来的 bat/txt 同理
                if os.path.isfile(os.path.join(self.root, f)) and f.lower().endswith(('.bat', '.txt')) and f not in keep:
                    os.remove(os.path.join(self.root, f))

    def rollback(self, name):
        if not isinstance(name, str) or not SNAP_RE.match(name):
            raise ApiError('备份名无效')
        snap = os.path.join(self.versions, name)
        if not os.path.isdir(snap):
            raise ApiError('备份不存在', 404)
        pre = self._snapshot('pre_rollback_v' + self.current_version)
        self._restore(snap, full=True)
        return {'restored': name, 'pre_rollback_backup': pre, 'need_restart': True}
