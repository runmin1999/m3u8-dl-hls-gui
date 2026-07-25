# Architecture & Technical Deep-Dive

[English](ARCHITECTURE.md) | [中文](ARCHITECTURE.zh-CN.md)

This document describes the internal workflow and technical principles of each core module in m3u8-dl-hls-gui.

---

## Table of Contents

1. [M3U8 Parsing](#1-m3u8-parsing)
2. [Multi-threaded Download](#2-multi-threaded-download)
3. [AES-128 Decryption](#3-aes-128-decryption)
4. [Resume Support](#4-resume-support)
5. [Resolution Selection](#5-resolution-selection)
6. [Proxy Support](#6-proxy-support)
7. [Custom Headers](#7-custom-headers)
8. [Complete Download Flow](#8-complete-download-flow)

---

## 1. M3U8 Parsing

### 1.1 What is M3U8

M3U8 is a playlist format used by HLS (HTTP Live Streaming). It is a plain-text file describing video segment information. HLS splits video into small `.ts` segments and downloads them one by one over HTTP.

### 1.2 Two Playlist Types

```
┌─────────────────────────────────────────────────────────┐
│                   Master Playlist                        │
│  (multi-bitrate entry, points to multiple Media Playlists)│
├─────────────────────────────────────────────────────────┤
│                                                         │
│  #EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360  │──→ 360p.m3u8
│  #EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720│──→ 720p.m3u8
│  #EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080│──→ 1080p.m3u8
│                                                         │
└─────────────────────────────────────────────────────────┘
                         │
                         ▼ User selects bitrate
┌─────────────────────────────────────────────────────────┐
│                   Media Playlist                         │
│  (actual TS segment list)                                │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  #EXT-X-TARGETDURATION:10                                │
│  #EXT-X-KEY:METHOD=AES-128,URI="key.bin"               │
│  #EXTINF:9.009,                                         │
│  segment000.ts                                          │
│  #EXTINF:9.009,                                         │
│  segment001.ts                                          │
│  #EXTINF:5.005,                                         │
│  segment002.ts                                          │
│  ...                                                    │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 1.3 Parsing Flow

```
M3U8 text content
    │
    ▼
┌──────────────────┐
│ Check #EXTM3U    │ ← Invalid file → raise error
│ header            │
└────────┬─────────┘
         │
         ▼
┌──────────────────────────────┐
│ Detect #EXT-X-STREAM-INF    │
└────────┬─────────────────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
  Master   Media
  Playlist Playlist
    │         │
    ▼         ▼
  Parse    Parse
  streams  segments
  ┌────────┐ ┌────────────────┐
  │BANDWIDTH│ │EXTINF: duration│
  │RESOLUTION│ │EXT-X-KEY: enc  │
  │URL      │ │segment URL     │
  └────────┘ └────────────────┘
```

### 1.4 Key Parsing Logic

**Attribute regex**: `([A-Z0-9_-]+)=("([^"]*)"|([^,]*))`

- Matches `KEY=VALUE` or `KEY="VALUE"` format
- Supports quoted values (e.g. `CODECS="avc1.64001e,mp4a.40.2"`)

**Relative URL resolution**: Uses `urllib.parse.urljoin` to convert relative paths to absolute URLs

**Encryption info propagation**: `EXT-X-KEY` propagates to subsequent segments until a new `EXT-X-KEY` tag is encountered

---

## 2. Multi-threaded Download

### 2.1 Architecture

```
┌─────────────────────────────────────────────────────┐
│                  ThreadPoolExecutor                  │
│                  (default 20 workers)                │
├─────┬─────┬─────┬─────┬─────┬─────┬─────┬─────────┤
│ T1  │ T2  │ T3  │ T4  │ T5  │ T6  │ ... │  T20    │
└──┬──┘└──┬─┘└──┬──┘└──┬──┘└──┬──┘└──┬──┘└──┬──┘└───┬──┘
   │      │     │      │      │      │      │       │
   ▼      ▼     ▼      ▼      ▼      ▼      ▼       ▼
┌─────────────────────────────────────────────────────┐
│              requests.Session (connection pool)      │
│         HTTPAdapter(pool_connections=50,             │
│                     pool_maxsize=50)                 │
├─────────────────────────────────────────────────────┤
│  conn1   conn2   conn3   conn4  ...   conn50        │
└─────────────────────────────────────────────────────┘
   │        │        │        │              │
   ▼        ▼        ▼        ▼              ▼
 HTTP server (TS segment files)
```

### 2.2 Atomic Write Mechanism

```
Write flow:

  session.get(segment.url)
       │
       ▼
  ┌──────────────┐
  │ Write .tmp   │ ← Temp file; interruption won't corrupt target
  │ (streaming)  │   chunk_size=8192 (8KB)
  └──────┬───────┘
         │ Write complete
         ▼
  ┌──────────────┐
  │ os.replace   │ ← Atomic operation: instant swap
  │ .tmp → .ts   │   Either fully exists or doesn't exist
  └──────────────┘
```

### 2.3 Retry Strategy

```
Download failed
   │
   ▼ Retry 1 (wait 1s)
   │── Success → return
   │── Failed ↓
   ▼ Retry 2 (wait 2s)
   │── Success → return
   │── Failed ↓
   ▼ Retry 3 (wait 3s)
   │── Success → return
   │── Failed ↓
   ▼ Retry 4 (wait 4s)
   │── Success → return
   │── Failed ↓
   ▼ Retry 5 (wait 5s)
   │── Success → return
   │── Failed → mark as failed segment
```

### 2.4 Thread Safety

```python
# Shared variables protected by threading.Lock
_lock = threading.Lock()

with _lock:
    results[seg.index] = filepath   # Update results
    completed += 1                   # Update counter
    bytes_downloaded += seg_size     # Update byte count
```

---

## 3. AES-128 Decryption

### 3.1 Encryption Principle

HLS uses AES-128-CBC mode to encrypt TS segments:

```
Plaintext data (TS segment)
      │
      ▼
┌─────────────────────────────────┐
│        AES-128-CBC Encryption   │
│                                 │
│  Key (16 bytes) ──→ AES cipher  │
│  IV (16 bytes) ──→            │
│  Plaintext ─────→            │
│                   ↓            │
│              Ciphertext         │
└─────────────────────────────────┘
```

### 3.2 Decryption Flow

```
EXT-X-KEY tag from M3U8
│
├── METHOD=AES-128
├── URI="https://example.com/key.bin"   ← Key URL
└── IV=0x1234567890ABCDEF...            ← Initialization vector (optional)
         │
         ▼
┌──────────────────────────────┐
│   fetch_key(key_url)         │
│                              │
│   Check key cache ──hit──→ return cached key
│       │                      │
│       miss                   │
│       ▼                      │
│   HTTP GET key.bin           │
│       │                      │
│       ▼                      │
│   Return 16-byte key         │
│   Write to cache (thread-safe)│
└──────────────────────────────┘
         │
         ▼
┌──────────────────────────────┐
│   decrypt_segment()          │
│                              │
│   encrypted_data (ciphertext)│
│   key (16-byte key)          │
│   iv (16-byte IV)            │
│       │                      │
│       ▼                      │
│   AES.new(key, CBC, iv)     │
│       │                      │
│       ▼                      │
│   AES.decrypt(data)          │
│       │                      │
│       ▼                      │
│   Remove PKCS7 padding       │
│       │                      │
│       ▼                      │
│   Return plaintext           │
└──────────────────────────────┘
```

### 3.3 PKCS7 Padding

AES requires data length to be a multiple of 16. PKCS7 pads the end:

```
Original data (14 bytes):  [A][B][C][D][E][F][G][H][I][J][K][L][M][N]
PKCS7 pad 2 bytes:         [A][B][C][D][E][F][G][H][I][J][K][L][M][N][02][02]
                                                               ↑padding

Original data (16 bytes):  [A][B][C][D][E][F][G][H][I][J][K][L][M][N][O][P]
PKCS7 pad 16 bytes:        [A][B][C][D][E][F][G][H][I][J][K][L][M][N][O][P][10]...[10]
```

### 3.4 Key Caching

```
1st encrypted segment
    │
    ▼
key_url in cache?
    │── Yes → return cached key directly
    │── No ↓
    │
    HTTP GET key_url
    │
    ▼
    Save to _key_cache[key_url] = key
    │
    ▼
    Return key

Nth encrypted segment (same key_url)
    │
    ▼
key_url in cache? → hit → return directly (skip network request)
```

---

## 4. Resume Support

### 4.1 Principle

Resume support is based on two key designs:

1. **Atomic writes**: Each segment is written to `.tmp` then renamed, ensuring downloaded `.ts` files are always complete
2. **Index set**: Tracks which segment indices have been successfully downloaded

### 4.2 Flow

```
Task start / resume
    │
    ▼
┌──────────────────────────────┐
│  Load downloaded segment     │
│  index set                   │
│  _downloaded_indices = {0,1,2}│
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│  Iterate all segments        │
│                              │
│  for seg in segments:        │
│    if seg.index in indices:  │
│      Verify .ts exists & > 0 │
│      │── exists → skip       │
│      │── missing → re-download│
│    else:                     │
│      Add to download queue   │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│  Multi-thread download of    │
│  remaining segments          │
│  Update index set on success │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│  Save index set to           │
│  tasks_history.json          │
│  Recoverable on next launch  │
└──────────────────────────────┘
```

### 4.3 Pause / Resume Mechanism

```
User clicks "Pause"
    │
    ▼
┌──────────────────┐
│ task._pause_flag  │
│ = True            │
│ task.status =     │
│   "paused"        │
└──────────────────┘
        │
        ▼  stop_check() in download thread detects pause
┌──────────────────┐
│ while _pause_flag │
│   sleep(0.3)     │  ← Loop waiting, don't exit thread
│                  │
│   _stop_flag?    │── True → exit (user clicked Stop)
│     │            │
│     False        │
│     ↓            │
│   Continue waiting│
└──────────────────┘
        │
        ▼ User clicks "Resume"
┌──────────────────┐
│ _pause_flag =    │
│   False           │
│ _stop_flag =     │
│   False           │
│ status =         │
│   "downloading"  │
└──────────────────┘
        │
        ▼  stop_check() returns False, download thread resumes
```

---

## 5. Resolution Selection

### 5.1 Flow

```
User inputs M3U8 URL
    │
    ▼
Parse Master Playlist
    │
    ▼
Extract available resolutions
┌──────────────────────────┐
│ streams:                  │
│   [0] 640x360   (800kbps)│
│   [1] 1280x720  (2Mbps)  │
│   [2] 1920x1080 (5Mbps)  │
└──────────────────────────┘
    │
    ▼
UI dropdown displays
┌──────────────────┐
│ ▼ Highest        │
│   640x360        │
│   1280x720       │
│   1920x1080      │
└──────────────────┘
    │
    ▼ User selects (or default highest)
┌──────────────────────────┐
│ Match by name to bitrate  │
│ selected_idx = ...        │
│ stream_url = streams[idx] │
└──────────────────────────┘
    │
    ▼
Fetch selected bitrate's Media Playlist
    │
    ▼
Continue download flow...
```

### 5.2 Per-task Independence

Each task card has its own resolution dropdown, independent of others:

```
Task A: [1080p ▼]  ← independent
Task B: [720p  ▼]  ← independent
Task C: [Highest ▼] ← independent
```

---

## 6. Proxy Support

### 6.1 Configuration

```
User enters proxy address in GUI
    │
    ▼
┌──────────────────────────┐
│ Proxy address:            │
│ http://127.0.0.1:7890    │
│ socks5://127.0.0.1:1080  │
└──────────────────────────┘
    │
    ▼ Auto-saved to config.json
```

### 6.2 Where Proxy is Applied

Proxy configuration is passed to all network requests:

```
proxy = "http://127.0.0.1:7890"
    │
    ├──→ fetch_m3u8()         Fetch M3U8 file
    ├──→ download_segment()   Download TS segments
    ├──→ fetch_key()          Fetch AES key
    └──→ merge/decrypt        Local operations, no network

Every requests call configures:
proxies = {
    "http": proxy,
    "https": proxy
}
```

### 6.3 Request Chain

```
App → HTTP Proxy Server → Target Server
  │         │                │
  │    Proxy forwards    Proxy forwards
  │    request           response
  │         │                │
  ◄─────────◄────────────────◄
```

---

## 7. Custom Headers

### 7.1 Referer Anti-hotlinking

Many video servers check the Referer header to prevent hotlinking:

```
Server check:
┌──────────────────────────────────────┐
│  Referer: https://example.com/video  │
│           ↑                          │
│  Must match expected domain,         │
│  otherwise returns 403               │
└──────────────────────────────────────┘

App configuration:
┌──────────────────────────┐
│ Referer origin page:      │
│ https://example.com/video │ ← User fills in
└──────────────────────────┘
    │
    ▼
All HTTP requests automatically include:
headers = {
    "Referer": "https://example.com/video",
    "User-Agent": "Mozilla/5.0 ..."
}
```

### 7.2 Where Headers are Applied

```
User fills in Referer
    │
    ▼
Auto-saved to config.json
    │
    ▼
Written to task.custom_headers on task creation
    │
    ├──→ fetch_m3u8()      Sent when fetching M3U8
    ├──→ download_all()    Sent when downloading segments
    └──→ fetch_key()       Sent when fetching AES key
```

---

## 8. Complete Download Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    Complete Download Flow                     │
└─────────────────────────────────────────────────────────────┘

  User inputs URL
      │
      ▼
  ┌──────────────┐
  │ Fetch M3U8   │ ← with retry (5x), proxy, custom headers
  └──────┬───────┘
         │
         ▼
  ┌──────────────────┐
  │ Parse M3U8       │
  └──────┬───────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
  Master   Media
    │         │
    ▼         │
  Select ─────┘
  bitrate
    │
    ▼
  ┌──────────────────┐
  │ Fetch Media       │
  │ Playlist          │
  └──────┬───────────┘
         │
         ▼
  ┌──────────────────┐
  │ Parse segment    │
  │ list + encryption│
  └──────┬───────────┘
         │
         ▼
  ┌──────────────────┐
  │ Resume check     │ ← Skip already downloaded segments
  └──────┬───────────┘
         │
         ▼
  ┌──────────────────────────────────┐
  │ ThreadPoolExecutor concurrent    │
  │ download                         │
  │                                  │
  │  T1 ──→ segment000.ts           │
  │  T2 ──→ segment001.ts           │
  │  T3 ──→ segment002.ts           │
  │  ...                            │
  │  T20 ──→ segment019.ts          │
  │                                  │
  │  Per segment:                    │
  │    1. Check stop/pause signal    │
  │    2. Stream download to .tmp    │
  │    3. Atomic rename to .ts       │
  │    4. Update progress & speed    │
  │    5. Auto-retry on failure (5x) │
  └──────┬───────────────────────────┘
         │
         ▼
  ┌──────────────────┐
  │ AES-128 decrypt  │ ← if encrypted
  │ (per segment)    │
  └──────┬───────────┘
         │
         ▼
  ┌──────────────────┐
  │ Merge segments   │
  │ segment000.ts    │
  │ + segment001.ts  │
  │ + segment002.ts  │
  │ + ...            │
  │ = output.ts      │
  └──────┬───────────┘
         │
         ▼
  ┌──────────────────┐
  │ Cleanup temp     │
  └──────┬───────────┘
         │
         ▼
    Task complete ✓
```

---

## Module Dependency

```
                    app.py (GUI main)
                   ╱    │    ╲
                  ╱     │     ╲
                 ╱      │      ╲
    m3u8_parser.py  downloader.py  decryptor.py  merger.py
         │              │              │            │
         └──────────────┴──────────────┴────────────┘
                         │
                    main.py (CLI entry)
```

Each module has a single responsibility and can be used independently or combined.
