# -*- coding: utf-8 -*-
"""data URL 图片的校验与落盘（备注贴图、产品图、供应商截图共用）。"""
import base64
import os
import re
import uuid

from .util import ApiError

EXTS = {'png': 'png', 'jpg': 'jpg', 'jpeg': 'jpg', 'gif': 'gif', 'webp': 'webp'}
MAX_BYTES = 10 * 1024 * 1024
_RE = re.compile(r'^data:image/(\w+);base64,(.*)$', re.S)


def decode_data_url(raw, default_ext='png'):
    """返回 (ext, bytes)。接受 data URL 或裸 base64。无效则抛 ApiError。"""
    m = _RE.match(raw or '')
    ext, b64 = (m.group(1).lower(), m.group(2)) if m else (default_ext, raw or '')
    if ext not in EXTS:
        raise ApiError('不支持的图片格式: %s' % ext)
    try:
        data = base64.b64decode(b64, validate=False)
    except Exception:
        raise ApiError('图片数据无效')
    if not data:
        raise ApiError('图片数据无效')
    if len(data) > MAX_BYTES:
        raise ApiError('图片超过 10MB')
    return EXTS[ext], data


def save_data_url(directory, raw, prefix=''):
    """落盘，返回文件名（不含目录）。"""
    ext, data = decode_data_url(raw)
    os.makedirs(directory, exist_ok=True)
    name = '%s%s.%s' % (prefix, uuid.uuid4().hex, ext)
    with open(os.path.join(directory, name), 'wb') as f:
        f.write(data)
    return name


def remove_file(directory, name):
    """只删 directory 下的普通文件名。"""
    if not name or name != os.path.basename(name):
        return
    try:
        os.remove(os.path.join(directory, name))
    except OSError:
        pass
