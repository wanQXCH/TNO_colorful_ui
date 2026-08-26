# -*- coding: utf-8 -*-
"""Codec tests for tno_color_gen."""
import struct, sys
import tno_color_gen as g

fails = 0

def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails += 1

# 1. DXT3 decode: leader_selection_entry_bg (510x98)
w, h, bgra, meta = g.read_dds(r"2980739000\gfx\interface\leader_selection_entry_bg.dds")
check("DXT3 size", (w, h) == (510, 98) and len(bgra) == w * h * 4)
n = w * h
cnt = sum(1 for i in range(0, len(bgra), 4) if bgra[i+3] > 0)
print("   DXT3 opaque px:", cnt, "/", n)
check("DXT3 has opaque", cnt > 0 and cnt < n)   # 真实 alpha：既有可见也有透明

# 2. DXT3 alpha nibble decode sanity: flag_blank_41_26
w, h, bgra, meta = g.read_dds(r"2980739000\gfx\interface\flag_blank_41_26.dds")
check("DXT3 flag size", (w, h) == (41, 26))
alphas = set(bgra[3::4])
print("   flag alphas:", sorted(alphas)[:10], "...")
check("DXT3 alpha values are 17-multiples", all(a % 17 == 0 or a in (0, 255) for a in alphas))

# 3. 24-bit RGB decode: railway_pattern (128x128)
w, h, bgra, meta = g.read_dds(r"2980739000\gfx\maparrows\railway_pattern.dds")
check("24-bit size", (w, h) == (128, 128) and len(bgra) == 128*128*4)
print("   railway px sample:", tuple(bgra[0:4]), tuple(bgra[100:104]))

# 4. BGRA32 read
w, h, bgra, meta = g.read_dds(r"2980739000\gfx\interface\topbar\background.dds")
check("BGRA32 read", w == 2347 and h == 87 and len(bgra) == 2347*87*4)

# 5. DDS write round trip
import os
g.write_dds_bgra32("_t.dds", w, h, bgra)
w2, h2, bgra2, meta2 = g.read_dds("_t.dds")
check("DDS round trip", (w2, h2) == (w, h) and bgra2 == bgra)
os.remove("_t.dds")

# 6. TGA read + write round trip
w, h, tga_px, hdr = g.read_tga(r"2980739000\gfx\interface\small_flag_overlay.tga")
print("   tga:", w, h, "header:", hdr[0:18].hex())
g.write_tga("_t.tga", w, h, tga_px, hdr)
w2, h2, tga2, _ = g.read_tga("_t.tga")
check("TGA round trip", (w2, h2) == (w, h) and tga2 == tga_px)
os.remove("_t.tga")

# 7. PNG round trip
rgba = bytearray()
for y in range(64):
    for x in range(96):
        rgba += bytes((x*2 % 256, y*4 % 256, (x+y) % 256, 255 if (x+y) % 3 else 128))
g.write_png("_t.png", 96, 64, bytes(rgba))
w2, h2, png2 = g.read_png("_t.png")
check("PNG round trip", (w2, h2) == (96, 64) and png2 == bytes(rgba))
os.remove("_t.png")

# 8. DX10 BGRA read
w, h, bgra, meta = g.read_dds(r"2980739000\gfx\interface\decisions\decision_category_manchuria_war.dds")
check("DX10 read", w == 51 and h == 40 and len(bgra) == 51*40*4)
print("   DX10 px:", tuple(bgra[:4]))

print("\n%s" % ("ALL PASS" if fails == 0 else "%d FAILURES" % fails))
sys.exit(1 if fails else 0)
