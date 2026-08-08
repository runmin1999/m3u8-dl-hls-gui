"""MP4 直接下载模块：curl 直链下载，支持自动重试和断点续传"""

import os
import re
import time
import shutil
import logging
import subprocess
import threading
from datetime import datetime
from src.utils.helpers import save_tasks, TASKS_HISTORY_FILE

logger = logging.getLogger(__name__)

CURL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def _find_curl():
    """通过 PATH 查找 curl 可执行文件，返回实际路径"""
    candidates = ("curl.exe", "curl") if os.name == "nt" else ("curl", "curl.exe")
    for name in candidates:
        path = shutil.which(name)
        if path:
            return path
    return None


def _build_curl_cmd(task, output_path, curl_path, parallel_max=8, resume=False):
    """构建 curl 命令行"""
    cmd = [
        curl_path,
        "-L",
        "-sS",
        "--fail",
        "-o", output_path,
        "--connect-timeout", "15",
        "--retry", "5",
        "--retry-delay", "3",
        "--retry-all-errors",
    ]

    if resume and os.path.exists(output_path):
        cmd.extend(["-C", "-"])

    cmd.extend(["--parallel", "--parallel-max", str(parallel_max), "--parallel-immediate"])

    if task.proxy:
        cmd.extend(["-x", task.proxy])

    headers = dict(CURL_HEADERS)
    headers.update(task.custom_headers)
    for k, v in headers.items():
        cmd.extend(["-H", f"{k}: {v}"])

    cmd.append(task.url)
    return cmd


def _get_file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _get_parallel_max(fallback=8):
    """从 config.json 读取 parallel_max，范围 1-32"""
    try:
        from src.utils.helpers import load_config, get_base_dir
        config_file = os.path.join(get_base_dir(), "config.json")
        config = load_config(config_file)
        return max(1, min(32, config.get("parallel_max", fallback)))
    except Exception:
        return fallback


def _is_download_complete(return_code, final_size, total_size):
    """判断下载是否完成"""
    if return_code != 0:
        return False
    if final_size <= 0:
        return False
    if total_size <= 0:
        # 无 Content-Length：curl 返回 0 且文件非空即完成
        return True
    return final_size >= total_size


def run_download_mp4(task, tasks_dict, on_progress=None):
    """MP4 下载（curl 直链，自动续传）"""
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

        task.current_action = "检测 curl..."
        if on_progress:
            on_progress(task)

        curl_path = _find_curl()
        if not curl_path:
            task.status = "failed"
            task.error = "未找到 curl，请安装 curl 或添加到 PATH"
            task.current_action = "失败"
            task.finished_at = datetime.now().isoformat()
            if on_progress:
                on_progress(task)
            save_tasks(tasks_dict, TASKS_HISTORY_FILE)
            return

        output_path = os.path.join(task.output_dir, task.output_name)
        tmp_path = output_path + ".tmp"
        parallel_max = _get_parallel_max(task.workers)

        task.current_action = "获取文件信息..."
        if on_progress:
            on_progress(task)

        total_size = _probe_size(task, curl_path)
        task.total_segments = total_size if total_size > 0 else 0

        task.downloaded_segments = 0
        task.current_action = "下载中..."
        if on_progress:
            on_progress(task)

        MAX_RETRIES = 10
        retry_count = 0

        while retry_count < MAX_RETRIES:
            if task._stop_flag or task._dl_id != my_dl_id:
                save_tasks(tasks_dict, TASKS_HISTORY_FILE)
                return

            # 断点续传：检查已有下载
            existing_size = _get_file_size(tmp_path)
            can_resume = existing_size > 0 and total_size > 0 and existing_size < total_size

            if can_resume and retry_count > 0:
                task.current_action = f"续传中... ({_fmt_size(existing_size)}/{_fmt_size(total_size)})"
            elif retry_count > 0:
                task.current_action = f"重试下载... (第{retry_count}次)"
            else:
                task.current_action = "下载中..."
            if on_progress:
                on_progress(task)

            cmd = _build_curl_cmd(task, tmp_path, curl_path, parallel_max=parallel_max, resume=can_resume)
            logger.info(f"curl cmd: {' '.join(cmd[:8])}...")

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )

            # 监控下载进度
            downloaded = _get_file_size(tmp_path)
            speed_samples = []
            last_size_check = time.time()
            stall_count = 0

            while True:
                if task._stop_flag or task._dl_id != my_dl_id:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    task.status = "stopped"
                    task.current_action = "已停止"
                    task._mp4_downloaded = 0
                    task._remaining_seconds = 0
                    if on_progress:
                        on_progress(task)
                    save_tasks(tasks_dict, TASKS_HISTORY_FILE)
                    return

                if task._pause_flag:
                    task.current_action = "暂停中..."
                    task.status = "paused"
                    if on_progress:
                        on_progress(task)
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    while task._pause_flag and not task._stop_flag and task._dl_id == my_dl_id:
                        time.sleep(0.3)
                    if task._stop_flag or task._dl_id != my_dl_id:
                        task.status = "stopped"
                        task.current_action = "已停止"
                        task._mp4_downloaded = 0
                        task._remaining_seconds = 0
                        if on_progress:
                            on_progress(task)
                        save_tasks(tasks_dict, TASKS_HISTORY_FILE)
                        return
                    task.status = "downloading"
                    task.current_action = "续传中..."
                    if on_progress:
                        on_progress(task)
                    break  # 跳出内层循环，外层 while 会重新启动 curl

                current_size = _get_file_size(tmp_path)
                now = time.time()

                if current_size > downloaded:
                    speed_samples.append((current_size - downloaded, now - last_size_check))
                    if len(speed_samples) > 10:
                        speed_samples.pop(0)
                    downloaded = current_size
                    stall_count = 0
                else:
                    stall_count += 1

                last_size_check = now

                task.downloaded_segments = downloaded
                task._mp4_downloaded = downloaded

                if total_size > 0:
                    task.progress = min(int((downloaded / total_size) * 100), 99)

                if speed_samples:
                    recent = speed_samples[-5:]
                    bytes_in = sum(s[0] for s in recent)
                    time_in = sum(s[1] for s in recent)
                    if time_in > 0:
                        task._download_speed = int(bytes_in / time_in)
                        # 计算剩余时间
                        if total_size > 0 and downloaded > 0:
                            remaining_bytes = total_size - downloaded
                            task._remaining_seconds = int(remaining_bytes / task._download_speed) if task._download_speed > 0 else 0
                        else:
                            task._remaining_seconds = 0

                if on_progress:
                    on_progress(task)

                # 检查 curl 是否结束
                if proc.poll() is not None:
                    break

                time.sleep(0.5)

            # curl 已退出，获取退出码和 stderr
            try:
                _, stderr_data = proc.communicate(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
                stderr_data = b""
            return_code = proc.returncode

            if return_code != 0:
                err_tail = stderr_data.decode("utf-8", errors="replace")[-500:]
                logger.warning(f"curl 退出码 {return_code}: {err_tail}")

            final_size = _get_file_size(tmp_path)

            # 下载完成判断
            if _is_download_complete(return_code, final_size, total_size):
                os.replace(tmp_path, output_path)

                # 文件完整性验证
                from src.utils.helpers import verify_media_file
                task.current_action = "验证文件..."
                if on_progress:
                    on_progress(task)
                task.verification = verify_media_file(output_path)

                task.status = "completed"
                task.progress = 100
                task.current_action = "完成"
                task.output_path = output_path
                task._mp4_downloaded = 0
                task._remaining_seconds = 0
                task.finished_at = datetime.now().isoformat()
                if on_progress:
                    on_progress(task)
                save_tasks(tasks_dict, TASKS_HISTORY_FILE)
                return

            # 文件大小为0，没有生成文件
            if final_size == 0 and not os.path.exists(tmp_path):
                retry_count += 1
                _interruptible_wait(2, task, my_dl_id)
                continue

            # 下载不完整，重试
            retry_count += 1
            if retry_count < MAX_RETRIES:
                _interruptible_wait(2, task, my_dl_id)
                continue

            # 重试次数用完
            if total_size > 0:
                task.status = "failed"
                task.error = f"下载不完整：{_fmt_size(final_size)}/{_fmt_size(total_size)}"
            else:
                task.status = "failed"
                task.error = f"下载失败：{_fmt_size(final_size)}"
            task.current_action = "失败"
            task._mp4_downloaded = 0
            task.finished_at = datetime.now().isoformat()
            if on_progress:
                on_progress(task)
            save_tasks(tasks_dict, TASKS_HISTORY_FILE)
            return

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


def _interruptible_wait(seconds, task, dl_id):
    """可中断的等待，检查停止信号和 dl_id"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if task._stop_flag or task._dl_id != dl_id:
            return
        remaining = deadline - time.monotonic()
        time.sleep(min(0.3, max(0, remaining)))


def _probe_size(task, curl_path):
    """探测文件大小"""
    try:
        cmd = [
            curl_path, "-sI", "-L",
            "--connect-timeout", "10", "--max-time", "15",
        ]
        if task.proxy:
            cmd.extend(["-x", task.proxy])
        headers = dict(CURL_HEADERS)
        headers.update(task.custom_headers)
        for k, v in headers.items():
            cmd.extend(["-H", f"{k}: {v}"])
        cmd.append(task.url)

        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        output = r.stdout + r.stderr
        match = re.search(r"content-length:\s*(\d+)", output, re.IGNORECASE)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return 0


def _fmt_size(n):
    """格式化文件大小"""
    if n < 1024:
        return f"{n}B"
    elif n < 1024 * 1024:
        return f"{n/1024:.0f}KB"
    elif n < 1024 * 1024 * 1024:
        return f"{n/1024/1024:.1f}MB"
    else:
        return f"{n/1024/1024/1024:.2f}GB"
