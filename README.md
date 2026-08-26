# TNO UI 换色 Mod 生成器

## 本工具使用DSH + Deepseek V4 Flash 制作
把 TNO（The New Order / TNO: Requiem）界面里所有的“TNO 蓝”自动替换成你想要的任意颜色，
一键生成可直接安装的 HOI4 Mod。不需要任何游戏内工具，不依赖 Photoshop。

## 为什么会有这个工具

- TNO 的 UI 以亮蓝 `(89,199,194)` + 深青黑 `(6,16,21)` 为主色调，长时间游玩刺眼。
- 创意工坊的换色 mod（如 tnoui orange / dark purple GUI）都是作者用 PS 逐张贴图手工换色：
  覆盖不全（漏掉科技图标等几千张）、颜色固定、且更新滞后于 TNO 本体。
- 本工具直接读取 TNO 本体的贴图，按“蓝青色带 → 目标色”的 HSL 色相映射自动批量换色，
  TNO 更新后重新跑一遍即可，永远适配。

## 使用方法

### 方式一：图形界面（推荐）

```
python tno_color_gen.py
```

1. 选择 TNO 本体目录（含 `descriptor.mod` 和 `gfx/` 的文件夹，即 2980739000 那种）；
2. （可选）在“汉化/UI 覆盖 mod 目录”里填汉化 mod（如 2243912940）——它的贴图会
   以更高优先级参与换色，不会再覆盖你的成果；可填多个，用 `;` 分隔；
3. 点“选色…”或直接输入 `#RRGGBB`，或点预设色块（橙/深紫/红/绿/金/青/玫红/天蓝/石墨灰）；
4. 可选：拖动“亮灰/白色压暗”滑杆做出暗色系风格；
5. 选输出目录（或勾选自动复制到 HOI4 mod 目录）；
6. 点“开始生成”，等待进度完成，打开输出文件夹：
   - 生成的 Mod 文件夹 + `.mod` 文件
   - `preview.png` —— 原版/换色后对照图，不用进游戏即可预览效果

默认输出 DXT5 压缩贴图（体积约 400~500 MB，原版游戏 UI 同款格式）；
取消勾选“压缩输出”则输出未压缩 32 位贴图（体积约 1.9 GB，与 TNO 自带格式一致）。

### 方式二：命令行

```
# 生成橙色（TNO 本体 + 汉化 mod 一起处理）
python tno_color_gen.py --tno 2980739000 --overlay 2243912940 --preset orange --out TNO_UI_Orange

# 自定义颜色（纯白，严格按输入色输出）
python tno_color_gen.py --tno 2980739000 --overlay 2243912940 --color "#FFFFFF" --out TNO_UI_White

# 暗色系风格（把白色压暗 40%）
python tno_color_gen.py --tno 2980739000 --overlay 2243912940 --color "#654680" --darken 0.4 --out TNO_UI_DarkPurple

# 只扫描不生成：列出会被改色的文件
python tno_color_gen.py --tno 2980739000 --preset orange --scan-only --list

# 生成后直接复制到 HOI4 mod 目录
python tno_color_gen.py --tno 2980739000 --preset red --install
```

## 安装生成的 Mod

把输出文件夹和旁边的 `XXX.mod` 一起复制到
`文档/Paradox Interactive/Hearts of Iron IV/mod/`，
在启动器里启用即可（本 Mod 需排在 TNO 本体之后加载；
若还装了其他 UI 美化 mod，把它排在最后）。

## 换色原理

TNO 界面贴图（`.dds` 绝大多数为未压缩 32 位 BGRA，另有少量 24 位 RGB / DXT3 / DX10-BGRA /
`.tga` / `.png`，均已兼容）由“TNO 蓝”色板构成。对每个像素：

- 色相在蓝青色带 **[145°, 240°]**（带余弦平滑过渡）→ **严格映射为目标色**：
  输出 = 目标色 × (像素明度 / 参考蓝明度)。因此参考蓝 `(89,199,194)` 恰好变成
  你输入的颜色，深青底变成目标色的暗色版，高光变成亮色版，层次完整保留且颜色严格贴合输入；
- **过亮目标（如纯白）自动保留亮部层次**：目标明度过亮时压缩扩张系数，并按每张贴图
  自身的亮度分布拉伸亮部，按钮斜面/高光不会全部撞上纯白而糊成一团
  （普通颜色如金/紫/绿映射与旧版完全一致）；
- 灰阶文字、红/绿强调色（如 +/− 图标）、白色、黑色一律不动；
- **加载界面/游戏内文字颜色**也会一起换：`interface/*.gfx` 字体定义里的蓝青色
  `textcolors`（如加载界面默认文字色 `D = { 89 199 194 }`）会被替换为目标色；
  没有 D 默认色键的字体文件（如汉化版字体）会自动补上 `D = 目标色`；
- 输出统一写为 DXT5 压缩 DDS（可选未压缩 32 位）/ 同规格 TGA / PNG，兼容性最好。

可选“压暗亮灰/白色”选项可复刻旧 dark purple mod 的整体变暗风格。

## 覆盖范围与过滤规则

扫描多个源目录的 `gfx/`（本体 + 汉化/UI 覆盖 mod，同名文件以高优先级为准），
把蓝色像素占比 ≥0.6% 且数量 ≥12 的贴图全部换色。以下内容一律**不处理**：

- `event_pictures` / `superevent_pictures` / `loadingscreens` / `background` /
  `custom_news_headers` / `fonts` / `FX` / `particles` / `entities` /
  `train_gfx_database` / `models`（3D 模型贴图）
- **国策图标**（`gfx/interface/goals/**`）、**领袖头像/照片**（`gfx/leaders/**`）、
  **国旗类贴图**（文件名含 flag）——保持原样
- 几乎没有蓝色像素的贴图（个别噪点不算）

## 依赖

仅 Python 3.8+ 标准库即可运行；安装 `numpy`（强烈建议）与 `Pillow` 会大幅提速，缺失时自动降级。

## 目录内容

- `tno_color_gen.py` —— 生成器（单文件，含 CLI + GUI）
- `test_transform.py` / `test_codec.py` / `test_dxt5.py` / `verify_v2.py` —— 自检脚本
- `2980739000/` —— TNO: Requiem 本体（换色素材源）
- `2243912940/` —— TNO 汉化 mod（覆盖源，需与本体一起参与换色）
- `2870558394/` `2871004535/` —— 旧版 orange / dark purple 换色 mod（参考对象；注意：2870558394 实际是 orange，2871004535 才是 dark purple）
- `generated_mods/` —— 已生成示例（均含汉化 mod 覆盖）：
  `TNO_UI_FFBA5C`（橙）、`TNO_UI_DarkPurple`（深紫）、`TNO_UI_White`（纯白，
  严格颜色输出示例），每个约 440 MB、14000 张贴图，可直接放入 mod 目录使用
