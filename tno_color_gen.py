#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TNO UI 换色 Mod 生成器
=====================
从 TNO (The New Order / TNO: Requiem) 本体 gfx 目录出发，把界面里所有“TNO 蓝”贴图
换成用户指定的任意颜色，自动生成一个可直接安装的 HOI4 Mod。

用法:
    图形界面:   python tno_color_gen.py
    命令行:     python tno_color_gen.py --tno 2980739000 --color "#FFBA5C" --out output
               python tno_color_gen.py --tno 2980739000 --preset orange
               python tno_color_gen.py --tno 2980739000 --scan-only   # 只列出会被改色的文件

原理:
    TNO 界面贴图几乎都是未压缩 32 位 BGRA 的 .dds（少数 24 位 RGB / DXT3 / DX10-BGRA、
    .tga、.png）。贴图由“TNO 蓝”色板构成：亮蓝 (89,199,194) 高光 + 深青黑 (6,16,21) 底色。
    本程序在 HSL 空间做“色相映射”：只把色相落在蓝青色带 [145°,240°] 的像素换为目标色相，
    保持明度(L)完全不变、饱和度按目标色等比缩放，因此阴影/高光层次、可读性全部保留；
    红色(负面)、绿色(正面)等强调色与灰阶文字不受影响。
    输出文件一律写为 TNO 同款未压缩 32 位 BGRA DDS / 同规格 TGA / PNG，兼容性最好。

只依赖 Python 标准库；装了 numpy 会快很多（自动检测）。
"""
import argparse
import colorsys
import math
import os
import queue
import re
import struct
import sys
import threading
import zlib

# ---------------------------------------------------------------------------
# 颜色模型
# ---------------------------------------------------------------------------

REF_BLUE = (89, 199, 194)          # TNO 招牌亮蓝
BAND_FULL_LO, BAND_FULL_HI = 160.0, 225.0   # 全权重色相区间(度)
BAND_ZERO_LO, BAND_ZERO_HI = 145.0, 240.0   # 权重降为 0 的边界(度)
MIN_SAT = 0.05                     # 低于此饱和度的像素视为灰阶，不动
MIN_LIGHT = 0.02                   # 纯黑保护
MAX_LIGHT = 0.985                  # 纯白保护
MAX_ANCHOR_L = 0.96                # 目标明度过亮(>0.96)时压缩扩张系数，
                                   # 保留亮部层次（按钮斜面/高光不糊成一团）

try:
    import numpy as _np
    HAS_NUMPY = True
except Exception:
    _np = None
    HAS_NUMPY = False

try:
    from PIL import Image as _PILImage
    HAS_PIL = True
except Exception:
    _PILImage = None
    HAS_PIL = False


def _band_weight(hdeg):
    """蓝青色带权重：全权重 160-225°，余弦过渡到 145/240° 外为 0。"""
    if BAND_FULL_LO <= hdeg <= BAND_FULL_HI:
        return 1.0
    if BAND_ZERO_LO < hdeg < BAND_FULL_LO:
        t = (hdeg - BAND_ZERO_LO) / (BAND_FULL_LO - BAND_ZERO_LO)
        return 0.5 * (1.0 - math.cos(math.pi * t))
    if BAND_FULL_HI < hdeg < BAND_ZERO_HI:
        t = (hdeg - BAND_FULL_HI) / (BAND_ZERO_HI - BAND_FULL_HI)
        return 0.5 * (1.0 + math.cos(math.pi * t))
    return 0.0


def make_params(target_rgb, darken_whites=0.0):
    """由目标颜色构造变换参数。
    严格模式：蓝青带内像素映射为目标色 × (输入明度/参考蓝明度)，参考蓝恰好变成
    用户指定的颜色。目标明度过亮时（如纯白）会压缩扩张系数，避免亮部层次
    全部撞上 255 上限导致按钮/斜面糊成一团。"""
    _ref_h, ref_l, _ref_s = colorsys.rgb_to_hls(*(c / 255.0 for c in REF_BLUE))
    _t_h, t_l, _t_s = colorsys.rgb_to_hls(*(c / 255.0 for c in target_rgb))
    k = max(1.0, t_l / MAX_ANCHOR_L)
    return {
        "target": tuple(target_rgb),
        "target_bgr": (target_rgb[2], target_rgb[1], target_rgb[0]),
        "ref_l": ref_l,
        "k": k,
        "darken": max(0.0, min(1.0, darken_whites)),
    }


def transform_scalar(p, r, g, b, l_hi=None):
    """单像素变换。返回 (r,g,b) 或 None(不变)。用 colorsys，作为参照实现。
    l_hi: 该贴图蓝青像素亮度的上分位数（逐贴图自适应，保留亮部层次）。"""
    h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    darken = p["darken"]
    if darken > 0 and s < 0.06 and l > 0.75:
        l = l + (0.52 - l) * darken
        return tuple(int(round(c * 255)) for c in colorsys.hls_to_rgb(h, l, s))
    w = _band_weight(h * 360.0)
    if w <= 1e-6 or s < MIN_SAT or l < MIN_LIGHT or l > MAX_LIGHT:
        return None
    ref_l = p["ref_l"]
    if p["k"] > 1.0 and l_hi is not None and l_hi > ref_l and l > ref_l:
        # 过亮目标（如纯白）+ 贴图自身亮部：把 [ref_l, l_hi] 拉伸到 [1/k, 1]，
        # 保留该贴图的斜面/高光层次，避免全部撞上 255 上限糊成一团
        t = (l - ref_l) / (l_hi - ref_l)
        ratio = (1.0 / p["k"]) + t * (1.0 - 1.0 / p["k"])
    else:
        ratio = (l / ref_l) / p["k"]
    tgt = p["target"]
    out = tuple(max(0, min(255, int(round(c * ratio)))) for c in tgt)
    if w >= 1.0 - 1e-9:
        return out
    # 色带边缘按权重与原始色混合，保证过渡平滑
    return tuple(int(round(o * w + orig * (1.0 - w))) for o, orig in zip(out, (r, g, b)))


# ---- numpy 向量化实现（与 transform_scalar 数学完全一致） ----
def _np_hls(r, g, b):
    mx = _np.maximum(_np.maximum(r, g), b)
    mn = _np.minimum(_np.minimum(r, g), b)
    l = (mx + mn) * 0.5
    d = mx - mn
    eps = 1e-9
    s = _np.where(l <= 0.5, d / (mx + mn + eps), d / (2.0 - mx - mn + eps))
    h = _np.zeros_like(r)
    m = (mx == r) & (d > 0)
    h = _np.where(m, ((g - b) / (d + eps)) % 6.0, h)
    m = (mx == g) & (d > 0)
    h = _np.where(m, (b - r) / (d + eps) + 2.0, h)
    m = (mx == b) & (d > 0)
    h = _np.where(m, (r - g) / (d + eps) + 4.0, h)
    return (h % 6.0) / 6.0, l, s


def _np_rgb(h, l, s):
    def v(m1, m2, hue):
        hue = hue % 1.0
        # colorsys._v 优先级：6h<1 → 2h<1 → 3h<2 → m1
        seg1 = m1 + (m2 - m1) * hue * 6.0
        seg3 = m1 + (m2 - m1) * (2.0 / 3.0 - hue) * 6.0
        inner = _np.where(hue * 3.0 < 2.0, seg3, m1)
        inner = _np.where(hue * 2.0 < 1.0, m2, inner)
        return _np.where(hue * 6.0 < 1.0, seg1, inner)
    m2 = _np.where(l <= 0.5, l * (1.0 + s), l + s - l * s)
    m1 = 2.0 * l - m2
    r = v(m1, m2, h + 1.0 / 3.0)
    g = v(m1, m2, h)
    b = v(m1, m2, h - 1.0 / 3.0)
    return r, g, b


def transform_numpy(p, bgra, l_hi=None):
    """bgra: (N,4) uint8 数组 -> 新 (N,4) uint8 数组。与 transform_scalar 一致。"""
    np = _np
    orig = bgra.astype(np.float32) / 255.0
    bgr = orig[:, :3]                      # B,G,R
    a = bgra[:, 3]
    r, g, b = orig[:, 2], orig[:, 1], orig[:, 0]
    h, l, s = _np_hls(r, g, b)
    darken = p["darken"]
    if darken > 0:
        mask_w = (s < 0.06) & (l > 0.75)
        nl = np.where(mask_w, l + (0.52 - l) * darken, l)
        nr, ng, nb = _np_rgb(h, nl, s)
        base = np.stack([nb, ng, nr], axis=1) * 255.0
    else:
        nl = l
        base = None
    hdeg = h * 360.0
    w = np.zeros_like(h)
    m_full = (hdeg >= BAND_FULL_LO) & (hdeg <= BAND_FULL_HI)
    m_lo = (hdeg > BAND_ZERO_LO) & (hdeg < BAND_FULL_LO)
    m_hi = (hdeg > BAND_FULL_HI) & (hdeg < BAND_ZERO_HI)
    w = np.where(m_full, 1.0, w)
    t = np.clip((hdeg - BAND_ZERO_LO) / (BAND_FULL_LO - BAND_ZERO_LO), 0.0, 1.0)
    w = np.where(m_lo, 0.5 * (1.0 - np.cos(np.pi * t)), w)
    t = np.clip((hdeg - BAND_FULL_HI) / (BAND_ZERO_HI - BAND_FULL_HI), 0.0, 1.0)
    w = np.where(m_hi, 0.5 * (1.0 + np.cos(np.pi * t)), w)
    keep = (w > 1e-6) & (s >= MIN_SAT) & (l >= MIN_LIGHT) & (l <= MAX_LIGHT)
    # 严格映射：目标色 × (输入明度 / 参考蓝明度) / 亮目标压缩系数；
    # 过亮目标且贴图有亮部时按贴图自身亮度分布拉伸，保留层次
    ref_l = p["ref_l"]
    if p["k"] > 1.0 and l_hi is not None and l_hi > ref_l:
        up = l > ref_l
        tt = np.clip((l - ref_l) / (l_hi - ref_l), 0.0, 1.0)
        ratio = (1.0 / p["k"]) + tt * (1.0 - 1.0 / p["k"])
        ratio = np.where(up, ratio, (l / ref_l) / p["k"])
    else:
        ratio = (l / ref_l) / p["k"]
    target_bgr = _np.array(p["target_bgr"], dtype=_np.float32) / 255.0
    colored = _np.clip(target_bgr[None, :] * ratio[:, None], 0.0, 1.0)
    mixed = colored * w[:, None] + bgr * (1.0 - w)[:, None]
    if darken > 0:
        out = np.where(mask_w[:, None], base, mixed)
    else:
        out = mixed
    result = np.empty_like(bgra, dtype=np.float32)
    result[:, :3] = np.where(keep[:, None], out * 255.0, bgr * 255.0)
    result[:, 3] = a.astype(np.float32)
    return np.clip(result, 0, 255).astype(np.uint8)


def apply_transform(p, bgra_bytes, w, h, l_hi=None):
    """对 BGRA 字节串做变换。返回新 BGRA 字节串。"""
    n = w * h
    if HAS_NUMPY:
        arr = _np.frombuffer(bgra_bytes, dtype=_np.uint8).reshape(-1, 4)
        new = transform_numpy(p, arr, l_hi=l_hi)
        return new.reshape(-1).tobytes()
    # 纯 Python 后备：按去重颜色表变换，再查表回填
    lut = {}
    for i in range(0, len(bgra_bytes), 4):
        key = bgra_bytes[i:i + 4]
        if key not in lut:
            r, g, b, a = key
            out = transform_scalar(p, r, g, b, l_hi=l_hi)
            lut[key] = bytes(out + (a,)) if out else key
    chunks = []
    for i in range(0, len(bgra_bytes), 65536):
        chunk = bgra_bytes[i:i + 65536]
        chunks.append(b"".join(lut[chunk[j:j + 4]] for j in range(0, len(chunk), 4)))
    return b"".join(chunks)


def inband_l_hi(bgra_bytes, w, h, max_sample=40000):
    """贴图蓝青像素亮度上分位数（98%），并保证至少比参考蓝高 6%，
    用于逐贴图保留亮部层次（斜面/高光不糊成一团）。无蓝青像素返回 None。"""
    n = w * h
    stride = max(1, n // max_sample)
    ref_l = _ref_l_of()
    floor = ref_l * 1.06
    if HAS_NUMPY:
        arr = _np.frombuffer(bgra_bytes, dtype=_np.uint8).reshape(-1, 4)[::stride]
        a = arr[:, 3]
        vis = a >= 16
        if not vis.any():
            return None
        r = arr[vis, 2].astype(_np.float32) / 255.0
        g = arr[vis, 1].astype(_np.float32) / 255.0
        b = arr[vis, 0].astype(_np.float32) / 255.0
        hh, ll, ss = _np_hls(r, g, b)
        hdeg = hh * 360.0
        inband = ((hdeg >= BAND_ZERO_LO) & (hdeg <= BAND_ZERO_HI)
                  & (ss >= MIN_SAT) & (ll >= MIN_LIGHT) & (ll <= MAX_LIGHT))
        if not inband.any():
            return None
        return max(float(_np.percentile(ll[inband], 98)), floor)
    ls = []
    for i in range(0, n, stride):
        off = i * 4
        bv, g_, r_, a = bgra_bytes[off], bgra_bytes[off + 1], bgra_bytes[off + 2], bgra_bytes[off + 3]
        if a < 16:
            continue
        h, l, s = colorsys.rgb_to_hls(r_ / 255.0, g_ / 255.0, bv / 255.0)
        if BAND_ZERO_LO <= h * 360.0 <= BAND_ZERO_HI and s >= MIN_SAT \
                and MIN_LIGHT <= l <= MAX_LIGHT:
            ls.append(l)
    if not ls:
        return None
    ls.sort()
    return max(ls[min(len(ls) - 1, int(len(ls) * 0.98))], floor)


def _ref_l_of():
    return colorsys.rgb_to_hls(*(c / 255.0 for c in REF_BLUE))[1]


def count_blue(bgra_bytes, w, h, max_sample=40000):
    """统计会被换色器真正改掉的像素（与 transform 的判定条件一致：
    蓝青色带内且饱和度/明度达标）。"""
    n = w * h
    stride = max(1, n // max_sample)
    if HAS_NUMPY:
        arr = _np.frombuffer(bgra_bytes, dtype=_np.uint8).reshape(-1, 4)
        arr = arr[::stride]
        a = arr[:, 3]
        opaque = a >= 16
        if not opaque.any():
            return 0, 0
        r = arr[opaque, 2].astype(_np.float32) / 255.0
        g = arr[opaque, 1].astype(_np.float32) / 255.0
        b = arr[opaque, 0].astype(_np.float32) / 255.0
        hh, ll, ss = _np_hls(r, g, b)
        ww = _np.zeros_like(hh)
        hdeg = hh * 360.0
        ww = _np.where((hdeg >= BAND_FULL_LO) & (hdeg <= BAND_FULL_HI), 1.0, ww)
        t = _np.clip((hdeg - BAND_ZERO_LO) / (BAND_FULL_LO - BAND_ZERO_LO), 0.0, 1.0)
        ww = _np.where((hdeg > BAND_ZERO_LO) & (hdeg < BAND_FULL_LO),
                       0.5 * (1.0 - _np.cos(_np.pi * t)), ww)
        t = _np.clip((hdeg - BAND_FULL_HI) / (BAND_ZERO_HI - BAND_FULL_HI), 0.0, 1.0)
        ww = _np.where((hdeg > BAND_FULL_HI) & (hdeg < BAND_ZERO_HI),
                       0.5 * (1.0 + _np.cos(_np.pi * t)), ww)
        blue = int(((ww > 1e-6) & (ss >= MIN_SAT) & (ll >= MIN_LIGHT) & (ll <= MAX_LIGHT)).sum())
        return blue, int(opaque.sum())
    blue = 0
    opaque = 0
    for i in range(0, n, stride):
        off = i * 4
        bv, g, r, a = bgra_bytes[off], bgra_bytes[off + 1], bgra_bytes[off + 2], bgra_bytes[off + 3]
        if a < 16:
            continue
        opaque += 1
        h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, bv / 255.0)
        if (_band_weight(h * 360.0) > 1e-6 and s >= MIN_SAT
                and MIN_LIGHT <= l <= MAX_LIGHT):
            blue += 1
    return blue, opaque


# ---------------------------------------------------------------------------
# DDS 编解码（读：BGRA32 / 24位RGB / DX10-BGRA / DXT1 / DXT3 / DXT5；写：BGRA32）
# ---------------------------------------------------------------------------

def _u32(b, off):
    return struct.unpack_from('<I', b, off)[0]


def _565_to_888(v):
    r = ((v >> 11) & 0x1F)
    g = ((v >> 5) & 0x3F)
    b = (v & 0x1F)
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def _decode_dxt1_color(block, out, x0, y0, w, h, alpha_mode):
    c0 = struct.unpack_from('<H', block, 0)[0]
    c1 = struct.unpack_from('<H', block, 2)[0]
    cols = [_565_to_888(c0), _565_to_888(c1)]
    if c0 > c1:
        cols.append(tuple((2 * cols[0][i] + cols[1][i]) // 3 for i in range(3)))
        cols.append(tuple((cols[0][i] + 2 * cols[1][i]) // 3 for i in range(3)))
        alphas = [255, 255, 255, 255]
    else:
        cols.append(tuple((cols[0][i] + cols[1][i]) // 2 for i in range(3)))
        cols.append((0, 0, 0))
        alphas = [255, 255, 255, 0]
    idx = struct.unpack_from('<I', block, 4)[0]
    for py in range(4):
        yy = y0 + py
        if yy >= h:
            break
        for px in range(4):
            xx = x0 + px
            if xx >= w:
                continue
            bit = (py * 4 + px) * 2
            ci = (idx >> bit) & 3
            cr, cg, cb = cols[ci]
            off = (yy * w + xx) * 4
            out[off] = cb
            out[off + 1] = cg
            out[off + 2] = cr
            if alpha_mode:
                out[off + 3] = alphas[ci]


def _decode_dxt3_alpha(block, out, x0, y0, w, h):
    for py in range(4):
        yy = y0 + py
        if yy >= h:
            break
        for px in range(4):
            xx = x0 + px
            if xx >= w:
                continue
            bit = (py * 4 + px) * 4
            nib = (block[bit // 8] >> (bit % 8)) & 0xF
            off = (yy * w + xx) * 4
            out[off + 3] = nib * 17


def _decode_dxt5_alpha(block, out, x0, y0, w, h):
    a0, a1 = block[0], block[1]
    alphas = [a0, a1]
    if a0 > a1:
        for i in range(6):
            alphas.append(((6 - i) * a0 + (i + 1) * a1) // 7)
    else:
        for i in range(4):
            alphas.append(((4 - i) * a0 + (i + 1) * a1) // 5)
        alphas += [0, 255]
    bits = 0
    for i in range(6):
        bits |= block[2 + i] << (8 * i)
    for py in range(4):
        yy = y0 + py
        if yy >= h:
            break
        for px in range(4):
            xx = x0 + px
            if xx >= w:
                continue
            bit = (py * 4 + px) * 3
            ai = (bits >> bit) & 7
            off = (yy * w + xx) * 4
            out[off + 3] = alphas[ai]


def read_dds(path):
    """读 DDS -> (w, h, bgra_topdown_bytes, meta)。meta 描述原格式。"""
    with open(path, 'rb') as f:
        b = f.read()
    if b[:4] != b'DDS ':
        raise ValueError("not a DDS file")
    h = _u32(b, 12)
    w = _u32(b, 16)
    mips = _u32(b, 28)
    fourcc = b[84:88]
    bits = _u32(b, 88)
    rmask = _u32(b, 92)
    gmask = _u32(b, 96)
    bmask = _u32(b, 100)
    amask = _u32(b, 104)
    data = b[128:]
    if fourcc == b'DX10':
        dxgi = _u32(b, 128)
        if dxgi in (87, 91):            # B8G8R8A8_UNORM(_SRGB)
            data = b[148:]
            need = w * h * 4
            if len(data) < need:
                raise ValueError("DX10 data too short")
            return w, h, bytes(data[:need]), ("dx10-bgra",)
        raise ValueError("unsupported DX10 format %d" % dxgi)
    if fourcc == b'DXT1':
        out = bytearray(w * h * 4)
        bx = (w + 3) // 4
        by = (h + 3) // 4
        for byy in range(by):
            for bxx in range(bx):
                blk = data[(byy * bx + bxx) * 8: (byy * bx + bxx) * 8 + 8]
                _decode_dxt1_color(blk, out, bxx * 4, byy * 4, w, h, True)
        return w, h, bytes(out), ("dxt1",)
    if fourcc == b'DXT3':
        out = bytearray(w * h * 4)
        bx = (w + 3) // 4
        by = (h + 3) // 4
        for byy in range(by):
            for bxx in range(bx):
                blk = data[(byy * bx + bxx) * 16: (byy * bx + bxx) * 16 + 16]
                _decode_dxt3_alpha(blk, out, bxx * 4, byy * 4, w, h)
                _decode_dxt1_color(blk[8:], out, bxx * 4, byy * 4, w, h, False)
        return w, h, bytes(out), ("dxt3",)
    if fourcc == b'DXT5':
        out = bytearray(w * h * 4)
        bx = (w + 3) // 4
        by = (h + 3) // 4
        for byy in range(by):
            for bxx in range(bx):
                blk = data[(byy * bx + bxx) * 16: (byy * bx + bxx) * 16 + 16]
                _decode_dxt5_alpha(blk, out, bxx * 4, byy * 4, w, h)
                _decode_dxt1_color(blk[8:], out, bxx * 4, byy * 4, w, h, False)
        return w, h, bytes(out), ("dxt5",)
    if fourcc == b'\x00\x00\x00\x00':
        if bits == 32:
            need = w * h * 4
            if len(data) < need:
                raise ValueError("data too short")
            px = bytes(data[:need])
            if amask == 0xFF000000 and rmask == 0x00FF0000:      # BGRA
                return w, h, px, ("bgra",)
            if amask == 0xFF000000 and rmask == 0x000000FF:      # RGBA -> BGRA
                out = bytearray(need)
                for i in range(0, need, 4):
                    out[i] = px[i + 2]
                    out[i + 1] = px[i + 1]
                    out[i + 2] = px[i]
                    out[i + 3] = px[i + 3]
                return w, h, bytes(out), ("rgba",)
            if amask == 0x000000FF and rmask == 0x00FF0000:      # BGRA(alpha低字节) -> BGRA
                return w, h, px, ("bgra",)
            if amask == 0 and rmask == 0x00FF0000:               # BGR 无 alpha -> 补 alpha=255
                out = bytearray(need)
                for i in range(0, need, 4):
                    out[i] = px[i]
                    out[i + 1] = px[i + 1]
                    out[i + 2] = px[i + 2]
                    out[i + 3] = 255
                return w, h, bytes(out), ("bgr",)
            raise ValueError("unsupported 32-bit masks")
        if bits == 24:
            aligned = ((w * 3 + 3) // 4) * 4
            packed = w * 3
            if len(data) >= aligned * h:
                row = aligned
            elif len(data) >= packed * h:
                row = packed
            else:
                raise ValueError("data too short")
            need = row * h
            out = bytearray(w * h * 4)
            if rmask == 0xFF0000:      # memory order RGB
                for y in range(h):
                    src = data[y * row: y * row + w * 3]
                    dst = y * w * 4
                    for x in range(w):
                        out[dst + x * 4] = src[x * 3 + 2]
                        out[dst + x * 4 + 1] = src[x * 3 + 1]
                        out[dst + x * 4 + 2] = src[x * 3]
                        out[dst + x * 4 + 3] = 255
            else:                        # memory order BGR
                for y in range(h):
                    src = data[y * row: y * row + w * 3]
                    dst = y * w * 4
                    for x in range(w):
                        out[dst + x * 4] = src[x * 3]
                        out[dst + x * 4 + 1] = src[x * 3 + 1]
                        out[dst + x * 4 + 2] = src[x * 3 + 2]
                        out[dst + x * 4 + 3] = 255
            return w, h, bytes(out), ("rgb24",)
        raise ValueError("unsupported pixel format bits=%d" % bits)
    raise ValueError("unsupported fourcc %r" % fourcc)


def write_dds_bgra32(path, w, h, bgra):
    """写未压缩 32 位 BGRA DDS（与 TNO 自带贴图同款头）。"""
    hdr = struct.pack('<4s7I44s', b'DDS ', 124, 0x100F, h, w, w * 4, 0, 0, b'\x00' * 44)
    hdr += struct.pack('<5I', 32, 0x41, 0, 32, 0x00FF0000)   # ddspf size/flags/fourcc/bitcount/R
    hdr += struct.pack('<5I', 0x0000FF00, 0x000000FF, 0xFF000000, 0x1000, 0)  # G/B/A/caps/caps2
    hdr += struct.pack('<3I', 0, 0, 0)                        # caps3/caps4/reserved2
    with open(path, 'wb') as f:
        f.write(hdr)
        f.write(bgra)


# ---------------------------------------------------------------------------
# DXT5 (BC3) 编码：输出压缩贴图，体积约为未压缩的 1/4（原版游戏 UI 即用此格式）
# ---------------------------------------------------------------------------

def encode_dxt5_np(bgra):
    """bgra: (H,W,4) uint8 (BGRA 内存序) -> DXT5 块数据 bytes（4x4 块，块序先 y 后 x）。"""
    np = _np
    H, W = bgra.shape[:2]
    pad_h = (4 - H % 4) % 4
    pad_w = (4 - W % 4) % 4
    img = np.pad(bgra, ((0, pad_h), (0, pad_w), (0, 0)), constant_values=0)
    bh, bw = (H + 3) // 4, (W + 3) // 4
    p = img.reshape(bh, 4, bw, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * bw, 16, 4)
    rgb = p[:, :, [2, 1, 0]].astype(np.int32)   # BGRA -> RGB
    a = p[:, :, 3].astype(np.int32)
    vis = a >= 8                                  # 只有可见像素参与颜色端点

    # ---- 颜色端点（可见像素包围盒角点，565 数值序保证 c0 > c1 -> 4 色模式） ----
    rmax = np.where(vis[:, :, None], rgb, -1)
    rmin = np.where(vis[:, :, None], rgb, 1 << 30)
    anyvis = vis.any(axis=1)
    mx = np.where(anyvis[:, None], rmax.max(axis=1), 0)
    mn = np.where(anyvis[:, None], rmin.min(axis=1), 0)

    def to565(c):
        return ((c[..., 0] >> 3) << 11) | ((c[..., 1] >> 2) << 5) | (c[..., 2] >> 3)

    e0, e1 = mx, mn
    v0 = to565(e0)
    v1 = to565(e1)
    swap = v0 < v1
    t = e0.copy()
    e0 = np.where(swap[:, None], e1, e0)
    e1 = np.where(swap[:, None], t, e1)
    v0 = to565(e0)
    v1 = to565(e1)
    c0 = e0.astype(np.float32)
    c1 = e1.astype(np.float32)
    cols = np.stack([c0, c1, (2.0 * c0 + c1) / 3.0, (c0 + 2.0 * c1) / 3.0], axis=1)
    d2 = ((rgb[:, None, :, :] - cols[:, :, None, :]) ** 2).sum(axis=-1)
    # 透明像素(alpha<8)颜色无意义，归到索引 0，避免随机噪声
    ci = d2.argmin(axis=1)                       # (B,16)
    ci = np.where(vis, ci, 0)
    packed = np.zeros(len(ci), dtype=np.uint32)
    for i in range(16):
        packed |= ci[:, i].astype(np.uint32) << (2 * i)

    # ---- alpha 端点 + 3bit 索引 ----
    a0 = a.max(axis=1)
    a1 = a.min(axis=1)
    denom = np.maximum(a0 - a1, 1)
    # 插值阶梯 w_k = (k*a0 + (7-k)*a1)/7：w_0=a1, w_7=a0
    k = np.clip(np.round(7.0 * (a - a1[:, None]) / denom[:, None]).astype(np.int32), 0, 7)
    # 格式索引：idx0=a0(=w_7), idx1=a1(=w_0), idx2..7=w_1..w_6
    ai = np.where(k == 7, 0, np.where(k == 0, 1, k + 1)).astype(np.uint64)
    packed_a = np.zeros(len(ai), dtype=np.uint64)
    for i in range(16):
        packed_a |= ai[:, i] << (3 * i)

    B = bh * bw
    out = np.empty((B, 16), dtype=np.uint8)
    out[:, 0] = a0.astype(np.uint8)
    out[:, 1] = a1.astype(np.uint8)
    out[:, 2:8] = packed_a.view(np.uint8).reshape(B, 8)[:, :6]
    out[:, 8] = (v0 & 0xFF).astype(np.uint8)
    out[:, 9] = (v0 >> 8).astype(np.uint8)
    out[:, 10] = (v1 & 0xFF).astype(np.uint8)
    out[:, 11] = (v1 >> 8).astype(np.uint8)
    out[:, 12:16] = packed.view(np.uint8).reshape(B, 4)
    return out.tobytes()


def write_dds_dxt5(path, w, h, bgra):
    """写 DXT5 压缩 DDS。"""
    if HAS_NUMPY:
        arr = _np.frombuffer(bgra, dtype=_np.uint8).reshape(h, w, 4)
        data = encode_dxt5_np(arr)
    else:
        raise RuntimeError("DXT5 压缩输出需要 numpy；请装 numpy 或改用未压缩输出")
    bx = (w + 3) // 4
    by = (h + 3) // 4
    hdr = struct.pack('<4s7I44s', b'DDS ', 124, 0x100F, h, w, bx * by * 16, 0, 0, b'\x00' * 44)
    hdr += struct.pack('<2I4s', 32, 0x4, b'DXT5')          # ddspf: size/flags/fourcc=DXT5
    hdr += struct.pack('<7I', 0, 0, 0, 0, 0, 0x1000, 0)    # bitcount/masks/caps/caps2
    hdr += struct.pack('<3I', 0, 0, 0)
    with open(path, 'wb') as f:
        f.write(hdr)
        f.write(data)


# ---------------------------------------------------------------------------
# TGA 编解码（type 2，32 位）
# ---------------------------------------------------------------------------

def read_tga(path):
    b = open(path, 'rb').read()
    if len(b) < 18:
        raise ValueError("bad tga")
    idlen, cmap, type_ = b[0], b[1], b[2]
    if type_ != 2 or cmap != 0:
        raise ValueError("unsupported tga type=%d cmap=%d" % (type_, cmap))
    w = struct.unpack_from('<H', b, 12)[0]
    h = struct.unpack_from('<H', b, 14)[0]
    depth = b[16]
    desc = b[17]
    if depth != 32:
        raise ValueError("unsupported tga depth %d" % depth)
    off = 18 + idlen
    need = w * h * 4
    px = b[off:off + need]
    if len(px) < need:
        raise ValueError("tga data too short")
    if desc & 0x20:          # 自顶向下
        data = px
    else:                    # 自底向上，翻转
        data = b"".join(px[y * w * 4:(y + 1) * w * 4] for y in range(h - 1, -1, -1))
    return w, h, data, b[:18]


def write_tga(path, w, h, bgra, orig_header):
    """bgra 为自顶向下数据；按原头方向写回（必要时翻转行）。"""
    hdr = bytearray(orig_header)
    hdr[0] = 0
    hdr[1] = 0
    hdr[2] = 2
    struct.pack_into('<H', hdr, 12, w)
    struct.pack_into('<H', hdr, 14, h)
    hdr[16] = 32
    top_down = bool(orig_header[17] & 0x20)
    hdr[17] = 0x28 if top_down else 0x08
    if top_down:
        data = bgra
    else:
        data = b"".join(bgra[y * w * 4:(y + 1) * w * 4] for y in range(h - 1, -1, -1))
    with open(path, 'wb') as f:
        f.write(hdr)
        f.write(data)


# ---------------------------------------------------------------------------
# PNG 编解码（8 位，灰度/RGB/调色板/灰度+alpha/RGBA，非隔行 + Adam7）
# ---------------------------------------------------------------------------

def _png_chunks(b):
    off = 8
    while off + 8 <= len(b):
        ln = struct.unpack_from('>I', b, off)[0]
        typ = b[off + 4:off + 8]
        yield typ, b[off + 8:off + 8 + ln]
        off += 12 + ln
        if typ == b'IEND':
            break


def _png_unfilter(scanlines, w, bpp, h):
    if HAS_NUMPY:
        np = _np
        out = np.empty(h * w * bpp, dtype=np.uint8)
        prev = np.zeros(w * bpp, dtype=np.uint8)
        for y in range(h):
            row = np.frombuffer(scanlines[y], dtype=np.uint8)
            f = int(row[0])
            cur = row[1:].astype(np.int32)
            if f == 0:
                pass
            elif f == 1:                       # Sub（按 bpp 分组前缀和）
                for start in range(bpp):
                    seg = cur[start::bpp]
                    cur[start::bpp] = np.cumsum(seg) & 0xFF
            elif f == 2:                       # Up
                cur = (cur + prev.astype(np.int32)) & 0xFF
            elif f == 3:                       # Average
                a = np.roll(cur, bpp)
                a[:bpp] = 0
                cur = (cur + ((a + prev.astype(np.int32)) >> 1)) & 0xFF
            else:                              # Paeth
                a = np.roll(cur, bpp)
                a[:bpp] = 0
                c = np.roll(prev.astype(np.int32), bpp)
                c[:bpp] = 0
                p = a + prev.astype(np.int32) - c
                pa = np.abs(p - a)
                pb = np.abs(p - prev.astype(np.int32))
                pc = np.abs(p - c)
                pr = np.where((pa <= pb) & (pa <= pc), a, np.where(pb <= pc, prev, c))
                cur = (cur + pr) & 0xFF
            out[y * w * bpp:(y + 1) * w * bpp] = cur.astype(np.uint8)
            prev = cur.astype(np.uint8)
        return out.tobytes()
    out = bytearray()
    prev = bytearray(w * bpp)
    for y in range(h):
        row = bytearray(scanlines[y])
        f = row[0]
        raw = row[1:]
        cur = bytearray(raw)
        for i in range(len(raw)):
            a = cur[i - bpp] if i >= bpp else 0
            bv = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                cur[i] = (cur[i] + a) & 0xFF
            elif f == 2:
                cur[i] = (cur[i] + bv) & 0xFF
            elif f == 3:
                cur[i] = (cur[i] + ((a + bv) >> 1)) & 0xFF
            elif f == 4:
                p = a + bv - c
                pa, pb, pc = abs(p - a), abs(p - bv), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (bv if pb <= pc else c)
                cur[i] = (cur[i] + pr) & 0xFF
        out += cur
        prev = cur
    return bytes(out)


_ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
          (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))


def read_png(path):
    if HAS_PIL:
        im = _PILImage.open(path).convert('RGBA')
        return im.size[0], im.size[1], im.tobytes()
    b = open(path, 'rb').read()
    if b[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError("not a png")
    idat = b''
    plte = b''
    trns = b''
    ihdr = None
    for typ, data in _png_chunks(b):
        if typ == b'IHDR':
            ihdr = data
        elif typ == b'PLTE':
            plte = data
        elif typ == b'tRNS':
            trns = data
        elif typ == b'IDAT':
            idat += data
    if ihdr is None:
        raise ValueError("png without IHDR")
    w, h, depth, ctype, comp, filt, interlace = struct.unpack('>IIBBBBB', ihdr)
    if depth != 8:
        raise ValueError("png depth %d unsupported" % depth)
    if ctype not in (0, 2, 3, 4, 6):
        raise ValueError("png color type %d unsupported" % ctype)
    raw = zlib.decompress(idat)
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]

    def expand(wp, hp, src):
        bpp = channels
        rows = [src[i * (wp * channels + 1):(i + 1) * (wp * channels + 1)] for i in range(hp)]
        px = _png_unfilter(rows, wp, channels, hp)
        out = bytearray(wp * hp * 4)
        for i in range(wp * hp):
            off = i * channels
            o4 = i * 4
            if ctype == 0:
                v = px[off]
                out[o4:o4 + 4] = bytes((v, v, v, 255))
            elif ctype == 2:
                out[o4:o4 + 4] = bytes(px[off:off + 3] + (255,))
            elif ctype == 4:
                v = px[off]
                out[o4:o4 + 4] = bytes((v, v, v, px[off + 1]))
            elif ctype == 6:
                out[o4:o4 + 4] = bytes(px[off:off + 4])
            else:  # palette
                idx = px[off]
                if idx * 3 + 2 < len(plte):
                    out[o4] = plte[idx * 3 + 2]; out[o4 + 1] = plte[idx * 3 + 1]; out[o4 + 2] = plte[idx * 3]
                if idx < len(trns):
                    out[o4 + 3] = trns[idx]
                else:
                    out[o4 + 3] = 255
        return bytes(out)

    if interlace == 0:
        rgba = expand(w, h, raw)
    else:
        canvas = bytearray(w * h * 4)
        pos = 0
        for x0, y0, dx, dy in _ADAM7:
            pw = (w - x0 + dx - 1) // dx
            ph = (h - y0 + dy - 1) // dy
            if pw == 0 or ph == 0:
                continue
            size = ph * (pw * channels + 1)
            seg = expand(pw, ph, raw[pos:pos + size])
            pos += size
            for py in range(ph):
                for px in range(pw):
                    dst = ((y0 + py * dy) * w + (x0 + px * dx)) * 4
                    src = (py * pw + px) * 4
                    canvas[dst:dst + 4] = seg[src:src + 4]
        rgba = bytes(canvas)
    return w, h, rgba


def write_png(path, w, h, rgba):
    if HAS_PIL:
        im = _PILImage.frombytes('RGBA', (w, h), bytes(rgba))
        im.save(path, 'PNG', compress_level=6)
        return

    def chunk(typ, data):
        return struct.pack('>I', len(data)) + typ + data + struct.pack('>I', zlib.crc32(typ + data) & 0xFFFFFFFF)
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)
    rows = bytearray()
    for y in range(h):
        rows.append(0)
        rows += rgba[y * w * 4:(y + 1) * w * 4]
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n')
        f.write(chunk(b'IHDR', ihdr))
        f.write(chunk(b'IDAT', zlib.compress(bytes(rows), 6)))
        f.write(chunk(b'IEND', b''))


def count_changed(new_bgra, old_bgra, w, h, max_sample=40000):
    """统计非透明像素(alpha>=16，游戏中可见)中 RGB 发生变化的数量/总数。"""
    n = w * h
    stride = max(1, n // max_sample)
    if HAS_NUMPY:
        oa = _np.frombuffer(old_bgra, dtype=_np.uint8).reshape(-1, 4)[::stride]
        na = _np.frombuffer(new_bgra, dtype=_np.uint8).reshape(-1, 4)[::stride]
        vis = oa[:, 3] >= 16
        if not vis.any():
            return 0, 0
        ch = int(((oa[vis, :3] != na[vis, :3]).any(axis=1)).sum())
        return ch, int(vis.sum())
    ch = 0
    vis = 0
    for i in range(0, n, stride):
        off = i * 4
        if old_bgra[off + 3] < 16:
            continue
        vis += 1
        if old_bgra[off:off + 3] != new_bgra[off:off + 3]:
            ch += 1
    return ch, vis


def patch_font_colors(roots, out_root, params):
    """把各源 interface/*.gfx 字体定义里的 textcolors（蓝青色文字色）替换为目标色。
    加载界面字体 load_screen_font.gfx 的默认色 D={89,199,194} 就在其中。
    同名文件取高优先级源（如汉化 mod 的字体定义覆盖 TNO 的）；
    若无 D 默认色键则补一条 D = 目标色，保证默认文字也随主题。"""
    changed = 0
    seen = set()
    target = params["target"]
    for root in reversed(roots):
        idir = os.path.join(root, 'interface')
        if not os.path.isdir(idir):
            continue
        for fn in sorted(os.listdir(idir)):
            if not fn.lower().endswith('.gfx'):
                continue
            if fn in seen:
                continue
            seen.add(fn)
            p = os.path.join(idir, fn)
            with open(p, 'r', encoding='utf-8', errors='ignore', newline='') as f:
                txt = f.read()
            if 'textcolors' not in txt:
                continue

            def repl(m):
                r, g, b = int(m.group(2)), int(m.group(3)), int(m.group(4))
                out = transform_scalar(params, r, g, b)
                if out is None:
                    return m.group(0)
                return '%s = { %d %d %d }' % (m.group(1), out[0], out[1], out[2])

            new = re.sub(r'([A-Za-z0-9$]) = \{ (\d+) (\d+) (\d+) \}', repl, txt)
            # 没有 D 键时补默认色（引擎对无标记文字用 D）
            if not re.search(r'\bD = \{', new):
                new = re.sub(r'(textcolors\s*=\s*\{)', r'\1\n\t\t\tD = { %d %d %d }' % target,
                             new, count=1)
            if new != txt:
                out_p = os.path.join(out_root, 'interface', fn)
                os.makedirs(os.path.dirname(out_p), exist_ok=True)
                with open(out_p, 'w', encoding='utf-8', newline='') as f:
                    f.write(new)
                changed += 1
    return changed


# ---------------------------------------------------------------------------
# 扫描与生成
# ---------------------------------------------------------------------------

# 不处理的目录（艺术图/模型/照片/字体图集/国策图标/领袖头像/国旗等）
EXCLUDE_DIRS = {
    'event_pictures', 'superevent_pictures', 'loadingscreens', 'flags',
    'background', 'custom_news_headers', 'fonts', 'FX', 'particles',
    'entities', 'train_gfx_database', 'models',
    'goals',          # 国策图标（焦点图标）
    'leaders',        # 领袖头像/照片
}

BLUE_MIN_COUNT = 12      # 至少这么多蓝色像素
BLUE_MIN_FRAC = 0.006    # 且占非透明像素比例不低于此


def classify_file(rel):
    low = rel.lower()
    if low.endswith('.dds'):
        return 'dds'
    if low.endswith('.tga'):
        return 'tga'
    if low.endswith('.png'):
        return 'png'
    return None


def dxt_probe_blue(path, w, h, fourcc):
    """只检查 DXT 块的两个 565 色端点是否可能为蓝青色，快速过滤无蓝色贴图。
    返回 (潜在蓝色块数, 总块数)。端点在蓝青范围 ⇒ 块内必有蓝青像素。"""
    with open(path, 'rb') as f:
        f.seek(128)
        data = f.read()
    if HAS_NUMPY:
        u16 = _np.frombuffer(data, dtype=_np.uint16)
        if fourcc == b'DXT1':
            c0 = u16[0::4]
            c1 = u16[1::4]
        else:
            c0 = u16[4::8]
            c1 = u16[5::8]

        def blue_mask(v):
            r5 = v >> 11
            g6 = (v >> 5) & 63
            b5 = v & 31
            return (b5 >= r5 + 1) & (g6 >= r5) & ((b5 + g6) >= 4) & ((r5 + g6 + b5) >= 3)
        n = len(c0)
        return int((blue_mask(c0) | blue_mask(c1)).sum()), n

    bx = (w + 3) // 4
    by = (h + 3) // 4
    total = bx * by
    bsize = 8 if fourcc == b'DXT1' else 16
    step = max(1, total // 4000)
    n = 0
    blue = 0
    for bi in range(0, total, step):
        off = bi * bsize
        c0 = struct.unpack_from('<H', data, off + (0 if fourcc == b'DXT1' else 8))[0]
        c1 = struct.unpack_from('<H', data, off + (2 if fourcc == b'DXT1' else 10))[0]
        n += 1
        for v in (c0, c1):
            r5 = v >> 11
            g6 = (v >> 5) & 63
            b5 = v & 31
            if b5 >= r5 + 1 and g6 >= r5 and (b5 + g6) >= 4 and (r5 + g6 + b5) >= 3:
                blue += 1
                break
    return blue, n


def read_for_scan(tno_root, rel, kind):
    """读图用于扫描；DXT 压缩文件先做廉价探测，无蓝色则 bgra 返回 None。
    注意：TNO 里有少量内容是 PNG 但文件名是 .dds 的文件，按内容识别。"""
    path = os.path.join(tno_root, rel)
    with open(path, 'rb') as f:
        head = f.read(148)
    if kind == 'dds':
        if head[:8] == b'\x89PNG\r\n\x1a\n':
            return read_png(path)[:3] + (("png",),)
        if len(head) >= 18 and head[0] == 0 and head[1] == 0 and head[2] == 2 and head[16] == 32:
            w, h, bgra, tga_hdr = read_tga(path)
            return w, h, bgra, ("tga", tga_hdr)
        if head[:4] != b'DDS ':
            raise ValueError("not a DDS file")
        fourcc = head[84:88]
        if fourcc in (b'DXT1', b'DXT3', b'DXT5'):
            w = _u32(head, 16)
            h = _u32(head, 12)
            blue, _total = dxt_probe_blue(path, w, h, fourcc)
            if blue == 0:
                return w, h, None, ('dds',)
        return read_dds(path)
    if kind == 'tga':
        return read_tga(path)
    return read_png(path)[:3] + (("png",),)


def read_image(tno_root, rel, kind):
    """读图 -> (w, h, bgra, meta) 或抛异常。"""
    path = os.path.join(tno_root, rel)
    if kind == 'dds':
        w, h, bgra, meta = read_dds(path)
        return w, h, bgra, ("dds",)
    if kind == 'tga':
        w, h, bgra, hdr = read_tga(path)
        return w, h, bgra, ("tga", hdr)
    w, h, bgra = read_png(path)
    return w, h, bgra, ("png",)


def write_image(out_root, rel, kind, w, h, bgra, meta, compress=True):
    """按内容格式写出（PNG 内容的 .dds 也写回 PNG，保持原样兼容）。
    compress=True 时 DDS 写为 DXT5（体积约 1/4，原版游戏 UI 同款格式）。"""
    path = os.path.join(out_root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fmt = meta[0]
    if fmt in ('bgra', 'rgba', 'bgr', 'rgb24', 'dx10-bgra', 'dxt1', 'dxt3', 'dxt5'):
        if compress and HAS_NUMPY:
            write_dds_dxt5(path, w, h, bgra)
        else:
            write_dds_bgra32(path, w, h, bgra)
    elif fmt == 'tga':
        write_tga(path, w, h, bgra, meta[1])
    else:
        write_png(path, w, h, bgra)


def collect_file_map(roots):
    """合并多个源目录的 gfx 文件表：relpath -> 所属根目录。
    后面的 root 优先级更高（如汉化 mod 覆盖 TNO 本体）。"""
    file_map = {}
    for root in roots:
        gfx = os.path.join(root, 'gfx')
        if not os.path.isdir(gfx):
            continue
        for dp, dirs, fns in os.walk(gfx):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for fn in fns:
                rel = os.path.relpath(os.path.join(dp, fn), root)
                file_map[rel] = root
    return file_map


def scan_and_build(roots, params, out_root,
                   progress_cb=None, log_cb=None, want_list=False, dry_run=False,
                   compress=True):
    """扫描多源 gfx，把蓝色贴图换色后写入 out_root。返回统计信息。"""
    log = log_cb or (lambda s: None)
    stats = {"scanned": 0, "blue": 0, "skipped_dir": 0, "unsupported": 0,
             "errors": [], "changed_bytes": 0}
    jobs = []
    file_map = collect_file_map(roots)
    all_files = sorted(file_map)
    total = len(all_files)
    for idx, rel in enumerate(all_files):
        root = file_map[rel]
        kind = classify_file(rel)
        if kind is None:
            stats["skipped_dir"] += 1
            continue
        # 国旗类贴图一律不换色（保持各国国旗原色）
        if 'flag' in os.path.basename(rel).lower():
            stats["skipped_dir"] += 1
            continue
        stats["scanned"] += 1
        try:
            w, h, bgra, meta = read_for_scan(root, rel, kind)
        except Exception as e:
            stats["unsupported"] += 1
            stats["errors"].append((rel, str(e)))
            continue
        if bgra is None:
            stats["skipped_dir"] += 1
            continue
        blue, opaque = count_blue(bgra, w, h)
        if blue >= BLUE_MIN_COUNT and (blue / max(1, opaque)) >= BLUE_MIN_FRAC:
            l_hi = inband_l_hi(bgra, w, h)
            nb = apply_transform(params, bgra, w, h, l_hi=l_hi)
            if not dry_run:
                write_image(out_root, rel, kind, w, h, nb, meta, compress=compress)
            stats["blue"] += 1
            stats["changed_bytes"] += len(nb)
            jobs.append(rel)
            if want_list:
                log("  改色 %s  (%dx%d, 蓝像素 %d/%d)" % (rel, w, h, blue, opaque))
        else:
            stats["skipped_dir"] += 1
        if progress_cb and (idx % 25 == 0 or idx == total - 1):
            progress_cb(idx + 1, total, stats["blue"])
    return stats, jobs


# ---------------------------------------------------------------------------
# mod 元数据生成
# ---------------------------------------------------------------------------

def _fmt01(v):
    s = "%.3f" % round(v, 3)
    return s.rstrip('0').rstrip('.')


def lighten(c, k):
    return tuple(int(round(ch + (255 - ch) * k)) for ch in c)


def darken(c, k):
    return tuple(int(round(ch * k)) for ch in c)


def make_sele_lua(target):
    state = lighten(target, 0.18)
    prov = darken(target, 0.42)
    return """NDefines.NGraphics.BORDER_COLOR_SELECTION_STATE_R = %s
NDefines.NGraphics.BORDER_COLOR_SELECTION_STATE_G = %s
NDefines.NGraphics.BORDER_COLOR_SELECTION_STATE_B = %s
NDefines.NGraphics.BORDER_COLOR_SELECTION_PROVINCE_R = %s
NDefines.NGraphics.BORDER_COLOR_SELECTION_PROVINCE_G = %s
NDefines.NGraphics.BORDER_COLOR_SELECTION_PROVINCE_B = %s
""" % tuple(_fmt01(c / 255.0) for c in state + prov)


def make_descriptor(mod_name, deps):
    dep_txt = "\n".join('\t"%s"' % d for d in deps)
    return ('version="1.0"\n'
            'tags={\n\t"Graphics"\n}\n'
            'dependencies={\n%s\n}\n'
            'name="%s"\n'
            'supported_version="1.19.*"\n') % (dep_txt, mod_name)


def make_thumbnail(path, target):
    """512x512：深色底 + 目标色圆环。"""
    W = 512
    rgba = bytearray(W * W * 4)
    bg = (14, 17, 20, 255)
    ring = target + (255,)
    for y in range(W):
        for x in range(W):
            d = math.hypot(x - W / 2, y - W / 2)
            o = y * W * 4 + x * 4
            if 150 <= d <= 190:
                rgba[o:o + 4] = bytes(ring)
            elif 190 < d <= 200:
                rgba[o:o + 4] = bytes((60, 66, 74, 255))
            else:
                rgba[o:o + 4] = bytes(bg)
    write_png(path, W, W, bytes(rgba))


def _resolve_root(roots, rel):
    """按优先级返回包含 rel 的源根目录（高优先级在前）。"""
    for root in roots:
        if os.path.exists(os.path.join(root, rel)):
            return root
    return roots[0]


def make_preview(roots, params, out_root, log):
    """拼一张 原图|换色后 对照图 preview.png。"""
    samples = [
        "gfx\\interface\\topbar\\background.dds",
        "gfx\\interface\\topbar\\achievements_button.dds",
        "gfx\\aces\\ace_none.dds",
        "gfx\\interface\\technologies\\1950_air_radar.dds",
        "gfx\\interface\\topbar\\nuke_icon.dds",
        "gfx\\interface\\topbar\\armyoverview_button.dds",
    ]
    H = 56
    GAP = 6
    label_h = 16
    rows = []
    used = 0
    for rel in samples:
        kind = classify_file(rel)
        root = _resolve_root(roots, rel)
        if not os.path.exists(os.path.join(root, rel)):
            continue
        try:
            w, h, bgra, _ = read_image(root, rel, kind)
        except Exception:
            continue
        nb = apply_transform(params, bgra, w, h, l_hi=inband_l_hi(bgra, w, h))
        if w <= 0 or h <= 0:
            continue
        sc = H / h
        nw = max(1, int(w * sc))
        if HAS_NUMPY:
            a = _np.frombuffer(bgra, dtype=_np.uint8).reshape(h, w, 4)
            b2 = _np.frombuffer(nb, dtype=_np.uint8).reshape(h, w, 4)
            def down(im):
                ys = (_np.arange(H) * h / H).astype(int)
                xs = (_np.arange(nw) * w / nw).astype(int)
                return im[ys][:, xs].tobytes()
            orig = down(a)
            new = down(b2)
        else:
            def down(im):
                out = bytearray(H * nw * 4)
                for yy in range(H):
                    sy = min(h - 1, int(yy * h / H))
                    for xx in range(nw):
                        sx = min(w - 1, int(xx * w / nw))
                        src = (sy * w + sx) * 4
                        dst = (yy * nw + xx) * 4
                        out[dst:dst + 4] = im[src:src + 4]
                return bytes(out)
            orig = down(bgra)
            new = down(nb)
        rows.append((orig, new, nw))
        used += 1
    if not rows:
        log("预览: 无可用样本")
        return
    maxw = max(r[2] for r in rows)
    W = maxw * 2 + GAP * 3
    Ht = used * (H + GAP) + label_h + GAP
    canvas = bytearray(W * Ht * 4)
    filler = bytes((22, 26, 30, 255))
    for i in range(W * Ht):
        canvas[i * 4:i * 4 + 4] = filler
    y = GAP
    for orig, new, nw in rows:
        x = GAP
        for im in (orig, new):
            for yy in range(H):
                src = yy * nw * 4
                dst = ((y + yy) * W + x) * 4
                canvas[dst:dst + nw * 4] = im[src:src + nw * 4]
            x += nw + GAP
        y += H + GAP
    # 底部色条：左=原始蓝，右=目标色
    bar_h = label_h
    for yy in range(bar_h):
        for xx in range(W):
            dst = ((y + yy) * W + xx) * 4
            if xx < W // 2:
                canvas[dst:dst + 4] = bytes((89, 199, 194, 255))
            else:
                canvas[dst:dst + 4] = bytes(params["target"] + (255,))
    write_png(os.path.join(out_root, 'preview.png'), W, Ht, bytes(canvas))
    log("预览图已生成: preview.png (%dx%d, %d 组样本)" % (W, Ht, used))


def mod_name_from_descriptor(root):
    """从 descriptor.mod 读取 mod 显示名（用于依赖声明）。"""
    p = os.path.join(root, 'descriptor.mod')
    if os.path.isfile(p):
        try:
            txt = open(p, 'r', encoding='utf-8', errors='ignore').read(4000)
            m = re.search(r'name\s*=\s*"([^"]+)"', txt)
            if m:
                return m.group(1)
        except Exception:
            pass
    return None


def generate_mod(roots, target_rgb, out_root, mod_name=None, darken=0.0,
                 compress=True, progress_cb=None, log_cb=None):
    """roots: 源目录列表（第一个为基础 TNO，后面的汉化/UI 覆盖 mod 优先级更高）。"""
    log = log_cb or (lambda s: None)
    params = make_params(target_rgb, darken)
    if mod_name is None:
        mod_name = "TNO UI #%02X%02X%02X GUI" % target_rgb
    out_root = os.path.abspath(out_root)
    os.makedirs(out_root, exist_ok=True)
    t0 = time_now()
    log("开始扫描 %s ..." % " + ".join(os.path.abspath(r) for r in roots))
    stats, jobs = scan_and_build(roots, params, out_root,
                                 progress_cb=progress_cb, log_cb=log,
                                 compress=compress)
    # 字体文字配色（加载界面默认文字色 D={89,199,194} 等；含汉化 mod 的字体）
    nfonts = patch_font_colors(roots, out_root, params)
    if nfonts:
        log("已替换 %d 个字体定义文件中的蓝色文字色" % nfonts)
    # 依赖：基础 TNO + 各覆盖 mod 的显示名（保证加载顺序在它们之后）
    deps = []
    for r in roots:
        nm = mod_name_from_descriptor(r)
        if nm:
            deps.append(nm)
    deps = list(dict.fromkeys(deps))
    # 元数据
    os.makedirs(os.path.join(out_root, 'common', 'defines'), exist_ok=True)
    with open(os.path.join(out_root, 'common', 'defines', 'sele_c.lua'), 'w', encoding='utf-8') as f:
        f.write(make_sele_lua(target_rgb))
    desc = make_descriptor(mod_name, deps)
    with open(os.path.join(out_root, 'descriptor.mod'), 'w', encoding='utf-8') as f:
        f.write(desc)
    base = os.path.basename(out_root)
    with open(os.path.join(out_root + '.mod'), 'w', encoding='utf-8') as f:
        f.write(desc + '\npath="mod/%s"\n' % base)
    make_thumbnail(os.path.join(out_root, 'thumbnail.png'), target_rgb)
    make_preview(roots, params, out_root, log)
    with open(os.path.join(out_root, 'README.txt'), 'w', encoding='utf-8') as f:
        f.write(readme_text(mod_name, target_rgb, base, deps))
    log("完成: 扫描 %d 个图片，改色 %d 个，跳过 %d 个，不支持 %d 个，耗时 %.1fs" % (
        stats["scanned"], stats["blue"], stats["skipped_dir"],
        stats["unsupported"], time_now() - t0))
    if stats["errors"]:
        log("警告: %d 个文件无法读取(已跳过):" % len(stats["errors"]))
        for rel, err in stats["errors"][:10]:
            log("   %s: %s" % (rel, err))
    return stats, jobs


def readme_text(mod_name, target, base, deps):
    r, g, b = target
    dep_txt = "、".join('"%s"' % d for d in deps)
    return """%s
====================
由 TNO UI 换色 Mod 生成器自动生成。
主色: #%02X%02X%02X
依赖: %s

安装方法（二选一）:
1) 把整个文件夹 "%s" 和旁边的 "%s.mod" 一起复制到
   "文档/Paradox Interactive/Hearts of Iron IV/mod/" 下，
   然后在启动器里启用该 Mod。
2) 或把文件夹 "%s" 直接放进 mod 目录并用启动器导入。

注意:
- 本 Mod 已按依赖声明排在 TNO 本体与汉化/UI 覆盖 Mod 之后加载，
  覆盖其所有蓝色贴图与字体颜色。
- 若游戏提示缺少依赖，在 descriptor.mod 的 dependencies 里改/删即可。
""" % (mod_name, r, g, b, dep_txt, base, base, base)


def time_now():
    import time
    return time.time()


# ---------------------------------------------------------------------------
# 预设与工具
# ---------------------------------------------------------------------------

PRESETS = [
    ("orange", "橙色（仿旧版 orange mod）", (255, 186, 92)),
    ("darkpurple", "深紫色（仿旧版 dark purple mod）", (101, 70, 128)),
    ("red", "红色", (229, 72, 77)),
    ("green", "绿色", (70, 167, 88)),
    ("yellow", "金色", (245, 165, 36)),
    ("teal", "青色", (18, 165, 148)),
    ("pink", "玫红", (214, 64, 159)),
    ("sky", "天蓝（弱化蓝）", (126, 178, 255)),
    ("gray", "石墨灰", (150, 155, 165)),
]


def parse_color(s):
    s = s.strip().lstrip('#')
    if len(s) == 6:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    raise ValueError("颜色格式应为 #RRGGBB，如 #FFBA5C")


def find_tno_root(cwd):
    cand = [cwd]
    for d in sorted(os.listdir(cwd)):
        p = os.path.join(cwd, d)
        if os.path.isdir(p):
            cand.append(p)
    for p in cand:
        desc = os.path.join(p, 'descriptor.mod')
        if os.path.isfile(desc):
            try:
                txt = open(desc, 'r', encoding='utf-8', errors='ignore').read(2000)
            except Exception:
                txt = ''
            if 'TNO' in txt and ('Requiem' in txt or 'New Order' in txt or '新秩序' in txt):
                return p
    return None


def find_hoi4_mod_dir():
    for base in (os.path.expanduser('~'), os.environ.get('USERPROFILE', '')):
        p = os.path.join(base, 'Documents', 'Paradox Interactive', 'Hearts of Iron IV', 'mod')
        if os.path.isdir(p):
            return p
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cli_main(argv):
    ap = argparse.ArgumentParser(description="TNO UI 换色 Mod 生成器")
    ap.add_argument('--tno', help='TNO 本体目录(含 descriptor.mod 与 gfx/)')
    ap.add_argument('--overlay', action='append', default=[],
                    help='汉化/UI 覆盖 mod 目录（可多次指定，优先级高于 TNO 本体；'
                         '如汉化 mod 2243912940）')
    ap.add_argument('--color', help='目标颜色 #RRGGBB')
    ap.add_argument('--preset', choices=[p[0] for p in PRESETS], help='预设颜色')
    ap.add_argument('--out', help='输出目录')
    ap.add_argument('--name', help='Mod 显示名')
    ap.add_argument('--darken', type=float, default=0.0, help='亮灰/白压暗强度 0~1(暗色系风格)')
    ap.add_argument('--install', action='store_true', help='生成后复制到 HOI4 mod 目录')
    ap.add_argument('--no-compress', action='store_true', help='输出未压缩 32 位 DDS（体积约大 4 倍）')
    ap.add_argument('--scan-only', action='store_true', help='只扫描列出会被改色的文件，不生成')
    ap.add_argument('--list', action='store_true', help='输出每个改色文件路径')
    args = ap.parse_args(argv)
    tno = args.tno or find_tno_root(os.getcwd())
    if not tno or not os.path.isdir(tno):
        print("找不到 TNO 目录，请用 --tno 指定。")
        return 1
    roots = [tno] + [o for o in args.overlay if os.path.isdir(o)]
    if len(args.overlay) != len(roots) - 1:
        print("警告: 部分 --overlay 目录不存在，已忽略。")
    if args.preset:
        target = dict((p[0], p[2]) for p in PRESETS)[args.preset]
        pname = dict((p[0], p[1]) for p in PRESETS)[args.preset]
    elif args.color:
        target = parse_color(args.color)
        pname = "颜色 #%02X%02X%02X" % target
    else:
        print("请用 --color 或 --preset 指定颜色。")
        return 1
    if args.scan_only:
        params = make_params(target, args.darken)
        print("扫描 %s ..." % " + ".join(roots))
        stats, jobs = scan_and_build(roots, params, os.path.join(tno, '.scan_tmp'),
                                     log_cb=print, want_list=args.list, dry_run=True)
        print("统计: 扫描 %d，将改色 %d，跳过 %d，不支持 %d" % (
            stats["scanned"], stats["blue"], stats["skipped_dir"], stats["unsupported"]))
        return 0
    out = args.out or os.path.join(os.getcwd(), 'generated_mods',
                                   'TNO_UI_%02X%02X%02X' % target)
    mod_name = args.name or ("TNO UI %s GUI" % pname)
    log = lambda s: print(s)
    stats, jobs = generate_mod(roots, target, out, mod_name=mod_name,
                               darken=args.darken, compress=not args.no_compress,
                               progress_cb=lambda i, n, b: None, log_cb=log)
    print("\n生成完成: %s" % out)
    print("共改色 %d 个贴图，输出体积 %.1f MB" % (stats["blue"], stats["changed_bytes"] / 1048576))
    if args.install:
        moddir = find_hoi4_mod_dir()
        if moddir:
            import shutil
            base = os.path.basename(out)
            dst = os.path.join(moddir, base)
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(out, dst)
            shutil.copy2(out + '.mod', os.path.join(moddir, base + '.mod'))
            print("已复制到 %s" % dst)
        else:
            print("未找到 HOI4 mod 目录，跳过安装。")
    return 0


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

def gui_main():
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, colorchooser, messagebox
    except Exception as e:
        print("GUI 需要 tkinter（Windows 官方 Python 自带）。改用命令行参数。", e)
        return 1

    root = tk.Tk()
    root.title("TNO UI 换色 Mod 生成器")
    root.geometry("720x640")
    root.minsize(640, 560)

    state = {"target": (255, 186, 92), "running": False}

    def hex_of(c):
        return "#%02X%02X%02X" % c

    pad = {'padx': 8, 'pady': 4}

    frm = ttk.Frame(root, padding=10)
    frm.pack(fill='both', expand=True)

    # TNO 目录
    ttk.Label(frm, text="TNO 本体目录（含 descriptor.mod 和 gfx/）:").grid(row=0, column=0, sticky='w', **pad)
    tno_var = tk.StringVar(value=find_tno_root(os.getcwd()) or "")
    tno_entry = ttk.Entry(frm, textvariable=tno_var, width=60)
    tno_entry.grid(row=1, column=0, sticky='we', **pad)
    ttk.Button(frm, text="浏览…", command=lambda: tno_var.set(
        filedialog.askdirectory(title="选择 TNO 本体目录"))).grid(row=1, column=1, **pad)

    # 汉化/UI 覆盖 mod 目录
    ttk.Label(frm, text="汉化/UI 覆盖 mod 目录（可多个，用 ; 分隔，优先级高于 TNO；"
                        "如汉化 mod 2243912940）:").grid(row=2, column=0, sticky='w', **pad)
    ov_row = ttk.Frame(frm)
    ov_row.grid(row=3, column=0, columnspan=2, sticky='we', **pad)
    ov_var = tk.StringVar(value="")
    ttk.Entry(ov_row, textvariable=ov_var, width=60).pack(side='left', fill='x', expand=True)
    ttk.Button(ov_row, text="浏览…", command=lambda: ov_var.set(
        filedialog.askdirectory(title="选择汉化/UI 覆盖 mod 目录"))).pack(side='left', padx=4)

    # 颜色
    ttk.Label(frm, text="目标颜色:").grid(row=4, column=0, sticky='w', **pad)
    color_row = ttk.Frame(frm)
    color_row.grid(row=5, column=0, sticky='w', **pad)
    color_var = tk.StringVar(value=hex_of(state["target"]))
    color_entry = ttk.Entry(color_row, textvariable=color_var, width=10)
    color_entry.pack(side='left')
    swatch = tk.Label(color_row, text="  ", bg=hex_of(state["target"]), width=3)
    swatch.pack(side='left', padx=6)

    def pick_color():
        c = colorchooser.askcolor(color=color_var.get(), title="选择目标颜色")
        if c and c[1]:
            color_var.set(c[1])
            update_swatch()

    def update_swatch():
        try:
            rgb = parse_color(color_var.get())
            swatch.configure(bg=hex_of(rgb))
            state["target"] = rgb
        except ValueError:
            pass

    color_var.trace_add('write', lambda *a: update_swatch())
    ttk.Button(color_row, text="选色…", command=pick_color).pack(side='left', padx=4)

    presets_row = ttk.Frame(frm)
    presets_row.grid(row=6, column=0, columnspan=2, sticky='w', **pad)
    ttk.Label(presets_row, text="预设:").pack(side='left')
    for key, label, rgb in PRESETS:
        def mk(rgb=rgb):
            def go():
                color_var.set(hex_of(rgb))
                update_swatch()
            return go
        b = tk.Button(presets_row, text=label.split("（")[0], bg=hex_of(rgb),
                      fg='white', width=7, command=mk())
        b.pack(side='left', padx=2, pady=2)

    # 选项
    opt = ttk.LabelFrame(frm, text="选项", padding=8)
    opt.grid(row=7, column=0, columnspan=2, sticky='we', **pad)
    darken_var = tk.DoubleVar(value=0.0)
    ttk.Label(opt, text="亮灰/白色压暗（暗色系风格）:").grid(row=0, column=0, sticky='w')
    darken_scale = ttk.Scale(opt, from_=0, to=1, variable=darken_var, length=220)
    darken_scale.grid(row=0, column=1, sticky='w')
    darken_lbl = ttk.Label(opt, text="0%")
    darken_lbl.grid(row=0, column=2)
    darken_var.trace_add('write', lambda *a: darken_lbl.configure(text="%d%%" % int(darken_var.get() * 100)))
    compress_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(opt, text="压缩输出 DXT5（体积约小 4 倍，原版游戏 UI 同款格式；取消则输出未压缩 32 位）",
                    variable=compress_var).grid(row=1, column=0, columnspan=3, sticky='w')

    # 输出
    ttk.Label(frm, text="输出:").grid(row=8, column=0, sticky='w', **pad)
    out_row = ttk.Frame(frm)
    out_row.grid(row=9, column=0, columnspan=2, sticky='we', **pad)
    default_out = os.path.join(os.getcwd(), 'generated_mods', 'TNO_UI_FFBA5C')
    out_var = tk.StringVar(value=default_out)
    ttk.Entry(out_row, textvariable=out_var, width=60).pack(side='left', fill='x', expand=True)
    ttk.Button(out_row, text="浏览…", command=lambda: out_var.set(
        filedialog.askdirectory(title="选择输出目录"))).pack(side='left', padx=4)
    install_var = tk.BooleanVar(value=True)
    moddir = find_hoi4_mod_dir()
    ttk.Checkbutton(frm, text=("生成后同时复制到 HOI4 mod 目录 (%s)" % moddir) if moddir
                    else "生成后同时复制到 HOI4 mod 目录（未检测到，请手动安装）",
                    variable=install_var).grid(row=10, column=0, columnspan=2, sticky='w', **pad)

    # 进度/日志
    prog = ttk.Progressbar(frm, maximum=1000)
    prog.grid(row=11, column=0, columnspan=2, sticky='we', **pad)
    logtxt = tk.Text(frm, height=12, width=90, state='disabled', font=('Consolas', 9))
    logtxt.grid(row=12, column=0, columnspan=2, sticky='nsew', **pad)
    frm.rowconfigure(12, weight=1)
    frm.columnconfigure(0, weight=1)

    def log(s):
        logtxt.configure(state='normal')
        logtxt.insert('end', s + "\n")
        logtxt.see('end')
        logtxt.configure(state='disabled')

    run_btn = ttk.Button(frm, text="开始生成", width=20)
    run_btn.grid(row=13, column=0, columnspan=2, **pad)

    def open_out():
        import subprocess
        d = out_var.get()
        if os.path.isdir(d):
            os.startfile(d)  # noqa

    open_btn = ttk.Button(frm, text="打开输出文件夹", command=open_out, state='disabled')
    open_btn.grid(row=14, column=0, columnspan=2, **pad)

    q = queue.Queue()

    def worker(roots, target, out, darken, compress, install):
        try:
            mod_name = "TNO UI #%02X%02X%02X GUI" % target
            stats, _ = generate_mod(
                roots, target, out, mod_name=mod_name, darken=darken,
                compress=compress,
                progress_cb=lambda i, n, b: q.put(("prog", i, n, b)),
                log_cb=lambda s: q.put(("log", s)))
            q.put(("done", stats))
            if install:
                moddir = find_hoi4_mod_dir()
                if moddir:
                    import shutil
                    base = os.path.basename(out)
                    dst = os.path.join(moddir, base)
                    if os.path.exists(dst):
                        shutil.rmtree(dst)
                    shutil.copytree(out, dst)
                    shutil.copy2(out + '.mod', os.path.join(moddir, base + '.mod'))
                    q.put(("log", "已复制到 %s" % dst))
                else:
                    q.put(("log", "未找到 HOI4 mod 目录，跳过复制。"))
        except Exception as e:
            import traceback
            q.put(("log", "出错: %s\n%s" % (e, traceback.format_exc())))
            q.put(("done", None))

    def start():
        if state["running"]:
            return
        tno = tno_var.get()
        if not tno or not os.path.isdir(tno):
            messagebox.showerror("错误", "请选择有效的 TNO 本体目录")
            return
        try:
            target = parse_color(color_var.get())
        except ValueError:
            messagebox.showerror("错误", "颜色格式应为 #RRGGBB")
            return
        out = out_var.get()
        if not out:
            messagebox.showerror("错误", "请填写输出目录")
            return
        roots = [tno] + [o.strip() for o in ov_var.get().split(';') if o.strip()]
        roots = [r for r in roots if os.path.isdir(r)]
        state["running"] = True
        run_btn.configure(state='disabled')
        open_btn.configure(state='disabled')
        logtxt.configure(state='normal')
        logtxt.delete('1.0', 'end')
        logtxt.configure(state='disabled')
        prog.configure(value=0)
        log("开始生成…")
        t = threading.Thread(target=worker, args=(
            roots, target, out, darken_var.get(), compress_var.get(),
            install_var.get()),
            daemon=True)
        t.start()
        poll()

    def poll():
        try:
            while True:
                msg = q.get_nowait()
                if msg[0] == "prog":
                    _, i, n, b = msg
                    prog.configure(value=int(i / max(1, n) * 1000))
                elif msg[0] == "log":
                    log(msg[1])
                elif msg[0] == "done":
                    stats = msg[1]
                    state["running"] = False
                    run_btn.configure(state='normal')
                    open_btn.configure(state='normal')
                    if stats:
                        log("全部完成：改色 %d 个贴图，输出 %.1f MB。预览图见 preview.png"
                            % (stats["blue"], stats["changed_bytes"] / 1048576))
        except queue.Empty:
            pass
        root.after(120, poll)

    run_btn.configure(command=start)
    log("TNO UI 换色 Mod 生成器就绪。\n选好颜色 -> 开始生成 -> 自动产出 Mod 文件夹 + 对照预览图。")
    root.mainloop()
    return 0


def main():
    if '--gui' in sys.argv:
        sys.argv.remove('--gui')
        return gui_main()
    if len(sys.argv) > 1:
        return cli_main(sys.argv[1:])
    return gui_main()


if __name__ == '__main__':
    sys.exit(main())
