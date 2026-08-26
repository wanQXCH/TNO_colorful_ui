# -*- coding: utf-8 -*-
"""Final verification: gold/white structure preservation on real game sources."""
import os
import numpy as np
import tno_color_gen as g

fails = 0

def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    if not cond:
        fails += 1

# 1. 金色 mod 的按钮结构（对比用户抱怨的贴图）
GOLD = os.path.join('generated_mods', 'TNO_UI_GOLD')
rels = [r'gfx\interface\topbar\toolbar\construction_button.dds',
        r'gfx\interface\topbar\toolbar\trade_button.dds',
        r'gfx\interface\mapmode\mapmode_buttons_deselected_small.dds',
        r'gfx\interface\endgame_score_button.dds',
        r'gfx\interface\worldtension_gui\world_tension_topbar_defcon_4.dds',
        r'gfx\interface\Mexico_GUI\Economy\map_button_1.dds']
for rel in rels:
    src = os.path.join(r'D:\heart of iron\SW00383\langou123\hoi4\mod\2438003901', rel)
    out = os.path.join(GOLD, rel)
    if not os.path.exists(out):
        check('gold output exists %s' % rel, False)
        continue
    w, h, ob, _ = g.read_dds(src)
    w2, h2, nb, _ = g.read_dds(out)
    oa = np.frombuffer(ob, dtype=np.uint8).astype(int).reshape(-1, 4)
    na = np.frombuffer(nb, dtype=np.uint8).astype(int).reshape(-1, 4)
    vis = oa[:, 3] >= 16
    d_o = len(set(map(tuple, oa[vis, :3].tolist())))
    d_n = len(set(map(tuple, na[vis, :3].tolist())))
    # 金色：保留 ≥40 个颜色层次（修复前只有 15~30，呈带状断层）
    check('gold structure %s' % os.path.basename(rel), d_n >= 40,
          '%d -> %d' % (d_o, d_n))

# 2. 金色输出中参考蓝精确映射为 #F5A524
rel = r'gfx\interface\topbar\toolbar\construction_button.dds'
out = os.path.join(GOLD, rel)
if os.path.exists(out):
    w, h, ob, _ = g.read_dds(os.path.join(r'D:\heart of iron\SW00383\langou123\hoi4\mod\2438003901', rel))
    w2, h2, nb, _ = g.read_dds(out)
    oa = np.frombuffer(ob, dtype=np.uint8).astype(int).reshape(-1, 4)
    na = np.frombuffer(nb, dtype=np.uint8).astype(int).reshape(-1, 4)
    ref_mask = (oa[:, 2] == 89) & (oa[:, 1] == 199) & (oa[:, 0] == 194) & (oa[:, 3] >= 200)
    if ref_mask.any():
        got = (na[ref_mask][0][2], na[ref_mask][0][1], na[ref_mask][0][0])
        check('gold ref-blue -> #F5A524', all(abs(got[i] - (245, 165, 36)[i]) <= 6 for i in range(3)),
              'got %s' % (got,))
    else:
        check('gold ref-blue px found', False)
else:
    print('   (跳过: %s 未生成)' % rel)

# 3. 白色 mod 结构
WHITE = os.path.join('generated_mods', 'TNO_UI_White')
for rel in rels[:4]:
    out = os.path.join(WHITE, rel)
    if not os.path.exists(out):
        continue
    w, h, ob, _ = g.read_dds(os.path.join(r'D:\heart of iron\SW00383\langou123\hoi4\mod\2438003901', rel))
    w2, h2, nb, _ = g.read_dds(out)
    oa = np.frombuffer(ob, dtype=np.uint8).astype(int).reshape(-1, 4)
    na = np.frombuffer(nb, dtype=np.uint8).astype(int).reshape(-1, 4)
    vis = oa[:, 3] >= 16
    d_o = len(set(map(tuple, oa[vis, :3].tolist())))
    d_n = len(set(map(tuple, na[vis, :3].tolist())))
    # 白色：灰阶输出 ≥8 个层次即平滑（白目标无色调，只保留明度结构，属固有极限）
    check('white structure %s' % os.path.basename(rel), d_n >= 8,
          '%d -> %d' % (d_o, d_n))

print('\n%s' % ('ALL PASS' if fails == 0 else '%d FAILURES' % fails))
