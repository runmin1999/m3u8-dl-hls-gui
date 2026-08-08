"""M3U8/HLS 下载编排模块：解析、下载、解密、合并、音视频 mux"""

import os
import re
import time
import shutil
import hashlib
import logging
from datetime import datetime
from src.utils.helpers import save_tasks, TASKS_HISTORY_FILE

logger = logging.getLogger(__name__)


def _download_audio_track(
    audio_url: str,
    temp_dir: str,
    workers: int,
    headers: dict,
    proxy: str,
    stop_check=None,
):
    """
    下载独立音频轨道：解析 m3u8 → 下载分片 → 解密 → 合并

    Returns:
        合并后的音频文件路径
    """
    from src.utils.helpers import fetch_m3u8
    from src.core.hls_parser import parse_m3u8
    from src.core.segment_downloader import download_all
    from src.core.decryptor import decrypt_files

    audio_content = fetch_m3u8(audio_url, headers=headers, proxy=proxy)
    audio_playlist = parse_m3u8(audio_content, audio_url)

    if not audio_playlist.segments:
        raise Exception("音频轨道没有分片")

    audio_temp_dir = os.path.join(temp_dir, "audio")
    os.makedirs(audio_temp_dir, exist_ok=True)

    audio_files = download_all(
        audio_playlist.segments, audio_temp_dir, max_workers=workers,
        headers=headers, proxy=proxy, stop_check=stop_check,
    )

    if len(audio_files) != len(audio_playlist.segments):
        raise RuntimeError(
            f"音频分片数量不一致：预期 {len(audio_playlist.segments)}，实际 {len(audio_files)}"
        )

    if any(s.encryption_method for s in audio_playlist.segments):
        audio_files = decrypt_files(
            audio_files, audio_playlist.segments,
            headers, proxy,
            media_sequence=audio_playlist.media_sequence,
        )

    audio_output = os.path.join(temp_dir, "audio_merged.mp4")
    from src.core.merger import merge_ts_files
    if not merge_ts_files(audio_files, audio_output):
        raise Exception("音频合并失败")

    return audio_output


def run_download(task, tasks_dict, on_progress=None, resolution="最高分辨率"):
    """
    执行下载任务的主流程（在子线程中运行）
    自动检测 MP4/M3U8 格式，调用对应下载函数
    """
    from src.utils.helpers import fetch_m3u8, get_base_url, check_ffmpeg
    from src.core.hls_parser import parse_m3u8
    from src.core.segment_downloader import download_all, download_init_segment, _create_session
    from src.core.decryptor import decrypt_files
    from src.core.merger import merge_to_ts, merge_fmp4, mux_audio_video

    # 检测是否为 MP4 链接（仅当 URL 路径以 .mp4 结尾，排除路径中间含 .mp4 的 M3U8）
    from urllib.parse import urlparse
    _parsed = urlparse(task.url)
    if _parsed.path.rstrip('/').lower().endswith('.mp4'):
        from src.core.mp4_downloader import run_download_mp4
        run_download_mp4(task, tasks_dict, on_progress)
        return

    if task.status != "downloading":
        return
    try:
        task.status = "downloading"
        task.started_at = datetime.now()
        if on_progress:
            on_progress(task)
        if task._stop_flag:
            return

        if not check_ffmpeg():
            logger.warning("FFmpeg 未找到，将使用简单拼接模式（输出可能不是标准 MP4）")

        # 步骤1：获取并解析 m3u8 文件
        task.current_action = "解析m3u8..."
        if on_progress:
            on_progress(task)

        # 优先使用本地 M3U8 内容，否则从网络获取
        if task.local_m3u8_content:
            content = task.local_m3u8_content
            base_url = task.local_m3u8_base or get_base_url(task.url)
        else:
            content = fetch_m3u8(task.url, task.custom_headers, task.proxy)
            base_url = get_base_url(task.url)
        playlist = parse_m3u8(content, base_url)

        # 步骤2：如果是 Master Playlist，选择分辨率
        if playlist.is_master and playlist.streams:
            selected_idx = 0
            if resolution != "最高分辨率":
                for i, s in enumerate(playlist.streams):
                    name = s.name or f"{s.bandwidth}bps"
                    if name == resolution:
                        selected_idx = i
                        break
            else:
                selected_idx = max(range(len(playlist.streams)), key=lambda i: playlist.streams[i].bandwidth)
            task.resolution = playlist.streams[selected_idx].name or f"{playlist.streams[selected_idx].bandwidth}bps"

            # 解析音频轨道
            if playlist.audio_tracks:
                selected_audio = task.audio_track
                if selected_audio and selected_audio != "默认":
                    for at in playlist.audio_tracks:
                        if at.name == selected_audio or at.language == selected_audio:
                            task._audio_track_url = at.url
                            break
                if not task._audio_track_url and playlist.audio_tracks:
                    default_track = next(
                        (at for at in playlist.audio_tracks if at.default),
                        playlist.audio_tracks[0]
                    )
                    task._audio_track_url = default_track.url

            stream_url = playlist.streams[selected_idx].url
            # 子播放列表：本地文件内嵌套的用 base_url 拼接，否则从网络获取
            if task.local_m3u8_content:
                from urllib.parse import urljoin
                resolved = urljoin(base_url, stream_url) if not stream_url.startswith("http") else stream_url
                # 如果是本地文件，尝试读取同级目录的子 m3u8
                local_sub = resolved.replace("file:///", "").replace("file://", "")
                if os.path.isfile(local_sub):
                    with open(local_sub, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    base_url = get_base_url(resolved)
                else:
                    content = fetch_m3u8(resolved, task.custom_headers, task.proxy)
                    base_url = get_base_url(resolved)
            else:
                content = fetch_m3u8(stream_url, task.custom_headers, task.proxy)
                base_url = get_base_url(stream_url)
            playlist = parse_m3u8(content, base_url)

        if not playlist.segments:
            raise Exception("未找到TS分片")

        # HLS 兼容性检查
        if playlist.is_live:
            logger.warning("检测到直播流（无 EXT-X-ENDLIST），将下载当前可用的分片")
        if playlist.discontinuity_count > 0:
            logger.info(f"播放列表包含 {playlist.discontinuity_count} 个 DISCONTINUITY 标记")

        # 检测格式：fMP4 还是 TS
        is_fmp4 = False
        if playlist.init_segment_url:
            is_fmp4 = True
        elif playlist.segments:
            sample_url = playlist.segments[0].url.lower()
            fmp4_extensions = ('.m4s', '.cmfv', '.cmfa', '.cmf')
            if any(sample_url.endswith(ext) for ext in fmp4_extensions):
                is_fmp4 = True
            elif any(s.byterange for s in playlist.segments[:5]):
                is_fmp4 = True
            elif playlist.segments[0].init_segment_url:
                is_fmp4 = True

        # 步骤3：准备下载参数
        task.total_segments = len(playlist.segments)
        has_enc = any(s.encryption_method for s in playlist.segments)
        enc_info = " [AES-128]" if has_enc else ""
        task.current_action = f"下载中 {task.total_segments} 个分片{enc_info}..."
        if on_progress:
            on_progress(task)

        output_path = os.path.join(task.output_dir, task.output_name)
        stable_id = hashlib.md5(task.url.encode()).hexdigest()[:12]
        temp_dir = os.path.join(task.output_dir, f".m3u8_temp_{stable_id}")

        os.makedirs(temp_dir, exist_ok=True)

        dl_id = task._dl_id

        def stop_check():
            if task._pause_flag:
                task.status = "paused"
                task.current_action = "暂停中..."
                if on_progress:
                    on_progress(task)
            while task._pause_flag and not task._stop_flag:
                time.sleep(0.1)
            if task._stop_flag or task._dl_id != dl_id:
                task.status = "stopped"
                task.current_action = "已停止"
                if on_progress:
                    on_progress(task)
                return True
            if task.status == "paused":
                task.status = "downloading"
                task.current_action = "续传中..."
                if on_progress:
                    on_progress(task)
            return False

        def progress_callback(completed, total):
            task.downloaded_segments = completed
            task.total_segments = total
            task.progress = int((completed / total) * 100) if total > 0 else 0
            task.current_action = f"下载中 {completed}/{total}"
            if on_progress:
                on_progress(task)

        def speed_callback(completed, bytes_downloaded):
            if task.status != "downloading":
                return
            task.downloaded_segments = completed
            task.progress = int((completed / task.total_segments) * 100) if task.total_segments > 0 else 0
            if task.started_at:
                if isinstance(task.started_at, str):
                    task.started_at = datetime.fromisoformat(task.started_at)
                elapsed = (datetime.now() - task.started_at).total_seconds()
                if elapsed > 0:
                    task._download_speed = int(bytes_downloaded / elapsed)
                    # 计算剩余时间
                    total = task.total_segments
                    if completed > 0 and total > completed:
                        remaining_segs = total - completed
                        avg_time_per_seg = elapsed / completed
                        task._remaining_seconds = int(remaining_segs * avg_time_per_seg)
                    else:
                        task._remaining_seconds = 0
            if on_progress:
                on_progress(task)

        # 步骤4：下载分片
        if is_fmp4:
            # fMP4 下载流程
            if playlist.init_segment_url:
                task.current_action = "下载初始化段..."
                if on_progress:
                    on_progress(task)
                dl_session = _create_session(task.custom_headers, task.proxy)
                init_path = os.path.join(temp_dir, "init.mp4")
                br = playlist.init_segment_byterange
                ok = download_init_segment(playlist.init_segment_url, init_path, dl_session, byterange=br, stop_check=stop_check)
                dl_session.close()
                if not ok:
                    if task._stop_flag:
                        return
                    raise RuntimeError("初始化段下载失败")
            else:
                init_path = ""

            if task._stop_flag:
                return

            task.current_action = f"下载中 {task.total_segments} 个分片..."
            if on_progress:
                on_progress(task)

            ts_files = download_all(
                playlist.segments, temp_dir, max_workers=task.workers,
                headers=task.custom_headers, proxy=task.proxy,
                progress_callback=progress_callback, stop_check=stop_check,
                skip_indices=task._downloaded_indices, speed_callback=speed_callback,
            )

            if task._stop_flag:
                return
            if len(ts_files) != len(playlist.segments):
                raise RuntimeError(
                    f"分片数量不一致：预期 {len(playlist.segments)}，实际 {len(ts_files)}"
                )

            task.downloaded_segments = len(ts_files)
            task._downloaded_indices.clear()
            for ts_file in ts_files:
                filename = os.path.basename(ts_file)
                if filename.endswith('.m4s') or filename.endswith('.ts'):
                    try:
                        task._downloaded_indices.add(int(filename.split('.')[0]))
                    except ValueError:
                        pass

            if any(s.encryption_method for s in playlist.segments):
                task.current_action = "解密中..."
                if on_progress:
                    on_progress(task)
                ts_files = decrypt_files(ts_files, playlist.segments, task.custom_headers, task.proxy, media_sequence=playlist.media_sequence)

            if task._stop_flag:
                return

            task.current_action = "合并中..."
            task.progress = 95
            if on_progress:
                on_progress(task)
            final_path = merge_fmp4(init_path, ts_files, output_path)

        else:
            # TS 下载流程
            ts_files = download_all(
                playlist.segments, temp_dir, max_workers=task.workers,
                headers=task.custom_headers, proxy=task.proxy,
                progress_callback=progress_callback, stop_check=stop_check,
                skip_indices=task._downloaded_indices, speed_callback=speed_callback,
            )

            if task._stop_flag:
                return
            if len(ts_files) != len(playlist.segments):
                raise RuntimeError(
                    f"分片数量不一致：预期 {len(playlist.segments)}，实际 {len(ts_files)}"
                )

            task.downloaded_segments = len(ts_files)
            task._downloaded_indices.clear()
            for ts_file in ts_files:
                filename = os.path.basename(ts_file)
                if filename.endswith('.ts'):
                    try:
                        task._downloaded_indices.add(int(filename[:-3]))
                    except ValueError:
                        pass

            if any(s.encryption_method for s in playlist.segments):
                task.current_action = "解密中..."
                if on_progress:
                    on_progress(task)
                ts_files = decrypt_files(ts_files, playlist.segments, task.custom_headers, task.proxy, media_sequence=playlist.media_sequence)

            if task._stop_flag:
                return

            task.current_action = "合并中..."
            task.progress = 95
            if on_progress:
                on_progress(task)
            final_path = merge_to_ts(ts_files, output_path)

        # 音视频合并
        if task._audio_track_url and not task._stop_flag:
            task.current_action = "下载音频轨道..."
            task.progress = 88
            if on_progress:
                on_progress(task)
            try:
                audio_file = _download_audio_track(
                    task._audio_track_url, temp_dir,
                    task.workers, task.custom_headers, task.proxy,
                    stop_check=stop_check,
                )
                if not task._stop_flag and os.path.exists(audio_file):
                    task.current_action = "合并音视频..."
                    task.progress = 93
                    if on_progress:
                        on_progress(task)
                    muxed_path = os.path.join(temp_dir, "muxed_output.mp4")
                    final_path = mux_audio_video(final_path, audio_file, muxed_path)
            except Exception as e:
                logger.warning(f"音频轨道处理失败（视频仍可用）: {e}")

        # 验证输出
        if not os.path.exists(final_path):
            raise Exception(f"输出文件不存在: {final_path}")

        file_size = os.path.getsize(final_path)
        if file_size == 0:
            raise Exception("输出文件大小为 0，合并可能失败")

        try:
            with open(final_path, "rb") as f:
                header = f.read(12)
            is_valid_mp4 = header[4:8] == b'ftyp'
            is_valid_ts = header[0] == 0x47
            if not is_valid_mp4 and not is_valid_ts:
                logger.warning(f"输出文件头不识别: {header[:8].hex()}，可能不是有效的视频文件")
        except Exception:
            pass

        if playlist.total_duration > 0:
            min_expected = playlist.total_duration * 1024
            if file_size < min_expected:
                logger.warning(f"输出文件偏小: {file_size} 字节，预期至少 {min_expected:.0f} 字节")

        # 文件完整性验证
        from src.utils.helpers import verify_media_file
        task.current_action = "验证文件..."
        if on_progress:
            on_progress(task)
        task.verification = verify_media_file(final_path)

        task.current_action = f"合并完成 ({file_size / (1024*1024):.1f} MB)"
        if on_progress:
            on_progress(task)

        # 清理临时文件
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

        # 标记任务完成
        task.status = "completed"
        task.progress = 100
        task.current_action = "完成"
        task.output_path = final_path
        task.finished_at = datetime.now().isoformat()
        if on_progress:
            on_progress(task)
        save_tasks(tasks_dict, TASKS_HISTORY_FILE)

    except Exception as e:
        if task._stop_flag:
            task.status = "stopped"
            task.current_action = "已停止"
        else:
            task.status = "failed"
            task.error = str(e)
            task.current_action = "失败"
        task.finished_at = datetime.now().isoformat()
        if on_progress:
            on_progress(task)
        save_tasks(tasks_dict, TASKS_HISTORY_FILE)
