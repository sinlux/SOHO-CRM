# -*- coding: utf-8 -*-
"""报价单输出：PDF (reportlab)、Excel (openpyxl，金额用公式)、WhatsApp 文案。"""
import io
import os
import re
from xml.sax.saxutils import escape

from ..core.util import ApiError
from .settings import render_template

BRAND = '#173C37'          # 与 LOGO 的深绿一致
ACCENT = '#2D8CC8'         # 与 LOGO 的蓝色一致
_FONTS = {}
FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'static', 'fonts', 'nunito')
_CJK = re.compile(r'([\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f]+)')


def _fonts():
    """返回字体名 dict：regular/bold/title(Nunito，圆润的无衬线，随程序打包) 和 cjk/cjkb（中文回退字体）。
    英文数字用 Nunito；中文字符自动切到系统中文字体（微软雅黑 > 黑体 > 宋体 > Linux Noto/WQY > reportlab 自带 CID 宋体）。"""
    if _FONTS:
        return _FONTS
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    def reg(name, path, idx=None):
        try:
            pdfmetrics.registerFont(TTFont(name, path, subfontIndex=idx) if idx is not None else TTFont(name, path))
            return name
        except Exception:
            return None
    cjk = [('MSYaHei', r'C:\Windows\Fonts\msyh.ttc', 0), ('MSYaHei', r'C:\Windows\Fonts\msyh.ttf', None),
           ('SimHei', r'C:\Windows\Fonts\simhei.ttf', None), ('SimSun', r'C:\Windows\Fonts\simsun.ttc', 0),
           ('Noto', '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', 0),
           ('WQY', '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc', 0), ('WQY', '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc', 0)]
    cjkb = [('MSYaHeiB', r'C:\Windows\Fonts\msyhbd.ttc', 0), ('MSYaHeiB', r'C:\Windows\Fonts\msyhbd.ttf', None),
            ('SimHeiB', r'C:\Windows\Fonts\simhei.ttf', None),
            ('NotoB', '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc', 0)]

    def first(cands):
        for name, path, idx in cands:
            if os.path.exists(path) and reg(name, path, idx):
                return name
        return None
    c = first(cjk)
    if not c:
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        c = 'STSong-Light'
    cb = first(cjkb) or c
    latin = {}
    for key, name, file in (('regular', 'Nunito-Medium', 'Nunito_500Medium.ttf'), ('semibold', 'Nunito-SemiBold', 'Nunito_600SemiBold.ttf'),
                            ('bold', 'Nunito-Bold', 'Nunito_700Bold.ttf'), ('title', 'Nunito-ExtraBold', 'Nunito_800ExtraBold.ttf')):
        pth = os.path.join(FONT_DIR, file)
        latin[key] = reg(name, pth) if os.path.exists(pth) else None
    _FONTS.update({'cjk': c, 'cjkb': cb, 'regular': latin['regular'] or c, 'semibold': latin['semibold'] or latin['bold'] or c,
                   'bold': latin['bold'] or cb, 'title': latin['title'] or latin['bold'] or cb, 'rounded': bool(latin['regular'])})
    return _FONTS


def _mix(html, cjk):
    """给已转义的段落文本里的中文片段套上中文字体（Nunito 没有汉字）。"""
    return _CJK.sub(lambda m: '<font name="%s">%s</font>' % (cjk, m.group(1)), html)


def _para(text, limit=None):
    """用户文本 → reportlab 段落：转义 & < >，换行变 <br/>。"""
    t = (text or '').strip()
    if limit and len(t) > limit:
        t = t[:limit].rstrip() + '…'
    return escape(t).replace('\r\n', '\n').replace('\n', '<br/>')


XFONT = 'Nunito'      # Excel 里的字体名：电脑装了 Nunito 就用它，没装 Excel 自动换成默认字体，不影响数据


class QuoteExporter:
    def __init__(self, db, settings, exports_dir, uploads_dir):
        self.db = db
        self.settings = settings
        self.exports_dir = exports_dir
        self.uploads_dir = uploads_dir

    def _image_path(self, product_id):
        """产品主图的缩略图（480px，体积小）；没有缩略图用原图；找不到返回 None。"""
        if not product_id:
            return None
        r = self.db.one('SELECT file, thumb FROM product_images WHERE product_id=? ORDER BY sort_order, id LIMIT 1', (product_id,))
        for name in ((r['thumb'], r['file']) if r else ()):
            if name:
                p = os.path.join(self.uploads_dir, os.path.basename(name))
                if os.path.exists(p):
                    return p
        return None

    @staticmethod
    def _safe_name(quote_no, ext):
        return re.sub(r'[^\w\-.]', '_', quote_no) + '.' + ext

    # ---------- PDF ----------
    def pdf(self, q):
        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import ParagraphStyle
            from reportlab.lib.units import mm
            from reportlab.pdfgen import canvas as rl_canvas
            from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        except ImportError:
            raise ApiError('缺少 reportlab 库，无法生成 PDF（请确认 app/libs/reportlab 存在）', 500)
        F = _fonts()
        reg, bold, cjk = F['regular'], F['bold'], F['cjk']
        co = self.settings.company()
        fname = self._safe_name(q['quote_no'], 'pdf')
        path = os.path.join(self.exports_dir, fname)
        os.makedirs(self.exports_dir, exist_ok=True)
        brand, accent = colors.HexColor(BRAND), colors.HexColor(ACCENT)
        S = lambda size=9, **kw: ParagraphStyle('s', **{'fontName': reg, 'fontSize': size, 'leading': size * 1.4, **kw})
        P = lambda html, style: Paragraph(_mix(html, cjk), style)
        contact_line = '   |   '.join(b for b in (co['email'], co['phone']) if b)

        class Numbered(rl_canvas.Canvas):          # 页脚：公司联系方式 + Page x / y
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self._saved = []

            def showPage(self):
                self._saved.append(dict(self.__dict__))
                self._startPage()

            def save(self):
                total = len(self._saved)
                for st in self._saved:
                    self.__dict__.update(st)
                    self.setStrokeColor(colors.HexColor('#DDDDDD'))
                    self.line(14 * mm, 13 * mm, A4[0] - 14 * mm, 13 * mm)
                    self.setFillColor(colors.HexColor('#777777'))
                    left = ' | '.join(b for b in (co['name'], co['email'], co['phone']) if b)
                    self.setFont(cjk if _CJK.search(left) else reg, 7.5)
                    self.drawString(14 * mm, 8.5 * mm, left)
                    self.setFont(reg, 7.5)
                    self.drawRightString(A4[0] - 14 * mm, 8.5 * mm, '%s    Page %d / %d' % (q['quote_no'], self._pageNumber, total))
                    super().showPage()
                super().save()

        doc = SimpleDocTemplate(path, pagesize=A4, topMargin=13 * mm, bottomMargin=19 * mm, leftMargin=14 * mm, rightMargin=14 * mm,
                                title='Quotation ' + q['quote_no'], author=co['name'])
        # 抬头：左 LOGO，右联系方式
        logo = ''
        lp = self.settings.logo_path(self.uploads_dir)
        if lp and os.path.exists(lp):
            try:
                from reportlab.lib.utils import ImageReader
                iw, ih = ImageReader(lp).getSize()
                w = 50 * mm
                logo = Image(lp, width=w, height=w * ih / iw)
            except Exception:
                logo = ''
        if not logo:
            logo = P(escape(co['name']), ParagraphStyle('t', fontName=F['title'], fontSize=22, leading=26, textColor=brand))
        right = []
        if co['contact']:
            right.append('<font name="%s">Contact:</font> %s' % (bold, _para(co['contact'])))
        for lab, key in (('Email', 'email'), ('Tel', 'phone')):
            if co[key]:
                right.append('<font name="%s">%s:</font> %s' % (bold, lab, _para(co[key])))
        if co['address']:
            right.append('<font name="%s">Add:</font> %s' % (bold, _para(co['address'])))
        head = Table([[logo, P('<br/>'.join(right), S(8.3, alignment=2, textColor=colors.HexColor('#444444')))]], colWidths=[68 * mm, 108 * mm])
        head.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (0, 0), 0), ('RIGHTPADDING', (-1, 0), (-1, 0), 0),
                                  ('LINEBELOW', (0, 0), (-1, 0), 1.2, brand), ('BOTTOMPADDING', (0, 0), (-1, -1), 7)]))
        story = [head, Spacer(1, 5 * mm), P('QUOTATION', ParagraphStyle('q', fontName=F['title'], fontSize=17, leading=21, textColor=brand)), Spacer(1, 2.5 * mm)]
        B = lambda t: '<font name="%s">%s</font>' % (bold, t)
        left = [B('To:') + ' ' + _para(q['company'] or q['customer_name'])]
        if q['customer_name'] and q['company']:
            left.append(B('Attn:') + ' ' + _para(q['customer_name']))
        for label, key in (('Address', 'address'), ('Country', 'country')):
            if q.get(key):
                left.append('%s %s' % (B(label + ':'), _para(q[key])))
        rt = [B('Quote No.:') + ' ' + escape(q['quote_no']), B('Date:') + ' ' + escape(q['created_at'][:10]),
              B('Valid until:') + ' %s (%d days)' % (escape(q['valid_until']), q['valid_days']), B('Currency:') + ' ' + escape(q['currency'])]
        meta = Table([[P('<br/>'.join(left), S(9.2)), P('<br/>'.join(rt), S(9.2))]], colWidths=[98 * mm, 78 * mm])
        meta.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LINEBELOW', (0, 0), (-1, 0), 0.6, colors.HexColor('#CCCCCC')),
                                  ('BOTTOMPADDING', (0, 0), (-1, -1), 7), ('LEFTPADDING', (0, 0), (0, 0), 0)]))
        story += [meta, Spacer(1, 4 * mm)]

        cur = q['currency']
        hs = S(8, textColor=colors.white, alignment=1, fontName=bold)
        rows = [[P(h, hs) for h in ('#', 'Image', 'SKU', 'Description', 'Qty', 'Unit', 'Price (%s)' % cur, 'Amount (%s)' % cur)]]
        for n, i in enumerate(q['items'], 1):
            desc = '<font name="%s">%s</font>' % (bold, _para(i['name'] or i['sku']))
            if i['spec']:
                desc += '<br/><font size="7.8" color="#555555">%s</font>' % _para(i['spec'], 1500)
            if i['remark']:
                desc += '<br/><font size="7.3" color="#888888">%s</font>' % _para(i['remark'], 300)
            img = ''
            ip = self._image_path(i['product_id'])
            if ip:
                try:
                    img = Image(ip, width=17 * mm, height=17 * mm, kind='proportional')
                except Exception:
                    img = ''
            rows.append([str(n), img, P(escape(i['sku'] or ''), S(8)), P(desc, S(8.7)), '%g' % i['quantity'], P(escape(i['unit'] or ''), S(8, alignment=1)),
                         '{:,.2f}'.format(i['unit_price']), '{:,.2f}'.format(i['amount'])])
        rows.append(['', '', '', '', '', '', 'TOTAL', '{:,.2f}'.format(q['total'])])
        t = Table(rows, colWidths=[7 * mm, 19 * mm, 23 * mm, 61 * mm, 11 * mm, 11 * mm, 22 * mm, 22 * mm], repeatRows=1, splitInRow=1)
        t.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), reg), ('FONTSIZE', (0, 0), (-1, -1), 8.5), ('FONTNAME', (0, 0), (-1, 0), bold),
            ('BACKGROUND', (0, 0), (-1, 0), brand), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('GRID', (0, 0), (-1, -2), 0.4, colors.HexColor('#D8D8D8')), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (4, 0), (-1, -1), 'RIGHT'), ('ALIGN', (0, 0), (0, -1), 'CENTER'), ('ALIGN', (1, 0), (1, -1), 'CENTER'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#F5F8F7')]),
            ('FONTNAME', (6, -1), (-1, -1), F['title']), ('FONTSIZE', (6, -1), (-1, -1), 10.5), ('TEXTCOLOR', (6, -1), (-1, -1), brand),
            ('LINEABOVE', (0, -1), (-1, -1), 1, brand), ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
        story += [t, Spacer(1, 5 * mm)]
        for label, key in (('Lead time', 'lead_time'), ('Payment terms', 'payment_terms'), ('Trade terms', 'shipping_terms'), ('Notes', 'notes')):
            if q.get(key):
                story.append(P('%s %s' % (B(label + ':'), _para(q[key])), S(9.2)))
        if co['bank']:
            ps = S(8.8)
            brow = [[P(B('Payment Information'), S(9.5, textColor=brand)), '']] + \
                   [[P(B(lab), ps), P(_para(val), ps)] for lab, val in co['bank']]
            bt = Table(brow, colWidths=[34 * mm, 142 * mm])
            bt.setStyle(TableStyle([('SPAN', (0, 0), (1, 0)), ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F5F8F7')),
                                    ('LINEBELOW', (0, 0), (-1, 0), 0.8, brand), ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#CFD9D6')),
                                    ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 2.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
                                    ('LEFTPADDING', (0, 0), (-1, -1), 7)]))
            story += [Spacer(1, 5 * mm), KeepTogether([bt])]
        story += [Spacer(1, 6 * mm), P('Thank you for your business!', S(10, textColor=accent, fontName=bold))]
        doc.build(story, canvasmaker=Numbered)
        return fname

    # ---------- Excel ----------
    def excel(self, q):
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        co = self.settings.company()
        fname = self._safe_name(q['quote_no'], 'xlsx')
        os.makedirs(self.exports_dir, exist_ok=True)
        wb = Workbook()
        ws = wb.active
        ws.title = 'Quotation'
        thin = Side(style='thin', color='D8D8D8')
        border = Border(left=thin, right=thin, top=thin, bottom=thin)
        green = '173C37'

        def put(ref, value, **font):
            c = ws[ref]
            c.value = value
            if isinstance(value, str) and value.startswith('='):
                c.data_type = 's'                  # 用户文本里以 = 开头的，当文本存，不当公式
            if font:
                c.font = Font(name=XFONT, **font)
            return c
        ws.row_dimensions[1].height = 48
        lp = self.settings.logo_path(self.uploads_dir)
        has_logo = False
        try:
            from openpyxl.drawing.image import Image as XImg0
            from PIL import Image as PImg0
            with PImg0.open(lp) as im0:
                lw, lh = im0.size
            xl = XImg0(lp)
            xl.width, xl.height = 150, round(150 * lh / lw)
            ws.add_image(xl, 'A1')
            ws.row_dimensions[1].height = max(48, xl.height * 0.75 + 4)
            has_logo = True
        except Exception:
            pass
        if not has_logo:
            ws.merge_cells('A1:E1'); put('A1', co['name'], bold=True, size=18, color=green)
        ws.merge_cells('F1:I1')
        put('F1', '\n'.join(b for b in (('Contact: ' + co['contact']) if co['contact'] else '', ('Email: ' + co['email']) if co['email'] else '',
                                        ('Tel: ' + co['phone']) if co['phone'] else '', ('Add: ' + co['address']) if co['address'] else '') if b),
            size=9, color='444444')
        ws['F1'].alignment = Alignment(horizontal='right', vertical='center', wrap_text=True)
        ws.merge_cells('A3:I3'); put('A3', 'QUOTATION', bold=True, size=15, color=green)
        put('A5', 'To: %s' % (q['company'] or q['customer_name'] or ''), bold=True)
        put('A6', 'Attn: %s' % (q['customer_name'] or '')); put('A7', 'Country: %s' % (q['country'] or ''))
        put('G5', 'Quote No.: %s' % q['quote_no']); put('G6', 'Date: %s' % q['created_at'][:10])
        put('G7', 'Valid until: %s (%d days)' % (q['valid_until'], q['valid_days']))
        hdr = 9
        heads = ['#', 'Image', 'SKU', 'Description', 'Specification', 'Qty', 'Unit', 'Price (%s)' % q['currency'], 'Amount (%s)' % q['currency']]
        for c, h in enumerate(heads, 1):
            cell = ws.cell(row=hdr, column=c, value=h)
            cell.font = Font(name=XFONT, bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', start_color=green)
            cell.border = border
            cell.alignment = Alignment(horizontal='center', vertical='center')
        r = hdr + 1
        try:
            from openpyxl.drawing.image import Image as XImage
            from PIL import Image as PImage
        except Exception:
            XImage = PImage = None
        for n, i in enumerate(q['items'], 1):
            vals = [n, None, i['sku'], i['name'], i['spec'], i['quantity'], i['unit'], i['unit_price'], None]
            for c, v in enumerate(vals, 1):
                cell = ws.cell(row=r, column=c, value=v)
                if isinstance(v, str) and v.startswith('='):
                    cell.data_type = 's'
                cell.border = border
                cell.font = Font(name=XFONT, size=10)
                cell.alignment = Alignment(vertical='center', wrap_text=c in (4, 5))
            ws.cell(row=r, column=9, value='=ROUND(F%d*H%d,2)' % (r, r)).border = border      # 逐行四舍五入，合计与 PDF 一致
            for c in (8, 9):
                ws.cell(row=r, column=c).number_format = '#,##0.00'
            ws.row_dimensions[r].height = 52
            ip = self._image_path(i['product_id'])
            if ip and XImage:
                try:
                    buf = io.BytesIO()
                    with PImage.open(ip) as im:
                        im = im.convert('RGB'); im.thumbnail((120, 120)); im.save(buf, 'PNG')
                    buf.seek(0)
                    xi = XImage(buf); xi.width, xi.height = 60, 60
                    ws.add_image(xi, 'B%d' % r)
                except Exception:
                    pass
            r += 1
        ws.cell(row=r, column=8, value='TOTAL').font = Font(name=XFONT, bold=True)
        tc = ws.cell(row=r, column=9, value='=SUM(I%d:I%d)' % (hdr + 1, r - 1))
        tc.font = Font(name=XFONT, bold=True, color=green); tc.number_format = '#,##0.00'; tc.border = border
        r += 2
        for label, key in (('Lead time', 'lead_time'), ('Payment terms', 'payment_terms'), ('Trade terms', 'shipping_terms'), ('Notes', 'notes')):
            if q.get(key):
                c = ws.cell(row=r, column=1, value='%s: %s' % (label, q[key]))
                if q[key].startswith('='):
                    c.data_type = 's'
                r += 1
        if co['bank']:
            r += 1
            ws.merge_cells('A%d:I%d' % (r, r))
            c = ws.cell(row=r, column=1, value='Payment Information')
            c.font = Font(name=XFONT, bold=True, color=green, size=11)
            c.fill = PatternFill('solid', start_color='F5F8F7')
            r += 1
            for lab, val in co['bank']:
                ws.merge_cells('A%d:C%d' % (r, r)); ws.merge_cells('D%d:I%d' % (r, r))
                a = ws.cell(row=r, column=1, value=lab); a.font = Font(name=XFONT, bold=True, size=10)
                b = ws.cell(row=r, column=4, value=val); b.font = Font(name=XFONT, size=10); b.alignment = Alignment(wrap_text=True, vertical='top')
                b.data_type = 's'
                for cc in (1, 4):
                    ws.cell(row=r, column=cc).fill = PatternFill('solid', start_color='F5F8F7')
                r += 1
        for col, w in zip('ABCDEFGHI', [5, 11, 16, 34, 40, 8, 8, 14, 14]):
            ws.column_dimensions[col].width = w
        ws.freeze_panes = 'A%d' % (hdr + 1)
        ws.page_setup.orientation = 'landscape'
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        wb.save(os.path.join(self.exports_dir, fname))
        return fname

    # ---------- WhatsApp ----------
    def whatsapp(self, q):
        lines = []
        for i in q['items']:
            spec = re.sub(r'\s+', ' ', i['spec'] or '').strip()
            spec = ' (%s)' % (spec[:80] + ('…' if len(spec) > 80 else '')) if spec else ''
            lines.append('- %s %s%s: %g %s × %s %g = %s %.2f' % (i['sku'], i['name'], spec, i['quantity'], i['unit'], q['currency'],
                                                               i['unit_price'], q['currency'], i['amount']))
        values = {'customer_name': q['customer_name'] or q['company'] or 'there', 'company': q['company'] or '', 'quote_no': q['quote_no'],
                  'items': '\n'.join(lines), 'currency': q['currency'], 'total': '%.2f' % q['total'], 'lead_time': q['lead_time'] or 'TBD',
                  'payment_terms': q['payment_terms'] or 'TBD', 'valid_days': q['valid_days'], 'valid_until': q['valid_until'],
                  'shipping_terms': q['shipping_terms'] or 'TBD', 'notes': q['notes'] or '', 'sender': self.settings.company()['name']}
        return render_template(self.settings.template(), values)
