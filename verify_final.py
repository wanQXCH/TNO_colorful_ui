# -*- coding: utf-8 -*-
"""Final verification of regenerated mods."""
import os, re
import tno_color_gen as g

fails = 0

def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    if not cond:
        fails += 1

for name, target in [('TNO_UI_FFBA5C', (255, 186, 92)),
                     ('TNO_UI_DarkPurple', (101, 70, 128)),
                     ('TNO_UI_Green', (70, 167, 88))]:
    root = os.path.join('generated_mods', name)
    n = sum(len(fns) for _, _, fns in os.walk(root))
    tot = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fns in os.walk(root) for f in fns)
    leaders = sum(1 for dp, _, fns in os.walk(os.path.join(root, 'gfx', 'leaders')) for f in fns) \
        if os.path.isdir(os.path.join(root, 'gfx', 'leaders')) else 0
    flags = sum(1 for dp, _, fns in os.walk(root) for f in fns if 'flag' in f.lower())
    print('=== %s: %d files, %.0f MB, leaders=%d, flag-named=%d' % (name, n, tot / 1048576, leaders, flags))
    check('no leaders ' + name, leaders == 0)
    check('no flags ' + name, flags == 0)

    # font gfx patched?
    fp = os.path.join(root, 'interface', 'load_screen_font.gfx')
    check('font patched ' + name, os.path.exists(fp))
    if os.path.exists(fp):
        txt = open(fp, encoding='utf-8').read()
        m = re.search(r'D = \{ (\d+) (\d+) (\d+) \}', txt)
        got = tuple(int(x) for x in m.groups()) if m else None
        exp = g.transform_scalar(g.make_params(target), 89, 199, 194)
        check('loading font D color ' + name, got is not None and abs(got[0]-exp[0]) <= 1 and abs(got[1]-exp[1]) <= 1 and abs(got[2]-exp[2]) <= 1,
              'D=%s exp=%s' % (got, exp))

    # pixel spot check on a key texture
    rel = r'gfx\interface\topbar\achievements_button.dds'
    w, h, nb, _ = g.read_dds(os.path.join(root, rel))
    w0, h0, ob, _ = g.read_dds(os.path.join('2980739000', rel))
    p = g.make_params(target)
    import numpy as np
    oa = np.frombuffer(ob, dtype=np.uint8).astype(int).reshape(-1, 4)
    na = np.frombuffer(nb, dtype=np.uint8).astype(int).reshape(-1, 4)
    vis = oa[:, 3] >= 16
    changed = int(((oa[vis, :3] != na[vis, :3]).any(axis=1)).sum())
    frac = changed / max(1, int(vis.sum()))
    check('button changed ' + name, frac > 0.5, '%.0f%%' % (100 * frac))

print('\n%s' % ('ALL PASS' if fails == 0 else '%d FAILURES' % fails))
