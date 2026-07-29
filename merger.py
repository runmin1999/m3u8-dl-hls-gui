"""合并模块：使用 FFmpeg 将 TS 分片 remux 为 MP4"""

import os
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
