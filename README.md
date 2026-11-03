# 校园跑 4km 路线规划器（桂林航天工业学院 · 南校区）

从 App 的「打卡点地图截图」自动识别 5 个打卡点，用 **2 个已知地标** 校准坐标，
在高德底图路网上规划一条 **4.00 km**、按 **1→2→3→4→5** 顺序打卡、终点回到起点附近的路线，
导出与原样例完全一致的 `.route.json`（BD-09 / WGS-84 双坐标）。

> ⚠️ 关键约束（App 路线模拟）：
> - **总点数 70~200**（默认 150）。点数过多会拉低步频（<90 步/分），建议 100~180。
> - **5 个打卡点必须是折线节点（0 m 误差）**，否则系统识别不到；本工具已强制把打卡点写入路线点。
> - 起点可由 `--start` 指定（App 上的「起」标记位置），路线首点即该点。
> - **打卡点锚点（默认 dy=40px）**：App 的打卡点真实位置在「数字圆圈下方的小尾巴尖」，
>   识别时会把锚点从圆圈中心下移 `marker_anchor.dy`（1080 宽截图坐标，可调 20~60），
>   命令行可用 `--anchor-dy 50` 覆盖。这样路线才是真正穿过打卡点，而不是从它上方擦过。
> - **绕点圈数（默认 1）**：在每个打卡点处绕半径约 12 m 的小圈，让模拟定位在打卡半径内
>   反复经过（20 m 内通常有 4~8 个路线点），避免"只路过一次"导致识别不到。
>   可在界面「绕点圈数」下拉 / 网页版下拉 / 命令行 `--passes N --pass-radius R` 调整（0=不绕）。
> - **里程选择（自动 / 手动）**：先点「计算可选路程」，工具会算出本校区**可精确规划的公里数**
>   （0.5 km 步进，如 2/2.5/3/3.5/4/4.5/5 km），默认自动取 4.0 km；也可在下拉框里选择其他公里数
>   （命令行 `--list-km` 查看列表、`--km 3.5` 指定）。

一套 Python 核心，同时提供：

| 平台 | 形式 | 入口 |
|---|---|---|
| Windows | 桌面 GUI（tkinter） | `python windows/app.py` / 双击 `run_windows.bat` |
| 安卓 | Termux 本地网页（手机浏览器操作） | `python android/server.py` → 浏览器开 `http://127.0.0.1:8765` |
| 任意 | 命令行 | `python planner_cli.py -s 截图.jpg -l "图书馆:223,997" -l "体育场:704,1912"` |

不依赖任何在线服务/大模型，**每规划一条路线 0 token**。

## 安装

```bash
pip install pillow numpy          # Windows
# 安卓 Termux:
pkg install python numpy && pip install pillow
```

## 使用（Windows GUI）

1. 打开 App 打卡点界面截图（5 个数字点位都要在画面内，避免被气泡/按钮遮挡）。
2. 「① 打开截图」。
3. 在左侧地标列表里选一个地标 → 「开始点选」→ 在右侧图上点它的图标位置；
   重复点 **至少 2 个（建议 3 个）** 地标。
4. 「③ 识别打卡点」→ 应识别出 1~5 号。
   - 若 1 号点显示成 ✓ 或被「起」徽标遮挡，用「手动补点」在图上点该点位。
5. 「计算可选路程」→ 下拉框列出可规划公里数（默认自动 4.0 km）；
   「④ 生成路线」→ 约 5~10 秒，输出到 `out/`：
   - `route_YYYYmmdd_HHMMSS.route.json`：路线文件（与样例格式一致）
   - `route_YYYYmmdd_HHMMSS_preview.png`：路线预览图

## 使用（安卓 Termux 网页版）

```bash
cd campus_route_tool
python android/server.py
```

手机浏览器打开 `http://127.0.0.1:8765`：
上传截图 → 选地标并点击（至少 2 个）→「识别打卡点」→「生成 4km 路线」→
页面显示预览图与「下载 .route.json」。

> 说明：安卓端交付的是 Termux/Pydroid 可运行的脚本 + 网页界面（无需 Android SDK；
> 本环境也无法签名打包 APK）。若要独立 APK，可把 `core/` + 一个 WebView 壳或 Kivy 工程自行打包。

## 手机移植（安卓）

- **最快：Termux 网页版**（已可用）
  ```bash
  # 手机 Termux 里，工具目录下：
  bash android/install_termux.sh
  # 手机浏览器打开 http://127.0.0.1:8765
  ```
- **真 APK：Kivy + Buildozer**（`mobile/` 目录）
  - `mobile/main.py`：手机 App 界面（截图/点地标/识别/补点/选里程/绕点圈数/生成 JSON）
  - `mobile/buildozer.spec`：打包配置
  - `bash mobile/build_apk.sh` → `mobile/.build/bin/*.apk`（需 Linux/WSL + JDK17）
  - 也可用 `mobile/build-apk.yml`（复制到 `.github/workflows/`）让 GitHub Actions 免费构建
  - 详见 `mobile/README.md`
- 手机版与电脑版共用 `core/`、`assets/`、`campus_profile.json`，路线结果完全一致。
- 本机没有 Android SDK/JDK，无法直接产出签名 APK；工程与构建脚本已备好。

## 命令行用法

```bash
# 看可用地标
python planner_cli.py --list-landmarks

# 只识别打卡点
python planner_cli.py -s shot.jpg --detect-only

# 完整规划（地标名:像素x,像素y）
# --points 默认 150（限制 70~200）；--start 可指定起点（App「起」的位置，经纬度或地标名）
# -m 手动补点：当点位被气泡/「起」徽标遮挡、或显示成对勾时使用（序号:像素x,像素y）
python planner_cli.py -s shot.jpg \
  -l "图书馆:223,997" -l "体育场:704,1912" -l "力源生鲜厨房:233,1455" \
  -m "1:420,1113" -m "5:262,1206" \
  --start "110.37586,25.278663" --points 150 --out out

# 指定公里数（不填 --km 就是自动）
python planner_cli.py -s shot.jpg -l ... --km 3.5 --out out
```

输出 JSON 摘要（`route_json` / `preview` / `length_m` / 各点经纬度）。

## 底图选择：高德矢量图 / 高德卫星图

- **规划永远走高德矢量路网**（工具从高德 z18 矢量瓦片提取白色路网，路网干净、连通性好），
  这样路线才能精确贴合道路。
- **出图/预览可以切换底图**：
  - 矢量底图（默认）：`assets/basemap.png`（高德标准地图）
  - 卫星底图：`assets/satellite.png`（高德卫星影像 style=6，**与矢量瓦片同一坐标体系 GCJ-02**，
    所以我们不需要做任何坐标转换，路线可以直接叠在卫星图上）
  - 用法：命令行加 `--sat`；Windows 界面勾选「预览用卫星底图」；
    安卓网页版在「底图」下拉里选「卫星」。
- 为什么不用卫星图做自动规划：卫星影像上没有干净的道路矢量线，自动提路/连通性很差，
  容易把路线画进绿化带/建筑；矢量图能保证路线沿路。卫星图主要用于**验收/展示**。
- 无论用哪种底图，导出的 `.route.json` 坐标完全相同（WGS-84 + BD-09），
  App 里显示仍然是用高德地图渲染。

## 目录结构

```
campus_route_tool/
├─ campus_profile.json     # 校区档案：底图坐标、地标 GCJ、路网栅格、环路节点、目标里程
├─ assets/
│  ├─ roadmask.png         # 校园路网掩膜（1408×1408，1 像素≈1.08m）
│  ├─ basemap.png          # 矢量预览底图（高德瓦片拼接）
│  ├─ satellite.png        # 卫星预览底图（高德卫星影像拼接，同一坐标系）
│  └─ digit_1..5.png       # 打卡点数字模板（白色数字）
├─ core/
│  ├─ geo.py               # GCJ / WGS / BD-09 互转、瓦片坐标、相似变换
│  ├─ profile.py           # 校区档案、路网最短路（Dijkstra）
│  ├─ detect.py            # 截图识别 5 个打卡点（颜色+圆圈+数字模板）
│  ├─ calib.py             # 2 点地标配准（最小二乘相似变换）
│  ├─ planner.py           # 顺序打卡 + 环路补里程 + 精确到 4000m
│  └─ routejson.py         # 导出 .route.json / 预览图
├─ windows/app.py          # Windows GUI
├─ android/server.py       # 安卓网页版（内置 HTTP 服务 + 页面）
├─ planner_cli.py          # 命令行
├─ android/                # 安卓 Termux 网页版 + install_termux.sh
├─ mobile/                 # 安卓 APK 版（Kivy 工程 + Buildozer + GitHub Actions）
├─ run_windows.bat / run_termux.sh
└─ out/                    # 输出目录
```

## 原理

1. **识别打卡点**：App 用灰/绿圆圈 + 白色数字渲染点位。按颜色聚类出圆 → 取圆内白色数字 →
   归一化后与数字模板做相关匹配（支持不同分辨率，实测 0.6x~1.5x 缩放均可 100% 识别）。
2. **半自动配准**：用户点 2 个已知 POI（如「图书馆」「体育场」），拟合
   `截图像素 → 高德底图像素` 的相似变换（旋转/缩放/平移），于是任意截图像素都能换算成 GCJ 坐标。
   点 3 个地标会给出残差校验（一般 2~5m）。
3. **路网规划**：打卡点吸附到校园路网（高德 z18 瓦片提取的白色路网掩膜），
   Dijkstra 求 起点→1→2→3→4→5 最短路径；再在预设校园环路上取前缀补足到 4km；
   最后用终点尾段做连续微调，使 WGS 里程精确等于 4000m（误差 <1m）。
4. **稀疏化**：按目标点数（默认 150，范围 70~200）等距抽稀，
   并把 5 个打卡点强制保留为折线节点（0 m 误差）；同时在每个打卡点处绕一个半径约 12 m 的
   小圈（默认 1 圈），让模拟定位在打卡半径内反复经过，保证 App 能识别。
4. **坐标输出**：GCJ→WGS→BD-09，按样例结构写入 `bdLongitude/bdLatitude/wgsLongitude/wgsLatitude/altitude`。

## 精度与限制

- 打卡点位置来自截图 + 2 点地标配准，与真实点位误差约 **10~20 m**（校园跑打卡半径通常 ≥50m，足够）。
- 里程按 WGS 逐点累加校准为 **4000.0 m**；BD-09 口径会短约 0.1%（属 BD-09 转换的正常现象，与样例一致）。
- 截图需完整显示 5 个数字点位（被提示气泡/GO 按钮遮挡会识别不全，工具会提示重截）。
- 目前只内置 **桂林航天工业学院（南校区）** 档案（用户指定）。换校区需要重新生成
  `campus_profile.json`（底图范围、路网掩膜、地标 GCJ、环路节点），流程可参考本项目 `core/`。
- App 地图旋转/缩放任意（配准会算进去）；手机分辨率不同也能识别（数字模板归一化）。

## Token 成本参考

- 本工具开发（在“手工打通一次”之后参数化）：约 **10~20 万 tokens**（本次会话完成）。
- 若从零开始（不含人工探索/校准）：全自动版 **20~36 万**，半自动版 **8~15 万**。
- 工具运行时：**0 token/条**（纯本地算法）。
- 若改用大模型每次直接看截图：约 3~8k tokens/条且不稳定，不建议。
