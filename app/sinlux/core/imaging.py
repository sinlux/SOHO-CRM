# -*- coding: utf-8 -*-
"""产品图自动规范化：不管上传什么格式/尺寸/比例，输出统一的「居中、白底、方形、高清」产品图。

流程：识别格式(JPEG/PNG/WebP/GIF/BMP/TIFF/AVIF…) → 按 EXIF 转正 → 透明/调色板/CMYK 统一成 RGB(透明处铺白)
→ 若四角是一致的纯色背景，则裁掉多余空白（让产品居中、占满画面）→ 等比缩放放进 1600×1600 白底画布，四周留 6% 边距
→ 输出 JPEG(q90) 主图 + 480×480 缩略图。原图分辨率偏低时标记 low_res，界面提示。

Pillow 不可用（没装/版本不匹配）时 available() 为 False，调用方退回"原样保存"，页面仍用 CSS 居中显示。
"""
import io

from .util import ApiError

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps
    Image.MAX_IMAGE_PIXELS = 120_000_000          # 约 1.2 亿像素以上视为异常（防解压炸弹）
    _OK = True
except Exception:      # ImportError，或 Windows 的 .pyd 在别的平台加载失败
    Image = ImageChops = ImageOps = None
    _OK = False

MAIN_SIZE = 1600
THUMB_SIZE = 480
MARGIN = 0.06                 # 画布四周留白比例
LOW_RES_EDGE = 700            # 裁掉空白后的产品主体最长边低于此值，提示"分辨率偏低"
TRIM_TOLERANCE = 22           # 与背景色差小于此值视为背景
BG = (255, 255, 255)


def available():
    return _OK


def _flatten(im):
    """各种色彩模式 → RGB，透明处铺白。"""
    if im.mode in ('I;16', 'I;16L', 'I;16B', 'I', 'F'):
        im = im.point(lambda p: p * (1 / 256.0)).convert('L') if im.mode.startswith('I') else im.convert('L')
    if im.mode == 'P':
        im = im.convert('RGBA')
    elif im.mode in ('LA', 'La', 'PA'):
        im = im.convert('RGBA')
    if im.mode == 'RGBA':
        bg = Image.new('RGB', im.size, BG)
        bg.paste(im, mask=im.getchannel('A'))
        return bg
    return im.convert('RGB')


def _corner_bg(im):
    """四个角的颜色一致 → 返回背景色，否则 None（实景图/渐变背景不处理）。"""
    w, h = im.size
    k = max(2, min(w, h) // 50)
    corners = [im.crop(b).resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
               for b in ((0, 0, k, k), (w - k, 0, w, k), (0, h - k, k, h), (w - k, h - k, w, h))]
    if max(max(c[i] for c in corners) - min(c[i] for c in corners) for i in range(3)) > TRIM_TOLERANCE:
        return None
    return tuple(sorted(c[i] for c in corners)[1] for i in range(3))


def _whiten_backdrop(im):
    """纯色但不是白色的影棚背景（灰/米/蓝…）→ 与四角相连的那片背景换成白色，边缘做一点柔化。
    只处理"从画面边缘连通进来"的背景色像素，产品内部碰巧同色的部分不动。"""
    bg = _corner_bg(im)
    if bg is None or min(bg) >= 245:
        return im
    w, h = im.size
    diff = ImageChops.difference(im, Image.new('RGB', im.size, bg)).convert('L')
    cand = diff.point(lambda p: 255 if p <= TRIM_TOLERANCE else 0)                 # 全分辨率：像背景的像素
    scale = min(1.0, 500.0 / max(w, h))
    small = cand.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.BOX).point(lambda p: 255 if p >= 128 else 0)
    sw, sh = small.size
    for xy in ((0, 0), (sw - 1, 0), (0, sh - 1), (sw - 1, sh - 1)):               # 低分辨率上做连通区域，快
        if small.getpixel(xy) == 255:
            ImageDraw.floodfill(small, xy, 128)
    region = small.point(lambda p: 255 if p == 128 else 0).resize((w, h), Image.Resampling.NEAREST)
    mask = ImageChops.darker(cand, region).filter(ImageFilter.GaussianBlur(1.0))
    return Image.composite(Image.new('RGB', im.size, BG), im, mask)


def _trim(im):
    """四角颜色一致 → 以该色为背景，裁到产品外接框（加少量内边距）。不满足条件原样返回。"""
    w, h = im.size
    if w < 40 or h < 40:
        return im
    k = max(2, min(w, h) // 50)
    corners = [im.crop(b).resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
               for b in ((0, 0, k, k), (w - k, 0, w, k), (0, h - k, k, h), (w - k, h - k, w, h))]
    spread = max(max(c[i] for c in corners) - min(c[i] for c in corners) for i in range(3))
    if spread > TRIM_TOLERANCE:           # 背景不均匀（实景图/渐变）：不裁，免得误伤
        return im
    bg = tuple(sorted(c[i] for c in corners)[1] for i in range(3))
    diff = ImageChops.difference(im, Image.new('RGB', im.size, bg)).convert('L').point(
        lambda p: 255 if p > TRIM_TOLERANCE else 0)
    box = diff.getbbox()
    if not box:
        return im
    bw, bh = box[2] - box[0], box[3] - box[1]
    if bw * bh < 0.0015 * w * h or max(bw, bh) < 8:   # 面积不到 0.15%：更像噪点/灰尘而不是产品
        return im
    # 白底留一点呼吸边；灰色/彩色影棚底则紧贴产品裁，免得白画布上留下一圈灰边
    pad = max(2, int(max(bw, bh) * 0.02)) if min(bg) >= 235 else 0
    box = (max(0, box[0] - pad), max(0, box[1] - pad), min(w, box[2] + pad), min(h, box[3] + pad))
    if box == (0, 0, w, h):
        return im
    return im.crop(box)


def _canvas(subject, size):
    inner = int(size * (1 - 2 * MARGIN))
    sw, sh = subject.size
    scale = inner / max(sw, sh)
    nw, nh = max(1, round(sw * scale)), max(1, round(sh * scale))
    resized = subject.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', (size, size), BG)
    canvas.paste(resized, ((size - nw) // 2, (size - nh) // 2))
    return canvas


def _jpeg(im, quality):
    buf = io.BytesIO()
    im.save(buf, 'JPEG', quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def process(data, trim=True):
    """data: 图片原始字节。返回 dict(main=bytes, thumb=bytes, width, height, src_width, src_height, low_res, format)。"""
    if not _OK:
        raise RuntimeError('Pillow 不可用')
    try:
        im = Image.open(io.BytesIO(data))
        fmt = im.format
        im.seek(0)                            # 动图只取第一帧
        im = ImageOps.exif_transpose(im)
        im.load()
    except Image.DecompressionBombError:
        raise ApiError('图片像素太大，请先缩小后再上传')
    except Exception:
        raise ApiError('无法识别这个文件，请上传 JPG / PNG / WebP / GIF / BMP / TIFF / AVIF 格式的图片')
    src_w, src_h = im.size
    if src_w < 8 or src_h < 8:
        raise ApiError('图片太小（至少 8×8 像素）')
    im = _flatten(im)
    if trim:
        im = _whiten_backdrop(im)
    subject = _trim(im) if trim else im
    main = _canvas(subject, MAIN_SIZE)
    thumb = main.resize((THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)
    return {'main': _jpeg(main, 90), 'thumb': _jpeg(thumb, 84), 'width': MAIN_SIZE, 'height': MAIN_SIZE,
            'src_width': src_w, 'src_height': src_h, 'format': fmt,
            'low_res': max(subject.size) < LOW_RES_EDGE}


def sniff(data):
    """不依赖 Pillow 的格式判断，仅用于 Pillow 不可用时的"原样保存"。返回扩展名或 None。"""
    if data[:3] == b'\xff\xd8\xff':
        return 'jpg'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    return None
