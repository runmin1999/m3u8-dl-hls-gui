"""MISSAV M3U8 GUI Downloader v0.10 - CustomTkinter Desktop Application"""

import os
import sys
import json
import threading
import hashlib
import logging
import shutil
import time
import ssl
import customtkinter as ctk
from tkinter import filedialog, messagebox
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── 获取基础目录（兼容 PyInstaller 打包） ──
def get_base_dir():
    """获取应用基础目录，兼容 PyInstaller 打包"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包模式，使用 exe 所在目录
        return os.path.dirname(sys.executable)
    else:
        # 正常 Python 运行模式
        return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_dir()

# ── 日志配置：记录到 Logs 文件夹 ──
LOGS_DIR = os.path.join(BASE_DIR, "Logs")
os.makedirs(LOGS_DIR, exist_ok=True)

def setup_logging():
    """配置日志，同时输出到控制台和文件"""
    now = datetime.now()
    log_filename = now.strftime(f"{now.year}-{now.month:02d}-{now.day:02d}_{now.hour:02d}-{now.minute:02d}-{now.second:03d}-{now.microsecond // 1000:03d}.log")
    log_file = os.path.join(LOGS_DIR, log_filename)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    
    # 文件处理器
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    
    # 控制台处理器
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

TASKS_HISTORY_FILE = os.path.join(BASE_DIR, "tasks_history.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

COLORS = {
    "bg": "#0a0a0f", "card": "#1a1a24", "input": "#0f0f15",
    "border": "#2a2a3a", "text": "#ffffff", "text2": "#a0a0b0",
    "muted": "#606070", "accent": "#6366f1", "accent2": "#818cf8",
    "success": "#22c55e", "error": "#ef4444", "warning": "#f59e0b",
    "grad1": "#6366f1", "grad2": "#8b5cf6",
}

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def get_default_output_dir():
    downloads = os.path.join(BASE_DIR, "Downloads")
    os.makedirs(downloads, exist_ok=True)
    return downloads


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存配置失败: {e}")


def fetch_m3u8(url, headers=None, proxy=""):
    """获取 m3u8 文件内容，带重试和SSL错误处理"""
    req_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", **(headers or {})}
    proxies = {"http": proxy, "https": proxy} if proxy else None
    last_error = None
    
    # 创建支持旧版SSL的session
    session = requests.Session()
    session.headers.update(req_headers)
    if proxies:
        session.proxies.update(proxies)
    
    for attempt in range(1, 6):  # 最多重试5次
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            return resp.text
        except ssl.SSLError as e:
            last_error = e
            logger.warning(f"SSL错误 (第{attempt}次): {e}")
            if attempt < 5:
                time.sleep(attempt * 2)  # SSL错误等待更久
        except requests.exceptions.ConnectionError as e:
            last_error = e
            logger.warning(f"连接错误 (第{attempt}次): {e}")
            if attempt < 5:
                time.sleep(attempt)
        except requests.exceptions.RequestException as e:
            last_error = e
            logger.warning(f"请求错误 (第{attempt}次): {e}")
            if attempt < 5:
                time.sleep(attempt)
        except Exception as e:
            last_error = e
            logger.warning(f"未知错误 (第{attempt}次): {e}")
            if attempt < 5:
                time.sleep(attempt)
    raise last_error


def get_base_url(url):
    return url.rsplit("/", 1)[0] + "/"


def format_speed(bps):
    if bps >= 1024 * 1024:
        return f"速度: {bps / (1024 * 1024):.2f} MB/s"
    elif bps >= 1024:
        return f"速度: {bps / 1024:.2f} KB/s"
    return f"速度: {bps} B/s"


class DownloadTask:
    def __init__(self, task_id, url, output_name, output_dir, workers, proxy, custom_headers):
        self.task_id = task_id
        self.url = url
        self.output_name = output_name or "output.ts"
        self.output_dir = output_dir or get_default_output_dir()
        self.workers = workers
        self.proxy = proxy
        self.custom_headers = custom_headers or {}
        self.status = "pending"
        self.progress = 0
        self.total_segments = 0
        self.downloaded_segments = 0
        self.current_action = ""
        self.error = None
        self.output_path = None
        self.resolution = ""  # 当前下载的分辨率
        self.started_at = None
        self.finished_at = None
        self._stop_flag = False
        self._pause_flag = False
        self._thread = None
        self._downloaded_indices = set()
        self._download_speed = 0
        self.available_resolutions = ["最高分辨率"]

    @property
    def download_speed(self):
        return self._download_speed

    def stop(self):
        self._stop_flag = True
        self.status = "stopped"
        self.current_action = "已停止"

    def pause(self):
        self._pause_flag = True
        self.status = "paused"
        self.current_action = "已暂停"

    def continue_task(self):
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
            "available_resolutions": getattr(self, 'available_resolutions', ["最高分辨率"]),
            "resolution": getattr(self, 'resolution', "最高分辨率"),
        }


def _save_tasks(tasks_dict):
    try:
        with open(TASKS_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump([t.to_dict() for t in tasks_dict.values()], f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存任务失败: {e}")


def _load_tasks():
    tasks = {}
    if not os.path.exists(TASKS_HISTORY_FILE):
        return tasks
    try:
        with open(TASKS_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            task = DownloadTask(
                task_id=item.get("task_id", ""), url=item.get("url", ""),
                output_name=item.get("output_name", "output.ts"),
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
            task.available_resolutions = item.get("available_resolutions", ["最高分辨率"])
            task.resolution = item.get("resolution", "最高分辨率")
            tasks[task.task_id] = task
    except Exception as e:
        logger.warning(f"加载任务失败: {e}")
    return tasks


def run_download(task, tasks_dict, on_progress=None, resolution="最高分辨率"):
    if task.status != "downloading":
        return
    try:
        task.status = "downloading"
        task.started_at = datetime.now()
        if on_progress:
            on_progress(task)
        if task._stop_flag:
            return

        task.current_action = "解析m3u8..."
        if on_progress:
            on_progress(task)

        content = fetch_m3u8(task.url, task.custom_headers, task.proxy)
        base_url = get_base_url(task.url)
        playlist = parse_m3u8(content, base_url)

        if playlist.is_master and playlist.streams:
            # 根据分辨率选择流
            selected_idx = 0  # 默认最高
            if resolution != "最高分辨率":
                for i, s in enumerate(playlist.streams):
                    name = s.name or f"{s.bandwidth}bps"
                    if name == resolution:
                        selected_idx = i
                        break
            else:
                selected_idx = max(range(len(playlist.streams)), key=lambda i: playlist.streams[i].bandwidth)
            task.resolution = playlist.streams[selected_idx].name or f"{playlist.streams[selected_idx].bandwidth}bps"
            stream_url = playlist.streams[selected_idx].url
            content = fetch_m3u8(stream_url, task.custom_headers, task.proxy)
            base_url = get_base_url(stream_url)
            playlist = parse_m3u8(content, base_url)

        if not playlist.segments:
            raise Exception("未找到TS分片")

        task.total_segments = len(playlist.segments)
        has_enc = any(s.encryption_method for s in playlist.segments)
        enc_info = " [AES-128]" if has_enc else ""
        task.current_action = f"下载中 {task.total_segments} 个分片{enc_info}..."
        if on_progress:
            on_progress(task)

        output_path = os.path.join(task.output_dir, task.output_name)
        stable_id = hashlib.md5(task.url.encode()).hexdigest()[:12]
        temp_dir = os.path.join(task.output_dir, f".m3u8_temp_{stable_id}")

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
            temp_dir = os.path.join(task.output_dir, f".m3u8_temp_{stable_id}_retry")

        def stop_check():
            while task._pause_flag and not task._stop_flag:
                time.sleep(0.3)
            return task._stop_flag

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
            ts_files = decrypt_files(ts_files, playlist.segments, task.custom_headers, task.proxy)

        if task._stop_flag:
            return

        task.current_action = "合并中..."
        task.progress = 95
        if on_progress:
            on_progress(task)
        final_path = merge_to_ts(ts_files, output_path)

        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

        task.status = "completed"
        task.progress = 100
        task.current_action = "完成"
        task.output_path = final_path
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


class TaskCard(ctk.CTkFrame):
    STATUS_COLORS = {
        "pending": (COLORS["warning"], "#2d2206"), "downloading": (COLORS["accent"], "#1a1740"),
        "completed": (COLORS["success"], "#0a2612"), "failed": (COLORS["error"], "#2d0f0f"),
        "stopped": (COLORS["error"], "#2d0f0f"), "paused": (COLORS["warning"], "#2d2206"),
    }
    STATUS_TEXT = {
        "pending": "等待中", "downloading": "下载中", "completed": "已完成",
        "failed": "失败", "stopped": "已停止", "paused": "已暂停",
    }

    def __init__(self, master, task, on_resume, on_pause, on_stop, on_delete, **kwargs):
        super().__init__(master, fg_color=COLORS["card"], corner_radius=6, **kwargs)
        self.task = task
        self.on_resume = on_resume
        self.on_pause = on_pause
        self.on_stop = on_stop
        self.on_delete = on_delete
        self._build()
        self.update_ui()

    def _build(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(10, 4))
        # 分辨率下拉菜单
        self.task_resolution_var = ctk.StringVar(value=self.task.resolution or "最高分辨率")
        available = getattr(self.task, 'available_resolutions', ["最高分辨率"])
        self.task_resolution_combo = ctk.CTkOptionMenu(header, variable=self.task_resolution_var,
                                                        values=available, width=100, height=26,
                                                        font=("", 10), corner_radius=4,
                                                        fg_color=COLORS["input"], button_color=COLORS["border"],
                                                        command=self._on_resolution_change)
        self.task_resolution_combo.pack(side="left")
        # 状态徽章
        self.status_label = ctk.CTkLabel(header, text="", font=("", 10, "bold"), corner_radius=10, padx=8, pady=2)
        self.status_label.pack(side="left", padx=(6, 0))
        # 截断过长的文件名
        display_name = self.task.output_name
        if len(display_name) > 30:
            display_name = display_name[:27] + "..."
        self.filename_label = ctk.CTkLabel(header, text=display_name, font=("", 12, "bold"), text_color=COLORS["text"], anchor="w")
        self.filename_label.pack(side="left", padx=(8, 0))

        bar_frame = ctk.CTkFrame(self, fg_color=COLORS["input"], height=8, corner_radius=4)
        bar_frame.pack(fill="x", padx=16, pady=(0, 5))
        bar_frame.pack_propagate(False)
        self.progressbar = ctk.CTkProgressBar(bar_frame, height=8, corner_radius=4, progress_color=COLORS["accent"])
        self.progressbar.pack(fill="x", padx=2, pady=2)

        info = ctk.CTkFrame(self, fg_color="transparent")
        info.pack(fill="x", padx=16, pady=(0, 4))
        self.percent_label = ctk.CTkLabel(info, text="0%", font=("Consolas", 11, "bold"), text_color=COLORS["text"])
        self.percent_label.pack(side="left")
        self.segments_label = ctk.CTkLabel(info, text="0 / 0", font=("", 11), text_color=COLORS["text2"])
        self.segments_label.pack(side="left", padx=(8, 0))
        self.speed_label = ctk.CTkLabel(info, text="", font=("", 11), text_color=COLORS["accent"])
        self.speed_label.pack(side="right")

        self.action_label = ctk.CTkLabel(self, text="", font=("", 9), text_color=COLORS["muted"], anchor="w")
        # self.action_label.pack(fill="x", padx=16, pady=(2, 2))  # 暂时隐藏，需要时取消注释

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

    def update_ui(self):
        t = self.task
        fg, bg = self.STATUS_COLORS.get(t.status, (COLORS["muted"], COLORS["card"]))
        self.status_label.configure(text=self.STATUS_TEXT.get(t.status, t.status), text_color=fg, fg_color=bg)
        self.progressbar.set(t.progress / 100)
        self.percent_label.configure(text=f"{t.progress}%")
        self.segments_label.configure(text=f"{t.downloaded_segments} / {t.total_segments} 分片")
        if t.status == "downloading" and t.download_speed > 0:
            self.speed_label.configure(text=format_speed(t.download_speed))
        else:
            self.speed_label.configure(text="")
        action = t.current_action or ""
        if t.error:
            action += f"\n错误: {t.error}"
        if t.output_path:
            action += f"\n{t.output_path}"
        self.action_label.configure(text=action)
        self.btn_resume.pack_forget()
        self.btn_pause.pack_forget()
        self.btn_stop.pack_forget()
        if t.status in ("pending", "paused", "stopped", "failed"):
            self.btn_resume.pack(side="left", padx=(0, 6))
        elif t.status == "downloading":
            self.btn_pause.pack(side="left", padx=(0, 6))
            self.btn_stop.pack(side="left", padx=(0, 6))


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("M3U8 视频下载器 v0.10")
        self.geometry("900x620")
        self.minsize(750, 500)
        self.configure(fg_color=COLORS["bg"])

        self.config_data = load_config()
        self.tasks = _load_tasks()
        self.task_cards = {}

        self._build_ui()
        self._refresh_task_list()
        self._poll_progress()

    def _build_ui(self):
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=20, pady=20)

        # Left card: settings
        left_card = ctk.CTkFrame(container, fg_color=COLORS["card"], corner_radius=16, border_width=1, border_color=COLORS["border"])
        left_card.pack(side="left", fill="y", padx=(0, 15))

        title_frame = ctk.CTkFrame(left_card, fg_color="transparent")
        title_frame.pack(fill="x", padx=20, pady=(16, 12))
        icon_bg = ctk.CTkFrame(title_frame, fg_color="#1a1740", width=32, height=32, corner_radius=8)
        icon_bg.pack(side="left", padx=(0, 8))
        icon_bg.pack_propagate(False)
        ctk.CTkLabel(icon_bg, text="📥", font=("", 14)).pack(expand=True)
        ctk.CTkLabel(title_frame, text="下载设置", font=("", 14, "bold"), text_color=COLORS["text"]).pack(side="left")

        form = ctk.CTkFrame(left_card, fg_color="transparent")
        form.pack(fill="x", padx=20, pady=(0, 16))

        lk = {"font": ("", 11), "text_color": COLORS["text2"]}
        ek = {"height": 34, "font": ("Consolas", 11), "fg_color": COLORS["input"], "border_color": COLORS["border"], "text_color": COLORS["text"], "corner_radius": 6}

        r = 0
        ctk.CTkLabel(form, text="M3U8 链接地址", **lk).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 4)); r += 1
        self.url_var = ctk.StringVar()
        ctk.CTkEntry(form, textvariable=self.url_var, placeholder_text="https://example.com/video.m3u8", **ek).grid(row=r, column=0, columnspan=2, sticky="ew", pady=(0, 8)); r += 1

        ctk.CTkLabel(form, text="Referer 来源页（可选）", **lk).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 4)); r += 1
        self.referer_var = ctk.StringVar(value=self.config_data.get("headers", ""))
        self.referer_var.trace_add("write", lambda *a: self._auto_save())
        ctk.CTkEntry(form, textvariable=self.referer_var, placeholder_text="https://...", **ek).grid(row=r, column=0, columnspan=2, sticky="ew", pady=(0, 8)); r += 1

        ctk.CTkLabel(form, text="保存文件名", **lk).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 4)); r += 1
        self.name_var = ctk.StringVar(value="")
        ctk.CTkEntry(form, textvariable=self.name_var, placeholder_text="output", **ek).grid(row=r, column=0, columnspan=2, sticky="ew", pady=(0, 8)); r += 1

        ctk.CTkLabel(form, text="保存目录", **lk).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 4)); r += 1
        dir_frame = ctk.CTkFrame(form, fg_color="transparent")
        dir_frame.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.dir_var = ctk.StringVar(value="")
        ctk.CTkEntry(dir_frame, textvariable=self.dir_var, placeholder_text=get_default_output_dir(), **ek).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(dir_frame, text="选择", width=50, height=34, font=("", 11), corner_radius=6, fg_color=COLORS["border"], command=self._browse_dir).pack(side="left", padx=(6, 0)); r += 1

        ctk.CTkLabel(form, text="代理地址", **lk).grid(row=r, column=0, sticky="w")
        ctk.CTkLabel(form, text="线程数", **lk).grid(row=r, column=1, sticky="w", padx=(12, 0)); r += 1
        self.proxy_var = ctk.StringVar(value=self.config_data.get("proxy", ""))
        self.proxy_var.trace_add("write", lambda *a: self._auto_save())
        ctk.CTkEntry(form, textvariable=self.proxy_var, placeholder_text="http://127.0.0.1:7890", width=190, **ek).grid(row=r, column=0, sticky="w", pady=(0, 8))
        self.workers_var = ctk.StringVar(value=str(self.config_data.get("workers", 20)))
        self.workers_var.trace_add("write", lambda *a: self._auto_save())
        ctk.CTkEntry(form, textvariable=self.workers_var, width=50, **ek).grid(row=r, column=1, sticky="w", padx=(12, 0), pady=(0, 8)); r += 1

        # Buttons
        btn_frame = ctk.CTkFrame(form, fg_color="transparent")
        btn_frame.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self._download_btn = ctk.CTkButton(btn_frame, text="开始下载", height=38, font=("", 13, "bold"), corner_radius=8, fg_color=COLORS["grad1"], hover_color=COLORS["grad2"], command=self._start_download)
        self._download_btn.pack(fill="x")

        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)

        # Right card: progress
        right_card = ctk.CTkFrame(container, fg_color=COLORS["card"], corner_radius=16, border_width=1, border_color=COLORS["border"])
        right_card.pack(side="right", fill="both", expand=True)

        title_frame2 = ctk.CTkFrame(right_card, fg_color="transparent")
        title_frame2.pack(fill="x", padx=20, pady=(16, 12))
        icon_bg2 = ctk.CTkFrame(title_frame2, fg_color="#1a1740", width=32, height=32, corner_radius=8)
        icon_bg2.pack(side="left", padx=(0, 8))
        icon_bg2.pack_propagate(False)
        ctk.CTkLabel(icon_bg2, text="📊", font=("", 14)).pack(expand=True)
        ctk.CTkLabel(title_frame2, text="下载进度", font=("", 14, "bold"), text_color=COLORS["text"]).pack(side="left")
        ctk.CTkButton(title_frame2, text="清空列表", width=70, height=28, font=("", 11), corner_radius=6, fg_color="transparent", border_width=1, border_color=COLORS["warning"], text_color=COLORS["warning"], hover_color="#3d2a0a", command=self._clear_all).pack(side="right")
        ctk.CTkButton(title_frame2, text="停止所有", width=70, height=28, font=("", 11), corner_radius=6, fg_color="transparent", border_width=1, border_color=COLORS["error"], text_color=COLORS["error"], hover_color="#3d1a1a", command=self._stop_all).pack(side="right", padx=14)
        ctk.CTkButton(title_frame2, text="开始所有", width=70, height=28, font=("", 11), corner_radius=6, fg_color="transparent", border_width=1, border_color=COLORS["success"], text_color=COLORS["success"], hover_color="#1a3d1a", command=self._start_all).pack(side="right", padx=0)

        self.task_scroll = ctk.CTkScrollableFrame(right_card, fg_color=COLORS["bg"], corner_radius=12, scrollbar_button_color=COLORS["border"], scrollbar_button_hover_color=COLORS["accent"])
        self.task_scroll.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _validate_workers(self):
        """验证线程数输入，只允许数字，最多4位"""
        value = self.workers_var.get()
        # 只保留数字，最多4位
        value = ''.join(filter(str.isdigit, value))[:4]
        if value != self.workers_var.get():
            self.workers_var.set(value)
        self._auto_save()

    def _auto_save(self):
        try:
            workers = max(1, min(100, int(self.workers_var.get())))
        except ValueError:
            workers = 20
        self.config_data = {
            "workers": workers,
            "proxy": self.proxy_var.get().strip(),
            "headers": self.referer_var.get().strip(),
        }
        save_config(self.config_data)

    def _browse_dir(self):
        path = filedialog.askdirectory(title="选择保存目录")
        if path:
            self.dir_var.set(path)

    def _start_download(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("警告", "请输入M3U8地址")
            return
        for t in self.tasks.values():
            if t.url == url:
                messagebox.showinfo("提示", "该地址已有下载任务")
                return

        # 先解析 M3U8 获取可用分辨率
        self._download_btn.configure(state="disabled")
        self._available_resolutions = ["最高分辨率"]

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
            except Exception as e:
                logger.warning(f"获取分辨率失败: {e}")
            finally:
                self.after(0, lambda: self._create_task(url))

        import threading
        threading.Thread(target=parse_and_create, daemon=True).start()

    def _create_task(self, url):
        """创建下载任务"""
        output_name = self.name_var.get().strip() or "output"
        # 清理文件名中的非法字符
        illegal_chars = '<>:"/\\|?*\n\r\t'
        for ch in illegal_chars:
            output_name = output_name.replace(ch, '_')
        # 去除首尾空格和点
        output_name = output_name.strip('. ')
        if not output_name:
            output_name = "output"
        if not output_name.lower().endswith(".ts"):
            output_name += ".ts"
        try:
            workers = max(1, min(100, int(self.workers_var.get())))
        except ValueError:
            workers = 20
        output_dir = self.dir_var.get().strip() or get_default_output_dir()
        task_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
        task = DownloadTask(task_id=task_id, url=url, output_name=output_name, output_dir=output_dir, workers=workers, proxy=self.proxy_var.get().strip(), custom_headers={})
        task.resolution = "最高分辨率"  # 默认最高分辨率，可在任务卡片中修改
        task.available_resolutions = getattr(self, '_available_resolutions', ["最高分辨率"])  # 保存可用分辨率列表
        referer = self.referer_var.get().strip()
        if referer:
            task.custom_headers["Referer"] = referer
        self.tasks[task_id] = task
        _save_tasks(self.tasks)
        self._refresh_task_list()
        self._download_btn.configure(state="normal")  # 恢复按钮

    def _start_all(self):
        for task in self.tasks.values():
            if task.status in ("pending", "stopped", "paused", "failed"):
                self._resume_task(task.task_id)

    def _stop_all(self):
        for task in self.tasks.values():
            if task.status in ("pending", "downloading"):
                task.stop()
        _save_tasks(self.tasks)
        self._refresh_task_list()

    def _clear_all(self):
        # 停止所有运行中的任务
        for task in self.tasks.values():
            if task.status in ("pending", "downloading"):
                task.stop()
                if task._thread and task._thread.is_alive():
                    task._thread.join(timeout=5)
        # 清理所有临时文件
        for task in self.tasks.values():
            stable_id = hashlib.md5(task.url.encode()).hexdigest()[:12]
            for suffix in ("", "_retry"):
                td = os.path.join(task.output_dir, f".m3u8_temp_{stable_id}{suffix}")
                if os.path.exists(td):
                    try:
                        shutil.rmtree(td)
                    except Exception:
                        pass
        self.tasks.clear()
        _save_tasks(self.tasks)
        self._refresh_task_list()

    def _resume_task(self, task_id):
        task = self.tasks.get(task_id)
        if not task or task.status not in ("pending", "stopped", "paused", "failed"):
            return
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
        task = self.tasks.get(task_id)
        if task:
            task.pause()
            _save_tasks(self.tasks)

    def _stop_task(self, task_id):
        task = self.tasks.get(task_id)
        if task:
            task.stop()
            _save_tasks(self.tasks)

    def _delete_task(self, task_id):
        task = self.tasks.get(task_id)
        if not task:
            return
        if task.status in ("pending", "downloading"):
            task.stop()
            if task._thread and task._thread.is_alive():
                task._thread.join(timeout=5)
        stable_id = hashlib.md5(task.url.encode()).hexdigest()[:12]
        for suffix in ("", "_retry"):
            td = os.path.join(task.output_dir, f".m3u8_temp_{stable_id}{suffix}")
            if os.path.exists(td):
                try:
                    shutil.rmtree(td)
                except Exception:
                    pass
        # 如果任务已完成，删除输出的视频文件
        if task.status == "completed" and task.output_path:
            if os.path.exists(task.output_path):
                try:
                    os.remove(task.output_path)
                except Exception:
                    pass
        del self.tasks[task_id]
        _save_tasks(self.tasks)
        self._refresh_task_list()

    def _refresh_task_list(self):
        # 删除空状态提示
        for w in self.task_scroll.winfo_children():
            if not isinstance(w, TaskCard):
                w.destroy()

        # 只在任务数量变化时重建，否则只更新现有卡片
        current_ids = set(self.tasks.keys())
        existing_ids = set(self.task_cards.keys())

        # 删除不再存在的任务卡片
        for task_id in existing_ids - current_ids:
            if task_id in self.task_cards:
                self.task_cards[task_id].destroy()
                del self.task_cards[task_id]

        # 添加新任务卡片
        for task_id in current_ids - existing_ids:
            task = self.tasks[task_id]
            card = TaskCard(self.task_scroll, task, on_resume=self._resume_task, on_pause=self._pause_task, on_stop=self._stop_task, on_delete=self._delete_task)
            card.pack(fill="x", padx=4, pady=(0, 8))
            self.task_cards[task_id] = card

        # 空列表提示
        if not self.tasks:
            empty = ctk.CTkFrame(self.task_scroll, fg_color="transparent")
            empty.pack(fill="both", expand=True)
            ctk.CTkLabel(empty, text="📭", font=("", 32), text_color=COLORS["muted"]).pack(pady=(40, 8))
            ctk.CTkLabel(empty, text="暂无下载任务", font=("", 11), text_color=COLORS["muted"]).pack()

    def _poll_progress(self):
        for task_id, card in self.task_cards.items():
            card.update_ui()
        self.after(500, self._poll_progress)


if __name__ == "__main__":
    setup_logging()
    app = App()
    app.mainloop()
