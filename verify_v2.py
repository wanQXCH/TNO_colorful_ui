# -*- coding: utf-8 -*-
"""Comprehensive verification: CN overlay merge, strict colors, exclusions, fonts, deps."""
import os, re
import numpy as np
import tno_color_gen as g

fails = 0

def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    if not cond:
        fails += 1

for name, target in [('TNO_UI_FFBA5C', (255, 186, 92)),
                     ('TNO_UI_DarkPurple', (101, 70, 128)),
                     ('TNO_UI_White', (255, 255, 255))]:
    root = os.path.join('generated_mods', name)
    n = sum(len(fns) for _, _, fns in os.walk(root))
    tot = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fns in os.walk(root) for f in fns)
    print('=== %s: %d files, %.0f MB' % (name, n, tot / 1048576))

    # 1. 排除项
    bad = []
    for dp, _, fns in os.walk(root):
        for fn in fns:
            rp = os.path.relpath(os.path.join(dp, fn), root)
            low = rp.lower()
            if 'flag' in os.path.basename(fn).lower() or low.startswith('gfx' + os.sep + 'leaders') \
               or os.sep + 'goals' + os.sep in low or low.startswith('gfx' + os.sep + 'interface' + os.sep + 'goals'):
                bad.append(rp)
    check('no flags/leaders/goals ' + name, not bad, str(bad[:5]))

    # 2. CN 覆盖生效：汉化mod改过的贴图已按汉化版换色（取一个 CN 特有的文件）
    cn_only = []
    tno_files = set()
    for dp, _, fns in os.walk(os.path.join('D:\heart of iron\SW00383\langou123\hoi4\mod\2438003901', 'gfx')):
        for fn in fns:
            tno_files.add(os.path.relpath(os.path.join(dp, fn), 'D:\heart of iron\SW00383\langou123\hoi4\mod\2438003901'))
    for dp, _, fns in os.walk(os.path.join('D:\heart of iron\SW00383\langou123\hoi4\mod\2243912940', 'gfx')):
        for fn in fns:
            rel = os.path.relpath(os.path.join(dp, fn), 'D:\heart of iron\SW00383\langou123\hoi4\mod\2243912940')
            if rel not in tno_files and rel.lower().endswith('.dds') and 'font' not in rel.lower():
                cn_only.append(rel)
    missing = [rel for rel in cn_only if not os.path.exists(os.path.join(root, rel))]
    # 缺失的应全部是：位于排除目录（国策/头像/字体/国旗）或无蓝色（低于阈值）的文件
    nonblue = True
    for rel in missing:
        parts = rel.split(os.sep)
        if len(parts) >= 3 and parts[1] in ('leaders', 'goals', 'flags', 'fonts', 'models',
                                            'event_pictures', 'superevent_pictures',
                                            'loadingscreens', 'background',
                                            'custom_news_headers', 'FX', 'particles',
                                            'entities', 'train_gfx_database'):
            continue
        if 'flag' in os.path.basename(rel).lower():
            continue
        try:
            w, h, bgra, _ = g.read_for_scan('D:\heart of iron\SW00383\langou123\hoi4\mod\2243912940', rel, 'dds')
            if bgra is not None:
                blue, op = g.count_blue(bgra, w, h)
                if blue >= g.BLUE_MIN_COUNT and blue / max(1, op) >= g.BLUE_MIN_FRAC:
                    nonblue = False
        except Exception:
            nonblue = False
    check('CN-only textures handled ' + name, nonblue,
          'missing %d (excluded or below blue threshold)' % len(missing))

    # 3+4. 严格颜色/深青底（输出未生成时跳过）
    rel = r'gfx\interface\topbar\achievements_button.dds'
    if not os.path.exists(os.path.join(root, rel)):
        print('   (跳过 %s: 输出未生成)' % name)
    else:
        w, h, nb, _ = g.read_dds(os.path.join(root, rel))
        w0, h0, ob, _ = g.read_dds(os.path.join('D:\heart of iron\SW00383\langou123\hoi4\mod\2438003901', rel))
        p = g.make_params(target)
        exp_ref = g.transform_scalar(p, *g.REF_BLUE)
        oa = np.frombuffer(ob, dtype=np.uint8).astype(int).reshape(-1, 4)
        na = np.frombuffer(nb, dtype=np.uint8).astype(int).reshape(-1, 4)
        vis = oa[:, 3] >= 16
        # 找到原图中是参考蓝的像素
        ref_mask = (oa[:, 2] == 89) & (oa[:, 1] == 199) & (oa[:, 0] == 194) & (oa[:, 3] >= 200)
        if ref_mask.any():
            got = (na[ref_mask][0][2], na[ref_mask][0][1], na[ref_mask][0][0])
            # DXT5 压缩允许小幅误差
            ok = all(abs(got[i] - exp_ref[i]) <= 6 for i in range(3))
            check('strict color: ref blue -> target %s' % name, ok, 'got %s exp %s' % (got, exp_ref))
        else:
            # 找最接近参考蓝的像素
            d = ((oa[vis, :3] - np.array([[89, 199, 194]])) ** 2).sum(axis=1)
            i = np.where(vis)[0][d.argmin()]
            got = (na[i][2], na[i][1], na[i][0])
            check('strict color (nearest) %s' % name, all(abs(got[i2] - exp_ref[i2]) <= 8 for i2 in range(3)),
                  'nearest blue %s -> %s exp %s' % (tuple(oa[i, :3]), got, exp_ref))
        # 深青底 -> 目标色暗色版（白mod -> 深灰）
        rel2 = r'gfx\interface\topbar\background.dds'
        if os.path.exists(os.path.join(root, rel2)):
            w2, h2, nb2, _ = g.read_dds(os.path.join(root, rel2))
            w02, h02, ob2, _ = g.read_dds(os.path.join('D:\heart of iron\SW00383\langou123\hoi4\mod\2438003901', rel2))
            oa2 = np.frombuffer(ob2, dtype=np.uint8).astype(int).reshape(-1, 4)
            na2 = np.frombuffer(nb2, dtype=np.uint8).astype(int).reshape(-1, 4)
            vis2 = oa2[:, 3] >= 16
            teal = (oa2[:, 2] == 6) & (oa2[:, 1] == 16) & (oa2[:, 0] == 21)
            if teal.any():
                got = (na2[teal][0][2], na2[teal][0][1], na2[teal][0][0])
                mx, mn = max(got), min(got)
                if name == 'TNO_UI_White':
                    ok = mx < 90 and (mx - mn) <= 12          # 暗灰
                else:
                    ok = mx < 90 and got[0] >= got[2]          # 暗目标色系（暖色目标 R>=B）
                check('dark teal -> dark target %s' % name, ok, 'got %s' % (got,))

    # 5. 字体：加载界面 D/B 颜色 + CN 字体文件被修补（CN 版无 D 键时会补一条）
    ifd = os.path.join(root, 'interface')
    fonts = sorted(os.listdir(ifd)) if os.path.isdir(ifd) else []
    lsf = os.path.join(ifd, 'load_screen_font.gfx')
    check('font files present ' + name, os.path.exists(lsf), str(fonts))
    if os.path.exists(lsf):
        txt = open(lsf, encoding='utf-8').read()
        m = re.search(r'D = \{ (\d+) (\d+) (\d+) \}', txt)
        got = tuple(int(x) for x in m.groups()) if m else None
        exp = g.transform_scalar(g.make_params(target), 89, 199, 194)
        # 汉化版字体原本没有 D 键，补的是原始目标色；有色目标两者一致，纯白取 255
        ok = got is not None and (
            all(abs(got[i] - exp[i]) <= 1 for i in range(3)) or
            (got == tuple(target) and target == (255, 255, 255)))
        check('loading font D -> target ' + name, ok, 'D=%s exp=%s' % (got, exp))
    # 6. 依赖声明包含汉化mod
    if os.path.exists(os.path.join(root, 'descriptor.mod')):
        desc = open(os.path.join(root, 'descriptor.mod'), encoding='utf-8').read()
        check('deps include CN mod ' + name, 'CN.Ver' in desc or '汉化' in desc,
              desc.splitlines()[3] if len(desc.splitlines()) > 3 else '')

print('\n%s' % ('ALL PASS' if fails == 0 else '%d FAILURES' % fails))
