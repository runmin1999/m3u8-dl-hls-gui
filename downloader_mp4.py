"""MP4 直接下载模块：多线程 Range 下载，支持断点续传"""

import os
import time
import logging
import threading
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, wait
from utils import save_tasks, TASKS_HISTORY_FILE

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def run_download_mp4(task, tasks_dict, on_progress=None):
    """MP4 多线程下载（支持断点续传、Range 分块）"""
    if task.status != "downloading":
        return
    try:
        task.status = "downloading"
        task.started_at = datetime.now()
        task._dl_id = getattr(task, '_dl_id', 0) + 1
        my_dl_id = task._dl_id
        if on_progress:
            on_progress(task)
        if task._stop_flag:
            return

        task.current_action = "连接服务器..."
        if on_progress:
            on_progress(task)

        output_path = os.path.join(task.output_dir, task.output_name)
        tmp_path = output_path + ".tmp"
        headers = dict(DEFAULT_HEADERS)
        headers.update(task.custom_headers)
        proxies = {"http": task.proxy, "https": task.proxy} if task.proxy else None

        # 探测文件大小和支持 Range
        total_size = 0
        accept_ranges = False

        try:
            resp = requests.head(task.url, headers=headers, timeout=15, proxies=proxies)
            if resp.status_code == 200:
                total_size = int(resp.headers.get('content-length', 0))
                accept_ranges = resp.headers.get('accept-ranges', '') == 'bytes'
        except Exception:
            pass

        if total_size == 0:
            try:
                resp = requests.get(task.url, headers=headers, timeout=15, stream=True, proxies=proxies)
                if resp.status_code == 200:
                    total_size = int(resp.headers.get('content-length', 0))
                    accept_ranges = resp.headers.get('accept-ranges', '') == 'bytes'
                resp.close()
            except Exception:
                pass

        if total_size > 0 and not accept_ranges:
            try:
                test_headers = dict(headers)
                test_headers["Range"] = "bytes=0-0"
                resp = requests.get(task.url, headers=test_headers, timeout=15, proxies=proxies)
                if resp.status_code == 206:
                    accept_ranges = True
                resp.close()
            except Exception:
                pass

        task.total_segments = total_size if total_size > 0 else 0

        # 断点续传检查
        existing_size = getattr(task, '_mp4_downloaded', 0)
        downloaded = 0
        if existing_size > 0 and os.path.exists(tmp_path) and accept_ranges:
            file_size = os.path.getsize(tmp_path)
            if file_size == existing_size:
                downloaded = file_size
            else:
                existing_size = 0
        elif existing_size > 0 and not accept_ranges:
            existing_size = 0

        task.downloaded_segments = downloaded
        task.current_action = "下载中..."
        if on_progress:
            on_progress(task)

        workers = task.workers if accept_ranges and total_size > 0 else 1
        chunk_size = total_size // workers if total_size > 0 and workers > 1 else 0

        def download_chunk(start, end, chunk_idx, file_lock):
            if task._stop_flag or task._dl_id != my_dl_id:
                return None
            chunk_session = requests.Session()
            chunk_session.headers.update(headers)
            if proxies:
                chunk_session.proxies.update(proxies)
            chunk_headers = dict(headers)
            chunk_headers["Range"] = f"bytes={start}-{end}"
            try:
                resp = chunk_session.get(task.url, headers=chunk_headers, timeout=30, stream=True)
                resp.raise_for_status()
                chunk_downloaded = 0
                local_bytes = 0
                for c in resp.iter_content(chunk_size=65536):
                    if task._stop_flag or task._dl_id != my_dl_id:
                        resp.close()
                        chunk_session.close()
                        return None
                    while task._pause_flag and not task._stop_flag and task._dl_id == my_dl_id:
                        time.sleep(0.1)
                    if c:
                        with file_lock:
                            with open(tmp_path, "r+b" if os.path.exists(tmp_path) else "wb") as f:
                                f.seek(start + chunk_downloaded)
                                f.write(c)
                        chunk_downloaded += len(c)
                        local_bytes += len(c)
                    if local_bytes >= 524288:
                        with _dl_lock:
                            _dl_downloaded[0] += local_bytes
                            task.downloaded_segments = _dl_downloaded[0]
                            task._mp4_downloaded = _dl_downloaded[0]
                            if total_size > 0:
                                task.progress = int((_dl_downloaded[0] / total_size) * 100)
                            if task.started_at:
                                elapsed = (datetime.now() - task.started_at).total_seconds()
                                if elapsed > 0:
                                    task._download_speed = int(_dl_downloaded[0] / elapsed)
                        local_bytes = 0
                resp.close()
                chunk_session.close()
                return (chunk_idx, chunk_downloaded)
            except Exception:
                chunk_session.close()
                return None

        if workers > 1 and chunk_size > 0:
            task.current_action = f"多线程下载中 ({workers}线程)..."
            if on_progress:
                on_progress(task)

            chunks = []
            for i in range(workers):
                start = downloaded + i * chunk_size
                end = start + chunk_size - 1 if i < workers - 1 else total_size - 1
                if start < total_size:
                    chunks.append((start, end, i))

            if not (downloaded > 0 and os.path.exists(tmp_path)):
                with open(tmp_path, "wb") as f:
                    if total_size > 0:
                        f.truncate(total_size)

            completed_chunks = 0
            _dl_downloaded = [downloaded]
            _dl_lock = threading.Lock()
            _file_lock = threading.Lock()

            executor = ThreadPoolExecutor(max_workers=workers)
            futures = {executor.submit(download_chunk, s, e, i, _file_lock): (s, e, i) for s, e, i in chunks}

            pending = set(futures.keys())
            while pending and not task._stop_flag:
                done, pending = wait(pending, timeout=0.5, return_when="FIRST_COMPLETED")
                for future in done:
                    result = future.result()
                    if result:
                        completed_chunks += 1
                        task.current_action = f"下载中... {completed_chunks}/{len(chunks)} 块"
                        if on_progress:
                            on_progress(task)

            if task._stop_flag:
                for f in pending:
                    f.cancel()
                task._mp4_downloaded = _dl_downloaded[0]
                save_tasks(tasks_dict, TASKS_HISTORY_FILE)

            executor.shutdown(wait=False)
        else:
            session = requests.Session()
            session.headers.update(headers)
            if proxies:
                session.proxies.update(proxies)

            req_headers = dict(headers)
            if downloaded > 0:
                req_headers["Range"] = f"bytes={downloaded}-"

            resp = session.get(task.url, headers=req_headers, timeout=60, stream=True)
            if downloaded > 0 and resp.status_code == 200:
                downloaded = 0
            resp.raise_for_status()

            write_mode = "ab" if downloaded > 0 and resp.status_code == 206 else "wb"
            with open(tmp_path, write_mode) as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if task._stop_flag:
                        break
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        task.downloaded_segments = downloaded
                        task._mp4_downloaded = downloaded
                        if total_size > 0:
                            task.progress = int((downloaded / total_size) * 100)
                        if task.started_at:
                            elapsed = (datetime.now() - task.started_at).total_seconds()
                            if elapsed > 0:
                                task._download_speed = int(downloaded / elapsed)
                        if on_progress:
                            on_progress(task)

        if task._stop_flag:
            save_tasks(tasks_dict, TASKS_HISTORY_FILE)
            return

        os.replace(tmp_path, output_path)

        task.status = "completed"
        task.progress = 100
        task.current_action = "完成"
        task.output_path = output_path
        task._mp4_downloaded = 0
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
