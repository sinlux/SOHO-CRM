# SINLUX CRM v5 重写进度

依据《SINLUX CRM v4.4 功能说明》分批重写。技术栈不变：Python 标准库 + SQLite + 原生 JS，零依赖，Windows 本地运行。

## 进度
- [x] 第一批：数据层 + 客户模块
- [x] 界面改版：左侧栏 + 吉卜力配色 + 苹果式圆角毛玻璃，字体霞鹜文楷（OFL，内置于 app/static/fonts）
- [x] 第二批：产品库 + 价格历史（含供应商比价、合并同类项、SKU 自动编号、汇率）
- [x] 第三批：报价单 + PDF/Excel/WhatsApp 输出
- [ ] 第四批：Excel 产品导入 + PI 导入
- [ ] 第五批：看板、设置（汇率/抬头/WA模板）、升级机制、旧 QuoteMaster 迁移

## 结构
```
app/main.py              入口
app/sinlux/core/         db(外键开启+事务) schema migrations http(路由/安全) backup
app/sinlux/customers/    service intake xlsx_io enrich routes
app/sinlux/products/     seeds catalog(类目/SKU) rates(中行汇率+定时) pricing(建议价/价格历史) media(相册/文档) service(产品/供应商/合并/复制) routes
app/sinlux/core/imaging.py  产品图自动规范化（Pillow）
app/sinlux/quotes/       service(创建/编辑/状态联动) exporter(PDF/Excel/WhatsApp) settings(抬头+模板) routes
app/static/              index.html + css + js/(lib, main, pages/*)  —— 原单文件 HTML 已拆分
tests/                   unittest（249项）+ browser_e2e.py（Playwright 无头浏览器）
```
运行测试：`python -m unittest discover -s tests`；浏览器测试：`python tests/browser_e2e.py`（需 `pip install playwright` + chromium，仅开发用）。

## 本批相对 v4.4 的行为变化（需知晓）
1. 删除客户：必须二次确认（接口返回影响范围），并**级联删除其报价单**；价格历史保留、客户置空。旧版会留下孤儿报价。
2. 旧库迁移（启动自动、幂等）：notes/reminders/enrichments 重建为带外键；已被旧版删除客户遗留的备注/提醒/报价，保全到名为「【已删除客户 #id】」的占位客户下，不丢数据；stage 的英文 key（won/quoted…）统一为中文。
3. 客户 Excel 导入改为「上传 → 预览 → 确认」，会话落盘（重启不丢），整批一个事务；LV 越界/小数会报告并置空。不再读取 data 文件夹里的 xlsx。
4. AI 背调：代码强制来源校验（来源 URL 不在抓取材料中的条目丢弃）；写入时只接受提取结果里的值；同一结果只能写入一次。
5. 设置页不再回显 API Key 明文。
6. 接口改为 REST 风格（GET/POST/PUT/DELETE），并拒绝非本机 Host/Origin。
7. 修复旧版：国家筛选无效（option 无 value）、阶段下拉重复、LIKE 通配符未转义、公式注入导出。
8. 本批 UI 只含：客户列表/详情/录入/提醒/Excel导入/设置(背调Key+备份)；产品、报价、看板等入口随后续批次加入。表已建好，旧库中的产品/报价数据原样保留。

## 记忆（踩过的坑）
- SQLite 改外键只能重建表；重建期间要 `PRAGMA foreign_keys=OFF`（事务外），且 `foreign_key_check` 只检查本次重建的表，否则别处历史问题会卡死启动。
- 旧库 notes 等无外键 → 有孤儿；先建同 id 占位客户再重建表，才不丢数据。
- 路由 `{id}` 必须只匹配数字，否则 `/api/customers/abc` 会 500。
- openpyxl 写入以 `=` 开头的字符串会当公式，需 `cell.data_type='s'`；不要给 `+` 开头的电话号码加前缀。
- 浏览器测试里"等元素出现"要避免命中上一页遗留的同名元素（竞态），应等具体内容。
- bat 脚本必须纯 ASCII（沿用 v4.4 的结论）。

## 第二批相对 v4.4 的行为变化
1. 删除产品需二次确认；会删价格历史、供应商报价（含截图文件），已有报价单明细保留为快照（仅解除产品关联）。
2. 只改名称/备注等不动价格的字段，不再重算建议价、不写价格历史（旧版每次保存都重算，会把"已对齐到成交价"的建议价冲掉）。
3. 供应商"采纳"现在真的会把该价格设为产品当前成本（CNY）并写入价格历史（旧版代码注释这么写，但接口没实现）。历史记录的生效日期用采纳当天，报价日期放备注。
4. 删除/手动添加价格记录后，产品当前成本和建议价按"日期最新记录"自动对齐（成本取最新成本记录；建议价取最新售价，没有售价则按公式）。
5. EUR/VND 成本沿用旧版"不换算"，界面标注"未换算"。汇率改动只影响之后保存的产品。
6. 首饰类目旧版没有预置 SKU 前缀，仍不替用户编造；需在「SKU前缀」里设置后才能自动编号。
7. 不含类目/规格字段的增删改界面（旧版也没有对应接口），本批未做。
## 记忆（第二批踩坑）
- 价格对齐按 effective_date 取最新：任何"现在做的决定"（采纳供应商）必须用当天日期，否则会被日期更晚的旧记录压过。
- 浏览器测试里被前面步骤删掉的测试数据不能再引用（客户 4 被删后再关联会失败）。
- 同一 `root.addEventListener('click')` 每次渲染都叠加会导致重复触发，统一用 `root.onclick =` 并在切页时清空。
- 测试里共享状态的 legacy 库类要拆成独立类，否则按字母序执行的用例会互相污染。

## 第二批增强（汇率 / 产品页布局 / SKU / 图片）
1. 汇率：默认自动模式，抓取中国银行「美元·现汇买入价」（人民币/100美元），汇率 = 100 ÷ 买入价。启动 15 秒后抓一次，之后每 2 小时一次（失败 30 分钟重试），同一发布日期只保留最新一条（rate_history）。**未在真实 BOC 页面上验证**（沙箱无法访问），解析器按已知页面结构+固定样本测试；抓不到时保留旧汇率并在界面显示原因，也可手动填「中行牌价」或汇率。
2. 产品页：身份栏（缩略图/SKU/状态/保存·复制·删除）+ 左侧吸顶（相册、价格摘要）+ 右侧 7 个页签。products 新增列：status/unit/brand/series/hs_code/origin/pcs_per_carton/carton_l·w·h/gross_weight/net_weight；新表 product_images / product_files / rate_history。
3. SKU：沿用 <类目前缀><子类前缀><6位序号>。预置 灯饰 SL（20 个子类）、家具 GL（25 个，含酒店 FF&E 品类）、装饰材料 DC（15 个）、其他 OT；选子类后自动编号；完整前缀全局唯一校验；种子只补不覆盖，选项只并入不删除。
4. 图片：上传→暂存→保存产品时认领；任何格式（JPEG/PNG/WebP/GIF/BMP/TIFF/AVIF）自动 EXIF 转正、透明铺白、纯色背景（含灰/彩影棚底）换白并裁到产品、居中放进 1600×1600 白底（四周留 6%），另出 480 缩略图；实景/渐变背景只等比放入不裁；分辨率偏低有提示。Pillow 不可用时原样保存，页面用 CSS 居中显示。旧图可在产品库「维护 ▾ → 统一旧图片规格」一键规范。
## 记忆（踩坑）
- Windows 的 Pillow（libs/PIL 里是 .pyd）在 Linux 上导不进；main.py 在 Windows 优先用内置 libs，其它系统把内置 libs 放最后。
- 纯色背景去除不能全图替换颜色，只处理"从四角连通进来"的背景，否则产品内部同色镂空会被误刷。连通域在 ≤500px 的缩略图上做，保证 1200 万像素图也很快。
- 15/16 位灰度 PNG 读出来是 I;16，需要 /256 缩到 8 位；测试数据本身也要真的是 16 位。
- 页签状态是模块变量，换产品要重置，否则"新建产品"可能停在上一次的隐藏页签里。

## 第三批（报价单）相对 v4.4 的行为变化
1. 草稿不推进客户阶段（发出/成交才变「已报价」）；沉睡客户收到报价也回到「已报价」（旧版意图如此，但旧代码因中英文混写没生效）。
2. 成交只写一次售价历史：旧版每点一次「成交」就重复写一遍并把客户推成「复购」。新版客户阶段由数据推出：该客户已有其它成交报价 → 复购，否则成交；同一张反复切换不会升级。
3. 取消成交（改回其它状态）或删除已成交的报价单，会撤销它写入的成交价；删除已成交的需二次确认。成交价的生效日期是成交当天（旧版用报价创建日）。
4. 新增：编辑（成交的除外）、复制为草稿、按明细 SKU/名称搜索、过期提示（超有效期仍为「已发送」时显示「已过有效期」，不自动改状态）。
5. 金额：逐行 ROUND_HALF_UP 到分，合计 = 各行金额之和；Excel 用 =ROUND(数量*单价,2) 和 =SUM；前端用整数运算避免 JS 浮点差一分。
6. WhatsApp 模板只替换已知变量（不再用 str.format，旧版用户模板里多一个花括号会整体报错，且 {0.__class__} 之类能读对象内部信息）。默认模板落款改为 {sender}（设置里的公司名）。
7. PDF：用户文本转义（旧版里含 & < > 会让 reportlab 报错/被当标记）；超长规格截断；单行过高时可跨页；页脚「Page x / y」；产品图用 480px 缩略图。找不到中文字体时用 reportlab 自带 CID 宋体，不再出现方块。
8. 报价币种仅 USD/EUR/CNY；产品建议价是美元：USD 报价自动带入，CNY 按当前汇率换算，EUR 不自动填（避免错价，提示手填）。
## 记忆（第三批踩坑）
- reportlab 的 Table 单行高于一页会抛 LayoutError：需 splitInRow=1 + 截断超长文本。
- 前端金额必须用整数/BigInt 运算，7×1.005 在 JS 浮点下四舍五入会和 Decimal 差一分。
- 「撤销成交价」按 source=报价单号 + customer_id 精确匹配，避免误删手动录入或 PI 导入的售价；SQLite 里 NULL 比较要用 `IS ?`。
- 报价单号按数值取最大序号（字符串排序下 -1000 会排在 -999 前面）。

## 第三批反馈修订（币种 / LOGO / 收款信息 / 字体）
- 币种：只有 USD（对客户）与 CNY（对供应商）。新输入拒绝 EUR/VND；旧数据遗留的 EUR/VND 可继续显示、在币种不变时可继续保存（`LEGACY_CURRENCIES`）。
- 已确认的行为：成交日期=点击成交当天；草稿不推进客户阶段；成交价不覆盖产品建议价（不同客户价格不同，只写价格历史）。
- 抬头/收款信息默认值来自用户提供的资料，只在该键从未保存过时生效（`QuoteSettings._val`）；清空后保存即为空，不会被默认值顶回来。银行名按用户原文 `CO..LTD` 保存，如有笔误请在设置里改。
- LOGO：内置 `app/assets/logo.png`（源图只有 234×83，放大 + 去底色 + 两色重绘）；设置页可上传替换（`imaging.logo_png` 自动去背景色、裁边、放大），恢复默认即删除自定义文件。
- 字体：Nunito（Medium/SemiBold/Bold/ExtraBold，OFL，`app/static/fonts/nunito/`）直接由 reportlab 嵌入 PDF，不需要安装到系统。Nunito 没有汉字，中文片段用 `_mix()` 套 `<font name=中文字体>`。
- 踩坑：reportlab `ParagraphStyle` 的 lambda 里同时给默认 fontName 和 kw 会重复关键字 → 合并 dict；页宽可用 = 182mm − 框架内边距，表格总宽别超 176mm，否则居中后左缘与段落错位；Playwright 在这个云环境要用 `CHROMIUM_PATH=/opt/pw-browsers/chromium-1194/chrome-linux/chrome`。
- 未验证：Windows 上的 PDF 中文字体实际效果；Excel 在没装 Nunito 的电脑上会回退默认字体（数据不受影响）。未做 Windows 自动装字体（PDF 用的是打包字体，不需要）。

## LOGO 换成用户提供的 SVG
- 用户 SVG 里的字体（Fraunces / Hanken Grotesk）系统没有 → 从 npm `@expo-google-fonts/*` 取静态 TTF，用 Chromium 以 4 倍分辨率渲染成透明 PNG（1509×304）再裁边，存为 `app/assets/logo.png`；后台侧边栏和报价单共用（侧边栏窄屏时回退成小图标）。
- 报价单主色改为 LOGO 的深蓝 #1F3A5F + 金色 #B8942A。
- 收款信息默认值按用户文字版更新（`CO.,LTD`、`NO.21 TIYU ROAD`；全角逗号改为半角）。已在设置页保存过旧值的电脑需手动改一次。

## 第四批（Excel 产品导入 + PI 导入）
- 代码：`app/sinlux/imports/`（xlsx_images 图片/表头读取、product_import 向导会话、pi_import PI 解析与写入、routes）；前端 `product_import.js` / `pi_import.js`；`QuoteService.create_imported` 用于 PI 成交单；xlrd 已放入 `app/libs`（纯 Python，只读 .xls）。
- 行为：会话只在内存，文件/图片在 `data/import_tmp/products/<会话>`，程序启动时清掉遗留；预览阶段完全只读；单行出错不拖垮整批，失败行会列出原因；PI 整张在一个事务里，任何一行出错整张回滚。
- 决定（与第三批一致）：PI 的成交价写入售价历史（带客户、PI 日期、PI 号），但**不覆盖产品建议价**（建议价按「成本×汇率×(1+利润率)」重算）——`PriceHistory.realign(use_sell=False)`；手动添加的售价记录仍沿用旧逻辑（对齐最新售价）。
- 币种：PI 成本固定 CNY；产品表里的币种列只认 CNY/USD（含 RMB/¥/人民币/$/美元别名），别的币种提醒并退回统一币种。
- 规格描述：产品表导入对已有 SKU 在有值时覆盖；PI 导入只在规格为空时填充。新建产品才应用统一利润率。
- 踩坑：测试里 X-Filename 头必须 URL 编码（中文文件名）；xlwt 仅测试用（`pip install xlwt`），生产不需要；路由 `header_row=0` 不能用 `or 1` 兜底（会把 0 吞成 1）。
- 未验证：真实 166MB 首饰表、真实 SINLUX PI（云端只有按文档模板造的假 PI）；Windows 上的 .xls 读取。

## 第五批（看板 / 设置 / 升级机制 / 旧 QuoteMaster 迁移）
- 看板口径写死在 `sinlux/dashboard.py`：转化率 = 成交 ÷ 非草稿报价；金额折美元（CNY 按当前汇率，旧 EUR/VND 不进排名只计数）；沉睡 = LV≥4 且 90 天内无备注无报价。首页默认就是看板（无 hash 时），未知路由仍回客户列表。
- 升级包（`core/updater.py`）：路径白名单 `safe_rel`（app/ 下任意文件 + 根目录 bat/txt；拒绝 data/ versions/ .. 绝对路径 盘符 反斜杠 符号链接 重复条目 超大包）；先解到临时目录再覆盖，中途失败自动还原；快照不含 libs（包里动到的 libs 文件才逐个进快照）；回退会删掉升级新增的 app 文件。API 不再接受任意 zip 路径，只认刚上传的 token。升级包生成：`python tools/make_update.py <旧ref> <新ref> "说明" out.zip`。
- 旧版 v4.4 的 `/api/update/apply` 接受任意 `zip_path`——新版已堵掉。
- 迁移（`migrate/quotemaster.py`）：整个迁移一个事务，图片在事务后处理（失败只给提示）；识别重复：SKU 不区分大小写、客户按邮箱/公司名/仅姓名、供应商比价按 产品+供应商+价格+日期、旧报价按 OLD-xxxx、备注按原文；旧成交报价的成交价写入售价历史（来源 OLD-xxxx）；旧 EUR 报价原币种保留。迁移前自动备份。
- 重启：升级/回退后需用户手动关闭窗口重开（没做自动重启——Windows 下 execv 不可靠，且手动更稳）。
- 未验证：真实 SQLCipher 解密（云端没有 sqlcipher3，用同结构的普通 sqlite 库测迁移逻辑）；Windows 上的升级覆盖正在运行的 .py（Python 已加载的模块不受影响，重启后生效）；真实旧 QuoteMaster 库的列是否与我按旧代码推断的完全一致（缺列会被容忍，但没见过真库）。

## 启动脚本修复（用户验收时发现）
- 现象：用户电脑没装 Python，启动.bat 走「自动下载便携版」，三个镜像全部失败，报 `'powershell' 不是内部或外部命令`——**根因是用户这台电脑的 PATH 里找不到 powershell，不是网络问题**（三次失败的原因相同）。
- 修复：① 完整安装包内置官方 Python 3.12（nuget.org 上 PSF 发布的 `python` 包，精简后放 `python/`），开箱即用，启动.bat 优先用它（app/libs 里的 Pillow 等是 cp312 编译的，系统装别的版本会不匹配）；② 下载兜底改用 `%SystemRoot%\System32\curl.exe`、powershell 全路径，解压失败再试 `tar`；③ 下载不下来时给出手动安装指引。
- 生成：`python tools/make_release.py HEAD 输出目录 --python python.3.12.10.nupkg`。
- 未在真实 Windows 上验证 bat 的新逻辑（云端只能检查语法/编码：纯 ASCII + CRLF）。

## 5.0.0-rc.2（用户验收反馈）
- 类目/字段/子类管理（`products/catalog.py` 末尾）：用户删掉的内置类目/字段/子类记在 settings 的 `seed_removed`，`ensure_seeds` 每次启动会补缺失的内置项，不记下来删了会"复活"。「子类」= 字段 `subcategory` 的选项 + `subcategory_prefixes`，两边一起改；改名时同步产品上已填的值和字段的适用范围。新字段 key 自动生成（`f_xxxx`），中文名只是 label。`category_fields.applies_to` 空 = 所有子类。UI 里被收起的字段仍在 DOM 里（只 display:none），所以保存时值不会丢。
- 供应商：新表 `suppliers` / `supplier_chats`，`supplier_quotes` 加 `supplier_id`、`project`；启动迁移 `_adopt_suppliers` 把历史名字收拢成档案（幂等）。API 用 `/api/vendors`（`/api/suppliers/{id}` 早就被「供应商报价」占用）。
- OCR：`suppliers/ocr.py` + `ocr_win.ps1` 用 Windows 自带 `Windows.Media.Ocr`（本机识别，不上传）；非 Windows 返回 unavailable。**未在真实 Windows 上验证**（云端只能测切条/清洗逻辑和状态流转）。需要系统装有中文语言包才能识别中文。识别结果会去掉汉字间的空格；超长截图按 5000px 切条。后台线程识别，期间用户手改过文字则不覆盖（`ocr_status='pending'` 才写回）。
- HS 编码：没有拿到用户说的海关网站，所以做成「设置里填查询网址（可用 {keyword}）+ 本库已用编码下拉复用」，没有内置任何编码数据（不编造）。
- 前端防抖：搜索框的 250ms 定时器触发时用户可能已切页，`load` 开头要判断元素还在。

## 5.0.0-rc.3
- 备注截图 `products/shots.py`（表 `product_shots`，目录 `data/product_shots`）：OCR 逻辑与供应商聊天一致（后台线程；手改过文字则不覆盖）；产品搜索 EXISTS 子查询搜 `ocr_text`；删除/合并产品时清文件/迁移。
- PI：预览返回 `rate` / `margin_pct` / `profit`（只估算，不写库）；采购价列兼容 `rmb/采购总价…`（总价÷数量）与 `采购单价…`（直接单价）。**真实 PI 的列名是否都能识别还没验证**——识别不了会报「无法识别明细表头」并指出缺哪列。
- HS：拿不到 hsbianma.com 的搜索网址格式（云端访问被拦），所以默认只打开网站并把产品名复制到剪贴板；如果用户确认它支持关键词网址，把 `{keyword}` 写进设置里的网址即可。
- 「升级不动数据/设置」有专门测试：`tests/test_remark_data_safety.py::TestUpgradeKeepsUserData`（走真实升级接口 → data 目录逐文件哈希不变；重跑迁移+种子后 settings/app_settings 全等；想改 data 的包被拒绝）。
- 踩坑：产品页会记住上次停留的页签（`activeTab`，同一个产品 id 重新进入时不重置），e2e 要先点「概览」页签再找 `#pRemark`。

## 5.0.0-rc.4（导入大改）
- **Failed to fetch 的根因**：上传路由在 `new_path()` 里发现扩展名不对就抛 ApiError，但请求体还没读，服务器直接回复并关连接，浏览器还在发送 → 只报 "Failed to fetch"。修复在 `core/http.py`：Request 跟踪未读字节 `_body_left`，错误回复前 `_drain()` 把剩余请求体读掉（上限 300MB，超过就关连接）。测试：`TestDocImportPI.test_wrong_extension_gives_clear_error_even_for_big_upload`（25MB 错误扩展名上传必须拿到 400 JSON）。
- `imports/grid.py`（xls/xlsx 统一成格子）、`docparse.py`（找表头 / 认列 / 表头上方抽 PI 号、日期、买家 / 判断类型）、`dedupe.py`（名称相似 + 图片感知哈希+颜色签名 + SKU 近似；全库查重用鸽巢分块）、`docimport.py`（识别 → 清洗 → 校验 → 写入）。
- 踩坑：① 「单价(RMB)」里的 RMB 只是币种，不能因此认成采购总价（_WEAK 词只在整格相等时才算）；② 中文表头词只有 2 个字（单价/数量），包含匹配要放行 CJK；③ 「Item No.」里全是 1、2、3 的是序号不是型号；④ 「采购价」列可能是单件也可能是整行合计——比较它和售价×7 的中位数，>1.6 就按合计折算并给出提示；⑤ 只比灰度哈希会把「同形状不同颜色」当成同一产品，必须加 4x4 颜色签名；⑥ 规范化后的库图和 PI 里的原图构图不同，查重前要把 PI 里的图走同样的 `imaging.process` 再比；⑦ 结果里 `skipped` 同时表示「PI 号重复」和「跳过的产品数」导致界面显示 undefined——计数改名 `products_skipped`。
- 供应商报价单只接受 CNY（`supplier_quotes.price_cny` 是人民币列），USD 报价会明确拒绝并说明，不静默丢。
- xls 里的图片取不出（BIFF 格式），界面提示另存为 xlsx。PDF / 图片版报价单暂不支持。
- 未验证：用户真实的 PI/报价单版式（只用造的几种版式测过，识别不准时有手动指定列兜底）。
