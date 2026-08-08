"""统一任务执行层：管理下载任务的生命周期"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def start_download(task, tasks_dict, on_progress=None, resolution="最高分辨率"):
    """
    启动下载任务（在子线程中运行）

    自动检测 MP4/M3U8 格式，调用对应下载函数。
    """
    import threading
    import re

    def _run():
        try:
            task._dl_id += 1

            from urllib.parse import urlparse as _urlparse
            _path = _urlparse(task.url).path.rstrip('/').lower()
            if _path.endswith('.mp4'):
                from src.core.mp4_downloader import run_download_mp4
                run_download_mp4(task, tasks_dict, on_progress)
            else:
                from src.core.hls_downloader import run_download
                run_download(task, tasks_dict, on_progress, resolution)
        except Exception as e:
            logger.error(f"任务执行异常: {e}")
            task.status = "failed"
            task.error = str(e)
            task.current_action = "失败"
            task.finished_at = datetime.now()
            if on_progress:
                on_progress(task)

    task.status = "downloading"
    task.error = None
    task.finished_at = None

    if task.started_at is None:
        task.started_at = datetime.now()

    thread = threading.Thread(target=_run, daemon=True)
    task._thread = thread
    thread.start()
    return thread
