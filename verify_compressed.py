# -*- coding: utf-8 -*-
"""Verify compressed orange mod: pixel transform + DXT5 round-trip on output files."""
import os
import numpy as np
import tno_color_gen as g

NEW = r"generated_mods\TNO_UI_FFBA5C"
fails = 0

def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    if not cond:
        fails += 1

p = g.make_params((255, 186, 92))
for rel in [
    r"gfx\interface\topbar\achievements_button.dds",
    r"gfx\aces\ace_none.dds",
    r"gfx\interface\technologies\1950_air_radar.dds",
]:
    w, h, ob, _ = g.read_dds(os.path.join(r"2980739000", rel))
    w2, h2, nb, _ = g.read_dds(os.path.join(NEW, rel))
    check("size %s" % rel, (w, h) == (w2, h2))
    oa = np.frombuffer(ob, dtype=np.uint8).astype(int).reshape(-1, 4)
    na = np.frombuffer(nb, dtype=np.uint8).astype(int).reshape(-1, 4)
    vis = oa[:, 3] >= 128
    # 期望值：原色经过换色后再经 DXT5 量化，允许小误差
    expect = []
    for i in np.where(vis)[0][::7]:
        r, g_, b = oa[i, 2], oa[i, 1], oa[i, 0]
        sc = g.transform_scalar(p, r, g_, b)
        expect.append((sc if sc else (r, g_, b), i))
    errs = []
    for (er, eg, eb), i in expect[:300]:
        errs.append(abs(er - na[i, 2]) + abs(eg - na[i, 1]) + abs(eb - na[i, 0]))
    import statistics
    med = statistics.median(errs) if errs else 0
    check("color transform holds %s" % os.path.basename(rel), med <= 24,
          "median abs err %.1f over %d px" % (med, len(errs)))
    # alpha: 原始 0/255 像素在输出中应保持 0/255（DXT5 硬 alpha）
    hard = (oa[:, 3] == 0) | (oa[:, 3] == 255)
    a_err = int((na[hard, 3] != oa[hard, 3]).sum())
    check("hard alpha kept %s" % os.path.basename(rel), a_err == 0, "%d mismatches" % a_err)

print("\n%s" % ("ALL PASS" if fails == 0 else "%d FAILURES" % fails))
