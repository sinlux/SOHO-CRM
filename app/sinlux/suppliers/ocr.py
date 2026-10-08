# -*- coding: utf-8 -*-
"""聊天截图的本地文字识别（OCR）。

引擎：Windows 自带的 OCR（Windows.Media.Ocr，Win10/11 内置，识别中文需要系统装了中文语言包）——完全在本机运行，截图不会上传到任何地方。
别的系统（开发/测试）没有这个引擎，状态记为 unavailable，界面上提示"可手动补充文字"，不影响归档和查看截图。
注意：识别结果只是"帮你搜索"的索引，不保证 100% 准确；原始截图永远保留，可以随时手动改识别文字。"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, 'ocr_win.ps1')
MAX_STRIP = 5000                        # Windows OCR 单张图最大边约 10000px；超长聊天截图按这个高度切条识别
_CJK = r'[\u2e80-\u9fff\uff00-\uffef\u3000-\u303f]'


def powershell():
    p = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    return p if os.path.exists(p) else None


def available():
    return sys.platform == 'win32' and powershell() is not None and os.path.exists(SCRIPT)


def clean_text(t):
    """Windows OCR 会在汉字之间插空格（'你 好 吗'），搜索会因此失败：去掉汉字/全角标点两侧的空格，保留英文单词间的空格。"""
    t = t.replace('\r', '')
    t = re.sub(r'(?<=%s)[ \t]+(?=%s)' % (_CJK, _CJK), '', t)
    t = re.sub(r'(?<=%s)[ \t]+(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])[ \t]+(?=%s)' % (_CJK, _CJK), ' ', t)
    return '\n'.join(l.strip() for l in t.split('\n') if l.strip())


def _strips(path, tmp):
    """超长截图切成多段（需要 Pillow）；没有 Pillow 或不超长就整张识别。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.load()
            w, h = im.size
            if h <= MAX_STRIP and w <= 9000:
                return [path]
            out = []
            for n, top in enumerate(range(0, h, MAX_STRIP)):
                p = os.path.join(tmp, 'strip%d.png' % n)
                im.convert('RGB').crop((0, top, w, min(h, top + MAX_STRIP))).save(p)
                out.append(p)
            return out
    except Exception:
        return [path]


def recognize(path):
    """返回 (状态, 文字, 错误)。状态：done / unavailable / failed。"""
    if not available():
        return 'unavailable', '', '本机没有可用的 OCR 引擎（Windows 10/11 自带，其它系统不支持）'
    try:
        with tempfile.TemporaryDirectory() as tmp:
            texts = []
            for part in _strips(path, tmp):
                r = subprocess.run([powershell(), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', SCRIPT, '-ImagePath', part],
                                   capture_output=True, timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if r.returncode != 0:
                    err = r.stderr.decode('utf-8', 'replace').strip()
                    if 'NO_OCR_LANGUAGE' in err:
                        return 'failed', '', '系统没有安装 OCR 语言包（设置 → 时间和语言 → 语言和区域 → 添加中文）'
                    return 'failed', '', err[:300] or '识别失败（退出码 %d）' % r.returncode
                texts.append(r.stdout.decode('utf-8', 'replace'))
        return 'done', clean_text('\n'.join(texts)), ''
    except subprocess.TimeoutExpired:
        return 'failed', '', '识别超时'
    except Exception as e:
        return 'failed', '', str(e)[:300]
