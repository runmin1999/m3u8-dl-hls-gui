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
| **MP4 Direct Download** | Multi-threaded Range download with resume support |
| **Auto Format Detection** | Automatically detects M3U8/MP4 URLs |
| **Multi-threaded Download** | Connection-pool-based engine, 1-100 configurable workers (default 20) |
| **AES-128 Decryption** | Auto-detects `#EXT-X-KEY`, decrypts with IV support and key caching |
| **Resume Support** | M3U8: atomic writes + segment index tracking; MP4: HTTP Range headers |
| **Resolution Selection** | Per-task dropdown; auto-selects highest bitrate by default |
| **Real-time Progress** | Progress bar, segment counter, and live download speed |
| **Fast Controls** | Pause/Stop response <0.5s; Delete with background cleanup |
| **Clipboard Auto-detect** | Auto-fills URL from clipboard when URL field is empty |
| **Smart Paste** | Ctrl+V detects M3U8/MP4 links and fills URL field automatically |
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
  "headers": "https://example.com/"
}
```

## Changelog

### v0.17

- 🔧 **fMP4 support** — Download fragmented MP4 streams (.m4s segments + init segment)
- 🔧 **EXT-X-MAP** — Parse init segment for fMP4 streams
- 🔧 **EXT-X-BYTERANGE** — Support byte-range based segmentation
- 🔧 **Audio track selection** — Choose from multiple audio tracks in master playlist
- 🔧 **Subtitle track detection** — Detect subtitle tracks (display only)
- 🔧 **FFmpeg mux** — Combine separate audio/video tracks into single MP4

### v0.16

- 🔧 **FFmpeg remux** — Use FFmpeg `-c copy` to remux TS segments into real MP4 container, compatible with all players and editors
- 🔧 **EXT-X-MEDIA-SEQUENCE** — Correctly parse segment start sequence for live/time-shifted streams
- 🔧 **AES IV fix** — Use MEDIA-SEQUENCE encoded as 128-bit big-endian when IV is not provided (per HLS spec)
- 🔧 **Segment verification** — Verify total segment count after download, auto-retry failed segments
- 🔧 **Output validation** — Check file size and duration reasonability after merge
- 🔧 **FFmpeg detection** — Check FFmpeg availability at startup, fallback to simple concat if missing

### v0.15

- 🔧 MP4 direct download (multi-threaded Range, resume support)
- 🔧 Auto-detect M3U8 / MP4 links
- 🔧 Custom right-click context menu (copy link, open directory, delete task)
- 🔧 Fast pause/stop response (<0.5s)
- 🔧 Delete with background cleanup (non-blocking UI)
- 🔧 Ctrl+V smart paste
- 🔧 Code refactoring (extracted utils.py)

### v0.14

- 🔧 Right-click context menu (custom rounded style)
- 🔧 Fast pause/stop response (64KB granularity)
- 🔧 Delete with background cleanup

### v0.13

- 🔧 Auto-detect M3U8 links from clipboard
- 🔧 M3U8 output format changed to .mp4

### v0.12

- 🔧 Code refactoring: extracted utils.py

### v0.11

- 🔧 MP4 direct download (multi-threaded Range)
- 🔧 Range support detection (HEAD → GET → 206 test)

### v0.10

- 🔧 First release
- 🔧 M3U8 parsing and multi-threaded download
- 🔧 AES-128 transparent decryption
- 🔧 Resolution selection
- 🔧 CustomTkinter dark theme GUI

## Acknowledgments

Core modules (M3U8 parsing, multi-threaded downloading, AES-128 decryption, TS merging) are based on [sdlw7757/M3U8-down](https://github.com/sdlw7757/M3U8-down). The original project uses a Flask + WebSocket web interface. This project rewrites the GUI as a CustomTkinter desktop application and adds MP4 download, PyInstaller packaging, resolution selection, clipboard auto-detect, and auto-save settings.

## License

MIT License
