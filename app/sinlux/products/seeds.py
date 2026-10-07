# -*- coding: utf-8 -*-
"""从旧QuoteMaster提取的类目与字段种子数据"""

DEFAULT_CATEGORIES = [
    {'code': 'lighting', 'name': '灯饰', 'icon': '💡', 'sort_order': 1},
    {'code': 'furniture', 'name': '家具', 'icon': '🪟', 'sort_order': 2},
    {'code': 'other', 'name': '其他', 'icon': '📦', 'sort_order': 3},
]

LIGHTING_FIELDS = [
    {'key': 'subcategory', 'label': '子类', 'type': 'select', 'required': True,
     'options': '射灯|筒灯|灯带|球泡|面板灯|吊灯|壁灯|台灯|工矿灯|路灯|投光灯|轨道灯|线条灯|装饰灯|其他'},
    {'key': 'wattage', 'label': '瓦数', 'type': 'number', 'required': True, 'unit': 'W'},
    {'key': 'cct', 'label': '色温', 'type': 'text', 'required': True, 'placeholder': '如 3000K / 4000K / 2700-6500K 可调'},
    {'key': 'voltage', 'label': '电压', 'type': 'select', 'required': True,
     'options': '110V|220V|110-240V|12V DC|24V DC|36V DC|48V DC'},
    {'key': 'ip_rating', 'label': 'IP 等级', 'type': 'select', 'required': True,
     'options': 'IP20|IP44|IP54|IP65|IP66|IP67|IP68'},
    {'key': 'beam_angle', 'label': '光束角', 'type': 'number', 'unit': '°'},
    {'key': 'lumen', 'label': '光通量', 'type': 'number', 'unit': 'lm'},
    {'key': 'cri', 'label': '显色指数 CRI', 'type': 'number', 'placeholder': '默认 80'},
    {'key': 'lamp_base', 'label': '灯头类型', 'type': 'select',
     'options': 'E27|E14|E12|GU10|GU5.3|G9|G4|B22|MR16|集成 LED|无'},
    {'key': 'dimensions', 'label': '尺寸', 'type': 'text', 'placeholder': '如 Φ100×H50mm'},
    {'key': 'dimming', 'label': '调光协议', 'type': 'select',
     'options': '不调光|Triac|0-10V|DALI|DMX512|PWM|Zigbee|蓝牙|WiFi'},
    {'key': 'lifespan', 'label': '寿命', 'type': 'number', 'unit': '小时'},
    {'key': 'warranty', 'label': '保修', 'type': 'select', 'options': '1 年|2 年|3 年|5 年|无'},
    {'key': 'certifications', 'label': '认证', 'type': 'multi',
     'options': 'CE|RoHS|UL|ETL|SAA|FCC|CCC|EMC|LM-80|ENEC'},
    {'key': 'remark', 'label': '备注', 'type': 'textarea', 'placeholder': '其他规格说明、包装信息等'},
]

FURNITURE_FIELDS = [
    {'key': 'subcategory', 'label': '子类', 'type': 'select', 'required': True,
     'options': '卫浴柜|办公椅|办公桌|沙发|床架|餐桌|餐椅|柜子|茶几|床头柜|书架|鞋柜|其他'},
    {'key': 'dimensions', 'label': '尺寸 W×D×H', 'type': 'text', 'required': True, 'placeholder': '如 900×500×850mm'},
    {'key': 'material', 'label': '主体材质', 'type': 'select', 'required': True,
     'options': '实木|多层板|MDF|刨花板|金属|玻璃|布艺|皮质|塑料|混合材质'},
    {'key': 'pack_method', 'label': '包装方式', 'type': 'select', 'required': True,
     'options': 'KD 拆装|RTA 平板装|整装|半拆装'},
    {'key': 'cbm_per_unit', 'label': '单件 CBM', 'type': 'number', 'required': True, 'unit': 'm³'},
    {'key': 'gross_weight', 'label': '单件毛重', 'type': 'number', 'unit': 'kg'},
    {'key': 'net_weight', 'label': '单件净重', 'type': 'number', 'unit': 'kg'},
    {'key': 'qty_20gp', 'label': '装柜量 20GP', 'type': 'number', 'unit': 'pcs'},
    {'key': 'qty_40hc', 'label': '装柜量 40HC', 'type': 'number', 'unit': 'pcs'},
    {'key': 'pack_material', 'label': '包装材料', 'type': 'select',
     'options': '5 层瓦楞纸箱+EPE|3 层纸箱+泡棉|木箱|牛皮纸|塑料膜+纸箱|其他'},
    {'key': 'finish', 'label': '表面处理', 'type': 'text', 'placeholder': '如哑光白 UV / 烤漆橡木色'},
    {'key': 'style', 'label': '风格', 'type': 'select',
     'options': '现代简约|北欧|美式|欧式|工业|中式|轻奢|日式|地中海'},
    {'key': 'hardware_brand', 'label': '五金件品牌', 'type': 'select',
     'options': 'DTC|Blum|海蒂诗 Hettich|海福乐 Hafele|国产|其他'},
    {'key': 'certifications', 'label': '认证', 'type': 'multi',
     'options': 'CARB Phase 2|TSCA Title VI|FSC|BSCI|SEDEX|CAL 117 阻燃|GREENGUARD'},
    {'key': 'warranty', 'label': '保修', 'type': 'select', 'options': '1 年|2 年|3 年|5 年|10 年|终身|无'},
    {'key': 'use_scenario', 'label': '适用场景', 'type': 'multi', 'options': '家用|酒店|办公|户外|商业|医疗'},
    {'key': 'customizable', 'label': '可定制', 'type': 'multi', 'options': '颜色|尺寸|材质|五金|包装|LOGO'},
    {'key': 'install_method', 'label': '安装方式', 'type': 'select', 'options': '无需安装|自带说明书|需专业安装|视频教程'},
    {'key': 'moq', 'label': '最小起订量', 'type': 'number', 'unit': 'pcs'},
    {'key': 'lead_time', 'label': '生产周期', 'type': 'number', 'unit': '天'},
    {'key': 'remark', 'label': '备注', 'type': 'textarea', 'placeholder': '客户特殊要求、定制说明等'},
]

OTHER_FIELDS = [
    {'key': 'subcategory', 'label': '子类', 'type': 'text', 'required': True,
     'placeholder': '自由填写，如 PET 瓶胚 / 办公纸 / 餐具…'},
    {'key': 'unit', 'label': '单位', 'type': 'select', 'required': True,
     'options': 'PCS|SET|BOX|CTN|KG|TON|MT|M|M²|M³'},
    {'key': 'specs', 'label': '规格说明', 'type': 'textarea', 'required': True,
     'placeholder': '所有产品规格请写在这里，越详细越好'},
    {'key': 'pack_info', 'label': '包装信息', 'type': 'textarea',
     'placeholder': '如 5000 pcs/编织袋；100 pcs/纸箱…'},
]

DEFAULT_CATEGORIES = DEFAULT_CATEGORIES + [
    {'code': 'jewelry', 'name': '首饰', 'icon': '💍', 'sort_order': 4},
]

# 首饰类目字段：subcategory 用于区分 耳环/戒指/手链/项链。
# 【重要说明】以下字段目前只依据"项链"报价单（款号/图片/莫桑钻尺寸/成品重/单件银重/货盘价）
# 确认过；戒指/耳环/手链的专属字段（如戒指的圈号、耳环的耳钉针类型等）尚未见到真实数据，
# 没有编造，等实际导入这三类文件时，用 Excel 导入向导的"新增字段"功能按需补充即可，
# 不会因为字段缺失导致数据丢失（导入向导会把无法识别的列原样存进备注）。
JEWELRY_FIELDS = [
    {'key': 'subcategory', 'label': '子类', 'type': 'select', 'required': True,
     'options': '项链|戒指|耳环|手链|其他'},
    {'key': 'gem_spec', 'label': '莫桑钻尺寸/规格', 'type': 'textarea', 'required': False,
     'placeholder': '如：全莫桑：圆形6.5mm*1粒 1.0-2,1.2-2,1.6-16/1.298CT'},
    {'key': 'finished_weight', 'label': '成品重', 'type': 'number', 'unit': 'g'},
    {'key': 'silver_weight', 'label': '单件银重', 'type': 'number', 'unit': 'g'},
    {'key': 'material', 'label': '材质', 'type': 'select',
     'options': '925银|S925镀金|S925镀白金|黄铜|其他'},
    {'key': 'remark', 'label': '备注', 'type': 'textarea'},
]

DEFAULT_FIELDS_MAP = {
    'lighting': LIGHTING_FIELDS,
    'furniture': FURNITURE_FIELDS,
    'other': OTHER_FIELDS,
    'jewelry': JEWELRY_FIELDS,
}
