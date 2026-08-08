"""合并模块：使用 FFmpeg 将 TS 分片 remux 为 MP4，支持 fMP4 拼接和音视频 mux"""

import os
import shutil
import logging
import subprocess
import threading
from typing import List

logger = logging.getLogger(__name__)

# 限制同时合并的 FFmpeg 进程数（避免多任务同时合并时磁盘 I/O 竞争）
# 默认 2，可通过 config.json 中的 ffmpeg_concurrency 配置
_merge_semaphore = None
_merge_concurrency = 0


def _get_merge_semaphore():
    """获取合并信号量，支持从 config 动态读取并发数"""
    global _merge_semaphore, _merge_concurrency
    from src.utils.helpers import load_config, get_base_dir
    config_file = os.path.join(get_base_dir(), "config.json")
    config = load_config(config_file)
    new_limit = max(1, min(16, config.get("ffmpeg_concurrency", 2)))
    if _merge_semaphore is None or new_limit != _merge_concurrency:
        _merge_semaphore = threading.Semaphore(new_limit)
        _merge_concurrency = new_limit
    return _merge_semaphore


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
        init_path: 初始化段文件路径（可为空）
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
        "-c", "copy",
        "-movflags", "+faststart",
        output_path,
    ]

    semaphore = _get_merge_semaphore()
    semaphore.acquire()
    try:
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        result = subprocess.run(
            cmd, capture_output=True, timeout=600,
            startupinfo=startupinfo,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("FFmpeg mux 超时")
    except FileNotFoundError:
        raise RuntimeError("未找到 FFmpeg")
    finally:
        semaphore.release()

    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace")[-500:]
        logger.error(f"FFmpeg mux 失败: {err}")
        raise RuntimeError("FFmpeg mux 失败")

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("FFmpeg mux 输出文件为空")

    logger.info(f"FFmpeg mux 完成: {output_path}")
    return output_path


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

        # 限制并发合并数，避免磁盘 I/O 竞争
        semaphore = _get_merge_semaphore()
        semaphore.acquire()
        try:
            # 执行 FFmpeg（隐藏控制台窗口）
            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=3600,  # 1 小时超时（大文件可能需要很长时间）
                startupinfo=startupinfo,
            )
        finally:
            semaphore.release()

        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="replace")[-500:]
            logger.error(f"FFmpeg remux 失败: {err}")
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
