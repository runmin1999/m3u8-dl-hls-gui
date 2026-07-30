"""m3u8-dl-hls-gui v0.15 - CustomTkinter 桌面应用"""

import os
import sys
import json
import threading
import hashlib
import logging
import shutil
import time
import re
import customtkinter as ctk
from tkinter import filedialog, messagebox
from datetime import datetime

import subprocess
import requests

# 将当前目录加入 sys.path，确保模块导入正常
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import fetch_m3u8, get_base_url, format_speed, load_config, save_config, save_tasks, load_tasks


# ── 获取基础目录（兼容 PyInstaller 打包） ──
def get_base_dir():
    """获取应用基础目录，兼容 PyInstaller 打包"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包模式，使用 exe 所在目录
        return os.path.dirname(sys.executable)
    else:
        # 正常 Python 运行模式，使用脚本所在目录
        return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()

# ── 日志配置：记录到 Logs 文件夹 ──
LOGS_DIR = os.path.join(BASE_DIR, "Logs")
os.makedirs(LOGS_DIR, exist_ok=True)


def setup_logging():
    """配置日志，同时输出到控制台和文件"""
    now = datetime.now()
    # 日志文件名格式：年-月-日_时-分-秒-毫秒.log
    log_filename = now.strftime(f"{now.year}-{now.month:02d}-{now.day:02d}_{now.hour:02d}-{now.minute:02d}-{now.second:03d}-{now.microsecond // 1000:03d}.log")
    log_file = os.path.join(LOGS_DIR, log_filename)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    # 文件处理器：写入日志文件
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    # 控制台处理器：输出到终端
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


from m3u8_parser import parse_m3u8
from downloader import download_all
from decryptor import decrypt_files
from merger import merge_to_ts

logger = logging.getLogger(__name__)

# 任务历史记录和配置文件路径
TASKS_HISTORY_FILE = os.path.join(BASE_DIR, "tasks_history.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# ── UI 配色方案（暗色主题） ──
COLORS = {
    "bg": "#0a0a0f",        # 主背景色（深黑）
    "card": "#1a1a24",      # 卡片背景色
    "input": "#0f0f15",     # 输入框背景色
    "border": "#2a2a3a",    # 边框色
    "text": "#ffffff",       # 主文字色（白色）
    "text2": "#a0a0b0",     # 次要文字色（灰色）
    "muted": "#606070",     # 弱化文字色
    "accent": "#6366f1",    # 主题色（紫色）
    "accent2": "#818cf8",   # 主题色变体
    "success": "#22c55e",   # 成功色（绿色）
    "error": "#ef4444",     # 错误色（红色）
    "warning": "#f59e0b",   # 警告色（橙色）
    "grad1": "#6366f1",     # 渐变起始色
    "grad2": "#8b5cf6",     # 渐变结束色
}

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def get_default_output_dir():
    """获取默认下载目录（应用目录下的 Downloads 文件夹）"""
    downloads = os.path.join(BASE_DIR, "Downloads")
    os.makedirs(downloads, exist_ok=True)
    return downloads


class DownloadTask:
    """下载任务数据模型，保存单个下载任务的所有状态"""

    def __init__(self, task_id, url, output_name, output_dir, workers, proxy, custom_headers):
        self.task_id = task_id                          # 任务唯一 ID（时间戳生成）
        self.url = url                                  # m3u8 链接
        self.output_name = output_name or "output.mp4"   # 输出文件名
        self.output_dir = output_dir or get_default_output_dir()  # 输出目录
        self.workers = workers                          # 并发线程数
        self.proxy = proxy                              # 代理地址
        self.custom_headers = custom_headers or {}      # 自定义请求头
        self.status = "pending"                         # 任务状态：pending/downloading/completed/failed/stopped/paused
        self.progress = 0                               # 下载进度百分比（0-100）
        self.total_segments = 0                         # 总分片数
        self.downloaded_segments = 0                    # 已下载分片数
        self.current_action = ""                        # 当前操作描述（显示在 UI 上）
        self.error = None                               # 错误信息
        self.output_path = None                         # 最终输出文件路径
        self.resolution = ""                            # 当前下载的分辨率
        self.started_at = None                          # 开始时间
        self.finished_at = None                         # 结束时间
        self._stop_flag = False                         # 停止标志（线程间通信）
        self._pause_flag = False                        # 暂停标志（线程间通信）
        self._thread = None                             # 下载线程引用
        self._downloaded_indices = set()                # 已下载分片索引集合（用于 M3U8 断点续传）
        self._mp4_downloaded = 0                        # MP4 已下载字节数（用于 MP4 断点续传）
        self._download_speed = 0                        # 下载速度（字节/秒）
        self._dl_id = 0                                 # 下载会话 ID（用于区分新旧线程）
        self.available_resolutions = ["最高分辨率"]     # 可用分辨率列表
        self.audio_track = ""                           # 选择的音频轨道名称
        self.available_audio_tracks = []                # 可用音频轨道列表
        self._audio_track_url = ""                      # 选中的音频轨道 m3u8 URL

    @property
    def download_speed(self):
        return self._download_speed

    def stop(self):
        """停止任务（设置停止标志，下载线程会在下一个检查点退出）"""
        self._stop_flag = True
        self.status = "stopped"
        self.current_action = "已停止"

    def pause(self):
        """暂停任务（设置暂停标志，下载线程会进入等待循环）"""
        self._pause_flag = True
        self.status = "paused"
        self.current_action = "已暂停"

    def continue_task(self):
        """继续任务（清除停止和暂停标志，恢复下载状态）"""
        self._stop_flag = False
        self._pause_flag = False
        self.status = "downloading"
        self.current_action = "继续下载..."
        self.error = None
        self.finished_at = None
        self.downloaded_segments = len(self._downloaded_indices)
        if self.started_at is None:
            self.started_at = datetime.now()

    def to_dict(self):
        """序列化为字典，用于保存到 JSON 文件"""
        # 确保时间字段为 datetime 对象
        if isinstance(self.started_at, str):
            self.started_at = datetime.fromisoformat(self.started_at)
        if isinstance(self.finished_at, str):
            self.finished_at = datetime.fromisoformat(self.finished_at)
        return {
            "task_id": self.task_id, "url": self.url, "output_name": self.output_name,
            "output_dir": self.output_dir, "status": self.status, "progress": self.progress,
            "total_segments": self.total_segments, "downloaded_segments": self.downloaded_segments,
            "current_action": self.current_action, "error": self.error, "output_path": self.output_path,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "custom_headers": self.custom_headers, "download_speed": self.download_speed,
            "downloaded_indices": list(self._downloaded_indices) if self._downloaded_indices else [],
            "mp4_downloaded": getattr(self, '_mp4_downloaded', 0),
            "available_resolutions": getattr(self, 'available_resolutions', ["最高分辨率"]),
            "resolution": getattr(self, 'resolution', "最高分辨率"),
            "audio_track": getattr(self, 'audio_track', ""),
            "available_audio_tracks": getattr(self, 'available_audio_tracks', []),
            "audio_track_url": getattr(self, '_audio_track_url', ""),
        }


def _save_tasks(tasks_dict):
    """将所有任务保存到 tasks_history.json"""
    save_tasks(tasks_dict, TASKS_HISTORY_FILE)


def _load_tasks():
    """从 tasks_history.json 加载历史任务"""
    tasks = {}
    data = load_tasks(TASKS_HISTORY_FILE)
    if not data:
        return tasks
    for item in data:
        task = DownloadTask(
            task_id=item.get("task_id", ""), url=item.get("url", ""),
            output_name=item.get("output_name", "output.mp4"),
            output_dir=item.get("output_dir", ""), workers=item.get("workers", 20),
            proxy=item.get("proxy", ""), custom_headers=item.get("custom_headers", {}),
        )
        task.status = item.get("status", "pending")
        if task.status == "downloading":
            task.status = "stopped"
        task.progress = item.get("progress", 0)
        task.total_segments = item.get("total_segments", 0)
        task.downloaded_segments = item.get("downloaded_segments", 0)
        task.current_action = item.get("current_action", "")
        task.error = item.get("error", None)
        task.output_path = item.get("output_path", None)
        task.started_at = item.get("started_at", None)
        task.finished_at = item.get("finished_at", None)
        task._downloaded_indices = set(item.get("downloaded_indices", []))
        task._mp4_downloaded = item.get("mp4_downloaded", 0)
        task.available_resolutions = item.get("available_resolutions", ["最高分辨率"])
        task.resolution = item.get("resolution", "最高分辨率")
        task.audio_track = item.get("audio_track", "")
        task.available_audio_tracks = item.get("available_audio_tracks", [])
        task._audio_track_url = item.get("audio_track_url", "")
        tasks[task.task_id] = task
    return tasks


def run_download_mp4(task, tasks_dict, on_progress=None):
    """MP4 多线程下载（支持断点续传、Range 分块）"""
    if task.status != "downloading":
        return
    try:
        task.status = "downloading"
        task.started_at = datetime.now()
        # 递增下载会话 ID，旧线程自动失效
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
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        headers.update(task.custom_headers)
        proxies = {"http": task.proxy, "https": task.proxy} if task.proxy else None

        # 探测文件大小和支持 Range
        probe_headers = dict(headers)
        total_size = 0
        accept_ranges = False

        # 方法1：HEAD 请求探测
        try:
            resp = requests.head(task.url, headers=probe_headers, timeout=15, proxies=proxies)
            if resp.status_code == 200:
                total_size = int(resp.headers.get('content-length', 0))
                accept_ranges = resp.headers.get('accept-ranges', '') == 'bytes'
        except Exception:
            pass

        # 方法2：GET 请求探测（HEAD 可能被服务器拒绝）
        if total_size == 0:
            try:
                resp = requests.get(task.url, headers=probe_headers, timeout=15, stream=True, proxies=proxies)
                if resp.status_code == 200:
                    total_size = int(resp.headers.get('content-length', 0))
                    accept_ranges = resp.headers.get('accept-ranges', '') == 'bytes'
                resp.close()
            except Exception:
                pass

        # 方法3：实际发送 Range 请求测试（最可靠）
        if total_size > 0 and not accept_ranges:
            try:
                test_headers = dict(headers)
                test_headers["Range"] = "bytes=0-0"
                resp = requests.get(task.url, headers=test_headers, timeout=15, proxies=proxies)
                if resp.status_code == 206:
                    accept_ranges = True
                    logger.info(f"服务器支持 Range（通过 206 响应确认）")
                resp.close()
            except Exception:
                pass

        task.total_segments = total_size if total_size > 0 else 0

        # 检查已下载的部分（断点续传）
        existing_size = getattr(task, '_mp4_downloaded', 0)
        downloaded = 0
        if existing_size > 0 and os.path.exists(tmp_path) and accept_ranges:
            file_size = os.path.getsize(tmp_path)
            if file_size == existing_size:
                downloaded = file_size
                logger.info(f"断点续传: 从 {downloaded / (1024*1024):.1f} MB 继续")
            else:
                # 文件大小不匹配，从头开始
                existing_size = 0
                logger.info(f"文件大小不匹配，从头下载")
        elif existing_size > 0 and not accept_ranges:
            # 服务器不支持 Range，从头下载
            existing_size = 0
            logger.info(f"服务器不支持 Range，从头下载")

        task.downloaded_segments = downloaded
        task.current_action = "下载中..."
        if on_progress:
            on_progress(task)

        # 多线程下载
        workers = task.workers if accept_ranges and total_size > 0 else 1
        chunk_size = total_size // workers if total_size > 0 and workers > 1 else 0

        def download_chunk(start, end, chunk_idx, file_lock):
            """下载指定 Range 的数据块并直接写入文件"""
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
                    # 每个 chunk 检查停止/暂停（不加锁，只读布尔值）
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
                    # 每 512KB 同步进度（加锁更新共享状态）
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
            # 多线程 Range 下载
            task.current_action = f"多线程下载中 ({workers}线程)..."
            if on_progress:
                on_progress(task)

            chunks = []
            for i in range(workers):
                start = downloaded + i * chunk_size
                end = start + chunk_size - 1 if i < workers - 1 else total_size - 1
                if start < total_size:
                    chunks.append((start, end, i))

            # 创建或复用 .tmp 文件
            if downloaded > 0 and os.path.exists(tmp_path):
                # 断点续传：保留已有数据
                logger.info(f"断点续传: 从 {downloaded / (1024*1024):.1f} MB 处继续")
            else:
                # 新下载：创建文件并预分配空间
                with open(tmp_path, "wb") as f:
                    if total_size > 0:
                        f.truncate(total_size)

            completed_chunks = 0
            _dl_downloaded = [downloaded]
            _dl_lock = threading.Lock()
            _file_lock = threading.Lock()

            from concurrent.futures import ThreadPoolExecutor, wait
            executor = ThreadPoolExecutor(max_workers=workers)
            futures = {executor.submit(download_chunk, s, e, i, _file_lock): (s, e, i) for s, e, i in chunks}

            # 非阻塞轮询 future，支持随时停止
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
                # 取消所有未完成的 future
                for f in pending:
                    f.cancel()
                task._mp4_downloaded = _dl_downloaded[0]
                _save_tasks(tasks_dict)

            executor.shutdown(wait=False)
        else:
            # 单线程下载
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
            _save_tasks(tasks_dict)
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
        _save_tasks(tasks_dict)

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
        _save_tasks(tasks_dict)


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
    from utils import fetch_m3u8
    from m3u8_parser import parse_m3u8
    from downloader import download_all
    from decryptor import decrypt_files

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

    if not audio_files:
        raise Exception("音频分片下载失败")

    if any(s.encryption_method for s in audio_playlist.segments):
        audio_files = decrypt_files(
            audio_files, audio_playlist.segments,
            headers, proxy,
            media_sequence=audio_playlist.media_sequence,
        )

    audio_output = os.path.join(temp_dir, "audio_merged.mp4")
    from merger import merge_ts_files
    if not merge_ts_files(audio_files, audio_output):
        raise Exception("音频合并失败")

    return audio_output


def run_download(task, tasks_dict, on_progress=None, resolution="最高分辨率"):
    """
    执行下载任务的主流程（在子线程中运行）
    自动检测 MP4/M3U8 格式，调用对应下载函数
    """
    # 检测是否为 MP4 链接
    if re.search(r'https?://\S+\.mp4(\?\S*)?', task.url, re.IGNORECASE):
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

        # 检查 FFmpeg 是否可用（TS 合并需要）
        from utils import check_ffmpeg
        if not check_ffmpeg():
            logger.warning("FFmpeg 未找到，将使用简单拼接模式（输出可能不是标准 MP4）")

        # 步骤1：获取并解析 m3u8 文件
        task.current_action = "解析m3u8..."
        if on_progress:
            on_progress(task)

        content = fetch_m3u8(task.url, task.custom_headers, task.proxy)
        base_url = get_base_url(task.url)
        playlist = parse_m3u8(content, base_url)

        # 步骤2：如果是 Master Playlist（多码率），根据用户选择的分辨率获取对应的 Media Playlist
        if playlist.is_master and playlist.streams:
            selected_idx = 0  # 默认选择最高码率
            if resolution != "最高分辨率":
                # 按名称匹配用户选择的分辨率
                for i, s in enumerate(playlist.streams):
                    name = s.name or f"{s.bandwidth}bps"
                    if name == resolution:
                        selected_idx = i
                        break
            else:
                # 自动选择最高码率
                selected_idx = max(range(len(playlist.streams)), key=lambda i: playlist.streams[i].bandwidth)
            task.resolution = playlist.streams[selected_idx].name or f"{playlist.streams[selected_idx].bandwidth}bps"

            # 解析音频轨道 URL（在 playlist 被替换前）
            if playlist.audio_tracks:
                selected_audio = task.audio_track
                if selected_audio and selected_audio != "默认":
                    for at in playlist.audio_tracks:
                        if at.name == selected_audio or at.language == selected_audio:
                            task._audio_track_url = at.url
                            break
                # 未选择或找不到时，使用默认音频轨道
                if not task._audio_track_url and playlist.audio_tracks:
                    default_track = next(
                        (at for at in playlist.audio_tracks if at.default),
                        playlist.audio_tracks[0]
                    )
                    task._audio_track_url = default_track.url

            # 获取选定码率的 Media Playlist
            stream_url = playlist.streams[selected_idx].url
            content = fetch_m3u8(stream_url, task.custom_headers, task.proxy)
            base_url = get_base_url(stream_url)
            playlist = parse_m3u8(content, base_url)

        if not playlist.segments:
            raise Exception("未找到TS分片")

        # 检测格式：fMP4 还是 TS
        is_fmp4 = False
        if playlist.init_segment_url:
            is_fmp4 = True
        elif playlist.segments:
            sample_url = playlist.segments[0].url.lower()
            # 检查常见 fMP4 扩展名
            fmp4_extensions = ('.m4s', '.cmfv', '.cmfa', '.cmf')
            if any(sample_url.endswith(ext) for ext in fmp4_extensions):
                is_fmp4 = True
            # 检查是否有 BYTERANGE（fMP4 流的典型特征）
            elif any(s.byterange for s in playlist.segments[:5]):
                is_fmp4 = True
            # 检查 segment 级别的 init_segment_url
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
        # 用 URL 的 MD5 哈希作为临时目录标识，避免不同任务的临时文件冲突
        stable_id = hashlib.md5(task.url.encode()).hexdigest()[:12]
        temp_dir = os.path.join(task.output_dir, f".m3u8_temp_{stable_id}")

        # 检查临时目录是否可写（防止被其他程序锁定）
        _temp_dir_ok = True
        if os.path.exists(temp_dir):
            try:
                _test_f = os.path.join(temp_dir, ".write_test")
                with open(_test_f, "wb") as f:
                    f.write(b"test")
                os.remove(_test_f)
            except (OSError, PermissionError):
                _temp_dir_ok = False
        if not _temp_dir_ok:
            # 主目录不可写，使用备用目录
            temp_dir = os.path.join(task.output_dir, f".m3u8_temp_{stable_id}_retry")

        # 停止检查函数：暂停时进入等待循环，直到恢复或停止
        def stop_check():
            while task._pause_flag and not task._stop_flag:
                time.sleep(0.1)
            return task._stop_flag

        # 进度回调：更新任务的进度信息
        def progress_callback(completed, total):
            task.downloaded_segments = completed
            task.total_segments = total
            task.progress = int((completed / total) * 100) if total > 0 else 0
            task.current_action = f"下载中 {completed}/{total}"
            if on_progress:
                on_progress(task)

        # 速度回调：根据已下载字节数和耗时计算实时速度
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
            if on_progress:
                on_progress(task)

        # 步骤4：下载分片（根据格式选择不同流程）
        if is_fmp4:
            # ── fMP4 下载流程 ──
            # 4a. 下载 init segment
            if playlist.init_segment_url:
                task.current_action = "下载初始化段..."
                if on_progress:
                    on_progress(task)
                from downloader import download_init_segment, _create_session
                dl_session = _create_session(task.custom_headers, task.proxy)
                init_path = os.path.join(temp_dir, "init.mp4")
                br = playlist.init_segment_byterange
                download_init_segment(playlist.init_segment_url, init_path, dl_session, byterange=br, stop_check=stop_check)
                dl_session.close()
            else:
                init_path = ""

            if task._stop_flag:
                return

            # 4b. 下载 media segments
            task.total_segments = len(playlist.segments)
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
            if not ts_files:
                raise Exception("没有成功下载任何分片")

            # 记录已下载的分片索引
            task.downloaded_segments = len(ts_files)
            task._downloaded_indices.clear()
            for ts_file in ts_files:
                filename = os.path.basename(ts_file)
                if filename.endswith('.m4s') or filename.endswith('.ts'):
                    try:
                        task._downloaded_indices.add(int(filename.split('.')[0]))
                    except ValueError:
                        pass

            # 4c. 解密（如果需要）
            if any(s.encryption_method for s in playlist.segments):
                task.current_action = "解密中..."
                if on_progress:
                    on_progress(task)
                ts_files = decrypt_files(ts_files, playlist.segments, task.custom_headers, task.proxy, media_sequence=playlist.media_sequence)

            if task._stop_flag:
                return

            # 4d. 合并 fMP4
            task.current_action = "合并中..."
            task.progress = 95
            if on_progress:
                on_progress(task)
            from merger import merge_fmp4
            final_path = merge_fmp4(init_path, ts_files, output_path)

        else:
            # ── TS 下载流程（原有逻辑） ──
            ts_files = download_all(
                playlist.segments, temp_dir, max_workers=task.workers,
                headers=task.custom_headers, proxy=task.proxy,
                progress_callback=progress_callback, stop_check=stop_check,
                skip_indices=task._downloaded_indices, speed_callback=speed_callback,
            )

            if task._stop_flag:
                return
            if not ts_files:
                raise Exception("没有成功下载任何分片")

            # 记录已下载的分片索引（用于断点续传）
            task.downloaded_segments = len(ts_files)
            task._downloaded_indices.clear()
            for ts_file in ts_files:
                filename = os.path.basename(ts_file)
                if filename.endswith('.ts'):
                    try:
                        task._downloaded_indices.add(int(filename[:-3]))
                    except ValueError:
                        pass

            # 步骤5：如果视频有 AES-128 加密，进行解密
            if any(s.encryption_method for s in playlist.segments):
                task.current_action = "解密中..."
                if on_progress:
                    on_progress(task)
                ts_files = decrypt_files(ts_files, playlist.segments, task.custom_headers, task.proxy, media_sequence=playlist.media_sequence)

            if task._stop_flag:
                return

            # 步骤6：将所有 TS 分片合并为一个完整文件
            task.current_action = "合并中..."
            task.progress = 95
            if on_progress:
                on_progress(task)
            final_path = merge_to_ts(ts_files, output_path)

        # 步骤6.5：音视频合并（如果选择了独立音频轨道）
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
                    from merger import mux_audio_video
                    muxed_path = os.path.join(temp_dir, "muxed_output.mp4")
                    final_path = mux_audio_video(final_path, audio_file, muxed_path)
            except Exception as e:
                logger.warning(f"音频轨道处理失败（视频仍可用）: {e}")

        # 步骤7：验证输出文件
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

        # 步骤7：清理临时文件
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
        _save_tasks(tasks_dict)

    except Exception as e:
        # 区分"用户主动停止"和"下载失败"
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
        _save_tasks(tasks_dict)


class TaskCard(ctk.CTkFrame):
    """单个下载任务的 UI 卡片组件"""

    # 各状态对应的颜色（前景色, 背景色）
    STATUS_COLORS = {
        "pending": (COLORS["warning"], "#2d2206"),
        "downloading": (COLORS["accent"], "#1a1740"),
        "completed": (COLORS["success"], "#0a2612"),
        "failed": (COLORS["error"], "#2d0f0f"),
        "stopped": (COLORS["error"], "#2d0f0f"),
        "paused": (COLORS["warning"], "#2d2206"),
    }
    # 各状态的中文显示文本
    STATUS_TEXT = {
        "pending": "等待中", "downloading": "下载中", "completed": "已完成",
        "failed": "失败", "stopped": "已停止", "paused": "已暂停",
    }

    def __init__(self, master, task, on_resume, on_pause, on_stop, on_delete, **kwargs):
        super().__init__(master, fg_color=COLORS["card"], corner_radius=6, **kwargs)
        self.task = task
        self.on_resume = on_resume    # 继续回调
        self.on_pause = on_pause      # 暂停回调
        self.on_stop = on_stop        # 停止回调
        self.on_delete = on_delete    # 删除回调
        self._build()
        self.update_ui()
        # 右键菜单
        self._create_context_menu()
        self._bind_right_click(self)  # 给卡片及所有子控件绑定右键

    def _build(self):
        """构建卡片 UI 布局"""
        # ── 顶部行：分辨率下拉 + 状态标签 + 文件名 ──
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(10, 4))
        # 分辨率下拉菜单（每个任务独立）
        self.task_resolution_var = ctk.StringVar(value=self.task.resolution or "最高分辨率")
        available = getattr(self.task, 'available_resolutions', ["最高分辨率"])
        self.task_resolution_combo = ctk.CTkOptionMenu(header, variable=self.task_resolution_var,
                                                        values=available, width=100, height=26,
                                                        font=("", 10), corner_radius=4,
                                                        fg_color=COLORS["input"], button_color=COLORS["border"],
                                                        command=self._on_resolution_change)
        self.task_resolution_combo.pack(side="left")
        # 状态徽章（彩色标签）
        self.status_label = ctk.CTkLabel(header, text="", font=("", 10, "bold"), corner_radius=10, padx=8, pady=2)
        self.status_label.pack(side="left", padx=(6, 0))
        # 文件名（超过 30 字符截断）
        display_name = self.task.output_name
        if len(display_name) > 30:
            display_name = display_name[:27] + "..."
        self.filename_label = ctk.CTkLabel(header, text=display_name, font=("", 12, "bold"), text_color=COLORS["text"], anchor="w")
        self.filename_label.pack(side="left", padx=(8, 0))

        # ── 进度条 ──
        bar_frame = ctk.CTkFrame(self, fg_color=COLORS["input"], height=8, corner_radius=4)
        bar_frame.pack(fill="x", padx=16, pady=(0, 5))
        bar_frame.pack_propagate(False)  # 固定高度，不随内容扩展
        self.progressbar = ctk.CTkProgressBar(bar_frame, height=8, corner_radius=4, progress_color=COLORS["accent"])
        self.progressbar.pack(fill="x", padx=2, pady=2)

        # ── 信息行：百分比 + 分片数 + 速度 ──
        info = ctk.CTkFrame(self, fg_color="transparent")
        info.pack(fill="x", padx=16, pady=(0, 4))
        self.percent_label = ctk.CTkLabel(info, text="0%", font=("Consolas", 11, "bold"), text_color=COLORS["text"])
        self.percent_label.pack(side="left")
        self.segments_label = ctk.CTkLabel(info, text="0 / 0", font=("", 11), text_color=COLORS["text2"])
        self.segments_label.pack(side="left", padx=(8, 0))
        self.speed_label = ctk.CTkLabel(info, text="", font=("", 11), text_color=COLORS["accent"])
        self.speed_label.pack(side="right")

        # 操作描述标签（默认隐藏）
        self.action_label = ctk.CTkLabel(self, text="", font=("", 9), text_color=COLORS["muted"], anchor="w")

        # ── 控制按钮：删除 / 继续 / 暂停 / 停止 ──
        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.pack(fill="x", padx=16, pady=(0, 8))
        self.btn_delete = ctk.CTkButton(controls, text="删除", width=54, height=28, fg_color="#2d0f0f", text_color=COLORS["error"], hover_color=COLORS["error"], font=("", 11), corner_radius=6, command=lambda: self.on_delete(self.task.task_id))
        self.btn_delete.pack(side="right")
        self.btn_resume = ctk.CTkButton(controls, text="继续", width=54, height=28, fg_color="#0a2612", text_color=COLORS["success"], hover_color=COLORS["success"], font=("", 11), corner_radius=6, command=lambda: self.on_resume(self.task.task_id))
        self.btn_pause = ctk.CTkButton(controls, text="暂停", width=54, height=28, fg_color="#2d2206", text_color=COLORS["warning"], hover_color=COLORS["warning"], font=("", 11), corner_radius=6, command=lambda: self.on_pause(self.task.task_id))
        self.btn_stop = ctk.CTkButton(controls, text="停止", width=54, height=28, fg_color="#2d0f0f", text_color=COLORS["error"], hover_color=COLORS["error"], font=("", 11), corner_radius=6, command=lambda: self.on_stop(self.task.task_id))

    def _on_resolution_change(self, value):
        """分辨率下拉菜单变化时更新任务的分辨率"""
        self.task.resolution = value

    def _create_context_menu(self):
        """创建右键上下文菜单（圆角自定义样式）"""
        self._context_menu = None  # 延迟创建

    def _bind_right_click(self, widget):
        """递归给控件及所有子控件绑定右键菜单"""
        widget.bind("<Button-3>", self._show_context_menu)
        for child in widget.winfo_children():
            self._bind_right_click(child)

    def _show_context_menu(self, event):
        """显示圆角右键菜单"""
        self._hide_context_menu()
        import tkinter as tk
        menu = tk.Toplevel(self)
        menu.overrideredirect(True)
        menu.attributes("-topmost", True)
        menu.configure(bg=COLORS["border"], highlightthickness=0)

        inner = ctk.CTkFrame(menu, fg_color=COLORS["card"], corner_radius=8)
        inner.pack(padx=1, pady=1)

        items = [
            ("复制链接", COLORS["text"], COLORS["accent"], self._copy_url),
            ("打开下载目录", COLORS["text"], COLORS["accent"], self._open_task_dir),
            None,
            ("删除任务", COLORS["error"], "#3d1a1a", lambda: self.on_delete(self.task.task_id)),
        ]

        for item in items:
            if item is None:
                # 分割线：用 Canvas 画一条细线
                sep = ctk.CTkCanvas(inner, height=2, bg=COLORS["border"], highlightthickness=0)
                sep.pack(fill="x", padx=12, pady=4)
                sep.create_line(0, 1, 200, 1, fill="#ffffff", width=1)
            else:
                label, fg, hover_bg, cmd = item
                def make_cmd(c=cmd):
                    def callback():
                        self._hide_context_menu()
                        c()
                    return callback
                btn = ctk.CTkButton(inner, text=label, font=("", 12), height=30,
                                    fg_color="transparent", hover_color=hover_bg,
                                    text_color=fg, corner_radius=4, anchor="w",
                                    command=make_cmd(cmd))
                btn.pack(fill="x", padx=4, pady=1)

        # 用 inner frame 的实际宽度设置窗口大小
        x = event.x_root
        y = event.y_root
        inner.update_idletasks()
        w = inner.winfo_reqwidth() // 5 * 3 + 4  # +4 for outer padding
        h = inner.winfo_reqheight() + 4
        menu.geometry(f"{w}x{h}+{x}+{y}")

        self._context_menu = menu
        # 点击菜单外部关闭菜单
        self.bind("<Button-1>", self._on_outside_click)
        self.bind("<Button-3>", self._on_outside_click)

    def _hide_context_menu(self):
        """关闭右键菜单"""
        if self._context_menu:
            self._context_menu.destroy()
            self._context_menu = None
            self.unbind("<Button-1>")
            self.unbind("<Button-3>")

    def _on_outside_click(self, event):
        """点击外部关闭菜单"""
        if self._context_menu:
            # 检查点击是否在菜单内
            try:
                x, y = event.x_root, event.y_root
                mx = self._context_menu.winfo_rootx()
                my = self._context_menu.winfo_rooty()
                mw = self._context_menu.winfo_width()
                mh = self._context_menu.winfo_height()
                if not (mx <= x <= mx + mw and my <= y <= my + mh):
                    self._hide_context_menu()
            except Exception:
                self._hide_context_menu()

    def _copy_url(self):
        """复制任务的 M3U8 链接到剪贴板"""
        self.clipboard_clear()
        self.clipboard_append(self.task.url)

    def _open_task_dir(self):
        """在文件管理器中打开任务的下载目录"""
        dir_path = self.task.output_dir
        if not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(dir_path)
        else:
            import subprocess
            subprocess.Popen(["xdg-open" if sys.platform == "linux" else "open", dir_path])

    def update_ui(self):
        """根据任务状态刷新卡片 UI 显示"""
        t = self.task
        # 更新状态标签颜色和文字
        fg, bg = self.STATUS_COLORS.get(t.status, (COLORS["muted"], COLORS["card"]))
        self.status_label.configure(text=self.STATUS_TEXT.get(t.status, t.status), text_color=fg, fg_color=bg)
        # 更新进度条和百分比
        self.progressbar.set(t.progress / 100)
        self.percent_label.configure(text=f"{t.progress}%")
        self.segments_label.configure(text=f"{t.downloaded_segments} / {t.total_segments} 分片")
        # 更新下载速度（仅下载中显示）
        if t.status == "downloading" and t.download_speed > 0:
            self.speed_label.configure(text=format_speed(t.download_speed))
        else:
            self.speed_label.configure(text="")
        # 更新操作描述（包含错误信息和输出路径）
        action = t.current_action or ""
        if t.error:
            action += f"\n错误: {t.error}"
        if t.output_path:
            action += f"\n{t.output_path}"
        self.action_label.configure(text=action)
        # 根据状态显示/隐藏控制按钮
        self.btn_resume.pack_forget()
        self.btn_pause.pack_forget()
        self.btn_stop.pack_forget()
        if t.status in ("pending", "paused", "stopped", "failed"):
            # 待处理/暂停/停止/失败 → 显示"继续"按钮
            self.btn_resume.pack(side="left", padx=(0, 6))
        elif t.status == "downloading":
            # 下载中 → 显示"暂停"和"停止"按钮
            self.btn_pause.pack(side="left", padx=(0, 6))
            self.btn_stop.pack(side="left", padx=(0, 6))


class App(ctk.CTk):
    """主应用窗口"""

    def __init__(self):
        super().__init__()
        self.title("m3u8-dl-hls-gui v0.17")
        self.geometry("930x620")
        self.minsize(750, 500)
        self.configure(fg_color=COLORS["bg"])

        self.config_data = load_config(CONFIG_FILE)      # 加载用户配置
        self.tasks = _load_tasks()            # 加载历史任务
        self.task_cards = {}                  # task_id → TaskCard 映射

        self._build_ui()
        self._refresh_task_list()
        self._poll_progress()  # 启动定时刷新
        self._start_clipboard_monitor()  # 启动剪贴板监控
        self._setup_drop()  # 启用拖拽

    def _build_ui(self):
        """构建主界面（左右两栏布局）"""
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=20, pady=20)

        # ── 左侧面板：下载设置 ──
        left_card = ctk.CTkFrame(container, fg_color=COLORS["card"], corner_radius=16, border_width=1, border_color=COLORS["border"])
        left_card.pack(side="left", fill="y", padx=(0, 15))

        title_frame = ctk.CTkFrame(left_card, fg_color="transparent")
        title_frame.pack(fill="x", padx=20, pady=(16, 12))
        icon_bg = ctk.CTkFrame(title_frame, fg_color="#1a1740", width=32, height=32, corner_radius=8)
        icon_bg.pack(side="left", padx=(0, 8))
        icon_bg.pack_propagate(False)
        ctk.CTkLabel(icon_bg, text="📥", font=("", 14)).pack(expand=True)
        ctk.CTkLabel(title_frame, text="下载设置", font=("", 14, "bold"), text_color=COLORS["text"]).pack(side="left")

        # 表单区域
        form = ctk.CTkFrame(left_card, fg_color="transparent")
        form.pack(fill="x", padx=20, pady=(0, 16))

        # 标签和输入框的通用样式
        lk = {"font": ("", 11), "text_color": COLORS["text2"]}
        ek = {"height": 34, "font": ("Consolas", 11), "fg_color": COLORS["input"], "border_color": COLORS["border"], "text_color": COLORS["text"], "corner_radius": 6}

        r = 0  # 行号计数器
        # 链接地址（支持 M3U8 和 MP4 等格式）
        ctk.CTkLabel(form, text="视频链接地址", **lk).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 4)); r += 1
        self.url_var = ctk.StringVar()
        ctk.CTkEntry(form, textvariable=self.url_var, placeholder_text="https://example.com/video.m3u8 / .mp4", **ek).grid(row=r, column=0, columnspan=2, sticky="ew", pady=(0, 8)); r += 1

        # Referer 来源页（防盗链）
        ctk.CTkLabel(form, text="Referer 来源页（可选）", **lk).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 4)); r += 1
        self.referer_var = ctk.StringVar(value=self.config_data.get("headers", ""))
        self.referer_var.trace_add("write", lambda *a: self._auto_save())  # 写入时自动保存
        ctk.CTkEntry(form, textvariable=self.referer_var, placeholder_text="https://...", **ek).grid(row=r, column=0, columnspan=2, sticky="ew", pady=(0, 8)); r += 1

        # 保存文件名
        ctk.CTkLabel(form, text="保存文件名", **lk).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 4)); r += 1
        self.name_var = ctk.StringVar(value="")
        ctk.CTkEntry(form, textvariable=self.name_var, placeholder_text="output", **ek).grid(row=r, column=0, columnspan=2, sticky="ew", pady=(0, 8)); r += 1

        # 保存目录
        ctk.CTkLabel(form, text="保存目录", **lk).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 4)); r += 1
        dir_frame = ctk.CTkFrame(form, fg_color="transparent")
        dir_frame.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.dir_var = ctk.StringVar(value="")
        ctk.CTkEntry(dir_frame, textvariable=self.dir_var, placeholder_text=get_default_output_dir(), **ek).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(dir_frame, text="选择", width=50, height=34, font=("", 11), corner_radius=6, fg_color=COLORS["border"], command=self._browse_dir).pack(side="left", padx=(6, 0))
        ctk.CTkButton(dir_frame, text="打开", width=50, height=34, font=("", 11), corner_radius=6, fg_color=COLORS["border"], text_color=COLORS["accent"], command=self._open_dir).pack(side="left", padx=(4, 0)); r += 1

        # 音频轨道选择
        ctk.CTkLabel(form, text="音频轨道", **lk).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 4)); r += 1
        self.audio_var = ctk.StringVar(value="默认")
        self.audio_combo = ctk.CTkOptionMenu(form, variable=self.audio_var, values=["默认"], width=190, height=34, font=("", 11), fg_color=COLORS["input"], button_color=COLORS["border"])
        self.audio_combo.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 8)); r += 1

        # 代理地址 和 线程数（同行显示）
        ctk.CTkLabel(form, text="代理地址", **lk).grid(row=r, column=0, sticky="w")
        ctk.CTkLabel(form, text="线程数", **lk).grid(row=r, column=1, sticky="w", padx=(12, 0)); r += 1
        self.proxy_var = ctk.StringVar(value=self.config_data.get("proxy", ""))
        self.proxy_var.trace_add("write", lambda *a: self._auto_save())
        ctk.CTkEntry(form, textvariable=self.proxy_var, placeholder_text="http://127.0.0.1:7890", width=190, **ek).grid(row=r, column=0, sticky="w", pady=(0, 8))
        self.workers_var = ctk.StringVar(value=str(self.config_data.get("workers", 20)))
        self.workers_var.trace_add("write", lambda *a: self._auto_save())
        ctk.CTkEntry(form, textvariable=self.workers_var, width=50, **ek).grid(row=r, column=1, sticky="w", padx=(12, 0), pady=(0, 8)); r += 1

        # 开始下载按钮
        btn_frame = ctk.CTkFrame(form, fg_color="transparent")
        btn_frame.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self._download_btn = ctk.CTkButton(btn_frame, text="开始下载", height=38, font=("", 13, "bold"), corner_radius=8, fg_color=COLORS["grad1"], hover_color=COLORS["grad2"], command=self._start_download)
        self._download_btn.pack(fill="x")

        # 设置两列等宽
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)

        # ── 右侧面板：下载进度 ──
        right_card = ctk.CTkFrame(container, fg_color=COLORS["card"], corner_radius=16, border_width=1, border_color=COLORS["border"])
        right_card.pack(side="right", fill="both", expand=True)

        title_frame2 = ctk.CTkFrame(right_card, fg_color="transparent")
        title_frame2.pack(fill="x", padx=20, pady=(16, 12))
        icon_bg2 = ctk.CTkFrame(title_frame2, fg_color="#1a1740", width=32, height=32, corner_radius=8)
        icon_bg2.pack(side="left", padx=(0, 8))
        icon_bg2.pack_propagate(False)
        ctk.CTkLabel(icon_bg2, text="📊", font=("", 14)).pack(expand=True)
        ctk.CTkLabel(title_frame2, text="下载进度", font=("", 14, "bold"), text_color=COLORS["text"]).pack(side="left")
        # 操作按钮：清空列表 / 停止所有 / 开始所有
        ctk.CTkButton(title_frame2, text="清空列表", width=70, height=28, font=("", 11), corner_radius=6, fg_color="transparent", border_width=1, border_color=COLORS["warning"], text_color=COLORS["warning"], hover_color="#3d2a0a", command=self._clear_all).pack(side="right")
        ctk.CTkButton(title_frame2, text="停止所有", width=70, height=28, font=("", 11), corner_radius=6, fg_color="transparent", border_width=1, border_color=COLORS["error"], text_color=COLORS["error"], hover_color="#3d1a1a", command=self._stop_all).pack(side="right", padx=14)
        ctk.CTkButton(title_frame2, text="开始所有", width=70, height=28, font=("", 11), corner_radius=6, fg_color="transparent", border_width=1, border_color=COLORS["success"], text_color=COLORS["success"], hover_color="#1a3d1a", command=self._start_all).pack(side="right", padx=0)

        # 任务列表滚动区域
        self.task_scroll = ctk.CTkScrollableFrame(right_card, fg_color=COLORS["bg"], corner_radius=12, scrollbar_button_color=COLORS["border"], scrollbar_button_hover_color=COLORS["accent"])
        self.task_scroll.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _validate_workers(self):
        """验证线程数输入，只允许数字，最多4位"""
        value = self.workers_var.get()
        value = ''.join(filter(str.isdigit, value))[:4]
        if value != self.workers_var.get():
            self.workers_var.set(value)
        self._auto_save()

    def _auto_save(self):
        """自动保存当前设置到 config.json"""
        try:
            workers = max(1, min(100, int(self.workers_var.get())))
        except ValueError:
            workers = 20
        self.config_data = {
            "workers": workers,
            "proxy": self.proxy_var.get().strip(),
            "headers": self.referer_var.get().strip(),
        }
        save_config(self.config_data, CONFIG_FILE)

    def _browse_dir(self):
        """打开目录选择对话框"""
        path = filedialog.askdirectory(title="选择保存目录")
        if path:
            self.dir_var.set(path)

    def _open_dir(self):
        """在系统文件管理器中打开下载目录"""
        dir_path = self.dir_var.get().strip() or get_default_output_dir()
        if not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        # Windows 使用 os.startfile，跨平台使用 subprocess
        if sys.platform == "win32":
            os.startfile(dir_path)
        else:
            subprocess.Popen(["xdg-open" if sys.platform == "linux" else "open", dir_path])

    def _start_download(self):
        """开始下载：验证输入 → 检测格式 → 创建任务"""
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("警告", "请输入视频链接地址")
            return
        # 检查是否已有相同 URL 的任务
        for t in self.tasks.values():
            if t.url == url:
                messagebox.showinfo("提示", "该地址已有下载任务")
                return

        # 禁用按钮，防止重复点击
        self._download_btn.configure(state="disabled")
        self._available_resolutions = ["最高分辨率"]
        self._available_audio_tracks = ["默认"]

        # 判断是否为 MP4 链接
        is_mp4 = bool(re.search(r'https?://\S+\.mp4(\?\S*)?', url, re.IGNORECASE))

        if is_mp4:
            # MP4 直接下载，无需解析 M3U8
            self._create_task(url, is_mp4=True)
        else:
            # M3U8：在子线程中解析获取分辨率列表
            def parse_and_create():
                try:
                    headers = {}
                    referer = self.referer_var.get().strip()
                    if referer:
                        headers['Referer'] = referer
                    content = fetch_m3u8(url, headers, self.proxy_var.get().strip())
                    base = get_base_url(url)
                    playlist = parse_m3u8(content, base)
                    if playlist.is_master and playlist.streams:
                        resolutions = ["最高分辨率"]
                        for s in playlist.streams:
                            name = s.name or f"{s.bandwidth}bps"
                            if name not in resolutions:
                                resolutions.append(name)
                        self._available_resolutions = resolutions
                    # 提取音频轨道列表
                    if playlist.audio_tracks:
                        self._available_audio_tracks = ["默认"]
                        for t in playlist.audio_tracks:
                            name = t.name or t.language or "Audio"
                            if name not in self._available_audio_tracks:
                                self._available_audio_tracks.append(name)
                    else:
                        self._available_audio_tracks = ["默认"]
                except Exception as e:
                    logger.warning(f"获取分辨率失败: {e}")
                    self._available_audio_tracks = ["默认"]
                finally:
                    self.after(0, lambda: self._create_task(url, is_mp4=False))

            import threading
            threading.Thread(target=parse_and_create, daemon=True).start()

    def _create_task(self, url, is_mp4=False):
        """创建下载任务并添加到列表"""
        output_name = self.name_var.get().strip() or "output"
        # 清理文件名中的 Windows 非法字符
        illegal_chars = '<>:"/\\|?*\n\r\t'
        for ch in illegal_chars:
            output_name = output_name.replace(ch, '_')
        output_name = output_name.strip('. ')
        if not output_name:
            output_name = "output"
        # 根据格式设置扩展名
        if is_mp4:
            if not output_name.lower().endswith(".mp4"):
                output_name += ".mp4"
        else:
            # M3U8 下载也输出为 .mp4（TS 合并后兼容性更好）
            if not output_name.lower().endswith(".mp4"):
                output_name += ".mp4"
        try:
            workers = max(1, min(100, int(self.workers_var.get())))
        except ValueError:
            workers = 20
        output_dir = self.dir_var.get().strip() or get_default_output_dir()
        # 用精确时间戳生成唯一任务 ID
        task_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
        task = DownloadTask(task_id=task_id, url=url, output_name=output_name, output_dir=output_dir, workers=workers, proxy=self.proxy_var.get().strip(), custom_headers={})
        task.resolution = "最高分辨率"
        task.available_resolutions = getattr(self, '_available_resolutions', ["最高分辨率"])
        task.audio_track = self.audio_var.get() if hasattr(self, 'audio_var') else "默认"
        task.available_audio_tracks = getattr(self, '_available_audio_tracks', ["默认"])
        # 更新音频轨道下拉菜单
        if hasattr(self, 'audio_combo') and self._available_audio_tracks:
            self.audio_combo.configure(values=self._available_audio_tracks)
        referer = self.referer_var.get().strip()
        if referer:
            task.custom_headers["Referer"] = referer
        self.tasks[task_id] = task
        _save_tasks(self.tasks)
        self._refresh_task_list()
        self._download_btn.configure(state="normal")

    def _start_all(self):
        """开始所有待处理/已停止/已暂停/已失败的任务"""
        for task in self.tasks.values():
            if task.status in ("pending", "stopped", "paused", "failed"):
                self._resume_task(task.task_id)

    def _stop_all(self):
        """停止所有正在下载的任务"""
        for task in self.tasks.values():
            if task.status in ("pending", "downloading"):
                task.stop()
        _save_tasks(self.tasks)
        self._refresh_task_list()

    def _clear_all(self):
        """清空所有任务（停止运行中的任务，清理临时文件）"""
        # 先停止所有运行/暂停中的任务
        for task in self.tasks.values():
            if task.status in ("pending", "downloading", "paused"):
                task._stop_flag = True
                task._pause_flag = False
                if task._thread and task._thread.is_alive():
                    task._thread.join(timeout=10)
        # 清理所有临时文件
        for task in self.tasks.values():
            stable_id = hashlib.md5(task.url.encode()).hexdigest()[:12]
            for suffix in ("", "_retry"):
                td = os.path.join(task.output_dir, f".m3u8_temp_{stable_id}{suffix}")
                if os.path.exists(td):
                    try:
                        shutil.rmtree(td)
                    except Exception:
                        self.after(3000, lambda p=td: self._retry_cleanup(p))
        self.tasks.clear()
        _save_tasks(self.tasks)
        self._refresh_task_list()

    def _resume_task(self, task_id):
        """继续/恢复下载任务（在子线程中运行）"""
        task = self.tasks.get(task_id)
        if not task or task.status not in ("pending", "stopped", "paused", "failed"):
            return
        # 如果旧线程还在运行，先停止它
        if task._thread and task._thread.is_alive():
            task._stop_flag = True
            task._pause_flag = False
            task._thread.join(timeout=5)
        task.continue_task()
        _save_tasks(self.tasks)
        self._refresh_task_list()
        # 使用任务自己保存的分辨率，而不是全局下拉菜单
        resolution = task.resolution if task.resolution else "最高分辨率"
        thread = threading.Thread(target=run_download, args=(task, self.tasks, lambda t: None, resolution), daemon=True)
        task._thread = thread
        thread.start()

    def _pause_task(self, task_id):
        """暂停任务"""
        task = self.tasks.get(task_id)
        if task:
            task.pause()
            # 保存当前下载进度用于续传
            _save_tasks(self.tasks)

    def _stop_task(self, task_id):
        """停止任务"""
        task = self.tasks.get(task_id)
        if task:
            task.stop()
            _save_tasks(self.tasks)
            _save_tasks(self.tasks)

    def _delete_task(self, task_id):
        """删除任务（立即移除，后台清理）"""
        task = self.tasks.get(task_id)
        if not task:
            return
        # 标记停止（线程自行退出）
        task._stop_flag = True
        task._pause_flag = False
        # 立即从列表移除
        del self.tasks[task_id]
        _save_tasks(self.tasks)
        self._refresh_task_list()
        # 后台清理临时文件（不阻塞 UI）
        def cleanup():
            # 等线程退出（最多 5 秒）
            if task._thread and task._thread.is_alive():
                task._thread.join(timeout=5)
            time.sleep(0.5)  # 额外等待确保文件句柄释放
            # 清理 M3U8 临时目录
            stable_id = hashlib.md5(task.url.encode()).hexdigest()[:12]
            for suffix in ("", "_retry"):
                td = os.path.join(task.output_dir, f".m3u8_temp_{stable_id}{suffix}")
                if os.path.exists(td):
                    try: shutil.rmtree(td)
                    except: pass
            # 清理 MP4 临时文件（重试 3 次）
            if task.output_name:
                mp4_tmp = os.path.join(task.output_dir, task.output_name + ".tmp")
                for attempt in range(3):
                    if os.path.exists(mp4_tmp):
                        try:
                            os.remove(mp4_tmp)
                            break
                        except:
                            time.sleep(1)
        t = threading.Thread(target=cleanup)
        t.daemon = False
        t.start()

    def _retry_cleanup(self, path, retries=3):
        """延迟重试清理文件/目录"""
        if os.path.exists(path):
            try:
                if os.path.isfile(path):
                    os.remove(path)
                else:
                    shutil.rmtree(path)
            except Exception:
                if retries > 0:
                    self.after(3000, lambda p=path, r=retries-1: self._retry_cleanup(p, r))

    def _refresh_task_list(self):
        """增量刷新任务列表（只创建/删除变化的卡片，避免全部重建导致闪烁）"""
        # 删除非 TaskCard 的占位控件（如"暂无任务"提示）
        for w in self.task_scroll.winfo_children():
            if not isinstance(w, TaskCard):
                w.destroy()

        current_ids = set(self.tasks.keys())
        existing_ids = set(self.task_cards.keys())

        # 删除已不存在的任务卡片
        for task_id in existing_ids - current_ids:
            if task_id in self.task_cards:
                self.task_cards[task_id].destroy()
                del self.task_cards[task_id]

        # 添加新任务的卡片
        for task_id in current_ids - existing_ids:
            task = self.tasks[task_id]
            card = TaskCard(self.task_scroll, task, on_resume=self._resume_task, on_pause=self._pause_task, on_stop=self._stop_task, on_delete=self._delete_task)
            card.pack(fill="x", padx=4, pady=(0, 8))
            self.task_cards[task_id] = card

        # 空列表时显示提示
        if not self.tasks:
            empty = ctk.CTkFrame(self.task_scroll, fg_color="transparent")
            empty.pack(fill="both", expand=True)
            ctk.CTkLabel(empty, text="📭", font=("", 32), text_color=COLORS["muted"]).pack(pady=(40, 8))
            ctk.CTkLabel(empty, text="暂无下载任务", font=("", 11), text_color=COLORS["muted"]).pack()

    def _poll_progress(self):
        """定时刷新所有任务卡片的 UI（每 500ms 执行一次）"""
        for task_id, card in self.task_cards.items():
            card.update_ui()
        self.after(500, self._poll_progress)

    # ── 剪贴板监控：自动识别 M3U8 链接 ──

    def _start_clipboard_monitor(self):
        """启动剪贴板监控，检测到 M3U8 链接时自动填入 URL 输入框"""
        self._last_clipboard = ""
        self._clipboard_paused = False  # 应用失焦时暂停监控
        self.bind("<FocusIn>", lambda e: setattr(self, '_clipboard_paused', False))
        self.bind("<FocusOut>", lambda e: setattr(self, '_clipboard_paused', True))
        self._check_clipboard()

    def _check_clipboard(self):
        """定时检查剪贴板内容（仅窗口聚焦 + URL 为空时检查，每 2 秒一次）"""
        if not self._clipboard_paused and not self.url_var.get().strip():
            try:
                clipboard = self.clipboard_get()
                if clipboard != self._last_clipboard:
                    self._last_clipboard = clipboard
                    match = re.search(r'https?://\S+\.(m3u8|mp4)(\?\S*)?', clipboard, re.IGNORECASE)
                    if match:
                        self.url_var.set(match.group())
                        self._show_toast("已检测到 M3U8 链接")
            except Exception:
                pass
        self.after(2000, self._check_clipboard)

    def _show_toast(self, text, duration=2000):
        """显示简短提示信息"""
        toast = ctk.CTkLabel(self, text=text, font=("", 11),
                             fg_color=COLORS["accent"], text_color="white",
                             corner_radius=6, padx=12, pady=6)
        toast.place(relx=0.5, rely=0.95, anchor="center")
        self.after(duration, toast.destroy)

    # ── Ctrl+V 粘贴检测 ──

    def _setup_drop(self):
        """启用 Ctrl+V 粘贴检测"""
        self.bind("<Control-v>", self._on_paste)

    def _on_paste(self, event=None):
        """Ctrl+V 粘贴时检测 M3U8 链接"""
        try:
            clipboard = self.clipboard_get()
            match = re.search(r'https?://\S+\.(m3u8|mp4)(\?\S*)?', clipboard, re.IGNORECASE)
            if match:
                # 检测到 M3U8 链接，填入 URL 框，阻止默认粘贴
                self.url_var.set(match.group())
                self._show_toast("已填入 M3U8 链接")
                return "break"  # 阻止默认粘贴行为
            # 非 M3U8 内容，允许默认粘贴
        except Exception:
            pass
        except Exception:
            pass


if __name__ == "__main__":
    setup_logging()
    app = App()
    app.mainloop()
