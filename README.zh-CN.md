# M3U8-DL-HLS-GUI

[English](README.md) | [中文](README.zh-CN.md)

> 基于 Python + CustomTkinter 构建的高性能 M3U8/HLS 视频下载桌面工具。

## 亮点

- 多线程并发下载 + 连接池优化
- AES-128 透明解密
- 暂停 / 继续 / 停止，完整状态持久化
- 每任务独立分辨率选择
- 一键 PyInstaller 打包为 exe

## 功能特性

| 功能 | 说明 |
|------|------|
| **M3U8 解析** | 支持 Master Playlist（多码率）和 Media Playlist，自动拼接相对 URL |
| **多线程下载** | 基于连接池的并发引擎，1-100 线程可调（默认 20） |
| **AES-128 解密** | 自动检测 `#EXT-X-KEY` 并解密，支持 IV 向量和密钥缓存 |
| **断点续传** | 原子写入确保文件完整，记录已下载分片索引 |
| **分辨率选择** | 每任务独立下拉菜单，默认自动选择最高码率 |
| **实时进度** | 进度条、分片计数、实时下载速度 |
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
   - **M3U8 链接地址** — 完整的 m3u8 URL
   - **Referer 来源页** — 防盗链页面地址（可选）
   - **保存文件名** — 输出文件名（默认 `output.ts`）
   - **保存目录** — 点击"选择"浏览目录，点击"打开"在文件管理器中打开
   - **代理地址** — 如 `http://127.0.0.1:7890`（可选）
   - **线程数** — 并发下载线程数（默认 20）
3. 点击 **开始下载**
4. 右侧任务列表 — 切换分辨率、暂停 / 继续 / 停止 / 删除任务

### CLI 模式

```bash
# 单个下载
python main.py https://example.com/video.m3u8
python main.py https://example.com/video.m3u8 -o movie.ts -d D:/Videos

# 批量下载
python main.py -f urls.txt -d D:/Downloads
```

批量下载文件格式（`urls.txt`）：

```
https://example.com/video1.m3u8 电影1.ts
https://example.com/video2.m3u8 电影2.ts
# 以 # 开头的是注释行
```

### CLI 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `url` | m3u8 文件 URL | — |
| `-f, --file` | 批量下载文件 | — |
| `-o, --output` | 输出文件名 | `output.ts` |
| `-d, --dir` | 输出目录 | 桌面 |
| `-w, --workers` | 并发数 | `20` |
| `-p, --proxy` | 代理地址 | — |
| `-k, --keep` | 保留临时 TS 分片 | `false` |
| `-s, --stream` | 选择码率流索引 | 自动（最高） |
| `-v, --verbose` | 详细日志 | `false` |
| `--headers` | 自定义请求头 `Key=Value` | — |

## 技术架构

```
app.py (GUI)          main.py (CLI)
    │                      │
    ├── m3u8_parser.py     ├── m3u8_parser.py
    ├── downloader.py      ├── downloader.py
    ├── decryptor.py       ├── decryptor.py
    └── merger.py          └── merger.py
```

详细流程图和技术原理请参阅 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 配置文件

`config.json`（自动生成）：

```json
{
  "workers": 20,
  "proxy": "",
  "headers": "https://example.com/"
}
```

## 项目结构

```
m3u8-dl-hls-gui/
├── app.py              # 主程序（CustomTkinter GUI）
├── main.py             # CLI 命令行入口
├── m3u8_parser.py      # M3U8 播放列表解析模块
├── downloader.py       # 多线程下载引擎
├── decryptor.py        # AES-128 解密模块
├── merger.py           # TS 分片合并模块
├── requirements.txt    # Python 依赖
├── start.bat           # Windows 一键启动脚本
├── docs/
│   └── ARCHITECTURE.md # 技术架构文档
├── config.json         # 用户配置（自动生成）
├── Logs/               # 日志目录（自动生成）
└── Downloads/          # 默认下载目录（自动生成）
```

## 致谢

核心模块（M3U8 解析、多线程下载、AES-128 解密、TS 合并）基于 [sdlw7757/M3U8-down](https://github.com/sdlw7757/M3U8-down) 开发。原项目采用 Flask + WebSocket 网页界面，本项目将 GUI 层重写为 CustomTkinter 桌面应用，并新增 PyInstaller 打包、分辨率选择、配置自动保存等功能。

## 许可证

MIT License
