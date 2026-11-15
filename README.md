# OGcode Studio — 非平面螺旋与外墙凸出纹理 3D 打印切片工具

**OGcode Studio** 是基于德国设计师 [Robert Dabi](https://www.dabi.me/apps.html) 开发的 [OGcode](https://ogcode.dabi.me/)（详见其官方演示视频 [I built a Web App that 3D-prints the Impossible](https://www.youtube.com/watch?v=zAJkwvvXDko) 与 [3DBite 深度评测](https://3dbite.com/ogcode-browser-based-gcode-generator-3d-printing/)）核心算法复刻并扩展的开源纯前端 Web 切片工具与 Python 命令行引擎。

它打破了传统 **CAD → STL → 2.5D 等高平面切片** 的物理限制，允许你直接导入现有 `.stl` 模型或手绘 2D 旋转母线，在模型外表面生成**常规切片软件无法切出的立体凸出针脚（Protruding Stitches）、奇偶层交错悬空编织网格（Woven Mesh）以及真·三维非平面 Z 轴正弦波浪层（True Non-Planar Z-Wave）**，并支持一键导出完美兼容 **Bambu Studio / OrcaSlicer / PrusaSlicer** 预览及上机打印的 `.gcode` 文件。

## 一、 仓库文件结构

| 文件名 | 类型 | 功能说明 |
| :--- | :--- | :--- |
| `ogcode_studio.html` | 单文件 Web 应用 | 零依赖（内联 Three.js 与 Tailwind CSS），双击即可在任意浏览器运行；含 5 步参数化设计管线、STL 切片器、**单色 3D 实体耗材线管渲染器**与 G-code/STL 导出器 |
| `ogcode_stl_slicer.py` | Python 命令行脚本 | 独立 CLI 批处理切片工具，支持读取本地 `.stl` 模型并嫁接参考 `.gcode` 头尾代码，批量生成非平面与外墙凸出纹理 `.gcode` |
| `README.md` | 项目文档 | 核心数学原理、Bambu Studio 防报错/防卡死机制说明、快速上手与 GitHub 部署指南 |

## 二、 核心功能与五步工作流

1. **Step 01 · 基础模型来源（手绘母线或导入本地 `.STL` 切片）**
   - **2D 交互式母线编辑器**：支持平滑 Catmull-Rom 样条与折线硬边切换，双击添加控制点、右键删除控制点，实时塑造回转体并支持 3～120 边形截面过渡。
   - **本地 `.STL` 极坐标射线切片**：支持直接拖入二进制或 ASCII `.stl` 文件，自动计算包围盒并在圆柱极坐标系 $(R, \theta, Z)$ 下提取外轮廓高度场 $R_{\text{STL}}(\theta, u)$。
2. **Step 02 · 宏观几何表面图案（Macro Patterns · 可导出为 STL）**
   - 内置 **立体正弦波浪棱（Waves）**、**向日葵黄金螺旋（Phyllotaxis）**、**钻石蜂窝浮雕（Diamond Cage）** 及局部区域吸引子包络（Live Attractor Mask）。
3. **Step 03 · 真非平面 Z 轴波浪 & 外墙凸出纹理走线（Toolpath & Non-Planar Modulation）**
   - **真·非平面三维波浪打印（True Non-Planar Z-Wave）**：在连续螺旋上升的同时施加 Z 轴正弦起伏 $Z(\theta, u) = u \cdot H + A_z \cdot \text{env}(u) \cdot \sin(N_z \theta + \phi_z)$，并内置**自适应体积挤出补偿（Adaptive Volumetric E）**（参考 [Reddit 非平面自适应挤出研究](https://www.reddit.com/r/3Dprinting/comments/1ov4hxw/nonplanar_3d_printing_with_custom_gcode_because/) 与 [FullControl GCode 框架](https://gist.github.com/fullcontrol-xyz)）与喷嘴最大爬坡角防撞监测。
   - **外墙表面凸出纹理（Protruding Stitches / Woven Mesh / Ripple）**：周期性沿外法向脉冲凸出 $\Delta r = 1.0 \sim 4.5\text{ mm}$，同时自动降低打印速度（如 $20\text{ mm/s}$）并提高局部挤出倍率（如 $135\%$），在模型表面挂出饱满的立体线圈、针脚或交错编织网孔（参考 [WaveOverhangs 悬垂走线控制](https://waveoverhangs.com/)）。
4. **Step 04 · 底部生成模式（Floor Configuration）**
   - 支持 **同心圆实心封底**（花瓶/容器）、**中心留孔同心圆底**（E27/E14 灯罩穿线孔）与 **无底直通模式**。
5. **Step 05 · Bambu Studio 深度兼容导出与模板嫁接**
   - 内置单色 3D 实体打印线管渲染（提供拓竹翠绿 `#00AE42`、哑光象牙白、陶土赤褐、丝绸琥珀金、太空金属灰 5 款耗材色板）。
   - 支持一键上传你在 Bambu Studio 导出的任意参考 `.gcode`，自动提取并嫁接你机器专属的调平、擦嘴与 AMS 换料起止代码（参考 [YouTube 非平面打印模板嫁接工作流](https://www.youtube.com/watch?v=-c6Ye7PMJ9s)）。

## 三、 Bambu Studio 导入预览防卡死与防报错机制说明

在将外部生成的自定义 `.gcode` 拖入 Bambu Studio 时，常见的三类错误已在 `ogcode_studio.html` 和 `ogcode_stl_slicer.py` 中全部解决：

1. **解决 `Suspiciously low number of configuration values extracted from ...: 15` 报错**：
   Bambu Studio（基于 [PrusaSlicer 配置解析器](https://github.com/prusa3d/PrusaSlicer/issues/7555) 与 [Bambu G-code 规范](https://github.com/rjduran/bambu-gcode-reference)）在通过“导入配置”方式打开 `.gcode` 时，强制要求 `; CONFIG_BLOCK_START` 块内至少包含 60 项以上的标准切片参数键值对。本工具现已在导出文件中完整写入 **247 项标准 Bambu Studio 配置字段**，彻底消除该报错弹窗。
2. **解决 `正在加载gcode数据: 50%` 假死**：
   根据 [FilaManBambuUsage 对 Bambu Studio 02.08 的解析文档](https://github.com/Niko11111/FilaManBambuUsage/blob/main/docs/03_Bambu_Data_Sources.md)，本工具在每旋转一圈（$2\pi$）处自动注入 `; CHANGE_LAYER`、`; layer num/total_layer_count: k/N`、`; Z_HEIGHT` 与 `; FEATURE: Outer wall`，避免几十万条线段堆积在同一层。
3. **解决 `正在加载线段: 20% (未响应)` 假死**：
   Bambu Studio 原生预览器（`GCodeViewer`）的右侧层高滑块要求同一层内 $Z$ 坐标恒定且层间单调递增（参考 [OrcaSlicer 非平面预览讨论](https://github.com/OrcaSlicer/OrcaSlicer/issues/5053)）。因此在 **Step 05** 中提供了两种导出模式：
   - **模式 A（Bambu Studio 秒开预览模式 · 默认开启）**：每圈使用恒定递增的层高 $Z$，100% 保留所有外墙凸出针脚、编织网格与波浪轮廓，拖入 Bambu Studio 仅需 **1 秒即可加载完成**。
   - **模式 B（SD 卡直印 · 真非平面 Z 轴上下波浪模式）**：输出每一步 `G1` 实时上下正弦起伏的 $Z$ 坐标，适合直接在 **OGcode Studio** 内置的 3D 实体视图中预览后拷入打印机 SD 卡打印（参考 [Claywoven 非平面打印指南](https://www.patreon.com/Claywoven/posts/3d-print-lattice-162929053)）。

## 四、 本地运行与 Python 命令行使用指南

### 1. 运行 Web 可视化工作室
直接下载 `ogcode_studio.html` 并在 Chrome / Edge / Safari 浏览器中双击打开即可使用（亦可直接托管在 GitHub Pages 上在线访问）。

### 2. 使用 Python 命令行切片脚本
```bash
# 使用默认参数生成带表面凸出针脚的 Bambu Studio 兼容 G-code
python3 ogcode_stl_slicer.py --output bambu_stitches.gcode

# 传入您自己的 STL 模型并嫁接您的 Bambu Studio 参考 G-code 头尾代码
python3 ogcode_stl_slicer.py \
  --input my_model.stl \
  --template-gcode my_bambu_reference.gcode \
  --texture stitches \
  --tex-depth 2.2 \
  --tex-count 28 \
  --output my_custom_wavy.gcode
```

## 五、 一键上传并开启 GitHub Pages 在线访问

在本地包含 `ogcode_studio.html`、`ogcode_stl_slicer.py` 和 `README.md` 的文件夹下打开终端，依次执行以下命令即可发布到你的 GitHub 仓库：

```bash
git init
git add ogcode_studio.html ogcode_stl_slicer.py README.md
git commit -m "feat: initial release of OGcode Studio non-planar & wavy wall slicer"
git branch -M main
git remote add origin https://github.com/<你的GitHub用户名>/<你的仓库名>.git
git push -u origin main
```
推送完成后，在 GitHub 仓库的 **Settings → Pages** 中将 Branch 设为 `main`，即可直接通过浏览器在线使用你的专属 **OGcode Studio**（若将 `ogcode_studio.html` 复制或重命名为 `index.html`，访问根域名即可直接打开）。
