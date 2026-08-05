# m3u8-dl-hls-gui

[English](README.md) | [中文](README.zh-CN.md)

> A high-performance desktop M3U8/HLS & MP4 video downloader with a modern GUI, built with Python and CustomTkinter.

## Highlights

- M3U8 & MP4 direct download with auto-detection
- Multi-threaded concurrent downloads with connection pool optimization
- AES-128 transparent decryption
- Pause / Resume / Stop with fast response (<0.5s)
- Per-task resolution selection
- One-click exe packaging via PyInstaller

## Features

| Feature | Description |
|---------|-------------|
| **M3U8 Parsing** | Supports Master Playlist (multi-bitrate) and Media Playlist; auto-resolves relative URLs |
| **MP4 Direct Download** | curl.exe parallel multi-connection download with auto-retry and resume |
| **Auto Format Detection** | Automatically detects M3U8/MP4 URLs |
| **Multi-threaded Download** | Connection-pool-based engine, 1-100 configurable workers (default 20) |
| **AES-128 Decryption** | Auto-detects `#EXT-X-KEY`, decrypts with IV support and key caching |
| **Resume Support** | M3U8: atomic writes + segment index tracking; MP4: HTTP Range headers |
| **Resolution Selection** | Per-task dropdown; auto-selects highest bitrate by default |
| **Real-time Progress** | Progress bar, segment counter, and live download speed |
| **Fast Controls** | Pause/Stop response <0.5s; Delete with background cleanup |
| **Clipboard Auto-detect** | Auto-fills URL from clipboard when URL field is empty |
| **Smart Paste** | Ctrl+V detects M3U8/MP4 links and fills URL field automatically |
| **Local M3U8 Support** | Paste local .m3u8 file path via Ctrl+V to download directly |
| **Right-click Menu** | Copy link, open download directory, delete task |
| **Proxy Support** | HTTP / HTTPS / SOCKS proxy for all network requests |
| **Custom Headers** | Referer and arbitrary headers for anti-hotlinking |
| **Auto-save Settings** | Workers, proxy, and Referer persisted to `config.json` |
| **Logging** | Timestamped logs in `Logs/` directory |
| **PyInstaller** | Package as standalone `.exe`, no Python needed |
| **Filename Sanitization** | Auto-removes Windows-illegal characters |

## Screenshots

> *Coming soon*

## Quick Start

### Prerequisites

- Python 3.8+
- Windows (recommended) / macOS / Linux

### 1. Run from Source

```bash
pip install -r requirements.txt
python app.py
```

### 2. Windows One-Click

Double-click `start.bat` — auto-activates conda, installs deps, and launches.

### 3. Package as exe

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "m3u8-dl-hls-gui" --clean app.py
```

Output: `dist/m3u8-dl-hls-gui.exe`

## Usage

### GUI

1. Launch `python app.py`
2. Fill in the left panel:
   - **Video URL** — M3U8 or MP4 link
   - **Referer** — anti-hotlinking origin page (optional)
   - **Filename** — output name (default: `output.mp4`)
   - **Save Directory** — click Browse to pick, click Open to reveal in explorer
   - **Proxy** — e.g. `http://127.0.0.1:7890` (optional)
   - **Workers** — concurrency (default: 20)
3. Click **Start Download**
4. Right panel — switch resolution, pause / resume / stop / delete tasks
5. **Clipboard**: Copy an M3U8/MP4 link and it auto-fills when URL field is empty
6. **Ctrl+V**: Smart paste detects M3U8/MP4 links automatically
7. **Local Files**: Ctrl+V a local `.m3u8` file path to download directly

### CLI

```bash
# Single download
python main.py https://example.com/video.m3u8
python main.py https://example.com/video.m3u8 -o movie.mp4 -d D:/Videos

# Batch download
python main.py -f urls.txt -d D:/Downloads
```

Batch file format (`urls.txt`):

```
https://example.com/video1.m3u8 Movie1.mp4
https://example.com/video2.m3u8 Movie2.mp4
# comment lines start with #
```

### CLI Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `url` | M3U8/MP4 URL | — |
| `-f, --file` | Batch download file | — |
| `-o, --output` | Output filename | `output.mp4` |
| `-d, --dir` | Output directory | Desktop |
| `-w, --workers` | Concurrency | `20` |
| `-p, --proxy` | Proxy address | — |
| `-k, --keep` | Keep temp TS segments | `false` |
| `-s, --stream` | Bitrate stream index | auto (highest) |
| `-v, --verbose` | Verbose logging | `false` |
| `--headers` | Custom headers `Key=Value` | — |

## Architecture

```
                    app.py (GUI main)
                   ╱    │    ╲
                  ╱     │     ╲
                 ╱      │      ╲
    utils.py  m3u8_parser.py  downloader.py  decryptor.py  merger.py
         │         │              │              │            │
         └─────────┴──────────────┴──────────────┴────────────┘
                              │
                         main.py (CLI entry)
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (English) | [docs/ARCHITECTURE.zh-CN.md](docs/ARCHITECTURE.zh-CN.md) (中文) for detailed flow diagrams and technical deep-dive.

## Project Structure

```
m3u8-dl-hls-gui/
├── app.py              # Main application (CustomTkinter GUI)
├── utils.py            # Utility functions (fetch, config, tasks)
├── main.py             # CLI entry point
├── m3u8_parser.py      # M3U8 playlist parser
├── downloader.py       # Multi-threaded download engine
├── downloader_m3u8.py  # M3U8 download orchestration
├── downloader_mp4.py   # MP4 direct download
├── task_runner.py      # Unified task lifecycle manager
├── decryptor.py        # AES-128 decryption module
├── merger.py           # TS segment merger
├── requirements.txt    # Python dependencies
├── start.bat           # Windows one-click launcher
├── docs/
│   ├── ARCHITECTURE.md     # Technical architecture docs (EN)
│   └── ARCHITECTURE.zh-CN.md # Technical architecture docs (ZH)
├── config.json         # User config (auto-generated)
├── Logs/               # Log directory (auto-generated)
└── Downloads/          # Default download directory (auto-generated)
```

## Configuration

`config.json` (auto-generated):

```json
{
  "workers": 20,
  "proxy": "",
  "headers": "https://example.com/",
  "ffmpeg_concurrency": 2,
  "parallel_max": 8
}
```

## Changelog

### v0.22

- ✨ **MP4 download engine upgrade** — Switched from Python requests multi-threaded to `curl.exe --parallel` for faster, more stable downloads
- ✨ **Auto-retry resume** — Automatically retries up to 10 times on failure with HTTP Range resume support
- ✨ **Parallel max config** — Added hidden `parallel_max` config option (default 8, range 1-32) to control curl parallel connections
- 🔧 **FFmpeg concurrency config** — Added hidden `ffmpeg_concurrency` config option for concurrent merge processes
- 🐛 **Fix right-click menu** — Menu buttons now work correctly; menu dismisses when clicking outside the app
- 🔧 **Download speed optimization** — Added `--parallel-immediate` for faster parallel transfer startup

### v0.20

- 🐛 **Fix MP4 detection** — URLs with `.mp4` in path (e.g. `.../video.mp4/master.m3u8`) no longer misidentified as MP4 download
- 🐛 **Fix context menu** — Right-click menu now closes on focus loss and clicking outside
- ✨ **Local M3U8 support** — Ctrl+V a local `.m3u8` file path to download its segments directly
- 🐛 **Fix FFmpeg encoding** — Fixed `UnicodeDecodeError` on Chinese Windows (GBK vs UTF-8)

### v0.19

- 🔧 **Architecture refactor** — Download logic separated from GUI into dedicated modules (`downloader_mp4.py`, `downloader_m3u8.py`)
- 🔧 **Unified task execution** — MP4 and M3U8 downloads share a common task lifecycle manager (`task_runner.py`)
- 🔧 **Enhanced FFmpeg detection** — Checks common install paths on Windows, not just PATH
- 🔧 **Version consistency** — Fixed version string mismatch across the codebase

## Acknowledgments

Core modules (M3U8 parsing, multi-threaded downloading, AES-128 decryption, TS merging) are based on [sdlw7757/M3U8-down](https://github.com/sdlw7757/M3U8-down). The original project uses a Flask + WebSocket web interface. This project rewrites the GUI as a CustomTkinter desktop application and adds MP4 download, PyInstaller packaging, resolution selection, clipboard auto-detect, and auto-save settings.

## License

MIT License
