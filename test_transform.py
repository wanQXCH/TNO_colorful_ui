# -*- coding: utf-8 -*-
"""Unit tests for tno_color_gen transform (strict mode)."""
import random, sys
import tno_color_gen as g

fails = 0

def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails += 1

# 1. scalar vs numpy equivalence on random colors
random.seed(42)
p = g.make_params((255, 186, 92))
samples = []
for _ in range(3000):
    r, g_, b = (random.randint(0, 255) for _ in range(3))
    samples.append((r, g_, b))
arr = bytearray()
for r, g_, b in samples:
    arr += bytes((b, g_, r, 255))
import numpy as np
out_np = g.transform_numpy(p, np.frombuffer(bytes(arr), dtype=np.uint8).reshape(-1, 4))
bad = 0
for i, (r, g_, b) in enumerate(samples):
    sc = g.transform_scalar(p, r, g_, b)
    nr, ng, nb = int(out_np[i][2]), int(out_np[i][1]), int(out_np[i][0])
    if sc is None:
        if (nr, ng, nb) != (r, g_, b):
            bad += 1
    else:
        if abs(sc[0] - nr) > 1 or abs(sc[1] - ng) > 1 or abs(sc[2] - nb) > 1:
            bad += 1
check("scalar vs numpy equivalence (3000 colors)", bad == 0)

# 2. 严格映射：参考蓝 -> 恰好等于目标色（普通明度目标）
for target in [(255, 186, 92), (101, 70, 128), (70, 167, 88)]:
    p2 = g.make_params(target)
    out = g.transform_scalar(p2, *g.REF_BLUE)
    check("ref blue -> exact target %s" % (target,), out == target)
    if out != target:
        print("   got", out)

# 2b. 过亮目标（纯白）：参考蓝 -> 接近纯白（保留亮部层次）
p2b = g.make_params((255, 255, 255))
out = g.transform_scalar(p2b, *g.REF_BLUE)
check("white target: ref blue -> near-white", out is not None and out[0] >= 240)

# 3. 纯白目标：参考蓝 -> 纯白（原严格断言，用于对比）
# （新逻辑下为 245 左右，见 2b；此处验证亮部层次保留）
p3 = g.make_params((255, 255, 255))
out_hi = g.transform_scalar(p3, 140, 220, 220)   # 比参考蓝更亮的浅蓝
out_face = g.transform_scalar(p3, *g.REF_BLUE)
check("white target: highlight above face (层次保留)", out_hi[0] > out_face[0])

# 3b. 逐贴图自适应：l_hi 拉伸保留贴图亮部层次
# 模拟 US voting 按钮：face=参考蓝, bevel=0.574 亮度
p3b = g.make_params((255, 255, 255))
face = g.transform_scalar(p3b, *g.REF_BLUE, l_hi=0.59)
bevel = g.transform_scalar(p3b, 91, 202, 202, l_hi=0.59)   # HLS l≈0.574
check("per-texture: bevel above face preserved", bevel[0] > face[0],
      )
check("per-texture: face near-white", face[0] >= 240)

# 3c. 金色目标不受 l_hi 影响（与旧输出一致）
p3c = g.make_params((245, 165, 36))
a1 = g.transform_scalar(p3c, *g.REF_BLUE)
a2 = g.transform_scalar(p3c, *g.REF_BLUE, l_hi=0.59)
check("gold unaffected by l_hi", a1 == a2)

# 3d. scalar vs numpy equivalence with l_hi
p3d = g.make_params((255, 255, 255))
arr = np.frombuffer(bytes((194, 199, 89, 255, 202, 202, 91, 255)), dtype=np.uint8).reshape(-1, 4)
out_np = g.transform_numpy(p3d, arr, l_hi=0.59)
sc1 = g.transform_scalar(p3d, 89, 199, 194, l_hi=0.59)
sc2 = g.transform_scalar(p3d, 91, 202, 202, l_hi=0.59)
check("numpy/scalar agree with l_hi",
      abs(sc1[0] - int(out_np[0][2])) <= 1 and abs(sc2[0] - int(out_np[1][2])) <= 1)

# 4. 深青底 -> 目标色的暗色版（明度比例缩放）
p4 = g.make_params((255, 186, 92))
out = g.transform_scalar(p4, 6, 16, 21)
print("  dark teal ->", out, "(expect dark orange)")
check("dark teal changed", out is not None and out != (6, 16, 21))
h, l, s = __import__('colorsys').rgb_to_hls(*[c / 255 for c in out])
check("dark teal keeps dark", l < 0.12)

# 5. 保护色不变：红/绿/灰/白/黑
for c in [(224, 130, 130), (133, 234, 142), (49, 49, 49), (255, 255, 255), (0, 0, 0), (27, 27, 27)]:
    out = g.transform_scalar(p4, *c)
    check("protected %s unchanged" % (c,), out is None)

# 6. 中间蓝：明度比例介于目标与深色之间
out = g.transform_scalar(p4, 74, 167, 163)   # 中蓝 (L≈0.47, 参考 L≈0.565)
print("  mid blue ->", out)
check("mid blue changed to target family", out is not None and out[0] > out[2])

# 7. darken whites 选项
p5 = g.make_params((101, 70, 128), darken_whites=0.7)
out = g.transform_scalar(p5, 255, 255, 255)
print("  white with darken=0.7 ->", out)
check("white darkened", out is not None and out[0] < 200)

# 8. apply_transform 保持尺寸与 alpha
bgra = bytes(arr)
nb = g.apply_transform(p, bgra, 100, 30)
check("apply_transform size", len(nb) == len(bgra))
check("apply_transform alpha kept", nb[3::4] == bgra[3::4])

# 9. darken 模式下 scalar/numpy 一致性（含白色像素——曾因 keep 掩码导致 numpy 不压暗）
random.seed(7)
p9 = g.make_params((245, 165, 36), 0.7)
samples9 = [(255, 255, 255), (250, 250, 250), (240, 240, 240), (230, 230, 230)] + \
           [(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)) for _ in range(500)]
arr9 = bytearray()
for r, g_, b in samples9:
    arr9 += bytes((b, g_, r, 255))
out9 = g.transform_numpy(p9, np.frombuffer(bytes(arr9), dtype=np.uint8).reshape(-1, 4))
bad9 = 0
for i, (r, g_, b) in enumerate(samples9):
    sc = g.transform_scalar(p9, r, g_, b)
    nr, ng, nb = int(out9[i][2]), int(out9[i][1]), int(out9[i][0])
    if sc is None:
        if (nr, ng, nb) != (r, g_, b):
            bad9 += 1
    else:
        if abs(sc[0] - nr) > 1 or abs(sc[1] - ng) > 1 or abs(sc[2] - nb) > 1:
            bad9 += 1
check("darken scalar/numpy equivalence (504 colors)", bad9 == 0)
check("darken darkens white in numpy", out9[0][2] < 200, str(out9[0].tolist()))

print("\n%s" % ("ALL PASS" if fails == 0 else "%d FAILURES" % fails))
sys.exit(1 if fails else 0)
