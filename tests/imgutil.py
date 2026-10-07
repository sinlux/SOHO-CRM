# -*- coding: utf-8 -*-
"""测试用图片工具：不依赖 Pillow 生成合法 PNG（Pillow 只用来检查输出）。"""
import base64
import struct
import zlib


def png_bytes(w=64, h=48, bg=(255, 255, 255), box=None, box_color=(200, 30, 30), alpha=False):
    """w×h 的 PNG：背景 bg；box=(x0,y0,x1,y1) 内填 box_color。alpha=True 时背景完全透明。"""
    rows = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            inside = box and box[0] <= x < box[2] and box[1] <= y < box[3]
            c = box_color if inside else bg
            row += bytes(c) + (b'\xff' if (alpha and inside) else (b'\x00' if alpha else b''))
        rows.append(bytes(row))
    raw = b''.join(rows)

    def chunk(t, d):
        c = struct.pack('>I', len(d)) + t + d
        return c + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6 if alpha else 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def data_url(data, mime='image/png'):
    return 'data:%s;base64,%s' % (mime, base64.b64encode(data).decode())


PNG_RED_BOX = png_bytes(400, 300, box=(150, 100, 250, 200))          # 白底，中间一个 100×100 红块
