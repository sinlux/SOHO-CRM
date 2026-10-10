# -*- coding: utf-8 -*-
"""自动识别 PI / 供应商报价单 / 产品清单（xls、xlsx 都行），不依赖固定模板：
  1. 找「表头行」：前 60 行里，能对上最多已知列名（品名/型号/数量/单价/金额/采购价/图片…中英文）的那一行；
  2. 按列名把每一列认成一个字段，认不出的列全部并入规格描述（带列名，不丢信息）；
  3. 表头上方的区域找 PI 号 / 日期 / 买家 / 联系人 / 邮箱 / 电话 / 地址；
  4. 判断文件类型：有买家信息 → 客户 PI；价格是人民币且没有买家 → 供应商报价单；否则 → 产品清单；
  5. 识别不准的，界面上可以手动指定表头行和各列（reparse），不会因为"格式不对"就导不进去。
只做「识别」，不写库。写入由 DocImporter.apply 完成。"""
import datetime as _dt
import re

# 字段 → 表头关键词。先"整格相等"再"包含"；顺序即优先级（越靠前越先认，认过的列不重复认）。
FIELDS = [
    ('image', ('picture', 'image', 'photo', 'pic', 'img', '图片', '图像', '产品图', '照片')),
    ('cost_total', ('rmb', 'cny', '人民币', '采购总价', '采购金额', '成本合计', '采购成本', '成本总价', '采购额')),
    ('cost_unit', ('采购单价', '采购价', '成本单价', '成本价', '工厂价', '出厂价', 'unit cost', 'cost/pcs', 'cost price', 'factory price', 'purchase price', 'ex-factory', 'exw price')),
    ('amount', ('amount', 'total amount', 'total price', 'line total', 'sub total', 'subtotal', 'total', '金额', '总价', '合计', '总金额', '小计')),
    ('price', ('unit price', 'price/pcs', 'price/pc', 'price per', 'price', 'usd', 'fob', 'cif', 'cfr', 'ddp', '单价', '价格', '报价', '含税价', '售价', '美金')),
    ('qty', ('quantity', 'qty', 'q\'ty', 'pcs', '数量', '订购数量', '件数')),
    ('unit', ('unit', 'uom', '单位')),
    ('moq', ('moq', '起订量', '最小起订', '最低起订', 'min order')),
    ('sku', ('item code', 'product code', 'part no', 'part number', 'model no', 'model', 'sku', 'code', 'article', 'item no.', 'ref', 'reference', '款号', '货号', '型号', '产品编号', '编码', '料号', '产品代码')),
    ('name', ('product name', 'item name', 'description of goods', 'description', 'product', 'item', 'name', 'goods', '品名', '产品名称', '商品名称', '名称', '产品', '商品', '货物描述', '品类')),
    ('spec', ('parameters', 'parameter', 'specification', 'specifications', 'specs', 'spec', 'details', 'detail', 'remark', 'remarks', 'note', 'notes', 'size', 'dimension', 'material', 'color', 'colour', '规格', '参数', '备注', '描述', '尺寸', '材质', '颜色', '说明')),
    ('no', ('item no', 'no.', 'no', '#', 'sn', 's/n', 'ln', '序号', '编号')),
]
_CJK = re.compile(r'[\u4e00-\u9fff]')
_WEAK = {'rmb', 'cny', 'usd', '人民币', '美金', '$'}      # 只有整格就是这个词才算数（包含不算）
# 出现在这些字段里的列才算"关键列"（用来给候选表头行打分）
KEY = {'name', 'sku', 'qty', 'price', 'amount', 'cost_total', 'cost_unit'}
_CUR_WORDS = [('USD', ('usd', 'us$', '$', '美元', '美金')), ('CNY', ('rmb', 'cny', '¥', '￥', '人民币', '元')), ('EUR', ('eur', '€', '欧元'))]


def _clean(h):
    return re.sub(r'\s+', ' ', str(h or '').lower().replace('\n', ' ')).strip(' :：*')


def classify_header(h):
    """一个表头单元格 → 字段名或 None。"""
    t = _clean(h)
    if not t:
        return None
    for field, words in FIELDS:                                   # 先整格相等
        if t in words:
            return field
    for field, words in FIELDS:                                   # 再包含（长词优先，避免 'no' 误中很多）
        for w in sorted(words, key=len, reverse=True):
            if w in _WEAK:
                continue                                          # 「单价(RMB)」里的 RMB 只是币种，不能因此把它认成「采购总价」
            if (len(w) >= 3 or _CJK.search(w)) and w in t:
                return field
            if len(w) < 3 and re.search(r'(^|[^a-z])%s($|[^a-z])' % re.escape(w), t) and w in ('no', '#', 'sn', 'ln'):
                return field
    return None


def find_header(grid, max_scan=60):
    """返回 (表头行号, 得分)。得分 = 认出的不同字段数（关键列加权）。"""
    best, best_score = None, 0
    for r in range(min(grid.nrows, max_scan)):
        fields = [classify_header(grid.text(r, c)) for c in range(grid.ncols)]
        found = {f for f in fields if f}
        score = len(found) + sum(1 for f in found if f in KEY)
        has_name = bool(found & {'name', 'sku'})
        has_number = bool(found & {'qty', 'price', 'amount', 'cost_total', 'cost_unit'})
        if has_name and has_number and score > best_score:
            best, best_score = r, score
    return best, best_score


def map_columns(grid, header_row):
    """表头行 → ({字段: 列号}, [(列号, 表头文字)] 未认出的列)。同一字段出现多列时取第一列，其余归入未认出。"""
    cols, rest, taken = {}, [], set()
    for c in range(grid.ncols):
        h = grid.text(header_row, c)
        f = classify_header(h)
        if f and f not in cols:
            cols[f] = c
        elif h:
            rest.append((c, h))
    sc = cols.get('sku')
    if sc is not None:                                           # 「Item No.」这类列如果里面只是 1、2、3… 的序号，它不是产品型号
        vals = [grid.cell(header_row + k, sc) for k in range(1, 7)]
        vals = [v for v in vals if v is not None and str(v).strip() != '' and not re.match(r'^(total|grand|sub|合计|总计)', str(v).strip().lower())]
        if vals and all(isinstance(v, (int, float)) and float(v) == int(float(v)) and 0 < float(v) < 1000 for v in vals):
            cols.pop('sku')
            cols.setdefault('no', sc)
    return cols, rest


def detect_currency(headers, grid=None, header_row=None):
    """从价格相关表头、表头上方文字里找币种；找不到返回 None。"""
    text = ' '.join(headers).lower()
    for cur, words in _CUR_WORDS:
        if any(w in text for w in words):
            return cur
    return None


_DATE_RE = re.compile(r'(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})')
_EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
_LABELS = {
    'invoice_no': re.compile(r'^(?:proforma\s+)?(?:invoice|pi|p/i|order|quotation|quote|contract)\s*(?:no\.?|number|#|num)?\s*[:：]?\s*(.*)$|^(?:发票号|形式发票号|单号|合同号|报价单号)\s*[:：]?\s*(.*)$', re.I),
    'date': re.compile(r'^(?:issued\s+)?date(?:\s+of\s+issue)?\s*[:：]?\s*(.*)$|^(?:日期|开票日期|报价日期)\s*[:：]?\s*(.*)$', re.I),
    'buyer': re.compile(r'^(?:buyer|customer|bill\s*to|sold\s*to|consignee|client|to)\s*[:：]\s*(.*)$|^(?:买家|客户|购买方|收货人)\s*[:：]?\s*(.*)$', re.I),
    'contact': re.compile(r'^(?:contact(?:\s+person)?|attn\.?|attention)\s*[:：]\s*(.*)$|^(?:联系人)\s*[:：]?\s*(.*)$', re.I),
    'email': re.compile(r'^(?:e-?mail)\s*[:：]\s*(.*)$|^(?:邮箱|电子邮件)\s*[:：]?\s*(.*)$', re.I),
    'phone': re.compile(r'^(?:phone|tel(?:ephone)?|mobile|whatsapp)\s*[:：]\s*(.*)$|^(?:电话|手机)\s*[:：]?\s*(.*)$', re.I),
    'address': re.compile(r'^(?:address|add)\s*[:：]\s*(.*)$|^(?:地址)\s*[:：]?\s*(.*)$', re.I),
}


def _as_date(v):
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.strftime('%Y-%m-%d')
    if isinstance(v, (int, float)) and 20000 < v < 80000:                      # Excel 日期序列号
        return (_dt.datetime(1899, 12, 30) + _dt.timedelta(days=float(v))).strftime('%Y-%m-%d')
    m = _DATE_RE.search(str(v or ''))
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return _dt.date(y, mo, d).strftime('%Y-%m-%d')
        except ValueError:
            return ''
    return ''


def extract_meta(grid, header_row):
    """表头上方（没有表头就看前 20 行）找 PI 号 / 日期 / 买家等。标签和值可以在同一格（'Invoice No.: ABC'），也可以在右边的格子里。"""
    meta = {}
    limit = header_row if header_row is not None else min(grid.nrows, 20)
    for r in range(min(limit, 40)):
        for c in range(grid.ncols):
            raw = grid.cell(r, c)
            t = grid.text(r, c)
            if not t or len(t) > 120:
                continue
            for key, rx in _LABELS.items():
                if key in meta:
                    continue
                m = rx.match(t)
                if not m:
                    continue
                val = next((g for g in m.groups() if g), '').strip() if m.groups() else ''
                if not val:                                       # 值在右边最近的非空格子
                    for cc in range(c + 1, min(grid.ncols, c + 6)):
                        v = grid.cell(r, cc)
                        if v is not None and str(v).strip() != '':
                            raw, val = v, grid.text(r, cc)
                            break
                if key == 'date':
                    d = _as_date(raw if not isinstance(raw, str) or not m.groups() or not any(m.groups()) else val) or _as_date(val)
                    if d:
                        meta['date'] = d
                elif val:
                    meta[key] = val
    if 'email' not in meta:
        for r in range(min(limit, 40)):
            m = _EMAIL_RE.search(' '.join(grid.row_texts(r)))
            if m:
                meta['email'] = m.group(0)
                break
    return meta


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if v is None:
        return None
    s = re.sub(r'[^\d.\-]', '', str(v).replace(',', ''))
    if s in ('', '-', '.'):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_items(grid, header_row, cols, rest, images=None):
    """表头行下面逐行读取，遇到 Total / 合计 行结束。没有价格和数量、只有金额的行（运费等）标 is_product=False。"""
    items, total_amount = [], None
    images = images or {}
    blank_run = 0
    for r in range(header_row + 1, grid.nrows):
        texts = grid.row_texts(r)
        first = next((t for t in texts if t), '')
        if not first:
            blank_run += 1
            if blank_run >= 4 and items:
                break
            continue
        blank_run = 0
        low = first.lower()
        if re.match(r'^(total|grand total|sub\s*total|合计|总计|total payment|amount total)\b', low) or any(re.match(r'^(total payment|grand total)', t.lower()) for t in texts if t):
            ac = cols.get('amount')
            v = _num(grid.cell(r, ac)) if ac is not None else None
            if v is None:                                         # 合计金额可能写在别的列
                nums = [_num(grid.cell(r, c)) for c in range(grid.ncols) if _num(grid.cell(r, c)) is not None]
                v = nums[-1] if nums else None
            total_amount = round(v, 2) if v is not None else None
            break
        g = lambda f: grid.cell(r, cols[f]) if f in cols else None
        name = grid.text(r, cols['name']) if 'name' in cols else ''
        sku = grid.text(r, cols['sku']) if 'sku' in cols else ''
        qty, price, amount = _num(g('qty')), _num(g('price')), _num(g('amount'))
        cost_total, cost_unit = _num(g('cost_total')), _num(g('cost_unit'))
        if not (name or sku) and amount is None:
            continue
        lines = [l.strip() for l in name.split('\n') if l.strip()]
        base = lines[0] if lines else ''
        spec_parts = []
        sp = grid.text(r, cols['spec']) if 'spec' in cols else ''
        if sp:
            spec_parts.append(sp)
        if len(lines) > 1:
            spec_parts.append(' '.join(lines[1:]))
        for c, h in rest:                                          # 没认出的列：带列名并入规格，不丢信息
            v = grid.text(r, c)
            if v:
                spec_parts.append('%s: %s' % (h, v))
        spec = '\n'.join(spec_parts).strip()
        code = sku
        if not code:                                               # 规格里写着 "Code: XXXX" 的（旧 PI 模板）
            m = re.search(r'Code[:：]\s*([A-Za-z0-9][A-Za-z0-9\-_./]*)', spec)
            code = m.group(1) if m else ''
        is_product = bool(base or code) and (qty is not None or price is not None) and not (qty is None and price is None)
        if not is_product and amount is None:
            continue
        unit_cost = cost_unit
        if unit_cost is None and cost_total and qty:
            unit_cost = round(cost_total / qty, 3)
        items.append({
            'row': r + 1, 'is_product': is_product, 'sku': code or (base if is_product else ''), 'sku_from_name': bool(is_product and not code),
            'name': base or code, 'spec': spec,
            'unit': grid.text(r, cols['unit']) if 'unit' in cols else '', 'quantity': qty, 'unit_price': price,
            'amount': round(amount, 2) if amount is not None else (round(qty * price, 2) if qty is not None and price is not None else None),
            'unit_cost_cny': unit_cost if unit_cost and unit_cost > 0 else None, 'moq': int(_num(g('moq'))) if _num(g('moq')) else None,
            'image': images.get(r + 1), 'include': True})
    return items, total_amount


def guess_kind(meta, cols, currency, items):
    """pi（客户 PI）/ supplier（供应商报价单）/ list（产品清单）。"""
    if meta.get('buyer') or meta.get('invoice_no') and currency in ('USD', 'EUR', None) and 'price' in cols:
        return 'pi'
    if currency == 'CNY' or 'cost_unit' in cols or 'cost_total' in cols and 'price' not in cols:
        return 'supplier'
    return 'pi' if meta.get('invoice_no') else 'list'


def parse_grid(grid, images=None, header_row=None, columns=None):
    """识别一张表。header_row / columns 由用户手动指定时覆盖自动识别（columns: {字段: 列号}）。"""
    warnings = []
    score = 0
    if header_row is None:
        header_row, score = find_header(grid)
    if header_row is None:
        return {'ok': False, 'header_row': None, 'warnings': ['没有找到表头行（需要至少一列「品名/型号」和一列「数量/单价/金额」）。请在下面手动指定表头行和各列。']}
    cols, rest = map_columns(grid, header_row)
    if columns:
        cols = {f: int(c) for f, c in columns.items() if c not in (None, '')}
        used = set(cols.values())
        rest = [(c, grid.text(header_row, c)) for c in range(grid.ncols) if c not in used and grid.text(header_row, c)]
    headers = [grid.text(header_row, c) for c in range(grid.ncols)]
    price_heads = [headers[cols[f]] for f in ('price', 'amount') if f in cols and cols[f] < len(headers)] or \
                  [headers[cols[f]] for f in ('cost_unit', 'cost_total') if f in cols and cols[f] < len(headers)]      # 文件币种看售价列，没有售价列才看采购列
    currency = detect_currency(price_heads) or detect_currency(headers)
    meta = extract_meta(grid, header_row)
    items, total = parse_items(grid, header_row, cols, rest, images)
    if 'cost_unit' in cols and 'cost_total' not in cols and 'price' in cols:
        # 「采购价」这类列可能是单件价，也可能是整行合计：单件采购价不会比售价高出很多，明显更高的就是合计，按数量折算
        ratios = sorted(it['unit_cost_cny'] / (it['unit_price'] * 7.0) for it in items
                        if it['is_product'] and it['unit_cost_cny'] and it['unit_price'] and it['quantity'] and it['quantity'] > 1 and (currency or 'USD') != 'CNY')
        if ratios and ratios[len(ratios) // 2] > 1.6:
            for it in items:
                if it['is_product'] and it['unit_cost_cny'] and it['quantity']:
                    it['unit_cost_cny'] = round(it['unit_cost_cny'] / it['quantity'], 3)
            warnings.append('「采购价」列的数值明显大于单价，看起来是整行合计，已按数量折算成单件采购价，请核对。')
    if not items:
        warnings.append('表头识别到了，但下面没有读到产品行，请检查表头行是否选对。')
    if 'name' not in cols and 'sku' not in cols:
        warnings.append('没有识别出「品名」或「型号」列，请手动指定。')
    kind = guess_kind(meta, cols, currency, items)
    # 币种不明时：PI 默认 USD，供应商报价单默认 CNY
    if not currency:
        currency = 'CNY' if kind == 'supplier' else 'USD'
        warnings.append('表头里没写币种，按 %s 处理，请核对。' % currency)
    return {'ok': True, 'kind': kind, 'header_row': header_row, 'header_score': score, 'columns': cols, 'headers': headers,
            'unmapped': [{'col': c, 'header': h} for c, h in rest], 'currency': currency, 'meta': meta, 'items': items,
            'total_amount': total, 'warnings': warnings}
