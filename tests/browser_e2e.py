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
    ctx, server = create_app(data, port=0, net=net)
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
            d.accept()
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
