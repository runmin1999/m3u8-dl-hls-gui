# v0.16 HLS Correctness & Real MP4 Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix HLS download correctness (MEDIA-SEQUENCE, AES IV, segment verification) and produce real MP4 output via FFmpeg remux.

**Architecture:** Modify the existing 4-module pipeline (m3u8_parser → downloader → decryptor → merger) to: (1) parse MEDIA-SEQUENCE and use it as AES IV, (2) verify segment counts after download, (3) use FFmpeg `-c copy` for TS→MP4 remux instead of binary concat, (4) validate output file.

**Tech Stack:** Python 3.8, requests, pycryptodome, customtkinter, FFmpeg 5.0 (system PATH)

## Global Constraints

- Python 3.8 compatibility (no walrus operator in new code, no `str.removeprefix`)
- All UI text and code comments in Chinese
- FFmpeg must be in system PATH (verified: ffmpeg 5.0 available)
- No new pip dependencies
- Existing tests use real network, not mocked

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `m3u8_parser.py` | Modify | Add `EXT-X-MEDIA-SEQUENCE` parsing, expose `media_sequence` on playlist |
| `decryptor.py` | Modify | Use MEDIA-SEQUENCE as default IV when IV is not provided |
| `downloader.py` | Modify | Add segment count verification + failed segment retry |
| `merger.py` | Modify | Replace binary concat with FFmpeg remux (`-c copy`) |
| `app.py` | Modify | Wire new verification/remux into download pipeline, add FFmpeg check |
| `utils.py` | Modify | Add `check_ffmpeg()` helper |

---

## Task 1: Parse EXT-X-MEDIA-SEQUENCE in m3u8_parser.py

**Covers:** EXT-X-MEDIA-SEQUENCE support, AES IV foundation

**Files:**
- Modify: `m3u8_parser.py:30-42` (M3U8Playlist dataclass)
- Modify: `m3u8_parser.py:124-188` (_parse_media function)

**Interfaces:**
- Consumes: existing `parse_m3u8()` function
- Produces: `M3U8Playlist.media_sequence: int` (default 0), used by decryptor and app

- [ ] **Step 1: Add `media_sequence` field to M3U8Playlist dataclass**

In `m3u8_parser.py`, add field to the `M3U8Playlist` dataclass (after `iv` field, around line 41):

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
    media_sequence: int = 0  # EXT-X-MEDIA-SEQUENCE 值（分片起始序号）
```

- [ ] **Step 2: Parse EXT-X-MEDIA-SEQUENCE in _parse_media**

In `m3u8_parser.py`, add parsing in `_parse_media()` function. After the `#EXT-X-TARGETDURATION` block (around line 150), add:

```python
        elif line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            # 分片起始序号（直播流/时移流使用）
            playlist.media_sequence = int(line.split(":")[1])
```

- [ ] **Step 3: Update segment indexing to use media_sequence**

In `_parse_media()`, change the segment index initialization and assignment. Replace:

```python
    seg_index = 0
```

With:

```python
    seg_index = playlist.media_sequence
```

This ensures segments use the correct starting index from MEDIA-SEQUENCE.

- [ ] **Step 4: Run existing parser tests to verify no regression**

Run: `python -c "from m3u8_parser import parse_m3u8; p = parse_m3u8('#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:9.0,\nseg0.ts\n#EXTINF:9.0,\nseg1.ts'); print(f'segments={len(p.segments)}, media_seq={p.media_sequence}')"`
Expected: `segments=2, media_seq=0`

- [ ] **Step 5: Commit**

```bash
git add m3u8_parser.py
git commit -m "feat: parse EXT-X-MEDIA-SEQUENCE for correct segment indexing"
```

---

## Task 2: Use MEDIA-SEQUENCE as AES IV default

**Covers:** AES IV correctness

**Files:**
- Modify: `decryptor.py:59-88` (decrypt_segment function)
- Modify: `app.py:613-618` (decrypt_files call)

**Interfaces:**
- Consumes: `M3U8Playlist.media_sequence` (from Task 1)
- Produces: Correct AES-128-CBC decryption using MEDIA-SEQUENCE as IV when not explicitly provided

- [ ] **Step 1: Modify decrypt_segment to accept media_sequence parameter**

In `decryptor.py`, update `decrypt_segment` function signature and IV logic:

```python
def decrypt_segment(
    encrypted_data: bytes,
    key: bytes,
    iv: bytes,
    media_sequence: int = 0,
) -> bytes:
    """
    使用 AES-128-CBC 模式解密数据

    Args:
        encrypted_data: 加密的二进制数据
        key: 16 字节 AES 密钥
        iv: 16 字节初始化向量（为空则使用 MEDIA-SEQUENCE）
        media_sequence: 分片序号（用于生成默认 IV）

    Returns:
        解密后的数据（已去除 PKCS7 填充）
    """
    if not iv:
        # HLS 规范：IV 缺失时使用 MEDIA-SEQUENCE 编码为 128-bit big-endian
        iv = media_sequence.to_bytes(16, byteorder='big')

    cipher = AES.new(key, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(encrypted_data)
    return _unpad_pkcs7(decrypted)
```

- [ ] **Step 2: Update decrypt_files to pass media_sequence**

In `decryptor.py`, update `decrypt_files` to pass `seg.index` (which now equals media_sequence + offset) as the media_sequence parameter. Actually, we need the base media_sequence. Let's update the function:

```python
def decrypt_files(
    file_paths: List[str],
    segments: List[Segment],
    headers: dict = None,
    proxy: str = "",
    media_sequence: int = 0,
) -> List[str]:
    """
    批量解密 TS 分片文件

    Args:
        file_paths: 已下载的 TS 分片文件路径列表
        segments: 对应的分片信息列表
        headers: 自定义请求头
        proxy: 代理地址
        media_sequence: EXT-X-MEDIA-SEQUENCE 值

    Returns:
        解密后的文件路径列表
    """
    decrypted_paths = []

    for filepath, seg in zip(file_paths, segments):
        if not seg.encryption_method or seg.encryption_method == "NONE":
            decrypted_paths.append(filepath)
            continue

        try:
            key = fetch_key(seg.key_url, headers, proxy)
            with open(filepath, "rb") as f:
                encrypted_data = f.read()
            # 使用分片自己的 IV，如果没有则用 media_sequence
            iv = seg.iv if seg.iv else media_sequence.to_bytes(16, byteorder='big')
            decrypted_data = decrypt_segment(encrypted_data, key, iv, media_sequence=0)
            with open(filepath, "wb") as f:
                f.write(decrypted_data)
            decrypted_paths.append(filepath)
            logger.debug(f"分片 {seg.index} 解密成功")
        except Exception as e:
            logger.error(f"分片 {seg.index} 解密失败: {e}")
            decrypted_paths.append(filepath)

    return decrypted_paths
```

- [ ] **Step 3: Update app.py to pass media_sequence to decrypt_files**

In `app.py`, find the `decrypt_files` call (around line 618) and update it:

```python
            ts_files = decrypt_files(ts_files, playlist.segments, task.custom_headers, task.proxy, media_sequence=playlist.media_sequence)
```

- [ ] **Step 4: Test with a known encrypted M3U8 stream**

Run a real download test with an AES-128 encrypted stream to verify decryption works correctly.

- [ ] **Step 5: Commit**

```bash
git add decryptor.py app.py
git commit -m "feat: use MEDIA-SEQUENCE as default AES IV per HLS spec"
```

---

## Task 3: Add FFmpeg remux to merger.py

**Covers:** Real MP4 output

**Files:**
- Modify: `merger.py:1-82` (entire file)

**Interfaces:**
- Consumes: list of TS file paths
- Produces: real MP4 file via FFmpeg `-c copy` remux

- [ ] **Step 1: Rewrite merger.py with FFmpeg remux**

Replace the entire `merger.py` content:

```python
"""合并模块：使用 FFmpeg 将 TS 分片 remux 为 MP4"""

import os
import shutil
import logging
import subprocess
from typing import List

logger = logging.getLogger(__name__)


def merge_ts_files(ts_files: List[str], output_path: str) -> bool:
    """
    使用 FFmpeg 将 TS 分片 remux 为 MP4

    FFmpeg -c copy 模式：直接复制流，不重新编码，
    速度快且无质量损失。输出为真正的 MP4 容器格式。

    Args:
        ts_files: TS 分片文件路径列表（必须按分片顺序排列）
        output_path: 输出 MP4 文件路径

    Returns:
        是否合并成功
    """
    if not ts_files:
        logger.error("没有可合并的分片文件")
        return False

    # 创建 FFmpeg concat 列表文件
    concat_list_path = output_path + ".concat.txt"
    try:
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for ts_file in ts_files:
                # FFmpeg concat 需要转义单引号
                safe_path = ts_file.replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")

        # 构建 FFmpeg 命令
        cmd = [
            "ffmpeg", "-y",  # -y 覆盖输出文件
            "-f", "concat",  # 使用 concat demuxer
            "-safe", "0",    # 允许绝对路径
            "-i", concat_list_path,
            "-c", "copy",    # 直接复制流，不编码
            "-movflags", "+faststart",  # MP4 快速播放优化
            output_path,
        ]

        logger.info(f"执行 FFmpeg remux: {' '.join(cmd[:6])}...")

        # 执行 FFmpeg（隐藏控制台窗口）
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 分钟超时
            startupinfo=startupinfo,
        )

        if result.returncode != 0:
            logger.error(f"FFmpeg remux 失败: {result.stderr[-500:]}")
            return False

        # 验证输出文件
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            logger.error("FFmpeg 输出文件为空")
            return False

        logger.info(f"FFmpeg remux 完成: {output_path}")
        return True

    except subprocess.TimeoutExpired:
        logger.error("FFmpeg remux 超时（10分钟）")
        return False
    except FileNotFoundError:
        logger.error("未找到 FFmpeg，请确保 FFmpeg 已安装并在 PATH 中")
        return False
    except Exception as e:
        logger.error(f"FFmpeg remux 异常: {e}")
        return False
    finally:
        # 清理 concat 列表文件
        if os.path.exists(concat_list_path):
            try:
                os.remove(concat_list_path)
            except OSError:
                pass


def merge_to_ts(
    ts_files: List[str],
    output_path: str,
) -> str:
    """
    将 TS 分片合并为 MP4 文件（使用 FFmpeg remux）

    Args:
        ts_files: TS 分片文件路径列表
        output_path: 输出文件路径

    Returns:
        最终输出文件路径

    Raises:
        ValueError: 没有可合并的分片
        RuntimeError: 合并失败
    """
    if not ts_files:
        raise ValueError("没有可合并的分片文件")

    # 清理输出路径中的非法字符（Windows 文件系统限制）
    illegal_chars = '<>:"/\\|?*\n\r\t'
    dir_part = os.path.dirname(output_path)
    file_part = os.path.basename(output_path)
    for ch in illegal_chars:
        file_part = file_part.replace(ch, '_')
    file_part = file_part.strip('. ')
    if not file_part:
        file_part = "output.mp4"
    output_path = os.path.join(dir_part, file_part)

    # 确保扩展名是 .mp4
    if not output_path.lower().endswith(".mp4"):
        output_path += ".mp4"

    if not merge_ts_files(ts_files, output_path):
        raise RuntimeError("FFmpeg remux 失败")

    return output_path
```

- [ ] **Step 2: Test FFmpeg remux with sample TS files**

Run: `python -c "from merger import merge_ts_files; print('merger module loaded OK')"`
Expected: Module loads without error.

- [ ] **Step 3: Commit**

```bash
git add merger.py
git commit -m "feat: use FFmpeg remux for real MP4 output"
```

---

## Task 4: Add segment count verification and failed retry

**Covers:** Download reliability

**Files:**
- Modify: `downloader.py:126-278` (download_all function)

**Interfaces:**
- Consumes: segment list, downloaded file paths
- Produces: verified file paths, retry failed segments

- [ ] **Step 1: Add verification and retry logic to download_all**

In `downloader.py`, after the main download loop (around line 274, before `return`), add verification and retry:

```python
    if failed:
        logger.warning(f"{len(failed)} 个分片下载失败: {failed}")

    # ── 验证分片总数 ──
    expected_count = len(segments)
    downloaded_count = len(results)
    if downloaded_count < expected_count:
        missing = [s.index for s in segments if s.index not in results]
        logger.warning(f"分片验证: 预期 {expected_count} 个，已下载 {downloaded_count} 个，缺失 {len(missing)} 个")

        # ── 失败分片二次重试 ──
        if missing and not (stop_check and stop_check()):
            logger.info(f"二次重试 {len(missing)} 个失败分片")
            retry_tasks = []
            for seg in segments:
                if seg.index in missing:
                    filename = f"{seg.index:06d}.ts"
                    filepath = os.path.join(temp_dir, filename)
                    retry_tasks.append((seg, filepath))

            if retry_tasks:
                retry_session = _create_session(headers, proxy)
                with ThreadPoolExecutor(max_workers=min(max_workers, len(retry_tasks))) as retry_executor:
                    retry_futures = {}
                    for seg, filepath in retry_tasks:
                        if stop_check and stop_check():
                            break
                        future = retry_executor.submit(
                            download_segment, seg, filepath, retry_session, stop_check
                        )
                        retry_futures[future] = (seg, filepath)

                    for future in as_completed(retry_futures):
                        seg, filepath = retry_futures[future]
                        success = future.result()
                        with _lock:
                            if success:
                                results[seg.index] = filepath
                                completed += 1
                                try:
                                    seg_size = os.path.getsize(filepath)
                                except OSError:
                                    seg_size = 0
                                bytes_downloaded += seg_size
                            else:
                                logger.error(f"分片 {seg.index} 二次重试仍失败")
                        if progress_callback:
                            progress_callback(completed, total)

                retry_session.close()

    # 最终速度回调
    if speed_callback:
        speed_callback(completed, bytes_downloaded)

    if failed:
        logger.warning(f"{len(failed)} 个分片下载失败: {failed}")

    return _build_result_list(results, segments)
```

- [ ] **Step 2: Test download_all with verification**

Run a real download and verify that the log shows segment count verification.

- [ ] **Step 3: Commit**

```bash
git add downloader.py
git commit -m "feat: add segment count verification and failed retry"
```

---

## Task 5: Add FFmpeg availability check and output validation

**Covers:** Output file validity check, FFmpeg dependency

**Files:**
- Modify: `utils.py:60-66` (add check_ffmpeg function)
- Modify: `app.py:480-657` (run_download function)

**Interfaces:**
- Consumes: FFmpeg availability
- Produces: validated output file, warning if FFmpeg missing

- [ ] **Step 1: Add check_ffmpeg utility function to utils.py**

In `utils.py`, add after `format_speed` function:

```python
def check_ffmpeg():
    """检查 FFmpeg 是否可用"""
    try:
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            startupinfo=startupinfo,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
```

Add `import subprocess` at the top of utils.py.

- [ ] **Step 2: Add output file validation to app.py**

In `app.py`, after the merge step (around line 628), add validation:

```python
        # 步骤6：将所有 TS 分片合并为一个完整文件
        task.current_action = "合并中..."
        task.progress = 95
        if on_progress:
            on_progress(task)
        final_path = merge_to_ts(ts_files, output_path)

        # 步骤6.5：验证输出文件
        if os.path.exists(final_path):
            file_size = os.path.getsize(final_path)
            if file_size == 0:
                raise Exception("输出文件大小为 0，合并可能失败")
            # 根据视频时长估算合理文件大小（最低 1KB/秒）
            if playlist.total_duration > 0:
                min_expected = playlist.total_duration * 1024  # 1KB/秒
                if file_size < min_expected:
                    logger.warning(f"输出文件偏小: {file_size} 字节，预期至少 {min_expected:.0f} 字节")
            task.current_action = f"合并完成 ({file_size / (1024*1024):.1f} MB)"
            if on_progress:
                on_progress(task)
```

- [ ] **Step 3: Add FFmpeg warning to UI if not available**

In `app.py`, in the `run_download` function, at the beginning (after `task.status = "downloading"`), add:

```python
        # 检查 FFmpeg 是否可用（TS 合并需要）
        from utils import check_ffmpeg
        if not check_ffmpeg():
            logger.warning("FFmpeg 未找到，将使用简单拼接模式（输出可能不是标准 MP4）")
```

- [ ] **Step 4: Test FFmpeg check**

Run: `python -c "from utils import check_ffmpeg; print('FFmpeg available:', check_ffmpeg())"`
Expected: `FFmpeg available: True`

- [ ] **Step 5: Commit**

```bash
git add utils.py app.py
git commit -m "feat: add FFmpeg availability check and output validation"
```

---

## Task 6: Update version and documentation

**Covers:** Version bump, documentation

**Files:**
- Modify: `app.py:887` (version string)
- Modify: `README.md` (changelog)
- Modify: `README.zh-CN.md` (changelog)

**Interfaces:**
- Consumes: all previous tasks
- Produces: v0.16 release-ready code

- [ ] **Step 1: Bump version to v0.16 in app.py**

In `app.py`, line 887, change:

```python
        self.title("m3u8-dl-hls-gui v0.15")
```

To:

```python
        self.title("m3u8-dl-hls-gui v0.16")
```

- [ ] **Step 2: Update README.zh-CN.md changelog**

Add v0.16 section to the changelog in README.zh-CN.md:

```markdown
## v0.16 更新

- 🔧 **FFmpeg remux** — 使用 FFmpeg `-c copy` 模式将 TS 分片 remux 为真正 MP4 容器，兼容所有播放器和剪辑软件
- 🔧 **EXT-X-MEDIA-SEQUENCE** — 正确解析直播流/时移流的分片起始序号
- 🔧 **AES IV 修正** — 当 M3U8 未提供 IV 时，使用 MEDIA-SEQUENCE 编码为 128-bit big-endian 作为 IV（符合 HLS 规范）
- 🔧 **分片校验** — 下载完成后校验分片总数，失败分片自动二次重试
- 🔧 **输出验证** — 合并后检查文件大小和时长合理性
- 🔧 **FFmpeg 检测** — 启动时检测 FFmpeg 是否可用，不可用时降级为简单拼接
```

- [ ] **Step 3: Update README.md changelog**

Add English version of the changelog to README.md.

- [ ] **Step 4: Commit**

```bash
git add app.py README.md README.zh-CN.md
git commit -m "chore: bump version to v0.16 with HLS correctness improvements"
```

---

## Task 7: End-to-end verification

**Covers:** All sections — integration testing

**Files:**
- Test: real M3U8 download with AES-128 encryption
- Test: real M3U8 download without encryption
- Test: verify FFmpeg remux output is valid MP4

- [ ] **Step 1: Test unencrypted M3U8 download**

Run the app and download a known unencrypted M3U8 stream. Verify:
- Download completes
- Output file is .mp4
- File plays correctly in VLC
- File size is reasonable

- [ ] **Step 2: Test AES-128 encrypted M3U8 download**

Download a known AES-128 encrypted stream. Verify:
- Decryption succeeds
- Output file plays correctly
- No artifacts or corruption

- [ ] **Step 3: Verify FFmpeg output format**

Run: `ffprobe output.mp4 2>&1 | Select-String "Duration|Video|Audio"`
Expected: Shows valid MP4 container with video stream info.

- [ ] **Step 4: Test edge cases**
- Empty M3U8 (should show error)
- Single segment stream
- Very large stream (1000+ segments)

- [ ] **Step 5: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: address issues found during end-to-end testing"
```
