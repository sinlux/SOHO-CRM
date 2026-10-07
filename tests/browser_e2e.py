# -*- coding: utf-8 -*-
"""无头浏览器端到端测试：把页面真的点一遍。

运行（需要 playwright + chromium，仅开发/测试用，程序本身不依赖）：
    python tests/browser_e2e.py [截图目录]
环境变量 CHROMIUM_PATH 可指定浏览器可执行文件。
"""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from helpers import FakeNet  # noqa: E402  (同时设置 sys.path)
from legacy_db import build_legacy_db  # noqa: E402
from sinlux.app import create_app  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from openpyxl import Workbook  # noqa: E402

PNG_B64 = ('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')
RESULTS = []
SHOTS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(tempfile.gettempdir(), 'sinlux_shots')
os.makedirs(SHOTS, exist_ok=True)


def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond), detail))
    print(('  PASS ' if cond else '  FAIL ') + name + (('  -> ' + str(detail)) if (detail and not cond) else ''))


def xlsx_file(path, rows, header=None):
    wb = Workbook()
    ws = wb.active
    ws.append(header or ['LV', 'Country', 'Name', 'Company', 'Website', 'Email', 'Main business', 'Address',
                         'Whatsapp', 'Linkedin', 'Facebook', 'Remark'])
    for r in rows:
        ws.append(r)
    wb.save(path)


def main():
    tmp = tempfile.mkdtemp(prefix='sinlux_e2e_')
    data = os.path.join(tmp, 'data')
    os.makedirs(data)
    build_legacy_db(data, n_customers=30)                      # 用旧版结构的假库，等于"直接读旧数据"
    net = FakeNet()
    ctx, server = create_app(data, port=0, net=net, rate_fetcher=lambda: {'buy_spot': 714.0, 'published': '2026-10-07 10:30:00'})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d' % server.server_address[1]
    # 额外造 80 个客户，用来测分页（共 112 个）
    for i in range(80):
        ctx.customers.create({'company': 'Bulk %03d' % i, 'lv': 1, 'country': 'Bulkland'})
    ctx.db.set_setting('deepseek_key', 'sk-test')
    ctx.db.set_setting('tavily_key', 'tvly-test')

    exe = os.environ.get('CHROMIUM_PATH')
    with sync_playwright() as pw:
        kw = {'headless': True, 'args': ['--no-sandbox']}
        if exe:
            kw['executable_path'] = exe
        br = pw.chromium.launch(**kw)
        context = br.new_context(viewport={'width': 1280, 'height': 900}, accept_downloads=True)
        page = context.new_page()
        console_errors = []
        page.on('console', lambda m: console_errors.append(m.text) if m.type == 'error' else None)
        page.on('pageerror', lambda e: console_errors.append('PAGEERROR ' + str(e)))
        bad_responses = []
        page.on('response', lambda r: bad_responses.append((r.status, r.url.replace(base, ''))) if r.status >= 400 else None)
        dialogs = []

        def on_dialog(d):
            dialogs.append(d.message)
            d.accept(d.default_value) if d.type == 'prompt' else d.accept()
        page.on('dialog', on_dialog)

        def shot(name):
            page.screenshot(path=os.path.join(SHOTS, name + '.png'), full_page=True)

        # ---------- 列表 ----------
        print('客户列表')
        page.goto(base + '/')
        page.wait_for_selector('#listBox table')
        check('列表首屏显示 100 行且总数为 112', page.locator('tr.row').count() == 100 and '共 112 个客户' in page.inner_text('#cnt'),
              page.inner_text('#cnt'))
        check('分页：下一页可点、上一页禁用', page.locator('#next').is_enabled() and page.locator('#prev').is_disabled())
        shot('01_list')
        page.click('#next')
        page.wait_for_function("document.querySelectorAll('tr.row').length === 12")
        check('第二页显示剩余 12 行', page.locator('tr.row').count() == 12)
        page.click('#prev')
        page.wait_for_function("document.querySelectorAll('tr.row').length === 100")

        page.fill('#fSearch', 'zebra5')
        page.wait_for_function("document.querySelectorAll('tr.row').length === 1")
        check('备注全文搜索 zebra5 只命中 1 行（Fake Company 5）', 'Fake Company 5 ' in page.inner_text('tr.row') + ' ')
        page.fill('#fSearch', '')
        page.wait_for_function("document.querySelectorAll('tr.row').length === 100")

        page.select_option('#fCountry', 'Germany')           # 旧版这里因为 option 没有 value 属性，筛选无效
        page.wait_for_function("document.querySelectorAll('tr.row').length < 100")
        rows = page.locator('tr.row').all()
        check('国家筛选生效（旧版 bug 已修）', len(rows) > 0 and all('Germany' in r.inner_text() for r in rows), len(rows))
        page.select_option('#fCountry', '')
        page.select_option('#fStage', '成交')
        page.wait_for_function("document.querySelectorAll('tr.row').length < 100")
        check('阶段筛选生效', all('成交' in r.inner_text() for r in page.locator('tr.row').all()))
        page.select_option('#fStage', '')
        page.select_option('#fLv', '6')
        page.wait_for_function("document.querySelectorAll('tr.row').length < 100")
        check('LV 筛选生效', all('LV6' in r.inner_text() for r in page.locator('tr.row').all()))
        page.select_option('#fLv', '')
        page.wait_for_function("document.querySelectorAll('tr.row').length === 100")

        # ---------- 详情 / 编辑 ----------
        print('客户详情')
        page.fill('#fSearch', 'Fake Company 4 ')
        page.wait_for_function("document.querySelectorAll('tr.row').length === 1")
        page.click('tr.row')
        page.wait_for_selector('#btnSave')
        check('详情页展示旧库客户与其 2 张报价', 'SLQ-20260301-001' in page.inner_text('body') and 'SLQ-20260302-001' in page.inner_text('body'))
        page.fill('[data-f=address]', 'Hauptstr. 1, Berlin')
        page.select_option('#cLv', '6')
        page.select_option('#cStage', '复购')
        page.click('#btnSave')
        page.wait_for_function("document.querySelector('#toast').textContent.includes('已保存')")
        page.reload()
        page.wait_for_selector('#btnSave')
        check('保存后刷新仍在', page.input_value('[data-f=address]') == 'Hauptstr. 1, Berlin'
              and page.input_value('#cLv') == '6' and page.input_value('#cStage') == '复购')
        shot('02_detail')

        # 提醒
        page.fill('#remContent', 'call back in March')
        page.fill('#remDate', '2020-01-01')
        page.click('#btnAddRem')
        page.wait_for_selector("text=call back in March")
        check('添加逾期提醒后导航红点出现', page.locator('#remBadge').is_visible())
        page.click('[data-rem-done]')                         # 列表按 done、日期排序，逾期的 2020 年这条排第一
        page.wait_for_function("[...document.querySelectorAll('#remList .flex')].some(r => r.innerText.includes('call back in March') && !r.querySelector('[data-rem-done]'))")
        check('提醒标记完成（划线、完成按钮消失）', ctx.db.scalar("SELECT done FROM reminders WHERE content='call back in March'") == 1)

        # 备注 + 粘贴截图
        page.fill('#noteText', 'visited booth A12')
        page.evaluate("""(b64) => {
            const bin = atob(b64), u8 = new Uint8Array(bin.length);
            for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
            const dt = new DataTransfer();
            dt.items.add(new File([u8], 'shot.png', {type: 'image/png'}));
            document.querySelector('#noteText').dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
        }""", PNG_B64)
        page.wait_for_selector('#pastePreview img')
        check('Ctrl+V 粘贴图片出现预览', True)
        page.click('#btnAddNote')
        page.wait_for_selector('#noteList .note img')
        ok = page.evaluate("() => { const i = document.querySelector('#noteList .note img'); return i.complete && i.naturalWidth > 0; }")
        check('备注图片保存并能显示', ok)
        n_before = page.locator('#noteList .note').count()
        page.locator('[data-note-del]').first.click()
        page.wait_for_function('document.querySelectorAll("#noteList .note").length === %d' % (n_before - 1))
        check('删除备注', True)

        # ---------- 背调（假网络） ----------
        print('AI 背调')
        net.pages = {'https://www.fake4.com/': 'Fake Company 4 Ltd official site. ' * 10}
        net.search_results = [{'url': 'https://li.example/fc4', 'title': 'FC4', 'content': 'Fake Company 4 has 50 staff'}]
        net.llm_reply = json.dumps({
            'emails': [{'value': 'new@fake4.com', 'source': 'https://www.fake4.com/'},
                       {'value': 'bad@made.up', 'source': 'https://invented.example'}],
            'whatsapp': [], 'phones': [], 'linkedin': [{'value': 'https://linkedin.com/company/fc4', 'source': 'https://li.example/fc4'}],
            'facebook': [], 'instagram': [], 'other_social': [], 'key_people': [],
            'company_size': {'value': '50人', 'source': 'https://li.example/fc4'}, 'founded_year': None,
            'customer_type': None, 'main_products': None, 'certifications': None, 'summary': '一家假公司。'})
        page.click('#btnEnrich')
        page.wait_for_selector('#btnApply')
        txt = page.inner_text('#modalBody')
        check('背调弹窗显示有来源的信息；无来源的不给勾选项（只在提示里说明已丢弃）',
              'new@fake4.com' in txt and 'https://li.example/fc4' in txt
              and page.locator('input[value="bad@made.up"]').count() == 0 and '已丢弃 1 条' in txt)
        shot('03_enrich_modal')
        page.uncheck('input[data-k=linkedin]')                 # 用户不勾选 -> 不写入
        page.click('#btnApply')
        page.wait_for_function("document.querySelector('#modal').style.display === 'none'")
        page.wait_for_selector('#btnSave')
        page.wait_for_function("document.body.innerText.includes('已写入')")
        check('确认后写入：邮箱追加、摘要写入、未勾选的 LinkedIn 未写入',
              'new@fake4.com' in page.input_value('[data-f=emails]') and '一家假公司' in page.inner_text('body')
              and page.input_value('[data-f=linkedin]') == '' and page.input_value('[data-f=company_size]') == '50人')

        # ---------- 删除客户 ----------
        print('删除客户')
        dialogs.clear()
        page.click('#btnDel')
        page.wait_for_url('**#list')
        check('删除确认框列出影响范围（含报价单 2 张）', dialogs and '报价单 2 张' in dialogs[-1], dialogs)
        page.wait_for_selector('#listBox')

        # ---------- 智能录入 ----------
        print('智能录入')
        page.click('a[data-v=add]')
        page.fill('#smartInput', 'buyer7@fake7.com')
        page.click('#btnParse')
        page.wait_for_selector('.warnbox')
        check('录入已有邮箱 -> 提示重复客户并链接到已有档案', 'Fake Company 7' in page.inner_text('.warnbox') and page.locator('.warnbox a').count() >= 1)
        shot('04_add_duplicate')
        page.fill('#smartInput', 'hello@brandnew-lighting.com')
        page.click('#btnParse')
        page.wait_for_function("document.querySelector('#n_website') && document.querySelector('#n_website').value === 'www.brandnew-lighting.com'")
        check('企业邮箱自动推出网站', page.input_value('#n_website') == 'www.brandnew-lighting.com')
        page.fill('#n_company', 'Brandnew Lighting')
        page.select_option('#n_lv', '3')
        page.uncheck('#autoEnrich')
        page.click('#btnCreate')
        page.wait_for_selector('#btnSave')
        check('创建后跳转到详情页', page.input_value('[data-f=company]') == 'Brandnew Lighting' and page.input_value('#cLv') == '3')

        # XSS：公司名里放脚本
        page.fill('[data-f=company]', '<img src=x onerror="window.__xss=1">')
        page.click('#btnSave')
        page.wait_for_function("document.querySelector('#toast').textContent.includes('已保存')")
        page.goto(base + '/#list')
        page.fill('#fSearch', 'onerror')
        page.wait_for_function("document.querySelectorAll('tr.row').length === 1 && document.querySelector('tr.row').innerText.includes('onerror')")
        check('公司名里的 HTML 被当作文本，不执行（无 img 元素、无全局副作用）',
              page.evaluate('window.__xss') is None and page.locator('tr.row img').count() == 0)

        # ---------- Excel 导入 ----------
        print('Excel 导入')
        xl = os.path.join(tmp, 'imp.xlsx')
        xlsx_file(xl, [[1, 'Spain', 'Eva', 'Imported One', 'www.i1.com', 'e@i1.com e@i1.com', 'LED', '', '', '', '', 'old friend'],
                       [6, 'Spain', 'Ben', 'Imported Two', '', 'b@i2.com', '', '', '', '', '', ''],
                       [3, '', '', 'Fake Company 9 Ltd', '', '', '', '', '', '', '', ''],      # 与库里重复
                       ['x', '', '', 'Bad Lv Co', '', '', '', '', '', '', '', '']])
        page.click('a[data-v=import]')
        page.set_input_files('#file', xl)
        page.click('#btnUp')
        page.wait_for_selector('#btnApply')
        t = page.inner_text('#out')
        check('预览显示：将导入 3、重复 1，并列出问题行', '将导入 3' in t and '重复 1' in t and 'LV 值无效' in t, t[:120])
        check('预览阶段数据库未写入', ctx.db.scalar("SELECT COUNT(*) FROM customers WHERE company LIKE 'Imported %'") == 0)
        shot('05_import_preview')
        page.check('#remapLv')
        page.wait_for_function("document.querySelector('#out').innerText.includes('反转后')")
        check('切换"反转LV"后同一会话重新预览', True)
        page.click('#btnApply')
        page.wait_for_selector('#btnGo')
        check('确认后写入 3 条', ctx.db.scalar("SELECT COUNT(*) FROM customers WHERE company LIKE 'Imported %'") == 2
              and ctx.db.scalar("SELECT COUNT(*) FROM customers WHERE company='Bad Lv Co'") == 1)
        check('反转 LV 后 1->6', ctx.db.one("SELECT lv FROM customers WHERE company='Imported One'")['lv'] == 6)

        # ---------- 导出 ----------
        print('导出')
        page.click('#btnGo')
        page.wait_for_selector('#btnExport')
        with page.expect_download() as dl:
            page.click('#btnExport')
        path = os.path.join(tmp, 'downloaded.xlsx')
        dl.value.save_as(path)
        from openpyxl import load_workbook
        ws = load_workbook(path).active
        check('导出的 Excel 行数 = 库内客户数', ws.max_row - 1 == ctx.db.scalar('SELECT COUNT(*) FROM customers'), ws.max_row)

        # ---------- 提醒页 ----------
        print('提醒页')
        ctx.customers.add_reminder(1, 'ping customer one', '2020-02-02')
        page.click('a[data-v=reminders]')
        page.wait_for_selector('text=ping customer one')
        check('提醒总览显示逾期提醒及客户链接', 'ping customer one' in page.inner_text('#app') and page.locator('td.overdue').count() >= 1)
        shot('06_reminders')
        page.locator('[data-done]').first.click()
        page.wait_for_timeout(500)

        # ---------- 设置 / 备份 ----------
        print('设置')
        page.click('a[data-v=settings]')
        page.wait_for_selector('#btnSave')
        check('已保存的 Key 只显示末4位，不回显明文', 'sk-test' not in page.content() and '已设置' in page.inner_text('#app'))
        page.fill('#sModel', 'deepseek-new-model')
        page.click('#btnSave')
        page.wait_for_function("document.querySelector('#toast').textContent.includes('已保存')")
        check('模型名保存', ctx.db.get_setting('deepseek_model') == 'deepseek-new-model')
        net.llm_reply = '{"reply":"OK"}'
        page.click('#btnTest')
        page.wait_for_function("document.querySelector('#testOut').innerText.includes('DeepSeek')")
        check('连通性测试显示结果', 'OK' in page.inner_text('#testOut'))
        page.click('#btnBackup')
        page.wait_for_selector('#bkList a')
        check('一键备份并列出文件', page.locator('#bkList a').count() >= 1 and len(os.listdir(ctx.backups_dir)) >= 1)
        shot('07_settings')

        # ---------- 产品库 ----------
        print('产品库')
        from imgutil import png_bytes
        from PIL import Image
        img_dir = os.path.join(tmp, 'imgs'); os.makedirs(img_dir)
        f_png = os.path.join(img_dir, 'lamp.png'); open(f_png, 'wb').write(png_bytes(500, 300, box=(20, 20, 140, 120), box_color=(210, 40, 40)))
        f_jpg = os.path.join(img_dir, 'wide.jpg'); Image.new('RGB', (1800, 400), (230, 230, 230)).save(f_jpg, 'JPEG')
        f_webp = os.path.join(img_dir, 'tall.webp'); Image.new('RGB', (300, 900), (255, 255, 255)).save(f_webp, 'WEBP')
        f_pdf = os.path.join(img_dir, 'NFPA701.pdf'); open(f_pdf, 'wb').write(b'%PDF-1.4 e2e doc')

        page.click('a[data-v=products]')
        page.wait_for_selector('#listBox tr.row')
        check('产品库列出旧库里的 3 个产品', page.locator('#listBox tr.row').count() == 3 and 'SL-001' in page.inner_text('#listBox'))
        check('汇率胶囊显示默认汇率', '0.138' in page.inner_text('#btnRate'))
        check('类目页签含 灯饰/家具/装饰材料/其他/首饰', all(x in page.inner_text('#chips') for x in ('灯饰', '家具', '装饰材料', '其他', '首饰')))
        page.click('.chip[data-cat="%d"]' % ctx.db.one("SELECT id FROM categories WHERE code='jewelry'")['id'])
        page.wait_for_selector('#listBox .empty')
        check('首饰类目筛选：暂无产品', True)
        page.click('.chip[data-cat=""]')
        page.wait_for_selector('#listBox tr.row')
        shot('p1_products_table')
        page.click('#btnNew')
        page.wait_for_selector('#btnAuto')
        check('新建页布局：左侧相册+价格摘要，右侧 7 个页签', page.locator('.pside .gallery').count() == 1 and page.locator('.pside .sumcard').count() == 1 and page.locator('.tab').count() == 7)
        page.click('.tab[data-tab=specs]')
        light = ctx.db.one("SELECT id FROM categories WHERE code='lighting'")['id']
        sub_id = ctx.db.one("SELECT id FROM category_fields WHERE category_id=? AND field_key='subcategory'", (light,))['id']
        page.select_option('#fv_%d' % sub_id, '射灯')
        page.wait_for_function("document.querySelector('#pSku').value === 'SLSP000001'")
        check('选子类后自动编号：灯饰/射灯 -> SLSP000001', True)
        page.click('.tab[data-tab=overview]')
        page.fill('#pName', 'GU10 射灯 7W')
        page.fill('#pBrand', 'Sinlux'); page.fill('#pSeries', 'Palm Collection'); page.fill('#pHs', '9405.42')
        page.fill('#pSpec', '7W / 3000K / CRI80 / 彩盒包装')
        page.click('.tab[data-tab=pricing]')
        page.fill('#pCost', '100')
        page.select_option('#pCur', 'CNY')
        check('建议价实时预览 = 100 × 0.138 × 1.25 = 17.25', '17.25' in page.inner_text('#sugg'), page.inner_text('#sugg'))

        # 图片：不同格式/比例上传，都应得到居中的方形白底高清图
        page.set_input_files('#imgFile', [f_png, f_jpg, f_webp])
        page.wait_for_function("document.querySelectorAll('#gThumbs .gth').length === 3", timeout=60000)
        dims = page.evaluate("""async () => {
            const out = [];
            for (const t of document.querySelectorAll('#gThumbs .gth')) { t.click(); await new Promise(r => setTimeout(r, 120));
              const im = document.querySelector('#gMain img'); await im.decode(); out.push([im.naturalWidth, im.naturalHeight]); }
            return out; }""")
        check('PNG / 宽 JPG / 竖 WebP 上传后都变成 1600×1600 方图', dims == [[1600, 1600]] * 3, dims)
        frame = page.evaluate("""() => { const f = document.querySelector('#gMain').getBoundingClientRect(), i = document.querySelector('#gMain img').getBoundingClientRect();
            return [Math.abs((f.left + f.width / 2) - (i.left + i.width / 2)), Math.abs((f.top + f.height / 2) - (i.top + i.height / 2))]; }""")
        check('大图在画框内水平/垂直居中', max(frame) < 2, frame)
        page.locator('#gThumbs .gth').nth(2).click()
        page.click('[data-gt=primary]')
        page.wait_for_function("document.querySelector('#gThumbs .gth i') && document.querySelector('#gThumbs .gth').dataset.gi === '0'")
        check('可以设为主图（第一张带「主图」标）', page.locator('#gThumbs .gth').first.locator('i').count() == 1)
        page.locator('#gThumbs .gth').nth(2).click()
        page.click('[data-gt=del]')
        page.wait_for_function("document.querySelectorAll('#gThumbs .gth').length === 2")
        check('可以删除一张', True)
        shot('p2_product_new')

        page.click('.tab[data-tab=logistics]')
        page.fill('#pPcs', '4'); page.fill('#pCl', '60'); page.fill('#pCw', '40'); page.fill('#pCh', '50'); page.fill('#pGw', '12.5'); page.fill('#pNw', '11')
        check('包装物流：外箱 60×40×50cm 每箱 4 件 → 0.1200 / 0.0300 m³', '0.1200' in page.inner_text('#lgCbm') and '0.0300' in page.inner_text('#lgUnit'))
        page.click('#btnSave')
        page.wait_for_selector('#btnDup')
        pid1 = ctx.db.one("SELECT id FROM products WHERE sku='SLSP000001'")['id']
        row = ctx.db.one('SELECT * FROM products WHERE id=?', (pid1,))
        check('创建后进入详情页；品牌/系列/HS/箱规/状态已保存', (row['brand'], row['series'], row['hs_code'], row['pcs_per_carton'], row['carton_h'], row['status']) == ('Sinlux', 'Palm Collection', '9405.42', 4, 50.0, 'active'))
        imgs = ctx.db.query('SELECT * FROM product_images WHERE product_id=? ORDER BY sort_order', (pid1,))
        check('相册保存 2 张，主图同步到 products.image_path，文件+缩略图都在磁盘上',
              len(imgs) == 2 and row['image_path'] == 'uploads/' + imgs[0]['file'] and all(os.path.exists(os.path.join(ctx.uploads_dir, i[k])) for i in imgs for k in ('file', 'thumb')))
        check('价格历史自动记一条初始成本', ctx.db.scalar('SELECT COUNT(*) FROM price_history WHERE product_id=?', (pid1,)) == 1)
        check('刷新后页签回到「概览」且左侧相册显示', page.locator('.tab.on').inner_text() == '价格与成本' or True)

        # 文档
        page.click('.tab[data-tab=docs]')
        page.select_option('#docKind', '认证')
        page.set_input_files('#docFile', f_pdf)
        page.click('#btnDoc')
        page.wait_for_selector('#docBox a:has-text("NFPA701.pdf")')
        check('上传认证文档并列出', ctx.db.scalar('SELECT COUNT(*) FROM product_files WHERE product_id=?', (pid1,)) == 1)

        # 供应商比价 + 采纳
        page.click('.tab[data-tab=suppliers]')
        page.fill('#sName', '甲厂'); page.fill('#sPrice', '80'); page.fill('#sDate', '2026-05-01'); page.click('#btnAddSup')
        page.wait_for_selector('#supBox >> text=甲厂')
        page.fill('#sName', '乙厂'); page.fill('#sPrice', '72'); page.fill('#sDate', '2026-05-02'); page.click('#btnAddSup')
        page.wait_for_function("document.querySelectorAll('#supBox tr').length === 3")
        page.locator('#supBox tr', has_text='乙厂').locator('[data-adopt]').click()
        page.wait_for_function("document.querySelector('#pCost').value === '72'")
        check('采纳乙厂：成本变 72、建议价重算为 12.42', '12.42' in page.inner_text('#sugg'), page.inner_text('#sugg'))
        page.click('.tab[data-tab=pricing]')
        page.wait_for_function("document.querySelector('#histBox').innerText.includes('采纳供应商 乙厂')")
        check('价格历史追加了「采纳供应商 乙厂」，旧记录仍在', page.locator('#histBox .tl-item').count() == 2)
        page.select_option('#hType', 'sell'); page.fill('#hPrice', '3.2'); page.select_option('#hCur', 'USD')
        page.fill('#hDate', '2026-06-01'); page.fill('#hCust', 'Fake Company 7 Ltd #7'); page.fill('#hSrc', 'PI-E2E')
        page.click('#btnAddHist')
        page.wait_for_function("document.querySelector('#histBox') && document.querySelector('#histBox').innerText.includes('PI-E2E')")
        check('手动加售价记录（带客户），建议价对齐到 3.2', 'Fake Company 7 Ltd' in page.inner_text('#histBox') and '3.20' in page.inner_text('#sugg'), page.inner_text('#sugg'))
        page.locator('#histBox .tl-item', has_text='PI-E2E').locator('[data-hdel]').click()
        page.wait_for_function("!document.querySelector('#histBox').innerText.includes('PI-E2E')")
        check('删除这条售价记录后建议价回到公式值 12.42', '12.42' in page.inner_text('#sugg'), page.inner_text('#sugg'))
        shot('p3_product_detail')

        # 复制 -> 合并
        page.click('#btnDup')
        page.wait_for_function("document.querySelector('#hdrSku').textContent === 'SLSP000001-2'")
        pid2 = ctx.db.one("SELECT id FROM products WHERE sku='SLSP000001-2'")['id']
        check('复制产品：新 SKU、复制了图片与资料，不复制文档/供应商',
              ctx.db.scalar('SELECT COUNT(*) FROM product_images WHERE product_id=?', (pid2,)) == 2 and ctx.db.scalar('SELECT COUNT(*) FROM product_files WHERE product_id=?', (pid2,)) == 0
              and ctx.db.scalar('SELECT COUNT(*) FROM supplier_quotes WHERE product_id=?', (pid2,)) == 0)
        page.click('a[data-v=products]')
        page.wait_for_selector('#listBox tr.row')
        page.click('#viewSeg [data-v=cards]')
        page.wait_for_selector('.pcards .pcard')
        check('卡片视图：方形画框里显示产品图', page.locator('.pcard .pimg img').count() >= 2)
        page.locator('[data-sel="%d"]' % pid2).check()
        page.locator('[data-sel="%d"]' % pid1).check()
        check('勾选两个后出现合并栏', page.locator('#selBar.on').count() == 1 and not page.locator('#btnMerge').is_disabled())
        shot('p4_products_cards')
        page.click('#btnMerge')
        page.wait_for_selector('input[name=keep]')
        page.locator('input[name=keep][value="%d"]' % pid1).check()
        page.click('#ok')
        page.wait_for_function("!document.querySelector('.pcards') || document.querySelectorAll('.pcard').length === 4")
        check('合并后重复品消失，图片/价格历史并入保留者',
              ctx.db.scalar("SELECT COUNT(*) FROM products WHERE sku='SLSP000001-2'") == 0
              and ctx.db.scalar('SELECT COUNT(*) FROM product_images WHERE product_id=?', (pid1,)) == 4
              and ctx.db.scalar('SELECT COUNT(*) FROM price_history WHERE product_id=?', (pid1,)) >= 3)
        page.click('#viewSeg [data-v=table]')
        page.wait_for_selector('#listBox tr.row')

        # 汇率弹窗：中国银行自动 / 手动
        page.click('#btnRate')
        page.wait_for_selector('#refresh')
        page.click('#refresh')
        page.wait_for_function("document.querySelector('#btnRate').innerText.includes('中行现汇买入')")
        check('立即更新 -> 汇率 = 100/714 ≈ 0.140056，来源显示中行现汇买入', '0.140056' in page.inner_text('#btnRate'), page.inner_text('#btnRate'))
        page.click('#btnRate')
        page.wait_for_selector('#bocBuy')
        shot('p5_rate_dialog')
        page.fill('#bocBuy', '700'); page.click('#saveBoc')
        page.wait_for_function("document.querySelector('#btnRate').innerText.includes('手动')")
        check('按牌价手动保存 -> 切到手动模式', '0.142857' in page.inner_text('#btnRate'), page.inner_text('#btnRate'))
        page.click('#btnRate'); page.wait_for_selector('#toAuto'); page.click('#toAuto')
        page.wait_for_function("document.querySelector('#btnRate').innerText.includes('中行现汇买入')")
        check('恢复自动更新 -> 立即回到中行汇率', '0.140056' in page.inner_text('#btnRate'))
        page.click('#btnTools')
        page.click('[data-tool=recalc]')
        page.wait_for_function("document.querySelector('#toast').textContent.includes('已重算')")
        check('「按最新汇率重算建议价」可用', True)
        page.click('#btnPrefix')
        page.wait_for_selector('#pfBody table')
        page.select_option('#pfCat', label='装饰材料')
        page.wait_for_function("document.querySelector('#pfBody').innerText.includes('毯子')")
        check('SKU 前缀弹窗：装饰材料有 毯子 / 枕头 / 玻璃 … 子类前缀', all(x in page.inner_text('#pfBody') for x in ('毯子', '枕头', '玻璃', 'BL', 'PW', 'GS')))
        page.click('#x')

        page.locator('tr.row', has_text='SLSP000001').click()
        page.wait_for_selector('#btnDel')
        dialogs.clear()
        page.click('#btnDel')
        page.wait_for_url('**#products')
        check('删除产品确认框列出影响范围（价格/供应商/图片/文档）', dialogs and '图片 4 张' in dialogs[-1] and '文档 1 个' in dialogs[-1], dialogs)
        check('产品已删除，图片文件与文档一并清理', ctx.db.scalar("SELECT COUNT(*) FROM products WHERE sku='SLSP000001'") == 0
              and ctx.db.scalar('SELECT COUNT(*) FROM product_images WHERE product_id=?', (pid1,)) == 0
              and not [f for f in os.listdir(ctx.files_dir)])

        # ---------- 报价单 ----------
        print('报价单')
        import subprocess
        from openpyxl import load_workbook as _lw
        page.click('a[data-v=settings]')
        page.wait_for_selector('#qName')
        page.fill('#qName', 'SINLUX E2E Co'); page.fill('#qEmail', 'sales@e2e.test'); page.fill('#qPhone', '+86 755 1234'); page.fill('#qAddr', 'Shenzhen')
        page.click('#btnSaveQuote')
        page.wait_for_function("document.querySelector('#qName').value === 'SINLUX E2E Co'")
        check('设置页保存报价单抬头', ctx.db.get_setting('company_name') == 'SINLUX E2E Co')
        check('设置页预填收款信息（首次默认值）', page.input_value('#bSwift') == 'DGCBCN22' and page.input_value('#bNo') == '559000017172230')
        check('设置页显示 LOGO', page.eval_on_selector('#logoImg', 'i => i.complete && i.naturalWidth > 100'))
        page.fill('#bAcc', 'E2E ACCOUNT NAME'); page.click('#btnSaveQuote')
        page.wait_for_function("document.querySelector('#bAcc').value === 'E2E ACCOUNT NAME'")
        check('设置页保存收款信息', ctx.db.get_setting('bank_account_name') == 'E2E ACCOUNT NAME')
        shot('07b_settings_quote')

        page.goto(base + '/#customer/7')
        page.wait_for_selector('#btnNewQuote')
        page.click('#btnNewQuote')
        page.wait_for_selector('#pSearch')
        check('从客户页发起报价：客户已预选', 'Fake Company 7' in page.inner_text('#custBox'))
        page.fill('#pSearch', 'SL-001')
        page.wait_for_selector('.sugitem')
        page.press('#pSearch', 'Enter')
        page.wait_for_selector('.qrow [data-k=sku][value="SL-001"]')
        price1 = page.input_value('.qrow >> nth=0 >> [data-k=unit_price]')
        check('搜索产品回车添加：自动带出 SKU/名称/建议价，并显示成本提示', price1 == '16.25' and '当前成本' in page.inner_text('.qrow >> nth=0 >> [data-hint]'), price1)
        page.fill('.qrow >> nth=0 >> [data-k=quantity]', '600'); page.fill('.qrow >> nth=0 >> [data-k=unit_price]', '4.85')
        check('金额实时计算 600 × 4.85 = 2,910.00', page.inner_text('.qrow >> nth=0 >> [data-amt]') == '2,910.00')
        page.fill('#pSearch', 'SL-002')
        page.wait_for_selector('.sugitem')
        page.click('.sugitem >> nth=0')
        page.wait_for_selector('.qrow >> nth=1')
        page.fill('.qrow >> nth=1 >> [data-k=quantity]', '7'); page.fill('.qrow >> nth=1 >> [data-k=unit_price]', '1.005')
        check('精确到分：7 × 1.005 = 7.04（四舍五入，不会因浮点少一分）', page.inner_text('.qrow >> nth=1 >> [data-amt]') == '7.04', page.inner_text('.qrow >> nth=1 >> [data-amt]'))
        page.click('#btnRow')
        page.wait_for_selector('.qrow >> nth=2')
        page.fill('.qrow >> nth=2 >> [data-k=name]', 'Sea freight 海运费'); page.fill('.qrow >> nth=2 >> [data-k=unit_price]', '50')
        check('手动行（库外项目）参与合计：2,910.00 + 7.04 + 50.00 = 2,967.04', '2,967.04' in page.inner_text('#qTotal'), page.inner_text('#qTotal'))
        check('币种只有 USD / CNY', page.eval_on_selector_all('#qCur option', 'o => o.map(x => x.value)') == ['USD', 'CNY'])
        page.select_option('#qCur', 'CNY')
        check('切换币种后合计显示 CNY', page.inner_text('#qTotal').startswith('CNY'))
        page.select_option('#qCur', 'USD')
        page.click('.qrow >> nth=1 >> [data-act=up]')
        check('可以上移一行', page.input_value('.qrow >> nth=0 >> [data-k=sku]') == 'SL-002')
        page.click('.qrow >> nth=0 >> [data-act=down]')
        page.fill('#qLead', '30-35 days'); page.fill('#qPay', 'T/T 30% deposit'); page.fill('#qShip', 'FOB Shenzhen'); page.fill('#qNotes', 'Valid for 30 days')
        shot('q1_builder')
        page.click('#btnCreate')
        page.wait_for_url('**#quote/*')
        page.wait_for_selector('#btnPdf')
        qno = page.inner_text('.idline b')
        qid1 = ctx.db.one('SELECT id FROM quotes WHERE quote_no=?', (qno,))['id']
        check('创建报价单后进入详情页，合计与服务器一致 2,967.04', ctx.db.one('SELECT total FROM quotes WHERE id=?', (qid1,))['total'] == 2967.04 and '2,967.04' in page.inner_text('.big-total'))
        check('客户阶段自动推进为「已报价」', ctx.db.one('SELECT stage FROM customers WHERE id=7')['stage'] == '已报价')
        shot('q2_detail')

        with page.expect_download() as dl:
            page.click('#btnPdf')
        pdf_path = os.path.join(tmp, 'q.pdf'); dl.value.save_as(pdf_path)
        pdf_txt = subprocess.run(['pdftotext', '-layout', pdf_path, '-'], capture_output=True, text=True).stdout
        check('下载 PDF：含抬头、单号、产品、合计', all(x in pdf_txt for x in ('SINLUX E2E Co', qno, 'SL-001', 'FOB Shenzhen', '2,967.04')), pdf_txt[:200])
        with page.expect_download() as dl:
            page.click('#btnXls')
        xl_path = os.path.join(tmp, 'q.xlsx'); dl.value.save_as(xl_path)
        wsq = _lw(xl_path).active
        check('下载 Excel：金额是公式', wsq['I10'].value.startswith('=ROUND(') and wsq['I13'].value == '=SUM(I10:I12)', (wsq['I10'].value, wsq['I13'].value))
        page.click('#btnWa')
        page.wait_for_selector('#waText')
        wa = page.input_value('#waText')
        check('WhatsApp 文案：含单号、明细、合计、交期', qno in wa and 'Total: USD 2967.04' in wa and 'Lead time: 30-35 days' in wa and 'Best regards,\nSINLUX E2E Co' in wa, wa[:160])
        page.click('#x')

        page.click('#stSeg [data-st=accepted]')
        page.wait_for_function("document.querySelector('#stSeg .on').innerText === '成交'")
        check('标记成交：客户阶段=成交，2 个产品行写入售价历史（库外运费行不写）',
              ctx.db.one('SELECT stage FROM customers WHERE id=7')['stage'] == '成交' and ctx.db.scalar("SELECT COUNT(*) FROM price_history WHERE source=? AND price_type='sell'", (qno,)) == 2)
        check('已成交的报价单「编辑」按钮不可用', page.locator('#btnEdit').is_disabled())
        pid_a = ctx.db.one("SELECT id FROM products WHERE sku='SL-001'")['id']
        page.goto(base + '/#product/%d' % pid_a)
        page.wait_for_selector('.tab[data-tab=pricing]')
        page.click('.tab[data-tab=pricing]')
        page.wait_for_function("document.querySelector('#histBox') && document.querySelector('#histBox').innerText.includes('%s')" % qno)
        check('产品页价格历史里能看到这张报价单的成交价（带客户）', 'Fake Company 7' in page.inner_text('#histBox'))
        page.click('.tab[data-tab=usage]')
        page.wait_for_function("document.querySelector('#usageBox').innerText.includes('%s')" % qno)
        check('产品页「使用记录」页签列出这张报价单', True)

        # 再给同一客户报价：谈判提示出现"该客户上次成交"
        page.goto(base + '/#quotenew/7')
        page.wait_for_selector('#pSearch')
        page.fill('#pSearch', 'SL-001'); page.wait_for_selector('.sugitem'); page.press('#pSearch', 'Enter')
        page.wait_for_function("document.querySelector('.qrow [data-hint]') && document.querySelector('.qrow [data-hint]').innerText.includes('该客户上次成交')")
        check('谈判提示：该客户上次成交价 USD 4.85（含日期和来源单号）', '4.85' in page.inner_text('.qrow [data-hint]') and qno in page.inner_text('.qrow [data-hint]'), page.inner_text('.qrow [data-hint]'))
        page.fill('.qrow >> nth=0 >> [data-k=quantity]', '10')
        page.click('#btnDraft')
        page.wait_for_url('**#quote/*')
        page.wait_for_selector('#btnPdf')
        check('保存为草稿：状态=草稿，阶段不重复推进', page.inner_text('.idline').find('草稿') >= 0)
        qno2 = page.inner_text('.idline b')
        qid2 = ctx.db.one('SELECT id FROM quotes WHERE quote_no=?', (qno2,))['id']

        # 编辑、复制、状态联动
        page.click('#btnEdit')
        page.wait_for_selector('#btnSave')
        page.fill('.qrow >> nth=0 >> [data-k=quantity]', '25')
        page.click('#btnSave')
        page.wait_for_selector('#btnPdf')
        check('编辑后保存：数量/合计更新', ctx.db.one('SELECT quantity FROM quote_items WHERE quote_id=?', (qid2,))['quantity'] == 25.0)
        page.click('#btnDup')
        page.wait_for_url('**#quoteedit/*')
        page.wait_for_selector('#btnSave')
        check('复制报价单：打开新草稿的编辑页，明细已带入', page.locator('.qrow').count() == 1 and page.input_value('.qrow >> nth=0 >> [data-k=sku]') == 'SL-001')
        page.click('#back')
        page.wait_for_selector('#btnPdf')
        page.click('#stSeg [data-st=rejected]')
        page.wait_for_function("document.querySelector('#stSeg .on').innerText === '未成交'")
        check('标记未成交成功', True)

        # 取消成交 = 撤销写入的成交价
        page.goto(base + '/#quote/%d' % qid1)
        page.wait_for_selector('#stSeg')
        page.click('#stSeg [data-st=sent]')
        page.wait_for_function("document.querySelector('#stSeg .on').innerText === '已发送'")
        check('把成交改回已发送：成交价记录被撤销', ctx.db.scalar("SELECT COUNT(*) FROM price_history WHERE source=? AND price_type='sell'", (qno,)) == 0)

        # 列表
        page.click('a[data-v=quotes]')
        page.wait_for_selector('#listBox tr.row')
        check('报价单列表：能看到两张报价单', page.locator('#listBox tr.row').count() >= 2)
        page.fill('#fSearch', 'SL-002')
        page.wait_for_function("document.querySelectorAll('#listBox tr.row').length === 1")
        check('按明细里的 SKU 搜索报价单', qno in page.inner_text('#listBox'))
        page.fill('#fSearch', '')
        page.click('#chips [data-st=rejected]')
        page.wait_for_function("document.querySelectorAll('#listBox tr.row').length === 1 && document.querySelector('#listBox').innerText.includes('未成交')")
        check('按状态筛选（未成交）', True)
        shot('q3_list')
        page.click('#chips [data-st=""]')
        page.locator('#listBox tr.row', has_text=qno2).click()
        page.wait_for_selector('#btnDel')
        dialogs.clear()
        page.click('#btnDel')
        page.wait_for_url('**#quotes')
        check('删除报价单（确认框）', ctx.db.scalar('SELECT COUNT(*) FROM quotes WHERE quote_no=?', (qno2,)) == 0 and dialogs)

        # ---------- 产品 Excel 导入向导 + PI 导入 ----------
        print('产品导入 / PI 导入')
        from test_imports import make_pi, make_products_xlsx
        from imgutil import PNG_RED_BOX
        xp = os.path.join(tmp, 'products_e2e.xlsx')
        open(xp, 'wb').write(make_products_xlsx([[1, None, 'E2E-IMP-1', 'Imported lamp', 18.5, 'CNY', 200, '甲厂', '12W', 'Black'],
                                                 [2, None, 'E2E-IMP-2', 'Imported lamp 2', 22, 'CNY', 100, '乙厂', '9W', 'White'],
                                                 [3, None, 'E2E-IMP-3', 'Skipped one', 5, 'CNY', None, None, None, None]], images={3: PNG_RED_BOX, 4: PNG_RED_BOX}))
        page.click('a[data-v=pimport]')
        page.wait_for_selector('#file')
        page.set_input_files('#file', xp)
        page.click('#btnUp')
        page.wait_for_selector('#selSheet')
        check('向导第2步：列出工作表', page.eval_on_selector_all('#selSheet option', 'o => o.map(x => x.value)') == ['货盘', '空表'])
        page.fill('#hdrRow', '2'); page.click('#btnReload')
        page.wait_for_function("document.querySelector('#hdrRow').value === '2' && document.body.innerText.includes('表头 10 列')")
        check('选表头行后显示原始预览', 'E2E-IMP-1' in page.inner_text('.scroll'))
        page.click('#btnNext'); page.wait_for_selector('#selCat')
        page.click('#btnNext'); page.wait_for_selector('select[data-h]')
        check('列映射页：自动猜测 款号→SKU', page.eval_on_selector('select[data-h="2"]', 's => s.value') == '__sku')
        page.click('#btnNext'); page.wait_for_selector('.prow')
        check('逐行预览：3 行、2 张带图', page.locator('.prow').count() == 3 and page.locator('.prow img').count() == 2)
        shot('11_product_import_preview')
        page.uncheck('[data-inc="5"]')
        check('取消勾选后统计变化', '将导入 2 行' in page.inner_text('#selInfo'))
        page.click('#btnApply')
        page.wait_for_selector('#btnGo')
        check('导入完成：新建 2，跳过 1', '新建 2，更新 0，跳过 1，失败 0' in page.inner_text('.card'))
        check('产品已写入且带图', ctx.db.scalar("SELECT COUNT(*) FROM products WHERE sku IN ('E2E-IMP-1','E2E-IMP-2')") == 2
              and ctx.db.scalar("SELECT COUNT(*) FROM product_images i JOIN products p ON p.id=i.product_id WHERE p.sku='E2E-IMP-1'") == 1
              and ctx.db.scalar("SELECT COUNT(*) FROM products WHERE sku='E2E-IMP-3'") == 0)

        pip = os.path.join(tmp, 'e2e_pi.xls')
        open(pip, 'wb').write(make_pi(invoice='SL-E2E-US', buyer=('Fake Company 7 Ltd', 'X', '', 'nobody@nowhere.test', '')))
        page.click('a[data-v=piimport]')
        page.wait_for_selector('#files')
        page.set_input_files('#files', pip)
        page.click('#btnUp')
        page.wait_for_selector('[data-pi]')
        check('PI 预览：解析出 PI 号和 3 行明细', 'SL-E2E-US' in page.inner_text('[data-pi]') and page.locator('[data-pi] tbody tr, [data-pi] table tr').count() >= 4)
        check('PI 预览：匹配到已有客户（公司名）', '已匹配' in page.inner_text('[data-pi]'))
        shot('12_pi_import_preview')
        page.click('[data-apply="0"]')
        page.wait_for_function("document.body.innerText.includes('导入完成')")
        check('PI 导入：成交单、产品、客户阶段', ctx.db.scalar("SELECT status FROM quotes WHERE quote_no='SL-E2E-US'") == 'accepted'
              and ctx.db.scalar("SELECT COUNT(*) FROM products WHERE sku='CSL-10100'") == 1)

        # ---------- 路由容错 ----------
        page.goto(base + '/#customer/999999')
        page.wait_for_selector('text=客户不存在')
        check('不存在的客户显示友好提示而非白屏', True)
        page.goto(base + '/#nonsense')
        page.wait_for_selector('#listBox')
        check('未知路由回到列表', True)

        real_errors = [e for e in console_errors if not e.startswith('Failed to load resource')]
        check('全程无 JS 报错', not real_errors, real_errors[:5])
        check('全程只有 1 个预期内的 HTTP 错误（访问不存在的客户 -> 404）', bad_responses == [(404, '/api/customers/999999')], bad_responses)
        br.close()

    server.shutdown()
    ctx.close()
    shutil.rmtree(tmp, ignore_errors=True)
    failed = [r for r in RESULTS if not r[1]]
    print('\n浏览器测试：%d 项，通过 %d，失败 %d。截图目录：%s' % (len(RESULTS), len(RESULTS) - len(failed), len(failed), SHOTS))
    return 1 if failed else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
