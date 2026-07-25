"""合并模块：将 TS 分片合并为 TS 文件"""

import os
import shutil
import logging
from typing import List

logger = logging.getLogger(__name__)


def merge_ts_files(ts_files: List[str], output_path: str) -> bool:
    """
    直接拼接 TS 分片

    Args:
        ts_files: TS 分片文件路径列表（需按顺序）
        output_path: 输出文件路径

    Returns:
        是否成功
    """
    try:
        with open(output_path, "wb") as out_f:
            for ts_file in ts_files:
                with open(ts_file, "rb") as in_f:
                    shutil.copyfileobj(in_f, out_f)
        logger.info(f"合并完成: {output_path}")
        return True
    except Exception as e:
        logger.error(f"合并失败: {e}")
        return False


def merge_to_ts(
    ts_files: List[str],
    output_path: str,
) -> str:
    """
    将 TS 分片合并为 TS 文件

    Args:
        ts_files: TS 分片文件路径列表
        output_path: 输出文件路径

    Returns:
        最终输出文件路径
    """
    if not ts_files:
        raise ValueError("没有可合并的分片文件")

    # 清理输出路径中的非法字符
    illegal_chars = '<>:"/\\|?*\n\r\t'
    dir_part = os.path.dirname(output_path)
    file_part = os.path.basename(output_path)
    for ch in illegal_chars:
        file_part = file_part.replace(ch, '_')
    file_part = file_part.strip('. ')
    if not file_part:
        file_part = "output.ts"
    output_path = os.path.join(dir_part, file_part)

    # 确保扩展名是 .ts
    if not output_path.lower().endswith(".ts"):
        output_path += ".ts"

    if not merge_ts_files(ts_files, output_path):
        raise RuntimeError("TS 分片合并失败")

    return output_path
