# M3U8-DL-HLS-GUI

[English](README.md) | [中文](README.zh-CN.md)

A desktop M3U8 video downloader built with Python and CustomTkinter, featuring AES-128 decryption, multi-threaded concurrent downloads, resume support, and resolution selection.

## Features

- **M3U8 Parsing** — Supports both Master Playlists (multi-bitrate) and Media Playlists, automatically parses TS segment lists
- **Multi-threaded Downloads** — Connection-pool-based concurrent download engine, default 20 threads, configurable 1-100
- **AES-128 Decryption** — Auto-detects encrypted segments and decrypts them, with IV vector support and key caching
- **Resume Support** — Continue downloads from already-downloaded segments after stopping; supports pause/resume
- **Resolution Selection** — Per-task resolution picker with auto-select highest resolution option
- **Real-time Progress** — Progress bar, segment count, and download speed displayed in real-time
- **Proxy Support** — HTTP/HTTPS proxy configuration
- **Custom Headers** — Support for Referer and other custom HTTP headers
- **Auto-save Settings** — Workers, proxy, Referer and other settings are persisted automatically
- **Logging System** — All operations logged to `Logs/` folder with timestamped filenames
- **PyInstaller Packaging** — Can be packaged as a standalone .exe, no Python installation required
- **Filename Sanitization** — Automatically removes Windows-illegal characters to prevent file errors

## Project Structure

```
missav_m3u8_gui/
├── app.py              # Main application (CustomTkinter GUI)
├── main.py             # CLI entry point
├── m3u8_parser.py      # M3U8 playlist parser
├── downloader.py       # Multi-threaded download engine
├── decryptor.py        # AES-128 decryption module
├── merger.py           # TS segment merger
├── requirements.txt    # Python dependencies
├── start.bat           # Windows one-click launcher
├── config.json         # User config (auto-generated)
├── Logs/               # Log directory (auto-generated)
└── Downloads/          # Default download directory (auto-generated)
```

## Installation & Running

### Requirements

- Python 3.8+
- Windows (recommended) / macOS / Linux

### Option 1: Run Directly

```bash
# Install dependencies
pip install -r requirements.txt

# Launch GUI
python app.py
```

### Option 2: Use start.bat (Windows)

Double-click `start.bat` to auto-activate the conda environment, install dependencies, and launch.

### Option 3: Package as exe

```bash
# Install PyInstaller
pip install pyinstaller

# Package
pyinstaller --onefile --windowed --name "missav_m3u8_GUI" --clean app.py
```

The generated exe is in the `dist/` directory.

## Usage

### GUI Mode

1. Launch `python app.py`
2. Fill in the left panel:
   - **M3U8 URL**: Full m3u8 URL
   - **Referer**: Anti-hotlinking page URL (optional)
   - **Filename**: Output filename (default: output.ts)
   - **Save Directory**: Click "Browse" to select a directory
   - **Proxy**: e.g. `http://127.0.0.1:7890` (optional)
   - **Workers**: Concurrent download threads (default: 20)
3. Click "Start Download"
4. In the right task list you can:
   - Switch resolution via the dropdown menu
   - Pause / Resume / Stop / Delete tasks

### CLI Mode

```bash
# Single download
python main.py https://example.com/video.m3u8
python main.py https://example.com/video.m3u8 -o movie.ts -d D:/Videos

# Batch download
python main.py -f urls.txt -d D:/Downloads
```

Batch download file format (`urls.txt`):

```
https://example.com/video1.m3u8 Movie1.ts
https://example.com/video2.m3u8 Movie2.ts
# This is a comment line
```

### CLI Arguments

| Argument | Description |
|----------|-------------|
| `url` | m3u8 file URL |
| `-f, --file` | Batch download file |
| `-o, --output` | Output filename |
| `-d, --dir` | Output directory (default: Desktop) |
| `-w, --workers` | Concurrency (default: 20) |
| `-p, --proxy` | Proxy address |
| `-k, --keep` | Keep temporary TS segments |
| `-s, --stream` | Select bitrate stream index |
| `-v, --verbose` | Verbose logging |
| `--headers` | Custom headers `Key=Value` |

## Technical Details

### Download Flow

1. Fetch and parse the M3U8 playlist
2. If Master Playlist, auto-select highest bitrate (or manually choose)
3. Concurrently download all TS segments (with connection pool optimization)
4. Detect AES-128 encryption and auto-decrypt
5. Merge all segments into a complete TS file in order
6. Clean up temporary files

### Error Handling

- SSL errors auto-retry (up to 5 times, exponential backoff)
- Connection timeout auto-retry
- Filename auto-sanitization for Windows-illegal characters
- Task state persistence; recoverable after program restart

### Config File

`config.json` auto-saves the following settings:

```json
{
  "workers": 20,
  "proxy": "",
  "headers": "https://example.com/"
}
```

## Logging

All operations are logged in the `Logs/` directory. Filename format:

```
2025-01-15_14-30-25-123.log
```

## Acknowledgments

The core modules in this project — M3U8 parsing, multi-threaded downloading, AES-128 decryption, and TS merging — are based on the [sdlw7757/M3U8-down](https://github.com/sdlw7757/M3U8-down) project. The original project uses a Flask + WebSocket web interface. This project rewrites the GUI layer as a CustomTkinter desktop application and adds features such as PyInstaller packaging, resolution selection, and auto-save settings.

## License

MIT License
