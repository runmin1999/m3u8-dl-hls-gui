# v0.17 fMP4 & Extended HLS Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fMP4/.m4s support, EXT-X-MAP/BYTERANGE parsing, audio/video separation, and FFmpeg mux to handle modern HLS streams.

**Architecture:** Extend the existing 4-module pipeline (m3u8_parser → downloader → decryptor → merger) to detect fMP4 format and route to appropriate download/merge paths. Add audio track selection to the UI and use FFmpeg for muxing separate tracks.

**Tech Stack:** Python 3.8, requests, pycryptodome, customtkinter, FFmpeg 5.0 (system PATH)

## Global Constraints

- Python 3.8 compatibility (no walrus operator in new code)
- All UI text and code comments in Chinese
- FFmpeg must be in system PATH
- No new pip dependencies
- Existing TS download flow must remain unchanged (backward compatible)

## Background: HLS Formats

**Traditional HLS (TS):**
```
Master Playlist
  ├── 720p.m3u8 (video+audio in TS segments)
  │     ├── #EXTINF:9.0, seg0.ts
  │     └── #EXTINF:9.0, seg1.ts
  └── audio.m3u8 (separate audio, optional)
        ├── #EXTINF:9.0, aac0.ts
        └── #EXTINF:9.0, aac1.ts
```

**Modern HLS (fMP4):**
```
Master Playlist
  ├── 720p.m3u8
  │     ├── #EXT-X-MAP:URI="init.mp4"    ← init segment (moov atom)
  │     ├── #EXTINF:6.0, seg0.m4s         ← media segment
  │     └── #EXTINF:6.0, seg1.m4s
  └── audio.m3u8
        ├── #EXT-X-MAP:URI="audio_init.mp4"
        ├── #EXTINF:6.0, aac0.m4s
        └── #EXTINF:6.0, aac1.m4s
```

**ByteRange HLS:**
```
Media Playlist
  ├── #EXT-X-BYTERANGE:1000@0             ← first 1000 bytes of file
  ├── init.mp4
  ├── #EXT-X-BYTERANGE:2000@1000          ← next 2000 bytes
  └── init.mp4
```

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `m3u8_parser.py` | Modify | Add EXT-X-MAP, EXT-X-BYTERANGE parsing; detect fMP4; extract audio tracks |
| `downloader.py` | Modify | Add init segment download; support Range headers for BYTERANGE |
| `merger.py` | Modify | Add fMP4 concatenation; add FFmpeg audio/video mux |
| `app.py` | Modify | Detect fMP4 vs TS; handle audio/video separation; add audio track UI |
| `utils.py` | No change | — |

---

## Task 1: Parse EXT-X-MAP in m3u8_parser.py

**Covers:** EXT-X-MAP support, fMP4 detection foundation

**Files:**
- Modify: `m3u8_parser.py:9-42` (dataclasses)
- Modify: `m3u8_parser.py:125-194` (_parse_media)

**Interfaces:**
- Consumes: existing `parse_m3u8()` function
- Produces: `M3U8Playlist.init_segment_url: str`, `M3U8Playlist.init_segment_byterange: str`

- [ ] **Step 1: Add init segment fields to M3U8Playlist**

```python
@dataclass
class M3U8Playlist:
    """m3u8 播放列表"""
    is_master: bool = False
    streams: List[StreamInfo] = field(default_factory=list)
    segments: List[Segment] = field(default_factory=list)
    target_duration: float = 0.0
    total_duration: float = 0.0
    encryption_method: str = ""
    key_url: str = ""
    iv: bytes = b""
    media_sequence: int = 0
    # fMP4 初始化段信息
    init_segment_url: str = ""           # EXT-X-MAP URI（init segment 地址）
    init_segment_byterange: str = ""     # EXT-X-MAP BYTERANGE（如 "812@0"）
```

- [ ] **Step 2: Parse #EXT-X-MAP in _parse_media**

In `_parse_media()`, after the `#EXT-X-MEDIA-SEQUENCE` block, add:

```python
        elif line.startswith("#EXT-X-MAP:"):
            # fMP4 初始化段（init segment）
            attrs = _parse_attributes(line[len("#EXT-X-MAP:"):])
            playlist.init_segment_url = _resolve_url(base_url, attrs.get("URI", ""))
            playlist.init_segment_byterange = attrs.get("BYTERANGE", "")
```

- [ ] **Step 3: Test parsing**

```bash
conda run -n py38 python -c "
from m3u8_parser import parse_m3u8
content = '''#EXTM3U
#EXT-X-TARGETDURATION:6
#EXT-X-MAP:URI=\"init.mp4\"
#EXTINF:6.0,
seg0.m4s
#EXTINF:6.0,
seg1.m4s'''
p = parse_m3u8(content)
print(f'init_url={p.init_segment_url}')
print(f'segments={len(p.segments)}, ext={p.segments[0].url.split(\".\")[-1]}')
"
```
Expected: `init_url=.../init.mp4`, segments end with `.m4s`

- [ ] **Step 4: Commit**

```bash
git add m3u8_parser.py
git commit -m "feat: parse EXT-X-MAP for fMP4 init segment support"
```

---

## Task 2: Parse EXT-X-BYTERANGE in m3u8_parser.py

**Covers:** EXT-X-BYTERANGE support

**Files:**
- Modify: `m3u8_parser.py:9-18` (Segment dataclass)
- Modify: `m3u8_parser.py:125-194` (_parse_media)

**Interfaces:**
- Consumes: existing `parse_m3u8()` function
- Produces: `Segment.byterange: str` (e.g., "1000@0"), `Segment.byterange_url: str`

- [ ] **Step 1: Add byterange fields to Segment**

```python
@dataclass
class Segment:
    """TS/fMP4 分片信息"""
    url: str
    duration: float = 0.0
    index: int = 0
    encryption_method: str = ""
    key_url: str = ""
    iv: bytes = b""
    # EXT-X-BYTERANGE 支持
    byterange: str = ""          # 字节范围（如 "1000@0"：长度@偏移）
    byterange_url: str = ""      # BYTERANGE 引用的文件 URL（为空时沿用前一个分片的 URL）
```

- [ ] **Step 2: Parse #EXT-X-BYTERANGE in _parse_media**

In `_parse_media()`, add after the `#EXT-X-MAP` block:

```python
        elif line.startswith("#EXT-X-BYTERANGE:"):
            # 字节范围：格式为 长度@偏移（如 "1000@0"）
            current_byterange = line[len("#EXT-X-BYTERANGE:"):]
            current_byterange_url = ""  # 如果下一行是 URL 则使用，否则沿用前一个
```

And when creating the Segment (in the `elif not line.startswith("#")` block), add byterange fields:

```python
            seg = Segment(
                url=_resolve_url(base_url, line),
                duration=current_duration,
                index=seg_index,
                encryption_method=enc_method,
                key_url=key_url,
                iv=iv,
                byterange=getattr(_parse_media, '_current_byterange', ''),
                byterange_url=getattr(_parse_media, '_current_byterange_url', ''),
            )
```

Actually, let me simplify. Use local variables like the existing `enc_method` pattern:

```python
    current_duration = 0.0
    seg_index = playlist.media_sequence
    enc_method = ""
    key_url = ""
    iv = b""
    current_byterange = ""
    current_byterange_url = ""
    last_seg_url = ""  # 用于 BYTERANGE 引用前一个分片 URL
```

And in the BYTERANGE parsing:

```python
        elif line.startswith("#EXT-X-BYTERANGE:"):
            current_byterange = line[len("#EXT-X-BYTERANGE:"):]
            # BYTERANGE 可能不带 URL，此时引用前一个分片的 URL
            current_byterange_url = ""
```

And when creating the Segment:

```python
            br_url = current_byterange_url if current_byterange_url else last_seg_url
            seg = Segment(
                url=_resolve_url(base_url, line) if not current_byterange else (br_url or _resolve_url(base_url, line)),
                duration=current_duration,
                index=seg_index,
                encryption_method=enc_method,
                key_url=key_url,
                iv=iv,
                byterange=current_byterange,
            )
            last_seg_url = seg.url
            current_byterange = ""
```

- [ ] **Step 3: Test BYTERANGE parsing**

```bash
conda run -n py38 python -c "
from m3u8_parser import parse_m3u8
content = '''#EXTM3U
#EXT-X-TARGETDURATION:6
#EXT-X-BYTERANGE:1000@0
init.mp4
#EXT-X-BYTERANGE:2000@1000
init.mp4'''
p = parse_m3u8(content)
print(f'seg0 byterange={p.segments[0].byterange}, url={p.segments[0].url}')
print(f'seg1 byterange={p.segments[1].byterange}')
"
```
Expected: seg0 has byterange="1000@0", seg1 has byterange="2000@1000"

- [ ] **Step 4: Commit**

```bash
git add m3u8_parser.py
git commit -m "feat: parse EXT-X-BYTERANGE for byte-range segment support"
```

---

## Task 3: Add init segment and BYTERANGE download to downloader.py

**Covers:** fMP4 download, BYTERANGE download

**Files:**
- Modify: `downloader.py:51-123` (download_segment)
- Modify: `downloader.py:126-325` (download_all)

**Interfaces:**
- Consumes: `Segment.byterange` from Task 2
- Produces: init segment file path; BYTERANGE-aware segment downloads

- [ ] **Step 1: Add download_init_segment function**

Add new function before `download_all`:

```python
def download_init_segment(
    init_url: str,
    save_path: str,
    session: requests.Session,
    byterange: str = "",
    stop_check: Optional[Callable[[], bool]] = None,
) -> bool:
    """
    下载 fMP4 初始化段（init segment）

    Args:
        init_url: init segment 的下载地址
        save_path: 保存路径
        session: 复用的 requests Session
        byterange: 字节范围（如 "812@0"），为空则下载完整文件
        stop_check: 停止检查函数

    Returns:
        是否下载成功
    """
    tmp_path = save_path + ".tmp"

    for attempt in range(1, MAX_RETRIES + 1):
        if stop_check and stop_check():
            return False
        try:
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except OSError: pass

            headers = {}
            if byterange:
                # 解析 "长度@偏移" 格式
                parts = byterange.split("@")
                length = int(parts[0])
                offset = int(parts[1]) if len(parts) > 1 else 0
                end = offset + length - 1
                headers["Range"] = f"bytes={offset}-{end}"

            resp = session.get(init_url, timeout=REQUEST_TIMEOUT, stream=True, headers=headers)
            resp.raise_for_status()

            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                    if stop_check and stop_check():
                        return False

            os.replace(tmp_path, save_path)
            return True
        except (requests.RequestException, OSError, PermissionError) as e:
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except OSError: pass
            if attempt < MAX_RETRIES:
                time.sleep(attempt * 1)
            else:
                logger.error(f"Init segment 下载失败: {e}")
    return False
```

- [ ] **Step 2: Modify download_segment to support BYTERANGE**

Update `download_segment` to accept optional `byterange` parameter:

```python
def download_segment(
    segment: Segment,
    save_path: str,
    session: requests.Session,
    stop_check: Optional[Callable[[], bool]] = None,
) -> bool:
```

Inside the download loop, add Range header if segment has byterange:

```python
            # 构建请求头
            req_headers = {}
            if segment.byterange:
                parts = segment.byterange.split("@")
                length = int(parts[0])
                offset = int(parts[1]) if len(parts) > 1 else 0
                end = offset + length - 1
                req_headers["Range"] = f"bytes={offset}-{end}"

            resp = session.get(
                segment.url,
                timeout=REQUEST_TIMEOUT,
                stream=True,
                headers=req_headers,
            )
```

- [ ] **Step 3: Update download_all to download init segment first**

At the beginning of `download_all`, after creating the session, add init segment download:

```python
    # 下载 fMP4 init segment（如果有）
    init_segment_path = ""
    if segments and segments[0].url:
        # 检查是否有 init segment URL（通过 playlist 传递）
        pass  # init segment 由 app.py 单独下载，这里不需要处理
```

Actually, the init segment should be downloaded separately by app.py, not by download_all. Let me reconsider.

The flow should be:
1. app.py detects fMP4 (has init_segment_url)
2. app.py downloads init segment separately
3. app.py calls download_all for media segments
4. app.py concatenates init + media segments

So download_all doesn't need to change for init segment. But it does need to support BYTERANGE.

- [ ] **Step 4: Test module loads**

```bash
conda run -n py38 python -c "from downloader import download_init_segment, download_segment; print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add downloader.py
git commit -m "feat: add init segment download and BYTERANGE support"
```

---

## Task 4: Add fMP4 concatenation and FFmpeg mux to merger.py

**Covers:** fMP4 output, audio/video mux

**Files:**
- Modify: `merger.py` (entire file)

**Interfaces:**
- Consumes: init segment path + media segment paths (fMP4), or video + audio paths (mux)
- Produces: final MP4 file

- [ ] **Step 1: Add merge_fmp4 function**

```python
def merge_fmp4(
    init_path: str,
    media_files: List[str],
    output_path: str,
) -> str:
    """
    合并 fMP4 初始化段和媒体分片

    fMP4 分片可以直接二进制拼接（init + media segments），
    生成完整的 fMP4 文件。

    Args:
        init_path: 初始化段文件路径
        media_files: 媒体分片文件路径列表（按顺序）
        output_path: 输出文件路径

    Returns:
        输出文件路径
    """
    if not media_files:
        raise ValueError("没有可合并的媒体分片")

    # 清理输出路径
    illegal_chars = '<>:"/\\|?*\n\r\t'
    dir_part = os.path.dirname(output_path)
    file_part = os.path.basename(output_path)
    for ch in illegal_chars:
        file_part = file_part.replace(ch, '_')
    file_part = file_part.strip('. ')
    if not file_part:
        file_part = "output.mp4"
    output_path = os.path.join(dir_part, file_part)
    if not output_path.lower().endswith(".mp4"):
        output_path += ".mp4"

    try:
        with open(output_path, "wb") as out_f:
            # 写入 init segment（moov atom）
            if init_path and os.path.exists(init_path):
                with open(init_path, "rb") as in_f:
                    shutil.copyfileobj(in_f, out_f)
            # 写入所有 media segments
            for mf in media_files:
                with open(mf, "rb") as in_f:
                    shutil.copyfileobj(in_f, out_f)

        logger.info(f"fMP4 合并完成: {output_path}")
        return output_path
    except Exception as e:
        logger.error(f"fMP4 合并失败: {e}")
        raise
```

- [ ] **Step 2: Add mux_audio_video function**

```python
def mux_audio_video(
    video_path: str,
    audio_path: str,
    output_path: str,
) -> str:
    """
    使用 FFmpeg 合并视频和音频轨道

    Args:
        video_path: 视频文件路径
        audio_path: 音频文件路径
        output_path: 输出文件路径

    Returns:
        输出文件路径
    """
    # 清理输出路径
    illegal_chars = '<>:"/\\|?*\n\r\t'
    dir_part = os.path.dirname(output_path)
    file_part = os.path.basename(output_path)
    for ch in illegal_chars:
        file_part = file_part.replace(ch, '_')
    file_part = file_part.strip('. ')
    if not file_part:
        file_part = "output.mp4"
    output_path = os.path.join(dir_part, file_part)
    if not output_path.lower().endswith(".mp4"):
        output_path += ".mp4"

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c", "copy",  # 直接复制，不重编码
        "-movflags", "+faststart",
        output_path,
    ]

    try:
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
            startupinfo=startupinfo,
        )

        if result.returncode != 0:
            logger.error(f"FFmpeg mux 失败: {result.stderr[-500:]}")
            raise RuntimeError("FFmpeg mux 失败")

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError("FFmpeg mux 输出文件为空")

        logger.info(f"FFmpeg mux 完成: {output_path}")
        return output_path
    except subprocess.TimeoutExpired:
        raise RuntimeError("FFmpeg mux 超时")
    except FileNotFoundError:
        raise RuntimeError("未找到 FFmpeg")
```

- [ ] **Step 3: Update merge_to_ts to detect fMP4 vs TS**

Update `merge_to_ts` to handle both formats. Actually, it's cleaner to keep `merge_to_ts` for TS only and use the new functions for fMP4. The routing logic goes in app.py.

- [ ] **Step 4: Test module loads**

```bash
conda run -n py38 python -c "from merger import merge_fmp4, mux_audio_video, merge_to_ts; print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add merger.py
git commit -m "feat: add fMP4 concatenation and FFmpeg audio/video mux"
```

---

## Task 5: Extract audio/subtitle tracks from master playlist

**Covers:** Audio/video separation, track selection

**Files:**
- Modify: `m3u8_parser.py:21-28` (StreamInfo dataclass)
- Modify: `m3u8_parser.py:77-122` (_parse_master)

**Interfaces:**
- Consumes: existing `parse_m3u8()` function
- Produces: `M3U8Playlist.audio_tracks: List[StreamInfo]`, `StreamInfo.track_type: str`

- [ ] **Step 1: Add track_type to StreamInfo**

```python
@dataclass
class StreamInfo:
    """流信息（master playlist 中的条目）"""
    bandwidth: int = 0
    resolution: str = ""
    url: str = ""
    name: str = ""
    track_type: str = "VIDEO"  # TRACK 类型：VIDEO / AUDIO / SUBTITLES
    language: str = ""         # 语言代码（如 "en", "zh"）
    default: bool = False      # 是否为默认轨道
```

- [ ] **Step 2: Add audio_tracks field to M3U8Playlist**

```python
    # 音频/字幕轨道列表
    audio_tracks: List[StreamInfo] = field(default_factory=list)
    subtitle_tracks: List[StreamInfo] = field(default_factory=list)
```

- [ ] **Step 3: Update _parse_master to extract all track types**

Replace the current `#EXT-X-MEDIA` parsing:

```python
        elif line.startswith("#EXT-X-MEDIA:"):
            attrs = _parse_attributes(line[len("#EXT-X-MEDIA:"):])
            track_type = attrs.get("TYPE", "")
            uri = attrs.get("URI", "")
            if uri and track_type in ("AUDIO", "SUBTITLES"):
                stream = StreamInfo()
                stream.track_type = track_type
                stream.name = attrs.get("NAME", track_type)
                stream.language = attrs.get("LANGUAGE", "")
                stream.default = attrs.get("DEFAULT", "NO") == "YES"
                stream.url = _resolve_url(base_url, attrs)
                if track_type == "AUDIO":
                    playlist.audio_tracks.append(stream)
                elif track_type == "SUBTITLES":
                    playlist.subtitle_tracks.append(stream)
```

Wait, the URI parsing needs to handle the quoted value. Let me check the `_parse_attributes` function. It already handles quoted values. But the URI might be `URI="audio.m3u8"` and after parsing, `attrs["URI"]` would be `audio.m3u8`. Let me use `_resolve_url(base_url, attrs["URI"])`.

```python
        elif line.startswith("#EXT-X-MEDIA:"):
            attrs = _parse_attributes(line[len("#EXT-X-MEDIA:"):])
            track_type = attrs.get("TYPE", "")
            uri = attrs.get("URI", "")
            if uri and track_type in ("AUDIO", "SUBTITLES"):
                stream = StreamInfo()
                stream.track_type = track_type
                stream.name = attrs.get("NAME", track_type)
                stream.language = attrs.get("LANGUAGE", "")
                stream.default = attrs.get("DEFAULT", "NO") == "YES"
                stream.url = _resolve_url(base_url, uri)
                if track_type == "AUDIO":
                    playlist.audio_tracks.append(stream)
                elif track_type == "SUBTITLES":
                    playlist.subtitle_tracks.append(stream)
```

- [ ] **Step 4: Test parsing**

```bash
conda run -n py38 python -c "
from m3u8_parser import parse_m3u8
content = '''#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID=\"audio\",NAME=\"English\",DEFAULT=YES,URI=\"audio_en.m3u8\"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID=\"audio\",NAME=\"Chinese\",URI=\"audio_zh.m3u8\"
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720,AUDIO-GROUP=\"audio\"
720p.m3u8'''
p = parse_m3u8(content, 'https://example.com/')
print(f'audio_tracks={len(p.audio_tracks)}')
for t in p.audio_tracks:
    print(f'  {t.name} ({t.language}) default={t.default} url={t.url}')
"
```
Expected: 2 audio tracks listed

- [ ] **Step 5: Commit**

```bash
git add m3u8_parser.py
git commit -m "feat: extract audio/subtitle tracks from master playlist"
```

---

## Task 6: Update app.py download flow for fMP4 and audio/video

**Covers:** All features integration

**Files:**
- Modify: `app.py:105-133` (DownloadTask class)
- Modify: `app.py:480-667` (run_download function)
- Modify: `app.py:690-730` (TaskCard UI)
- Modify: `app.py:920-970` (App._build_ui)

**Interfaces:**
- Consumes: all previous tasks
- Produces: complete fMP4/audio/video download flow

- [ ] **Step 1: Add audio track fields to DownloadTask**

In `DownloadTask.__init__`, add:

```python
        self.audio_track = ""           # 选择的音频轨道名称
        self.available_audio_tracks = []  # 可用音频轨道列表
```

Update `to_dict` and `_load_tasks` to persist these fields.

- [ ] **Step 2: Update run_download to detect and handle fMP4**

In `run_download`, after parsing the playlist, add fMP4 detection:

```python
        # 检测格式：fMP4 还是 TS
        is_fmp4 = bool(playlist.init_segment_url)
        if not is_fmp4 and playlist.segments:
            # 通过分片文件扩展名判断
            sample_url = playlist.segments[0].url.lower()
            is_fmp4 = sample_url.endswith('.m4s') or sample_url.endswith('.mp4')

        if is_fmp4:
            _run_fmp4_download(task, tasks_dict, playlist, output_path, temp_dir, on_progress, stop_check, progress_callback, speed_callback)
        else:
            # 原有 TS 下载流程
            ...
```

- [ ] **Step 3: Implement _run_fmp4_download helper**

```python
def _run_fmp4_download(task, tasks_dict, playlist, output_path, temp_dir, on_progress, stop_check, progress_callback, speed_callback):
    """fMP4 下载流程"""
    from downloader import download_all, download_init_segment, _create_session
    from merger import merge_fmp4, merge_to_ts

    # 1. 下载 init segment
    if playlist.init_segment_url:
        task.current_action = "下载初始化段..."
        if on_progress:
            on_progress(task)
        session = _create_session(task.custom_headers, task.proxy)
        init_path = os.path.join(temp_dir, "init.mp4")
        br = playlist.init_segment_byterange
        download_init_segment(playlist.init_segment_url, init_path, session, byterange=br, stop_check=stop_check)
        session.close()
    else:
        init_path = ""

    if task._stop_flag:
        return

    # 2. 下载 media segments
    task.total_segments = len(playlist.segments)
    task.current_action = f"下载中 {task.total_segments} 个分片..."
    if on_progress:
        on_progress(task)

    media_files = download_all(
        playlist.segments, temp_dir, max_workers=task.workers,
        headers=task.custom_headers, proxy=task.proxy,
        progress_callback=progress_callback, stop_check=stop_check,
        skip_indices=task._downloaded_indices, speed_callback=speed_callback,
    )

    if task._stop_flag:
        return
    if not media_files:
        raise Exception("没有成功下载任何分片")

    # 3. 解密（如果需要）
    if any(s.encryption_method for s in playlist.segments):
        task.current_action = "解密中..."
        if on_progress:
            on_progress(task)
        media_files = decrypt_files(media_files, playlist.segments, task.custom_headers, task.proxy, media_sequence=playlist.media_sequence)

    # 4. 合并 fMP4
    task.current_action = "合并中..."
    task.progress = 95
    if on_progress:
        on_progress(task)
    final_path = merge_fmp4(init_path, media_files, output_path)

    return final_path
```

- [ ] **Step 4: Add audio track selection to UI**

In `App._build_ui`, after the resolution dropdown, add audio track dropdown:

```python
        # 音频轨道选择
        ctk.CTkLabel(form, text="音频轨道", **lk).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 4)); r += 1
        self.audio_var = ctk.StringVar(value="默认")
        self.audio_combo = ctk.CTkOptionMenu(form, variable=self.audio_var, values=["默认"], width=190, height=34, font=("", 11), fg_color=COLORS["input"], button_color=COLORS["border"])
        self.audio_combo.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 8)); r += 1
```

- [ ] **Step 5: Update _start_download to pass audio track selection**

When creating a task, pass the audio track selection.

- [ ] **Step 6: Update run_download to handle audio/video separation**

After downloading video segments, check if there's a separate audio track selected. If so, download the audio playlist and segments, then mux.

- [ ] **Step 7: Test the full flow**

Run the app and test with a known fMP4 stream.

- [ ] **Step 8: Commit**

```bash
git add app.py
git commit -m "feat: integrate fMP4 download and audio track selection"
```

---

## Task 7: Update version and documentation

**Covers:** Version bump, documentation

**Files:**
- Modify: `app.py:887` (version string)
- Modify: `README.md`
- Modify: `README.zh-CN.md`

- [ ] **Step 1: Bump version to v0.17**

```python
        self.title("m3u8-dl-hls-gui v0.17")
```

- [ ] **Step 2: Update READMEs with v0.17 changelog**

Add v0.17 section to both READMEs:

**English:**
```markdown
### v0.17

- 🔧 **fMP4 support** — Download fragmented MP4 streams (.m4s segments + init segment)
- 🔧 **EXT-X-MAP** — Parse init segment for fMP4 streams
- 🔧 **EXT-X-BYTERANGE** — Support byte-range based segmentation
- 🔧 **Audio track selection** — Choose from multiple audio tracks in master playlist
- 🔧 **Subtitle track detection** — Detect subtitle tracks (display only)
- 🔧 **FFmpeg mux** — Combine separate audio/video tracks into single MP4
```

**中文：**
```markdown
### v0.17

- 🔧 **fMP4 支持** — 下载 fragmented MP4 流（.m4s 分片 + init segment）
- 🔧 **EXT-X-MAP** — 解析 fMP4 初始化段
- 🔧 **EXT-X-BYTERANGE** — 支持字节范围分片
- 🔧 **音频轨道选择** — 从多个音频轨道中选择
- 🔧 **字幕轨道检测** — 检测字幕轨道（仅显示）
- 🔧 **FFmpeg mux** — 合并独立的音视频轨道为单个 MP4
```

- [ ] **Step 3: Commit**

```bash
git add app.py README.md README.zh-CN.md
git commit -m "chore: bump version to v0.17 with fMP4 and HLS compat"
```

---

## Task 8: End-to-end verification

**Covers:** All sections

- [ ] **Step 1: Test fMP4 download** — Find a known fMP4 HLS stream and download
- [ ] **Step 2: Test TS download** — Verify existing TS flow still works
- [ ] **Step 3: Test audio track selection** — Download with separate audio
- [ ] **Step 4: Test BYTERANGE** — Find a BYTERANGE stream and download
- [ ] **Step 5: Final commit if fixes needed**
