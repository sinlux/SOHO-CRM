# -*- coding: utf-8 -*-
"""产品查重：名称相似 + 图片相似 + SKU 近似。只给「证据」，是否合并永远由用户决定。

- 名称：规范化后（小写、去标点、拆词）用 SequenceMatcher 比字符相似度，同时比词集合重合度，取较高者。
  ≥0.88 视为「高度相似」，≥0.72 视为「可能相似」。
- 图片：感知哈希（dHash 64 位）汉明距离 ≤5 高度相似，≤10 可能相似；纯白/纯色图不参与（会全部相等）。
- SKU：去掉符号和大小写后相同（CSL-10100 与 csl10100）。
"""
import difflib
import os
import re
import threading

HIGH_NAME, MID_NAME = 0.88, 0.72
HIGH_IMG, MID_IMG = 5, 10
COLOR_TOL = 60                    # 4x4 颜色签名里任一格的平均色差超过这个值，就不算同一张图
_CJK = re.compile(r'[一-鿿]')
_cache = {}                      # 文件路径 → (mtime, hash 或 None)
_lock = threading.Lock()


def norm_name(s):
    s = (s or '').lower()
    s = re.sub(r'[\(\)\[\]（）【】]', ' ', s)
    s = re.sub(r'[^0-9a-z一-鿿]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def tokens(s):
    n = norm_name(s)
    out = set()
    for w in n.split():
        if _CJK.search(w):
            out.update(w[i:i + 2] for i in range(max(1, len(w) - 1)))          # 中文用二元组
        else:
            out.add(w)
    return out


def name_score(a, b):
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = tokens(a), tokens(b)
    jac = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    sm = difflib.SequenceMatcher(None, na, nb)
    if sm.real_quick_ratio() < 0.5 or sm.quick_ratio() < 0.5:
        return jac * 0.95
    return max(sm.ratio(), jac * 0.95)


def sku_key(s):
    return re.sub(r'[^0-9a-z]', '', (s or '').lower())


def signature(path):
    """(64 位差值哈希, 4x4 颜色签名)；打不开或几乎纯色返回 None。按文件修改时间缓存。
    只比灰度形状会把「同形状不同颜色」的产品误判成同一个，所以同时比颜色。"""
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None
    with _lock:
        c = _cache.get(path)
        if c and c[0] == mt:
            return c[1]
    sig = None
    try:
        from PIL import Image, ImageStat
        with Image.open(path) as im:
            im = im.convert('RGB')
            g = im.convert('L').resize((9, 8), Image.Resampling.LANCZOS)
            if ImageStat.Stat(g).stddev[0] >= 6:
                px = list(g.getdata())
                h = 0
                for r in range(8):
                    for c2 in range(8):
                        h = (h << 1) | (1 if px[r * 9 + c2] > px[r * 9 + c2 + 1] else 0)
                small = im.resize((4, 4), Image.Resampling.BOX)
                sig = (h, list(small.getdata()))
    except Exception:
        sig = None
    with _lock:
        _cache[path] = (mt, sig)
    return sig


def dhash(path):
    s = signature(path)
    return s[0] if s else None


def img_distance(a, b):
    """两张图的差异（汉明距离）；颜色差太大直接当作不相似（返回 64）。"""
    if a is None or b is None:
        return 64
    worst = max(sum(abs(x - y) for x, y in zip(p, q)) / 3.0 for p, q in zip(a[1], b[1]))
    return 64 if worst > COLOR_TOL else bin(a[0] ^ b[0]).count('1')


def hamming(a, b):
    return bin(a ^ b).count('1')


def find_matches(items, existing, top=3):
    """items: [{'key', 'sku', 'name', 'image_path'}]  existing: [{'id','sku','name','image_path', ...}]
    返回 {key: [{'id', 'sku', 'name', 'reasons': [...], 'score'}]}（按相似度降序，最多 top 个）。"""
    index = {}
    for e in existing:
        for t in tokens(e['name']):
            index.setdefault(t, set()).add(e['id'])
    by_id = {e['id']: e for e in existing}
    by_sku = {}
    for e in existing:
        by_sku.setdefault(sku_key(e['sku']), []).append(e)
    ehash = {e['id']: signature(e['image_path']) if e.get('image_path') else None for e in existing}
    out = {}
    for it in items:
        cand = {}
        sk = sku_key(it.get('sku'))
        if sk:
            for e in by_sku.get(sk, []):
                cand.setdefault(e['id'], {'reasons': [], 'score': 0.0})
                cand[e['id']]['reasons'].append('sku_same' if (e['sku'] or '').lower() == (it['sku'] or '').lower() else 'sku_near')
                cand[e['id']]['score'] = max(cand[e['id']]['score'], 1.0 if (e['sku'] or '').lower() == (it['sku'] or '').lower() else 0.97)
        ids = set()
        for t in tokens(it.get('name')):
            ids |= index.get(t, set())
        for eid in ids:
            sc = name_score(it.get('name'), by_id[eid]['name'])
            if sc >= MID_NAME:
                c = cand.setdefault(eid, {'reasons': [], 'score': 0.0})
                c['reasons'].append('name_high' if sc >= HIGH_NAME else 'name_mid')
                c['score'] = max(c['score'], sc)
        ih = signature(it['image_path']) if it.get('image_path') else None
        if ih is not None:
            for eid, h in ehash.items():
                if h is None:
                    continue
                d = img_distance(ih, h)
                if d <= MID_IMG:
                    c = cand.setdefault(eid, {'reasons': [], 'score': 0.0})
                    c['reasons'].append('image_high' if d <= HIGH_IMG else 'image_mid')
                    c['score'] = max(c['score'], 1.0 - d / 64.0)
        rows = []
        for eid, c in cand.items():
            e = by_id[eid]
            rows.append({'id': eid, 'sku': e['sku'], 'name': e['name'], 'reasons': sorted(set(c['reasons'])), 'score': round(c['score'], 3),
                         'thumb_url': e.get('thumb_url', '')})
        rows.sort(key=lambda x: -x['score'])
        if rows:
            out[it['key']] = rows[:top]
    return out


def find_groups(items):
    """文件内部互相重复的：返回 [[key, key, ...], ...]（并查集）。items 同上。"""
    parent = {it['key']: it['key'] for it in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    hs = {it['key']: (signature(it['image_path']) if it.get('image_path') else None) for it in items}
    tk = {it['key']: tokens(it.get('name')) for it in items}
    n = len(items)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = items[i], items[j]
            same = False
            if sku_key(a.get('sku')) and sku_key(a.get('sku')) == sku_key(b.get('sku')):
                same = True
            elif tk[a['key']] & tk[b['key']] and name_score(a.get('name'), b.get('name')) >= HIGH_NAME:
                same = True
            elif hs[a['key']] is not None and hs[b['key']] is not None and img_distance(hs[a['key']], hs[b['key']]) <= HIGH_IMG:
                same = True
            if same:
                parent[find(a['key'])] = find(b['key'])
    groups = {}
    for it in items:
        groups.setdefault(find(it['key']), []).append(it['key'])
    return [g for g in groups.values() if len(g) > 1]


def scan_library(existing, max_groups=200):
    """全库查重（只读）：返回疑似重复的分组 [{'members': [{id, sku, name, thumb_url}], 'reasons': [...]}]。
    名称用词索引找候选；图片用鸽巢原理分块（64 位拆 6 块，汉明距离 ≤5 的两张图至少有一块完全相同）找候选，避免两两全比。"""
    n = len(existing)
    parent = list(range(n))
    why = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(i, j, reason):
        a, b = find(i), find(j)
        if a != b:
            parent[a] = b
        why.setdefault(i, set()).add(reason); why.setdefault(j, set()).add(reason)

    # SKU 近似
    by_sku = {}
    for i, e in enumerate(existing):
        k = sku_key(e['sku'])
        if k:
            by_sku.setdefault(k, []).append(i)
    for idx in by_sku.values():
        for a in idx[1:]:
            union(idx[0], a, 'sku_near')
    # 名称
    index = {}
    toks = [tokens(e['name']) for e in existing]
    for i, ts in enumerate(toks):
        for t in ts:
            index.setdefault(t, []).append(i)
    seen = set()
    for t, idx in index.items():
        if len(idx) > 150:                                         # 太常见的词（led / light）不拿来配对
            continue
        for x in range(len(idx)):
            for y in range(x + 1, len(idx)):
                pair = (idx[x], idx[y])
                if pair in seen:
                    continue
                seen.add(pair)
                if name_score(existing[pair[0]]['name'], existing[pair[1]]['name']) >= HIGH_NAME:
                    union(pair[0], pair[1], 'name_high')
    # 图片
    sigs = [signature(e['image_path']) if e.get('image_path') else None for e in existing]
    buckets = {}
    for i, sg in enumerate(sigs):
        if sg is None:
            continue
        for part in range(6):
            lo = part * 11
            buckets.setdefault((part, (sg[0] >> lo) & 0x7FF), []).append(i)
    done = set()
    for idx in buckets.values():
        if len(idx) > 300:
            continue
        for x in range(len(idx)):
            for y in range(x + 1, len(idx)):
                pair = (idx[x], idx[y])
                if pair in done:
                    continue
                done.add(pair)
                if img_distance(sigs[pair[0]], sigs[pair[1]]) <= HIGH_IMG:
                    union(pair[0], pair[1], 'image_high')
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = []
    for g in groups.values():
        if len(g) < 2:
            continue
        reasons = sorted({r for i in g for r in why.get(i, ())})
        out.append({'members': [{'id': existing[i]['id'], 'sku': existing[i]['sku'], 'name': existing[i]['name'], 'thumb_url': existing[i].get('thumb_url', '')} for i in g],
                    'reasons': reasons})
    out.sort(key=lambda x: (-len(x['members']), x['members'][0]['name']))
    return out[:max_groups]
