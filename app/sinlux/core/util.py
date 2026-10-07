# -*- coding: utf-8 -*-
"""通用小工具：时间、错误类型、校验、多值字段处理。"""
import re
import time
import datetime


class ApiError(Exception):
    """业务错误：带 HTTP 状态码和给用户看的中文信息。"""

    def __init__(self, message, status=400, extra=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.extra = extra or {}


def now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def today():
    return time.strftime('%Y-%m-%d')


def valid_date(s):
    """YYYY-MM-DD 且是真实存在的日期。"""
    if not isinstance(s, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', s):
        return False
    try:
        datetime.date.fromisoformat(s)
        return True
    except ValueError:
        return False


def like_escape(s):
    """把用户输入里的 % _ \\ 转义，配合 LIKE ? ESCAPE '\\' 使用。"""
    return s.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def like(s):
    return '%' + like_escape(s) + '%'


_SPLIT_RE = re.compile(r'[\s,;，；]+')


def split_multi(val):
    """邮箱/网址类字段：空白、逗号、分号分隔。"""
    if not val:
        return []
    return [p for p in _SPLIT_RE.split(str(val).strip()) if p]


def clean_multi(val):
    """拆分单元格里的多个邮箱/网址，大小写不敏感去重，用空格重新连接。"""
    seen, out = set(), []
    for p in split_multi(val):
        k = p.lower()
        if k not in seen:
            seen.add(k)
            out.append(p)
    return ' '.join(out)


def merge_multi(current, new_values):
    """追加去重（空白分隔字段）。返回新字符串；不改动已有内容的顺序。"""
    cur = split_multi(current)
    seen = {v.lower() for v in cur}
    for v in new_values:
        for p in split_multi(v):
            if p.lower() not in seen:
                seen.add(p.lower())
                cur.append(p)
    return ' '.join(cur)


_PHONE_SPLIT = re.compile(r'[;；,，\n]+')


def split_phones(val):
    """WhatsApp/电话字段：号码内部可含空格，所以只按 ; , 换行分隔。"""
    return [p.strip() for p in _PHONE_SPLIT.split(val or '') if p.strip()]


def merge_phones(current, new_values):
    cur = split_phones(current)
    seen = {re.sub(r'\D', '', v) or v.lower() for v in cur}
    for v in new_values:
        for p in split_phones(v):
            k = re.sub(r'\D', '', p) or p.lower()
            if k not in seen:
                seen.add(k)
                cur.append(p)
    return '; '.join(cur)
