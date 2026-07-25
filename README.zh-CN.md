# MISSAV M3U8 视频下载器

基于 Python + CustomTkinter 构建的 M3U8 视频下载桌面工具，支持 AES-128 解密、多线程并发下载、断点续传、分辨率选择等功能。

## 功能特性

- **M3U8 解析** — 支持 Master Playlist（多码率）和 Media Playlist，自动识别并解析 TS 分片列表
- **多线程下载** — 基于连接池的并发下载引擎，默认 20 线程，可自定义 1-100
- **AES-128 解密** — 自动检测加密分片并解密，支持 IV 向量和密钥缓存
- **断点续传** — 任务停止后可从已下载分片处继续，支持暂停/恢复
- **分辨率选择** — 每个任务独立选择分辨率，支持最高分辨率自动选择
- **实时进度** — 进度条、分片计数、下载速度实时显示
- **代理支持** — HTTP/HTTPS 代理配置
- **自定义请求头** — 支持 Referer 等自定义 Header
- **配置自动保存** — 线程数、代理、Referer 等设置自动持久化
- **日志系统** — 所有操作记录到 `Logs/` 文件夹，带时间戳文件名
- **PyInstaller 打包** — 支持打包为独立 exe 文件，无需安装 Python
- **文件名安全** — 自动清理 Windows 非法字符，防止文件名导致的错误

## 项目结构

```
missav_m3u8_gui/
├── app.py              # 主程序（CustomTkinter GUI）
├── main.py             # CLI 命令行入口
├── m3u8_parser.py      # M3U8 播放列表解析模块
├── downloader.py       # 多线程下载引擎
├── decryptor.py        # AES-128 解密模块
├── merger.py           # TS 分片合并模块
├── requirements.txt    # Python 依赖
├── start.bat           # Windows 一键启动脚本
├── config.json         # 用户配置（自动生成）
├── Logs/               # 日志目录（自动生成）
└── Downloads/          # 默认下载目录（自动生成）
```

## 安装与运行

### 环境要求

- Python 3.8+
- Windows（推荐）/ macOS / Linux

### 方式一：直接运行

```bash
# 安装依赖
pip install -r requirements.txt

# 启动 GUI
python app.py
```

### 方式二：使用 start.bat（Windows）

双击 `start.bat`，自动激活 conda 环境并安装依赖后启动。

### 方式三：打包为 exe

```bash
# 安装 PyInstaller
pip install pyinstaller

# 打包
pyinstaller --onefile --windowed --name "missav_m3u8_GUI" --clean app.py
```

生成的 exe 位于 `dist/` 目录下。

## 使用方法

### GUI 模式

1. 启动 `python app.py`
2. 在左侧填写：
   - **M3U8 链接地址**：完整的 m3u8 URL
   - **Referer 来源页**：防盗链页面地址（可选）
   - **保存文件名**：输出文件名（默认 output.ts）
   - **保存目录**：点击"选择"按钮浏览目录，点击"打开"可在文件管理器中打开目录
   - **代理地址**：如 `http://127.0.0.1:7890`（可选）
   - **线程数**：并发下载线程数（默认 20）
3. 点击"开始下载"
4. 右侧任务列表中可：
   - 通过下拉菜单切换分辨率
   - 暂停/继续/停止/删除任务

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
# 这是注释行
```

### CLI 参数

| 参数 | 说明 |
|------|------|
| `url` | m3u8 文件 URL |
| `-f, --file` | 批量下载文件 |
| `-o, --output` | 输出文件名 |
| `-d, --dir` | 输出目录（默认桌面） |
| `-w, --workers` | 并发数（默认 20） |
| `-p, --proxy` | 代理地址 |
| `-k, --keep` | 保留临时 TS 分片 |
| `-s, --stream` | 选择码率流索引 |
| `-v, --verbose` | 详细日志 |
| `--headers` | 自定义请求头 `Key=Value` |

## 技术细节

### 下载流程

1. 获取并解析 M3U8 播放列表
2. 如果是 Master Playlist，自动选择最高码率（或手动选择）
3. 并发下载所有 TS 分片（带连接池优化）
4. 检测 AES-128 加密并自动解密
5. 按顺序合并所有分片为完整 TS 文件
6. 清理临时文件

### 错误处理

- SSL 错误自动重试（最多 5 次，指数退避）
- 连接超时自动重试
- 文件名自动清理 Windows 非法字符
- 任务状态持久化，程序重启后可恢复

### 配置文件

`config.json` 自动保存以下设置：

```json
{
  "workers": 20,
  "proxy": "",
  "headers": "https://example.com/"
}
```

## 日志

所有操作记录在 `Logs/` 目录下，文件名格式：

```
2025-01-15_14-30-25-123.log
```

## 致谢

本项目的 M3U8 解析、多线程下载、AES-128 解密、TS 合并等核心模块基于 [sdlw7757/M3U8-down](https://github.com/sdlw7757/M3U8-down) 项目开发。原项目采用 Flask + WebSocket 的网页界面，本项目在此基础上将 GUI 层重写为 CustomTkinter 桌面应用，并新增了 PyInstaller 打包、分辨率选择、配置自动保存等功能。

## 许可证

MIT License
