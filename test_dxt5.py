# -*- coding: utf-8 -*-
"""DXT5 encoder round-trip quality test."""
import os, math, sys
import numpy as np
import tno_color_gen as g

fails = 0

def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    if not cond:
        fails += 1

def rmse(a, b):
    a = np.frombuffer(a, dtype=np.uint8).astype(np.int32)
    b = np.frombuffer(b, dtype=np.uint8).astype(np.int32)
    return math.sqrt(((a - b) ** 2).mean())

samples = [
    r"gfx\interface\topbar\background.dds",
    r"gfx\interface\topbar\achievements_button.dds",
    r"gfx\aces\ace_none.dds",
    r"gfx\interface\leader_selection_entry_bg.dds",
    r"gfx\maparrows\railway_pattern.dds",
    r"gfx\interface\technologies\1950_air_radar.dds",
]
for rel in samples:
    w, h, bgra, meta = g.read_dds(os.path.join(r"2980739000", rel))
    arr = np.frombuffer(bgra, dtype=np.uint8).reshape(h, w, 4)
    enc = g.encode_dxt5_np(arr)
    expected = ((w + 3) // 4) * ((h + 3) // 4) * 16
    check("enc size %s" % os.path.basename(rel), len(enc) == expected, "%d vs %d" % (len(enc), expected))
    # decode back via our reader
    tmp = "_enc_test.dds"
    g.write_dds_dxt5(tmp, w, h, bgra)
    w2, h2, dec, _ = g.read_dds(tmp)
    os.remove(tmp)
    check("dec size %s" % os.path.basename(rel), (w2, h2) == (w, h))
    a = np.frombuffer(bgra, dtype=np.uint8).astype(np.int32).reshape(-1, 4)
    d = np.frombuffer(dec, dtype=np.uint8).astype(np.int32).reshape(-1, 4)
    vis = a[:, 3] >= 128
    rgb_err = math.sqrt((((a[vis, :3] - d[vis, :3]) ** 2)).mean()) if vis.any() else 0.0
    hard = (a[:, 3] == 0) | (a[:, 3] == 255)
    a_err_hard = math.sqrt((((a[hard, 3] - d[hard, 3]) ** 2)).mean()) if hard.any() else 0.0
    print("   visible RGB RMSE: %.2f, hard-alpha RMSE: %.2f" % (rgb_err, a_err_hard))
    check("quality %s" % os.path.basename(rel), rgb_err < 12 and a_err_hard < 6,
          "rgb=%.2f alpha=%.2f" % (rgb_err, a_err_hard))

print("\n%s" % ("ALL PASS" if fails == 0 else "%d FAILURES" % fails))
sys.exit(1 if fails else 0)
