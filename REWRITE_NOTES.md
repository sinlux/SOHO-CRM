# SINLUX CRM v5 重写进度

依据《SINLUX CRM v4.4 功能说明》分批重写。技术栈不变：Python 标准库 + SQLite + 原生 JS，零依赖，Windows 本地运行。

## 进度
- [x] 第一批：数据层 + 客户模块
- [x] 界面改版：左侧栏 + 吉卜力配色 + 苹果式圆角毛玻璃，字体霞鹜文楷（OFL，内置于 app/static/fonts）
- [ ] 第二批：产品库 + 价格历史
- [ ] 第三批：报价单 + PDF/Excel 输出
- [ ] 第四批：Excel 产品导入 + PI 导入
- [ ] 第五批：看板、设置（汇率/抬头/WA模板）、升级机制、旧 QuoteMaster 迁移

## 结构
```
app/main.py              入口
app/sinlux/core/         db(外键开启+事务) schema migrations http(路由/安全) backup
app/sinlux/customers/    service intake xlsx_io enrich routes
app/static/              index.html + css + js/(lib, main, pages/*)  —— 原单文件 HTML 已拆分
tests/                   unittest（86项）+ browser_e2e.py（Playwright 无头浏览器）
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
