"""多线程下载模块：并发下载 TS 分片，支持进度显示和断点续传"""

import os
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Callable

import requests
from requests.adapters import HTTPAdapter

from m3u8_parser import Segment

logger = logging.getLogger(__name__)

# 默认请求头
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Connection": "keep-alive",
}

# 单个分片最大重试次数
MAX_RETRIES = 5
# 请求超时（秒）
REQUEST_TIMEOUT = 30
# 内部轮询间隔（秒），用于快速响应暂停/停止
POLL_INTERVAL = 0.5


def _create_session(headers: dict, proxy: str) -> requests.Session:
    """创建带连接池的 Session，提升并发下载速度"""
    session = requests.Session()
    session.headers.update({**DEFAULT_HEADERS, **(headers or {})})
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    # 连接池大小 = 并发数，避免频繁创建/销毁连接
    adapter = HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=0)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session


def download_segment(
    segment: Segment,
    save_path: str,
    session: requests.Session,
    stop_check: Optional[Callable[[], bool]] = None,
) -> bool:
    """
    下载单个 TS 分片（原子写入：写 .tmp 完成后重命名）

    Args:
        segment: 分片信息
        save_path: 保存路径
        session: 复用的 requests Session（含连接池）
        stop_check: 停止检查函数，返回 True 表示需要停止

    Returns:
        是否下载成功
    """
    # 临时文件路径（原子写入用）
    tmp_path = save_path + ".tmp"

    for attempt in range(1, MAX_RETRIES + 1):
        # 每次重试前检查是否需要停止/暂停
        if stop_check and stop_check():
            return False

        try:
            # 如果存在旧的 tmp 文件或目标文件被锁，先清理
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            if os.path.exists(save_path):
                try:
                    os.remove(save_path)
                except OSError:
                    pass

            resp = session.get(
                segment.url,
                timeout=REQUEST_TIMEOUT,
                stream=True,
            )
            resp.raise_for_status()
            # 写入临时文件
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                    # 流式下载中检查暂停/停止（每 64KB 检查一次）
                    if stop_check and stop_check():
                        return False
            # 写完成后原子重命名
            os.replace(tmp_path, save_path)
            return True
        except (requests.RequestException, OSError, PermissionError) as e:
            # 清理可能残留的 temp 文件
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            if attempt < MAX_RETRIES:
                wait = attempt * 1
                logger.debug(f"分片 {segment.index} 下载失败（第{attempt}次），{wait}秒后重试: {e}")
                time.sleep(wait)
            else:
                logger.error(f"分片 {segment.index} 下载失败，已达最大重试次数: {e}")
    return False


def download_all(
    segments: List[Segment],
    temp_dir: str,
    max_workers: int = 20,
    headers: dict = None,
    proxy: str = "",
    progress_callback: Optional[Callable[[int, int], None]] = None,
    stop_check: Optional[Callable[[], bool]] = None,
    skip_indices: Optional[set] = None,
    speed_callback: Optional[Callable[[int, int], None]] = None,
) -> List[str]:
    """
    多线程下载所有 TS 分片

    Args:
        segments: 分片列表
        temp_dir: 临时目录
        max_workers: 最大并发数
        headers: 自定义请求头
        proxy: 代理地址
        progress_callback: 进度回调函数 (completed_count, total_count)
        stop_check: 停止检查函数，返回 True 表示需要停止
        skip_indices: 要跳过的已下载分片索引集合
        speed_callback: 速度回调函数 (completed_count, bytes_downloaded)

    Returns:
        成功下载的分片文件路径列表（按分片顺序排列）
    """
    try:
        os.makedirs(temp_dir, exist_ok=True)
    except OSError as e:
        logger.error(f"无法创建临时目录 {temp_dir}: {e}")
        raise

    total = len(segments)
    completed = 0
    results: dict = {}
    failed: List[int] = []
    bytes_downloaded = 0  # 累计下载字节数
    new_bytes_downloaded = 0  # 仅新下载的字节数（用于更准确的速度计算）

    # 线程安全锁
    _lock = threading.Lock()

    # 构建任务：跳过已完整下载的分片（原子写入，.ts 存在即完整）
    tasks = []
    skip_indices = skip_indices or set()
    for seg in segments:
        # 跳过已下载的分片（基于索引集合），但需验证文件实际存在
        if seg.index in skip_indices:
            fp = os.path.join(temp_dir, f"{seg.index:06d}.ts")
            if os.path.exists(fp) and os.path.getsize(fp) > 0:
                results[seg.index] = fp
                completed += 1
                continue
            # 文件不存在，从跳过集合中移除，重新下载
            skip_indices.discard(seg.index)

        filename = f"{seg.index:06d}.ts"
        filepath = os.path.join(temp_dir, filename)
        tmp_path = filepath + ".tmp"
        # 清理上次残留的临时文件
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            results[seg.index] = filepath
            completed += 1
            # 不将已有文件大小计入 bytes_downloaded（速度只计算新下载的）
        else:
            tasks.append((seg, filepath))

    if completed > 0 and progress_callback:
        progress_callback(completed, total)

    if not tasks:
        return _build_result_list(results, segments)

    logger.info(f"开始下载 {len(tasks)} 个分片，并发数 {max_workers}")

    # 创建复用 Session（带连接池）
    session = _create_session(headers, proxy)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {}
        for seg, filepath in tasks:
            # 每次提交前检查是否需要停止
            if stop_check and stop_check():
                logger.info("检测到停止信号，取消剩余下载任务")
                break
            future = executor.submit(
                download_segment, seg, filepath, session, stop_check
            )
            future_map[future] = (seg, filepath)

            # 每提交一个任务后也检查一次，防止用户快速点击暂停
            if stop_check and stop_check():
                logger.info("检测到停止信号，取消剩余下载任务")
                # 取消所有未完成的任务
                for f in future_map.keys():
                    f.cancel()
                future_map.clear()
                break

        for future in as_completed(future_map):
            seg, filepath = future_map[future]
            # 每次处理完一个分片后检查是否需要暂停/停止
            if stop_check and stop_check():
                logger.info("检测到停止信号，取消剩余下载任务")
                # 取消所有未完成的任务
                for f in future_map:
                    if not f.done():
                        f.cancel()
                future_map.clear()
                break
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
                    new_bytes_downloaded += seg_size
                    # 每下载一个分片就调用 speed_callback 更新速度和进度
                    if speed_callback:
                        speed_callback(completed, bytes_downloaded)
                else:
                    failed.append(seg.index)

            if progress_callback:
                progress_callback(completed, total)

    session.close()

    if speed_callback:
        speed_callback(completed, bytes_downloaded)

    if failed:
        logger.warning(f"{len(failed)} 个分片下载失败: {failed}")

    return _build_result_list(results, segments)


def _build_result_list(results: dict, segments: List[Segment]) -> List[str]:
    """按分片顺序构建结果列表"""
    ordered = []
    for seg in segments:
        if seg.index in results:
            ordered.append(results[seg.index])
    return ordered
