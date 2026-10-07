# -*- coding: utf-8 -*-
"""极简 HTTP 框架（仅标准库）：路由、JSON、文件、流式上传、本机访问防护。"""
import json
import os
import re
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .util import ApiError

MIME = {
    'html': 'text/html; charset=utf-8', 'css': 'text/css; charset=utf-8',
    'js': 'text/javascript; charset=utf-8', 'json': 'application/json; charset=utf-8',
    'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'gif': 'image/gif',
    'webp': 'image/webp', 'svg': 'image/svg+xml', 'ico': 'image/x-icon',
    'pdf': 'application/pdf', 'zip': 'application/zip',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}


class FileResponse:
    def __init__(self, path, download_name=None):
        self.path = path
        self.download_name = download_name


class Request:
    def __init__(self, handler, method, path, query, match):
        self.handler = handler
        self.method = method
        self.path = path
        self.query = query
        self.params = match.groupdict() if match else {}
        self.headers = handler.headers
        self._body = None

    def arg(self, name, default=''):
        v = self.query.get(name)
        return v[0] if v else default

    def json(self):
        """请求体 JSON（必须是对象）。空体返回 {}。"""
        if self._body is None:
            n = int(self.headers.get('Content-Length') or 0)
            if n > 25 * 1024 * 1024:
                raise ApiError('请求体过大', 413)
            raw = self.handler.rfile.read(n) if n else b''
            try:
                self._body = json.loads(raw.decode('utf-8')) if raw else {}
            except (ValueError, UnicodeDecodeError):
                raise ApiError('请求体不是有效的 JSON')
            if not isinstance(self._body, dict):
                raise ApiError('请求体必须是 JSON 对象')
        return self._body

    def save_upload(self, dest_path, max_bytes):
        """流式落盘（不整体读进内存）。返回字节数。"""
        n = int(self.headers.get('Content-Length') or 0)
        if n <= 0:
            raise ApiError('上传内容为空')
        if n > max_bytes:
            raise ApiError('文件太大(%dMB)，超过 %dMB 上限' % (n // 1048576, max_bytes // 1048576), 413)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        remaining = n
        with open(dest_path, 'wb') as f:
            while remaining > 0:
                chunk = self.handler.rfile.read(min(1048576, remaining))
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)
        if remaining:
            os.remove(dest_path)
            raise ApiError('上传中断，文件不完整')
        return n

    def upload_filename(self, default='upload.bin'):
        raw = urllib.parse.unquote(self.headers.get('X-Filename', '') or '')
        return re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', os.path.basename(raw)) or default


class Router:
    def __init__(self):
        self.routes = []

    def add(self, method, pattern, func):
        # {id} 只匹配数字；其它占位符匹配一段非 / 的内容
        rx = re.compile('^' + re.sub(r'\{(\w+)\}', lambda m: '(?P<%s>%s)' % (
            m.group(1), r'\d+' if m.group(1) == 'id' else r'[^/]+'), pattern) + '$')
        self.routes.append((method, rx, func))

    def route(self, method, pattern):
        def deco(f):
            self.add(method, pattern, f)
            return f
        return deco

    def get(self, p): return self.route('GET', p)
    def post(self, p): return self.route('POST', p)
    def put(self, p): return self.route('PUT', p)
    def delete(self, p): return self.route('DELETE', p)

    def find(self, method, path):
        path_exists = False
        for m, rx, f in self.routes:
            mt = rx.match(path)
            if mt:
                path_exists = True
                if m == method:
                    return f, mt, True
        return None, None, path_exists


def make_handler(ctx, router, static_dir):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        # ---- 输出 ----
        def _send(self, code, body, ctype, extra=None):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'), MIME['json'])

        def send_file(self, path, download_name=None):
            try:
                with open(path, 'rb') as f:
                    body = f.read()
            except (FileNotFoundError, IsADirectoryError):
                return self.send_json({'ok': False, 'error': '文件不存在'}, 404)
            ext = path.rsplit('.', 1)[-1].lower() if '.' in path else ''
            extra = {}
            if download_name:
                extra['Content-Disposition'] = "attachment; filename*=UTF-8''" + urllib.parse.quote(download_name)
            self._send(200, body, MIME.get(ext, 'application/octet-stream'), extra)

        # ---- 安全：只接受发给本机本端口的请求，防 DNS 重绑定 / 跨站请求 ----
        def _origin_ok(self):
            host = (self.headers.get('Host') or '').lower()
            port = str(self.server.server_address[1])
            if host not in ('127.0.0.1:' + port, 'localhost:' + port):
                return False
            origin = self.headers.get('Origin')
            if origin and origin.lower() not in ('http://127.0.0.1:' + port, 'http://localhost:' + port):
                return False
            return True

        def _dispatch(self, method):
            parsed = urllib.parse.urlparse(self.path)
            path = urllib.parse.unquote(parsed.path)
            if not self._origin_ok():
                return self.send_json({'ok': False, 'error': '非法来源'}, 403)
            try:
                func, match, path_exists = router.find(method, path)
                if func:
                    req = Request(self, method, path, urllib.parse.parse_qs(parsed.query), match)
                    result = func(ctx, req)
                    if isinstance(result, FileResponse):
                        return self.send_file(result.path, result.download_name)
                    return self.send_json(result if result is not None else {'ok': True})
                if method == 'GET' and not path.startswith('/api/'):
                    return self._static(path)
                if path_exists:
                    return self.send_json({'ok': False, 'error': '不支持的请求方法'}, 405)
                return self.send_json({'ok': False, 'error': '接口不存在'}, 404)
            except ApiError as e:
                body = {'ok': False, 'error': e.message}
                body.update(e.extra)
                return self.send_json(body, e.status)
            except Exception as e:  # 不把堆栈暴露给前端，但打印到黑窗口便于排查
                traceback.print_exc()
                return self.send_json({'ok': False, 'error': '服务器内部错误: %s' % e}, 500)

        def _static(self, path):
            if path in ('', '/'):
                path = '/index.html'
            rel = path.lstrip('/')
            full = os.path.normpath(os.path.join(static_dir, rel))
            root = os.path.normpath(static_dir)
            if not (full == root or full.startswith(root + os.sep)) or not os.path.isfile(full):
                return self.send_json({'ok': False, 'error': '页面不存在'}, 404)
            return self.send_file(full)

        def do_GET(self): self._dispatch('GET')
        def do_POST(self): self._dispatch('POST')
        def do_PUT(self): self._dispatch('PUT')
        def do_DELETE(self): self._dispatch('DELETE')

    return Handler


def make_server(ctx, router, static_dir, port=8123, host='127.0.0.1'):
    server = ThreadingHTTPServer((host, port), make_handler(ctx, router, static_dir))
    server.daemon_threads = True
    return server
