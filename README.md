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

### 方式一：Web 界面（推荐，双击即用）

双击 **`启动GUI.bat`**（或 `python tno_color_gen.py` / `python tno_web_gui.py`），
自动启动本地服务（仅绑定 `127.0.0.1`）并打开浏览器——精美深色界面，功能齐全：

1. **所有 Mod 目录**：可增删的路径列表（TNO 本体 + 汉化 + 任意 sub mod），
   **越靠后优先级越高**；每行自动识别 descriptor 名称；一键「自动装配」按依赖找全套；
2. **自定义取色器**：色相环 + 饱和度/亮度滑杆 + `#RRGGBB` 手输 + 9 个预设色块，
   界面主色随之实时联动（参考蓝 `#59C7C2` 精确映射为目标色）；
3. **选项**：亮灰/白压暗滑杆（暗色系风格）、DXT5 压缩、并行进程数、Mod 显示名、
   生成后自动复制到 HOI4 mod 目录；
4. **输出目录** + 「生成 Mod / 仅扫描统计」两种模式；
5. 实时进度条 + 彩色日志 + 可取消；完成后显示统计卡片与
   `preview.png` 原版/换色后对照图，一键打开输出文件夹。

**默认输出未压缩 32 位 BGRA 贴图**（与 TNO 本体 UI 完全一致，零压缩瑕疵，
体积约 1.3 GB）——DXT5 块状压缩会在细线条/渐变上产生纯色噪点与明暗条纹，
因此不再作为默认；如确需缩小体积可勾选“压缩输出 DXT5”（体积约小 4 倍，可能有瑕疵）。

**多进程并行**：默认按 CPU 数并行（最多 8 进程），全量生成约 2~3 分钟
（单进程需 8~10 分钟）；可在界面或命令行 `--jobs N` 调整。

> Web 界面只依赖 Python 标准库（与生成核心一致）。停止服务 = 关闭启动窗口/按 Ctrl+C；
> 刷新浏览器即可重新打开界面。旧版 tkinter 界面仍保留：`python tno_color_gen.py --gui`。

### 方式二：命令行

```
# 生成橙色：一次传入所有相关 mod（本体 + 汉化 + LAR），越靠后优先级越高
python tno_color_gen.py --mods "D:\...\hoi4\mod\2438003901" "D:\...\hoi4\mod\2243912940" "D:\...\hoi4\mod\3256452254" --preset orange --out TNO_UI_Orange

# 不传 --mods：在游戏 mod 目录 / 工作目录下按 descriptor.mod 的 dependencies 自动装配
python tno_color_gen.py --preset orange --out TNO_UI_Orange

# 旧参数仍可用（--tno = 第一个 mod，--overlay = 追加的高优先级 mod）
python tno_color_gen.py --tno "D:\...\2438003901" --overlay "D:\...\2243912940" --preset orange --out TNO_UI_Orange

# 指定并行进程数
python tno_color_gen.py --mods "D:\...\2438003901" --jobs 4 --color "#F5A524" --out TNO_UI_Gold

# 自定义颜色（纯白，严格按输入色输出）
python tno_color_gen.py --mods "D:\...\2438003901" --color "#FFFFFF" --out TNO_UI_White

# 暗色系风格（把白色压暗 40%）
python tno_color_gen.py --mods "D:\...\2438003901" --color "#654680" --darken 0.4 --out TNO_UI_DarkPurple

# 只扫描不生成：列出会被改色的文件
python tno_color_gen.py --mods "D:\...\2438003901" --preset orange --scan-only --list

# 生成后直接复制到 HOI4 mod 目录
python tno_color_gen.py --mods "D:\...\2438003901" --preset red --install
```

## 安装生成的 Mod

把输出文件夹和旁边的 `XXX.mod` 一起复制到
`文档/Paradox Interactive/Hearts of Iron IV/mod/`，
在启动器里启用即可（本 Mod 需排在 TNO 本体之后加载；
若还装了其他 UI 美化 mod，把它排在最后）。

## 换色原理

TNO 界面贴图（`.dds` 绝大多数为未压缩 32 位 BGRA，另有少量 24 位 RGB / DXT3 / DX10-BGRA /
`.tga` / `.png`，均已兼容）由“TNO 蓝”色板构成。对每个像素：

- 色相在蓝青色带 **[145°, 240°]**（带余弦平滑过渡）→ **色相向目标色收敛 +
  饱和度缩放 + 亮度映射**：参考蓝 `(89,199,194)` 恰好变成你输入的颜色
  （h/s/l 三通道对齐），深青底变成目标色的暗色版、高光变亮色版；
  渐变不断层、不出现带状断层，也不把色带两端甩到红/黄绿；
- **过亮目标（纯白/亮金等）自动保留亮部层次**：目标明度过亮时压缩扩张系数，并按
  每张贴图自身的亮度分布拉伸亮部，按钮斜面/高光不会全部撞上 255 上限；
- 灰阶文字、红/绿强调色（如 +/− 图标）、白色、黑色一律不动；
- **压暗亮灰/白色为平滑加权**（无硬阈值）：纸张/渐变纹理不会出现突兀的暗色噪点；
- **加载界面/游戏内文字颜色**也会一起换：`interface/*.gfx` 字体定义里的蓝青色
  `textcolors`（如加载界面默认文字色 `D = { 89 199 194 }`）会被替换为目标色；
  没有 D 默认色键的字体文件（如汉化版字体）会自动补上 `D = 目标色`；
- 输出统一写为**未压缩 32 位 BGRA DDS**（与 TNO 本体一致，零压缩瑕疵；
  可选 DXT5 压缩）/ 同规格 TGA / PNG，兼容性最好。

可选“压暗亮灰/白色”选项可复刻旧 dark purple mod 的整体变暗风格。

## 覆盖范围与过滤规则

扫描所有输入 mod 的 `gfx/`（TNO 本体 + 汉化 / 任意 sub mod，同名文件以优先级高的为准），
把蓝色像素占比 ≥0.6% 且数量 ≥12 的贴图全部换色。以下内容一律**不处理**：

- `event_pictures` / `superevent_pictures` / `loadingscreens` / `background` /
  `custom_news_headers` / `fonts` / `FX` / `particles` / `entities` /
  `train_gfx_database` / `models`（3D 模型贴图）
- **国策图标**（`gfx/interface/goals/**`）——整体保持原样，仅白名单例外
  （`goal_unknown.dds` 未知国策占位图标会换色）；
  **领袖头像/照片**（`gfx/leaders/**`）、**国旗类贴图**（文件名含 flag）——保持原样
- **照片/大幅背景**（采样去重色数 > 4000，如主菜单背景、选国背景、事件纸张、
  实拍照片、人像）——整体跳过，天空/水面等局部蓝色不会被目标色替代
- **饼图等按名称模式保护的贴图**：文件名含 pie/piechart/pie_——语义色块保持原色，
  自动避开 Pierre/Pieces 等误伤
- **启用/未启用状态区分**：船坞图标（dockyard_icon*）正常换色，但 `*_metal`
  版本（未启用状态）换色后整体转为灰度，与启用状态的鲜艳目标色明显区分；
  其他 `_metal` 贴图（生产箭头、建筑条、瓦片等）不受影响
- **换色后几乎无变化的贴图**（可见像素变化 < 30 个）不打包——输出里每个文件
  都是真正变了色的

## 依赖

仅 Python 3.8+ 标准库即可运行；安装 `numpy`（强烈建议）与 `Pillow` 会大幅提速，缺失时自动降级。

## 目录内容

- `启动GUI.bat` —— 一键启动（双击即可：自动找 Python → 启动本地服务 → 打开浏览器）
- `tno_color_gen.py` —— 生成核心（CLI + 旧版 tkinter GUI；不传路径时按
  `descriptor.mod` 的 `dependencies` 自动装配全部相关 mod；无参数默认打开 Web 界面）
- `tno_web_gui.py` —— Web 界面后端（仅 Python 标准库：本地 HTTP 服务 + 作业管理 +
  系统目录对话框，复用生成核心）
- `web_gui/index.html` —— Web 界面前端（单文件：深色主题、自定义取色器、实时进度/日志）
- `test_transform.py` / `test_codec.py` / `test_dxt5.py` / `test_web_api.py` —— 自检脚本
- 换色素材源（游戏目录）：`D:\heart of iron\SW00383\langou123\hoi4\mod\2438003901`（TNO 本体）、
  `2243912940`（汉化）、`2781363716`（东方红）、`3256452254`（道阻且长 LAR）——
  生成时全部作为源参与换色，同名贴图以高优先级为准；Web/CLI 也可手动传入任意 mod 组合
- `generated_mods/` —— 已生成示例（源 = 自动装配的全部相关 mod）：
  `TNO_UI_GOLD`（亮金 #F5A524）、`TNO_UI_FFBA5C`（橙）、`TNO_UI_DarkPurple`（深紫）、
  `TNO_UI_White`（纯白），每个约 1.3 GB、13000 张贴图，可直接放入 mod 目录使用
  （`TNO_UI_GOLD_FOR_LAR` 是旧版算法生成的测试产物，请删除，改用 `TNO_UI_GOLD`）
