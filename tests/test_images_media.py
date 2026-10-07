# -*- coding: utf-8 -*-
"""产品图自动规范化（任意格式/尺寸 → 居中白底高清方图）、相册、文档附件、旧图统一。"""
import io
import os
import unittest
import urllib.parse

from helpers import AppTestCase
from imgutil import png_bytes, PNG_RED_BOX, data_url
from legacy_db import build_legacy_db
from sinlux.core import imaging

try:
    from PIL import Image, ImageChops
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

needs_pil = unittest.skipUnless(HAVE_PIL, '需要 Pillow')


def to_bytes(im, fmt, **kw):
    buf = io.BytesIO()
    im.save(buf, fmt, **kw)
    return buf.getvalue()


def subject_box(jpeg_bytes, tol=40):
    """输出图里非白区域的外接框（用来判断是否居中）。"""
    im = Image.open(io.BytesIO(jpeg_bytes)).convert('RGB')
    diff = ImageChops.difference(im, Image.new('RGB', im.size, (255, 255, 255))).convert('L').point(lambda p: 255 if p > tol else 0)
    return diff.getbbox(), im.size


def is_centered(box, size, slack=6):
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return abs(cx - size[0] / 2) <= slack and abs(cy - size[1] / 2) <= slack


@needs_pil
class TestImaging(unittest.TestCase):
    def check_standard(self, data, **kw):
        out = imaging.process(data, **kw)
        main = Image.open(io.BytesIO(out['main']))
        thumb = Image.open(io.BytesIO(out['thumb']))
        self.assertEqual((main.format, main.size, main.mode), ('JPEG', (1600, 1600), 'RGB'))
        self.assertEqual((thumb.format, thumb.size), ('JPEG', (480, 480)))
        return out

    def test_subject_is_trimmed_centered_and_fills_frame(self):
        out = self.check_standard(PNG_RED_BOX)               # 400×300 白底，中间 100×100 红块
        box, size = subject_box(out['main'])
        self.assertTrue(is_centered(box, size), box)
        w, h = box[2] - box[0], box[3] - box[1]
        self.assertTrue(1300 <= w <= 1420 and abs(w - h) <= 6, (w, h))      # 占满画面（四周各留 6%，主体约占 88%），不再是一小块

    def test_off_center_and_wide_subjects_get_centered(self):
        for box_in, size in (((10, 10, 60, 40), (400, 300)), ((330, 220, 390, 290), (400, 300)), ((5, 100, 395, 140), (400, 300))):
            out = self.check_standard(png_bytes(*size, box=box_in))
            box, sz = subject_box(out['main'])
            self.assertTrue(is_centered(box, sz), (box_in, box))
        out = self.check_standard(png_bytes(400, 300, box=(5, 100, 395, 140)))     # 又宽又扁：宽边顶满，高度按比例
        box, _ = subject_box(out['main'])
        self.assertTrue((box[2] - box[0]) > 1300 and (box[3] - box[1]) < 300)

    def test_gray_studio_backdrop_becomes_white_even_around_rounded_corners(self):
        from PIL import ImageDraw
        im = Image.new('RGB', (1200, 600), (214, 214, 218))
        ImageDraw.Draw(im).rounded_rectangle((100, 250, 520, 540), 90, fill=(168, 118, 86))
        out = self.check_standard(to_bytes(im, 'JPEG', quality=95))
        main = Image.open(io.BytesIO(out['main'])).convert('RGB')
        self.assertTrue(all(c >= 250 for c in main.getpixel((60, 60))))           # 画布四角是白的
        box, size = subject_box(out['main'], tol=15)
        self.assertTrue(is_centered(box, size))
        corner = main.getpixel((box[0] + 6, box[1] + 6))                          # 圆角外侧：灰底已换成白，不是一圈灰
        self.assertTrue(min(corner) >= 245, corner)
        mid = main.getpixel(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2))     # 产品本体颜色不受影响
        self.assertTrue(mid[0] > 140 and mid[2] < 120, mid)

    def test_object_same_color_as_backdrop_interior_is_kept(self):
        from PIL import ImageDraw
        im = Image.new('RGB', (600, 600), (214, 214, 218))
        d = ImageDraw.Draw(im)
        d.rectangle((150, 150, 450, 450), fill=(60, 60, 70))                       # 深色产品
        d.rectangle((250, 250, 350, 350), fill=(214, 214, 218))                    # 产品中间的"镂空"，颜色恰好和背景一样但不与外部相连
        out = self.check_standard(to_bytes(im, 'PNG'))
        main = Image.open(io.BytesIO(out['main'])).convert('RGB')
        hole = main.getpixel((800, 800))
        self.assertTrue(all(abs(a - b) <= 4 for a, b in zip(hole, (214, 214, 218))), hole)   # 镂空保持原样（没被误刷成白）

    def test_large_studio_photo_is_fast(self):
        import time
        from PIL import ImageDraw
        im = Image.new('RGB', (4000, 3000), (200, 205, 210))
        ImageDraw.Draw(im).ellipse((1200, 800, 2800, 2200), fill=(120, 60, 50))
        data = to_bytes(im, 'JPEG', quality=88)
        t = time.time()
        self.check_standard(data)
        self.assertLess(time.time() - t, 6.0)                                      # 1200 万像素图的处理耗时在可接受范围

    def test_non_uniform_background_is_not_trimmed(self):
        im = Image.new('RGB', (600, 300))
        for x in range(600):
            for y in range(0, 300, 5):
                im.putpixel((x, y), (x % 256, (x * 2) % 256, 120))
        im = im.resize((600, 300))
        out = self.check_standard(to_bytes(im, 'PNG'))
        box, size = subject_box(out['main'], tol=10)
        self.assertTrue((box[2] - box[0]) > 1300)                                    # 整幅图等比放入，没被误裁

    def test_transparent_background_becomes_white(self):
        out = self.check_standard(png_bytes(200, 200, box=(60, 60, 140, 140), alpha=True))
        im = Image.open(io.BytesIO(out['main'])).convert('RGB')
        self.assertTrue(all(c >= 250 for c in im.getpixel((5, 5))))                 # 透明处是白，不是黑
        box, size = subject_box(out['main'])
        self.assertTrue(is_centered(box, size))

    def test_all_common_formats_and_modes(self):
        base = Image.open(io.BytesIO(PNG_RED_BOX)).convert('RGB')
        cases = {
            'jpeg': to_bytes(base, 'JPEG'), 'webp': to_bytes(base, 'WEBP'), 'bmp': to_bytes(base, 'BMP'),
            'tiff': to_bytes(base, 'TIFF'), 'gif': to_bytes(base.convert('P'), 'GIF'),
            'png-palette': to_bytes(base.convert('P'), 'PNG'), 'png-gray': to_bytes(base.convert('L'), 'PNG'),
            'jpeg-cmyk': to_bytes(base.convert('CMYK'), 'JPEG'), 'png-LA': to_bytes(base.convert('LA'), 'PNG'),
            'png-16bit-gray': to_bytes(base.convert('L').convert('I').point(lambda p: p * 257), 'PNG'),
        }
        for name, data in cases.items():
            out = self.check_standard(data)
            box, size = subject_box(out['main'], tol=30)
            self.assertIsNotNone(box, name)
            self.assertTrue(is_centered(box, size), name)

    def test_animated_gif_uses_first_frame(self):
        f1 = Image.new('RGB', (100, 100), 'white'); f1.paste((200, 0, 0), (30, 30, 70, 70))
        f2 = Image.new('RGB', (100, 100), 'white'); f2.paste((0, 0, 200), (30, 30, 70, 70))
        out = self.check_standard(to_bytes(f1, 'GIF', save_all=True, append_images=[f2], duration=100, loop=0))
        im = Image.open(io.BytesIO(out['main'])).convert('RGB')
        r, g, b = im.getpixel((800, 800))
        self.assertTrue(r > 150 and b < 80, (r, g, b))

    def test_exif_orientation_applied(self):
        im = Image.new('RGB', (300, 150), 'white'); im.paste((200, 0, 0), (10, 40, 290, 110))      # 横向长条
        exif = Image.Exif(); exif[0x0112] = 6                                                     # 需顺时针转 90° 才正
        out = self.check_standard(to_bytes(im, 'JPEG', exif=exif))
        box, _ = subject_box(out['main'])
        self.assertTrue((box[3] - box[1]) > (box[2] - box[0]) * 2)                               # 转正后变成竖条

    def test_resolution_flag(self):
        self.assertTrue(imaging.process(png_bytes(40, 30, box=(10, 8, 30, 22)))['low_res'])
        self.assertFalse(imaging.process(to_bytes(Image.new('RGB', (2400, 1600), (200, 40, 40)), 'JPEG'))['low_res'])

    def test_all_white_and_tiny_and_huge_ratio(self):
        self.check_standard(png_bytes(200, 200))                                    # 纯白图：不崩
        self.check_standard(png_bytes(8, 8, box=(2, 2, 6, 6)))                      # 最小尺寸
        self.check_standard(png_bytes(3000, 40, box=(100, 5, 2900, 35)))             # 极端长宽比
        self.check_standard(png_bytes(40, 3000, box=(5, 100, 35, 2900)))

    def test_junk_rejected_with_clear_message(self):
        from sinlux.core.util import ApiError
        for data in (b'', b'not an image', b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', b'<html><script>alert(1)</script></html>',
                     PNG_RED_BOX[:80], b'\x89PNG\r\n\x1a\n' + b'\x00' * 50):
            with self.assertRaises(ApiError) as cm:
                imaging.process(data)
            self.assertIn('无法识别', cm.exception.message)
        with self.assertRaises(ApiError):
            imaging.process(png_bytes(4, 4))                                        # < 8px

    def test_decompression_bomb_guard(self):
        from sinlux.core.util import ApiError
        old = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = 1000
        try:
            with self.assertRaises(ApiError) as cm:
                imaging.process(png_bytes(100, 100))
            self.assertIn('像素太大', cm.exception.message)
        finally:
            Image.MAX_IMAGE_PIXELS = old


class TestGalleryApi(AppTestCase):
    def upload(self, data, name='x.png'):
        return self.c.j('POST', '/api/images/upload', raw=data, headers={'X-Filename': urllib.parse.quote(name)})

    def path(self, name):
        return os.path.join(self.ctx.uploads_dir, name)

    @needs_pil
    def test_upload_returns_normalized_image_and_serves_it(self):
        st, r = self.upload(PNG_RED_BOX)
        self.assertEqual(st, 200, r)
        img = r['image']
        self.assertTrue(img['normalized'] and img['file'].endswith('.jpg') and img['thumb'].endswith('_s.jpg'))
        self.assertEqual((img['width'], img['height']), (1600, 1600))
        st, raw, h = self.c.call('GET', img['url'])
        self.assertEqual((st, h['Content-Type']), (200, 'image/jpeg'))
        self.assertEqual(Image.open(io.BytesIO(raw)).size, (1600, 1600))
        st, raw, h = self.c.call('GET', img['thumb_url'])
        self.assertEqual(Image.open(io.BytesIO(raw)).size, (480, 480))
        self.assertEqual(self.ctx.db.one('SELECT product_id FROM product_images WHERE id=?', (img['id'],))['product_id'], None)  # 暂存

    def test_bad_uploads_leave_nothing_behind(self):
        before = set(os.listdir(self.ctx.uploads_dir))
        n = self.ctx.db.scalar('SELECT COUNT(*) FROM product_images')
        for data in (b'', b'junk bytes', b'<svg></svg>', b'MZ\x90\x00this is an exe'):
            st, r = self.upload(data)
            self.assertEqual(st, 400, data)
            self.assertIn('error', r)
        self.assertEqual(set(os.listdir(self.ctx.uploads_dir)), before)
        self.assertEqual(self.ctx.db.scalar('SELECT COUNT(*) FROM product_images'), n)

    @needs_pil
    def test_create_with_images_order_primary_and_mirror(self):
        a = self.upload(PNG_RED_BOX)[1]['image']
        b = self.upload(png_bytes(200, 200, box=(50, 50, 150, 150), box_color=(0, 100, 200)))[1]['image']
        pid = self.new_product(images=[a['id'], b['id']])
        p = self.c.get('/api/products/%d' % pid)[1]['product']
        self.assertEqual([i['id'] for i in p['images']], [a['id'], b['id']])
        self.assertEqual([i['is_primary'] for i in p['images']], [True, False])
        self.assertEqual((p['image_path'], p['image_url'], p['thumb_url']), ('uploads/' + a['file'], a['url'], a['thumb_url']))
        self.assertEqual(p['image_count'], 2)
        # 调整顺序：b 变主图
        self.c.put('/api/products/%d' % pid, {'images': [b['id'], a['id']]})
        p = self.c.get('/api/products/%d' % pid)[1]['product']
        self.assertEqual((p['images'][0]['id'], p['image_path']), (b['id'], 'uploads/' + b['file']))
        # 删除 a：文件和缩略图都清掉
        self.c.put('/api/products/%d' % pid, {'images': [b['id']]})
        self.assertFalse(os.path.exists(self.path(a['file'])) or os.path.exists(self.path(a['thumb'])))
        self.assertTrue(os.path.exists(self.path(b['file'])))
        # 不传 images：相册不变
        self.c.put('/api/products/%d' % pid, {'name': 'renamed only'})
        self.assertEqual(len(self.c.get('/api/products/%d' % pid)[1]['product']['images']), 1)
        # 清空
        self.c.put('/api/products/%d' % pid, {'images': []})
        p = self.c.get('/api/products/%d' % pid)[1]['product']
        self.assertEqual((p['images'], p['image_path'], p['image_url']), ([], None, ''))
        self.assertFalse(os.path.exists(self.path(b['file'])))

    @needs_pil
    def test_cannot_steal_or_reuse_images(self):
        a = self.upload(PNG_RED_BOX)[1]['image']
        p1 = self.new_product(images=[a['id']])
        p2 = self.new_product()
        st, r = self.c.put('/api/products/%d' % p2, {'images': [a['id']]})
        self.assertEqual(st, 400)
        self.assertIn('属于其他产品', r['error'])
        self.assertEqual(self.c.put('/api/products/%d' % p2, {'images': [987654]})[0], 404)
        self.assertEqual(self.c.put('/api/products/%d' % p2, {'images': ['x']})[0], 400)
        self.assertEqual(self.c.put('/api/products/%d' % p2, {'images': 'nope'})[0], 400)
        b = self.upload(PNG_RED_BOX)[1]['image']
        self.assertEqual(self.c.put('/api/products/%d' % p2, {'images': [b['id'], b['id']]})[0], 400)
        self.assertEqual(len(self.c.get('/api/products/%d' % p1)[1]['product']['images']), 1)      # p1 没被影响
        self.assertTrue(os.path.exists(self.path(a['file'])))

    @needs_pil
    def test_limit_and_failed_save_does_not_lose_images(self):
        ids = [self.upload(png_bytes(60, 60, box=(10, 10, 50, 50)))[1]['image']['id'] for _ in range(21)]
        st, r = self.c.post('/api/products', {'sku': 'LIM-1', 'name': 'x', 'category_id': self.cat_id('other'), 'images': ids})
        self.assertEqual(st, 400)
        self.assertIn('最多 20 张', r['error'])
        self.assertEqual(self.ctx.db.scalar("SELECT COUNT(*) FROM products WHERE sku='LIM-1'"), 0)
        self.assertEqual(self.c.post('/api/products', {'sku': 'LIM-2', 'name': 'x', 'category_id': self.cat_id('other'), 'images': ids[:20]})[0], 200)

    @needs_pil
    def test_legacy_image_data_and_remove_image_still_work(self):
        pid = self.new_product(image_data=data_url(PNG_RED_BOX))
        p = self.c.get('/api/products/%d' % pid)[1]['product']
        self.assertEqual(len(p['images']), 1)
        old = p['images'][0]['file']
        self.c.put('/api/products/%d' % pid, {'image_data': data_url(png_bytes(80, 80, box=(10, 10, 60, 60)))})
        p = self.c.get('/api/products/%d' % pid)[1]['product']
        self.assertEqual(len(p['images']), 1)                                            # 旧语义：替换
        self.assertFalse(os.path.exists(self.path(old)))
        self.c.put('/api/products/%d' % pid, {'remove_image': True})
        self.assertEqual(self.c.get('/api/products/%d' % pid)[1]['product']['images'], [])
        for bad in ('data:image/svg+xml;base64,AAAA', 'data:image/png;base64,', 'data:image/png;base64,bm90IGFuIGltYWdl'):
            self.assertEqual(self.c.put('/api/products/%d' % pid, {'image_data': bad})[0], 400, bad)

    @needs_pil
    def test_stale_staged_images_cleaned_but_claimed_ones_kept(self):
        a = self.upload(PNG_RED_BOX)[1]['image']
        b = self.upload(PNG_RED_BOX)[1]['image']
        self.new_product(images=[b['id']])
        self.ctx.db.execute("UPDATE product_images SET created_at='2020-01-01 00:00:00' WHERE id IN (?,?)", (a['id'], b['id']))
        self.assertEqual(self.ctx.media.cleanup_staged(), 1)
        self.assertFalse(os.path.exists(self.path(a['file'])))
        self.assertTrue(os.path.exists(self.path(b['file'])))

    @needs_pil
    def test_delete_product_removes_all_image_files(self):
        a = self.upload(PNG_RED_BOX)[1]['image']
        pid = self.new_product(images=[a['id']])
        self.c.delete('/api/products/%d' % pid, {'confirm': True})
        self.assertFalse(os.path.exists(self.path(a['file'])) or os.path.exists(self.path(a['thumb'])))
        self.assertEqual(self.ctx.db.scalar('SELECT COUNT(*) FROM product_images WHERE id=?', (a['id'],)), 0)

    @needs_pil
    def test_duplicate_copies_images_independently(self):
        a = self.upload(PNG_RED_BOX)[1]['image']
        lt = {f['key']: f['id'] for f in self.c.get('/api/categories/%d/fields' % self.cat_id('lighting'))[1]['fields']}
        src = self.new_product(sku='DUP-SRC', name='Source', cost=10, cost_currency='CNY', brand='Acme', series='S1', spec_text='规格',
                               field_values={str(lt['wattage']): '9'}, images=[a['id']])
        st, r = self.c.post('/api/products/%d/duplicate' % src, {'sku': 'DUP-COPY'})
        self.assertEqual(st, 200, r)
        cp = self.c.get('/api/products/%d' % r['id'])[1]['product']
        self.assertEqual((cp['sku'], cp['name'], cp['brand'], cp['series'], cp['spec_text'], cp['cost']), ('DUP-COPY', 'Source', 'Acme', 'S1', '规格', 10.0))
        self.assertEqual(cp['field_values'], {str(lt['wattage']): '9'})
        self.assertEqual(len(cp['images']), 1)
        self.assertNotEqual(cp['images'][0]['file'], a['file'])
        self.assertTrue(os.path.exists(self.path(cp['images'][0]['file'])))
        h = self.c.get('/api/products/%d/price_history' % r['id'])[1]['history']
        self.assertEqual([x['source'] for x in h], ['复制自 DUP-SRC'])                  # 新产品只有一条起始成本，不继承历史
        self.c.delete('/api/products/%d' % src, {'confirm': True})
        self.assertTrue(os.path.exists(self.path(cp['images'][0]['file'])))            # 删原件不影响副本的图
        self.assertEqual(self.c.post('/api/products/%d/duplicate' % r['id'], {'sku': 'DUP-COPY'})[0], 409)
        self.assertEqual(self.c.post('/api/products/%d/duplicate' % r['id'], {'sku': ' '})[0], 400)
        self.assertEqual(self.c.post('/api/products/987654/duplicate', {'sku': 'Z'})[0], 404)


class TestNoPillowFallback(AppTestCase):
    """Pillow 不可用（没装/不匹配）时：原样保存，不崩；页面用 CSS 居中显示。"""

    def test_fallback_stores_original_and_marks_unnormalized(self):
        old = imaging._OK
        imaging._OK = False
        try:
            st, r = self.c.j('POST', '/api/images/upload', raw=PNG_RED_BOX)
            self.assertEqual(st, 200, r)
            img = r['image']
            self.assertEqual((img['normalized'], img['file'].endswith('.png'), img['thumb']), (False, True, ''))
            self.assertEqual(img['thumb_url'], img['url'])
            self.assertEqual(self.c.j('POST', '/api/images/upload', raw=b'junk')[0], 400)
            self.assertEqual(self.c.j('POST', '/api/images/upload', raw=b'<svg/>')[0], 400)
            self.assertEqual(self.ctx.media.normalize(), {'done': 0, 'failed': [], 'no_pillow': True})
        finally:
            imaging._OK = old


@needs_pil
class TestNormalizeLegacy(AppTestCase):
    legacy = staticmethod(lambda d: build_legacy_db(d, 5))

    def test_legacy_single_image_in_gallery_then_normalized(self):
        pid = self.ctx.db.one("SELECT id FROM products WHERE sku='SL-001'")['id']
        p = self.c.get('/api/products/%d' % pid)[1]['product']
        self.assertEqual((len(p['images']), p['images'][0]['normalized'], p['images'][0]['file']), (1, False, 'legacyimg1.png'))
        self.assertEqual(p['image_url'], '/uploads/legacyimg1.png')                  # 没统一前也能显示（前端 CSS 居中）
        st, r = self.c.post('/api/products/normalize_images')
        self.assertEqual((st, r['done'], r['failed'], r['no_pillow']), (200, 1, [], False))
        p = self.c.get('/api/products/%d' % pid)[1]['product']
        img = p['images'][0]
        self.assertTrue(img['normalized'] and img['file'].endswith('.jpg'))
        self.assertEqual(p['image_path'], 'uploads/' + img['file'])
        self.assertFalse(os.path.exists(os.path.join(self.ctx.uploads_dir, 'legacyimg1.png')))      # 旧文件已被替换
        with open(os.path.join(self.ctx.uploads_dir, img['file']), 'rb') as fh:
            box, size = subject_box(fh.read())
        self.assertTrue(is_centered(box, size))
        self.assertEqual(self.c.post('/api/products/normalize_images')[1]['done'], 0)               # 幂等

    def test_missing_file_reported_not_fatal(self):
        pid = self.ctx.db.one("SELECT id FROM products WHERE sku='SL-002'")['id']
        self.ctx.db.execute("INSERT INTO product_images(product_id,file,normalized,sort_order) VALUES(?,?,0,0)", (pid, 'ghost.png'))
        r = self.c.post('/api/products/%d/images/normalize' % pid)[1]
        self.assertEqual((r['done'], len(r['failed'])), (0, 1))


class TestFiles(AppTestCase):
    def up(self, pid, name, data=b'%PDF-1.4 fake', kind=''):
        q = '?kind=' + urllib.parse.quote(kind) if kind else ''
        return self.c.j('POST', '/api/products/%d/files%s' % (pid, q), raw=data, headers={'X-Filename': urllib.parse.quote(name)})

    def test_upload_list_download_delete(self):
        pid = self.new_product()
        st, r = self.up(pid, '防火认证 NFPA701.pdf', b'%PDF-1.4 hello', '认证')
        self.assertEqual(st, 200, r)
        f = r['file']
        self.assertEqual((f['name'], f['kind'], f['size']), ('防火认证 NFPA701.pdf', '认证', 14))
        self.assertTrue(os.path.exists(os.path.join(self.ctx.files_dir, f['file'])))
        self.assertNotIn('防火', f['file'])                                          # 落盘名不用原名，避免重名/特殊字符
        st, raw, h = self.c.call('GET', f['url'])
        self.assertEqual((st, raw), (200, b'%PDF-1.4 hello'))
        self.assertIn('attachment', h['Content-Disposition'])                        # 强制下载，不在浏览器内渲染
        self.assertEqual([x['name'] for x in self.c.get('/api/products/%d' % pid)[1]['product']['files']], ['防火认证 NFPA701.pdf'])
        self.assertEqual(self.c.delete('/api/product_files/%d' % f['id'])[0], 200)
        self.assertFalse(os.path.exists(os.path.join(self.ctx.files_dir, f['file'])))
        self.assertEqual(self.c.call('GET', f['url'])[0], 404)
        self.assertEqual(self.c.delete('/api/product_files/%d' % f['id'])[0], 404)

    def test_type_and_name_validation(self):
        pid = self.new_product()
        for name in ('virus.exe', 'page.html', 'x.js', 'noext', 'a.svg', 'evil.php', 'x.bat'):
            st, r = self.up(pid, name)
            self.assertEqual(st, 400, name)
        st, r = self.up(pid, '../../etc/passwd.pdf')                                  # 路径穿越被清洗
        self.assertEqual(st, 200)
        self.assertEqual(r['file']['name'], 'passwd.pdf')
        self.assertEqual(os.path.dirname(os.path.join(self.ctx.files_dir, r['file']['file'])), self.ctx.files_dir)
        self.assertEqual(self.up(pid, 'ok.pdf', b'')[0], 400)
        self.assertEqual(self.up(987654, 'ok.pdf')[0], 404)
        self.assertEqual(self.up(pid, 'k.pdf', b'x', 'weird-kind')[1]['file']['kind'], '其他')

    def test_download_only_registered_files_no_traversal(self):
        for p in ('/files/..%2fcrm.db', '/files/crm.db', '/files/nothere.pdf'):
            st, raw, _ = self.c.call('GET', p)
            self.assertEqual(st, 404, p)
            self.assertNotIn(b'SQLite format', raw)

    def test_files_follow_merge_and_delete(self):
        a, b = self.new_product(), self.new_product()
        fb = self.up(b, 'spec.pdf')[1]['file']
        self.c.post('/api/products/merge', {'survivor_id': a, 'merge_ids': [b]})
        self.assertEqual([x['name'] for x in self.c.get('/api/products/%d' % a)[1]['product']['files']], ['spec.pdf'])
        self.c.delete('/api/products/%d' % a, {'confirm': True})
        self.assertFalse(os.path.exists(os.path.join(self.ctx.files_dir, fb['file'])))


if __name__ == '__main__':
    unittest.main()
