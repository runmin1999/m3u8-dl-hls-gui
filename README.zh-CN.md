# m3u8-dl-hls-gui

[English](README.md) | [中文](README.zh-CN.md)

> 基于 Python + CustomTkinter 构建的高性能 M3U8/HLS 及 MP4 视频下载桌面工具。

## 亮点

- M3U8 与 MP4 直链下载，自动识别格式
- 多线程并发下载 + 连接池优化
- AES-128 透明解密
- 暂停 / 继续 / 停止，支持快速响应的暂停、继续和停止操作
- 每任务独立分辨率选择
- 一键 PyInstaller 打包为 exe

## 功能特性

| 功能 | 说明 |
|------|------|
| **M3U8 解析** | 支持 Master Playlist（多码率）和 Media Playlist，自动拼接相对 URL |
| **MP4 直链下载** | 基于 curl 的直接下载，支持自动重试和断点续传 |
| **自动格式识别** | 自动识别 M3U8/MP4 链接 |
| **多线程下载** | 基于连接池的并发引擎，1-100 线程可调（默认 20） |
| **AES-128 解密** | 自动检测 `#EXT-X-KEY` 并解密，支持 IV 向量和密钥缓存 |
| **断点续传** | M3U8：原子写入 + 分片索引记录；MP4：HTTP Range 头续传 |
| **分辨率选择** | 每任务独立下拉菜单，默认自动选择最高码率 |
| **实时进度** | 进度条、分片计数、实时下载速度 |
| **快速控制** | 支持快速响应的暂停、继续和停止操作；删除后台清理不阻塞 UI |
| **剪贴板自动识别** | URL 输入框为空时，自动从剪贴板获取 M3U8/MP4 链接 |
| **智能粘贴** | Ctrl+V 自动识别 M3U8/MP4 链接并填入 URL 框 |
| **本地 M3U8 支持** | Ctrl+V 粘贴本地 .m3u8 文件路径，直接下载分片 |
| **右键菜单** | 复制链接、打开下载目录、删除任务 |
| **代理支持** | HTTP / HTTPS / SOCKS 代理，覆盖所有网络请求 |
| **自定义请求头** | Referer 及任意自定义 Header，应对防盗链 |
| **配置自动保存** | 线程数、代理、Referer 自动持久化到 `config.json` |
| **日志系统** | 带时间戳的日志文件，记录在 `Logs/` 目录 |
| **PyInstaller 打包** | 可打包为独立 `.exe`，无需安装 Python |
| **文件名安全** | 自动清理 Windows 非法字符，防止文件错误 |

## 截图

> *即将添加*

## 快速开始

### 环境要求

- Python 3.8+
- Windows（推荐）/ macOS / Linux

### 方式一：直接运行

```bash
pip install -r requirements.txt
python app.py
```

### 方式二：Windows 一键启动

双击 `start.bat`，自动激活 conda 环境、安装依赖并启动。

### 方式三：打包为 exe

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "m3u8-dl-hls-gui" --clean app.py
```

输出：`dist/m3u8-dl-hls-gui.exe`

## 使用方法

### GUI 模式

1. 启动 `python app.py`
2. 左侧面板填写：
   - **视频链接地址** — M3U8 或 MP4 链接
   - **Referer 来源页** — 防盗链页面地址（可选）
   - **保存文件名** — 输出文件名（默认 `output.mp4`）
   - **保存目录** — 点击"选择"浏览目录，点击"打开"在文件管理器中打开
   - **代理地址** — 如 `http://127.0.0.1:7890`（可选）
   - **线程数** — 并发下载线程数（默认 20）
3. 点击 **开始下载**
4. 右侧任务列表 — 切换分辨率、暂停 / 继续 / 停止 / 删除任务
5. **剪贴板**：复制 M3U8/MP4 链接后，URL 输入框为空时自动填入
6. **Ctrl+V**：智能粘贴，自动识别 M3U8/MP4 链接
7. **本地文件**：Ctrl+V 粘贴本地 `.m3u8` 文件路径，直接下载

### CLI 模式

```bash
# 单个下载
python main.py https://example.com/video.m3u8
python main.py https://example.com/video.m3u8 -o movie.mp4 -d D:/Videos

# 批量下载
python main.py -f urls.txt -d D:/Downloads
```

批量下载文件格式（`urls.txt`）：

```
https://example.com/video1.m3u8 电影1.mp4
https://example.com/video2.m3u8 电影2.mp4
# 以 # 开头的是注释行
```

### CLI 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `url` | M3U8/MP4 文件 URL | — |
| `-f, --file` | 批量下载文件 | — |
| `-o, --output` | 输出文件名 | `output.mp4` |
| `-d, --dir` | 输出目录 | 桌面 |
| `-w, --workers` | 并发数 | `20` |
| `-p, --proxy` | 代理地址 | — |
| `-k, --keep` | 保留临时 TS 分片 | `false` |
| `-s, --stream` | 选择码率流索引 | 自动（最高） |
| `-v, --verbose` | 详细日志 | `false` |
| `--headers` | 自定义请求头 `Key=Value` | — |

## 技术架构

```
                    app.py (GUI 主程序)
                   ╱    │    ╲
                  ╱     │     ╲
                 ╱      │      ╲
    utils.py  m3u8_parser.py  downloader.py  decryptor.py  merger.py
         │         │              │              │            │
         └─────────┴──────────────┴──────────────┴────────────┘
                              │
                         main.py (CLI 入口)
```

详细流程图和技术原理请参阅 [docs/ARCHITECTURE.zh-CN.md](docs/ARCHITECTURE.zh-CN.md)（中文）| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)（English）。

## 项目结构

```
m3u8-dl-hls-gui/
├── app.py              # 主程序（CustomTkinter GUI）
├── utils.py            # 工具函数（网络请求、配置、任务持久化）
├── main.py             # CLI 命令行入口
├── m3u8_parser.py      # M3U8 播放列表解析模块
├── downloader.py       # 多线程下载引擎
├── downloader_m3u8.py  # M3U8 下载编排模块
├── downloader_mp4.py   # MP4 直链下载模块
├── task_runner.py      # 统一任务生命周期管理
├── decryptor.py        # AES-128 解密模块
├── merger.py           # TS 分片合并模块
├── requirements.txt    # Python 依赖
├── start.bat           # Windows 一键启动脚本
├── docs/
│   ├── ARCHITECTURE.md     # 技术架构文档（英文）
│   └── ARCHITECTURE.zh-CN.md # 技术架构文档（中文）
├── config.json         # 用户配置（自动生成）
├── Logs/               # 日志目录（自动生成）
└── Downloads/          # 默认下载目录（自动生成）
```

## 配置文件

`config.json`（自动生成）：

```json
{
  "workers": 20,
  "proxy": "",
  "headers": "https://example.com/",
  "ffmpeg_concurrency": 2
}
```

## 更新日志

### v0.23

- 🐛 修复 AES-128 隐式 IV 的分片序号计算错误
- 🐛 修复解密失败后仍继续合并的问题
- 🐛 修复分片缺失时仍可能生成完成文件的问题
- 🐛 修复 EXT-X-BYTERANGE 隐式偏移和 Range 校验
- 🐛 修复 MP4 无 Content-Length 时被错误判定为失败
- 🔧 移除对单个 MP4 URL 无效的 curl parallel 参数
- 🔧 改进 curl 跨平台查找
- 🔧 配置、任务记录和解密结果改为原子写入
- 🔧 改进重试等待期间的停止响应

### v0.20

- 🐛 **修复 MP4 误判** — 路径中含 `.mp4` 的 M3U8 链接不再被误判为 MP4 直接下载
- 🐛 **修复右键菜单** — 点击其他区域或切换窗口时菜单自动关闭
- ✨ **支持本地 M3U8** — Ctrl+V 粘贴本地 `.m3u8` 文件路径，直接下载分片
- 🐛 **修复 FFmpeg 编码** — 修复中文 Windows 下 GBK/UTF-8 编码冲突导致的崩溃

### v0.19

- 🔧 **架构重构** — 下载逻辑从 GUI 中分离，代码更清晰、更易维护
- 🔧 **统一任务执行层** — MP4 和 M3U8 下载共用统一的任务生命周期管理
- 🔧 **FFmpeg 检测增强** — 支持常见安装路径，启动时自动检测
- 🔧 **版本统一** — 修复版本号不一致问题

## 致谢

核心模块（M3U8 解析、多线程下载、AES-128 解密、TS 合并）基于 [sdlw7757/M3U8-down](https://github.com/sdlw7757/M3U8-down) 开发。原项目采用 Flask + WebSocket 网页界面，本项目将 GUI 层重写为 CustomTkinter 桌面应用，并新增 MP4 下载、PyInstaller 打包、分辨率选择、剪贴板自动识别、配置自动保存等功能。

## 许可证

MIT License
