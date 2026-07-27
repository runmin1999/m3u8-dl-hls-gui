# m3u8-dl-hls-gui

[English](README.md) | [中文](README.zh-CN.md)

> A high-performance desktop M3U8/HLS video downloader with a modern GUI, built with Python and CustomTkinter.

## Highlights

- Multi-threaded concurrent downloads with connection pool optimization
- AES-128 transparent decryption
- Pause / Resume / Stop with full state persistence
- Per-task resolution selection
- One-click exe packaging via PyInstaller

## Features

| Feature | Description |
|---------|-------------|
| **M3U8 Parsing** | Supports Master Playlist (multi-bitrate) and Media Playlist; auto-resolves relative URLs |
| **Multi-threaded Download** | Connection-pool-based engine, 1-100 configurable workers (default 20) |
| **AES-128 Decryption** | Auto-detects `#EXT-X-KEY`, decrypts with IV support and key caching |
| **Resume Support** | Atomic file writes ensure safe restart; tracks downloaded segment indices |
| **Resolution Selection** | Per-task dropdown; auto-selects highest bitrate by default |
| **Real-time Progress** | Progress bar, segment counter, and live download speed |
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
   - **M3U8 URL** — full m3u8 link
   - **Referer** — anti-hotlinking origin page (optional)
   - **Filename** — output name (default: `output.ts`)
   - **Save Directory** — click Browse to pick, click Open to reveal in explorer
   - **Proxy** — e.g. `http://127.0.0.1:7890` (optional)
   - **Workers** — concurrency (default: 20)
3. Click **Start Download**
4. Right panel — switch resolution, pause / resume / stop / delete tasks

### CLI

```bash
# Single download
python main.py https://example.com/video.m3u8
python main.py https://example.com/video.m3u8 -o movie.ts -d D:/Videos

# Batch download
python main.py -f urls.txt -d D:/Downloads
```

Batch file format (`urls.txt`):

```
https://example.com/video1.m3u8 Movie1.ts
https://example.com/video2.m3u8 Movie2.ts
# comment lines start with #
```

### CLI Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `url` | m3u8 URL | — |
| `-f, --file` | Batch download file | — |
| `-o, --output` | Output filename | `output.ts` |
| `-d, --dir` | Output directory | Desktop |
| `-w, --workers` | Concurrency | `20` |
| `-p, --proxy` | Proxy address | — |
| `-k, --keep` | Keep temp TS segments | `false` |
| `-s, --stream` | Bitrate stream index | auto (highest) |
| `-v, --verbose` | Verbose logging | `false` |
| `--headers` | Custom headers `Key=Value` | — |

## Architecture

```
app.py (GUI)          main.py (CLI)
    │                      │
    ├── m3u8_parser.py     ├── m3u8_parser.py
    ├── downloader.py      ├── downloader.py
    ├── decryptor.py       ├── decryptor.py
    └── merger.py          └── merger.py
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (English) | [docs/ARCHITECTURE.zh-CN.md](docs/ARCHITECTURE.zh-CN.md) (中文) for detailed flow diagrams and technical deep-dive.

## Configuration

`config.json` (auto-generated):

```json
{
  "workers": 20,
  "proxy": "",
  "headers": "https://example.com/"
}
```

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

## Acknowledgments

Core modules (M3U8 parsing, multi-threaded downloading, AES-128 decryption, TS merging) are based on [sdlw7757/M3U8-down](https://github.com/sdlw7757/M3U8-down). The original project uses a Flask + WebSocket web interface. This project rewrites the GUI as a CustomTkinter desktop application and adds PyInstaller packaging, resolution selection, and auto-save settings.

## License

MIT License
