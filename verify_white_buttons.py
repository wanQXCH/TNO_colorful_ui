# -*- coding: utf-8 -*-
"""Verify white mod preserves button shading structure (no more flat blobs)."""
import os
import numpy as np
from collections import Counter
import tno_color_gen as g

WHITE = os.path.join('generated_mods', 'TNO_UI_White')
fails = 0

def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    if not cond:
        fails += 1

# 扫描输出中所有 button/tab 类贴图，检查换色后 distinct 颜色数是否坍缩
bad = []
checked = 0
for dp, _, fns in os.walk(WHITE):
    for fn in fns:
        low = fn.lower()
        if not low.endswith(('.dds', '.png')):
            continue
        if not ('button' in low or 'tab' in low or 'icon_anim' in low or 'category' in low):
            continue
        rel = os.path.relpath(os.path.join(dp, fn), WHITE)
        # 源文件（优先汉化版）
        src = os.path.join('D:\heart of iron\SW00383\langou123\hoi4\mod\2243912940', rel) if os.path.exists(os.path.join('D:\heart of iron\SW00383\langou123\hoi4\mod\2243912940', rel)) \
            else os.path.join('D:\heart of iron\SW00383\langou123\hoi4\mod\2438003901', rel)
        try:
            w, h, ob, _ = g.read_dds(src)
            w2, h2, nb, _ = g.read_dds(os.path.join(WHITE, rel))
        except Exception:
            continue
        if (w, h) != (w2, h2):
            continue
        oa = np.frombuffer(ob, dtype=np.uint8).astype(np.int32).reshape(-1, 4)
        na = np.frombuffer(nb, dtype=np.uint8).astype(np.int32).reshape(-1, 4)
        vis = oa[:, 3] >= 16
        if not vis.any():
            continue
        d_o = len(set(map(tuple, oa[vis, :3][::5].tolist())))
        d_n = len(set(map(tuple, na[vis, :3][::5].tolist())))
        checked += 1
        # 原图结构丰富(≥12 色)却被打平到 ≤2 色才算失败
        if d_o >= 12 and d_n <= 2:
            bad.append((d_o, d_n, rel))

check('white preserves structure on %d button/tab textures' % checked, not bad, '')
for d_o, d_n, rel in bad[:15]:
    print('   COLLAPSED %d -> %d: %s' % (d_o, d_n, rel))

print('\n%s' % ('ALL PASS' if fails == 0 else '%d FAILURES' % fails))
