"""m3u8-dl-hls-gui v0.28 - CustomTkinter 桌面应用"""

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

# 确保项目根目录在 sys.path 中
_project_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_dir not in sys.path:
    sys.path.insert(0, _project_dir)

from src.utils.helpers import (
    fetch_m3u8, get_base_url, format_speed, load_config, save_config,
    save_tasks, load_tasks, get_base_dir, TASKS_HISTORY_FILE, verify_media_file,
    check_for_update,
)
from src.models.download_task import DownloadTask, get_default_output_dir
from src.core.hls_parser import parse_m3u8
from src.core.segment_downloader import download_all
from src.core.decryptor import decrypt_files
from src.core.merger import merge_to_ts
from src.core.task_manager import start_download


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
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


logger = logging.getLogger(__name__)

# 任务历史记录和配置文件路径
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
        task.local_m3u8_content = item.get("local_m3u8_content", "")
        task.local_m3u8_base = item.get("local_m3u8_base", "")
        task.verification = item.get("verification", None)
        tasks[task.task_id] = task
    return tasks


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
        pass  # 菜单通过 App._active_context_menu 跟踪

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
                        # 取消待执行的关闭，先执行命令
                        pending = getattr(self, '_dismiss_pending', None)
                        if pending:
                            self.after_cancel(pending)
                            self._dismiss_pending = None
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

        # 通过 App 级别跟踪菜单，实现全局点击关闭
        app = self.winfo_toplevel()
        app._active_context_menu = (menu, self)

    def _hide_context_menu(self):
        """关闭右键菜单"""
        app = self.winfo_toplevel()
        if app._active_context_menu and isinstance(app._active_context_menu, tuple):
            menu, _ = app._active_context_menu
            try:
                menu.destroy()
            except Exception:
                pass
            app._active_context_menu = None

    def _on_outside_click(self, event):
        """点击外部关闭菜单"""
        app = self.winfo_toplevel()
        if app._active_context_menu and isinstance(app._active_context_menu, tuple):
            # 检查点击是否在菜单内
            try:
                menu, _ = app._active_context_menu
                x, y = event.x_root, event.y_root
                mx = menu.winfo_rootx()
                my = menu.winfo_rooty()
                mw = menu.winfo_width()
                mh = menu.winfo_height()
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
        # 更新下载速度和剩余时间（仅下载中显示）
        if t.status == "downloading" and t.download_speed > 0:
            speed_text = format_speed(t.download_speed)
            remaining = getattr(t, '_remaining_seconds', 0)
            if remaining > 0:
                rm, rs = divmod(remaining, 60)
                rh, rm = divmod(rm, 60)
                speed_text += f"  剩余 {rh:02d}:{rm:02d}:{rs:02d}" if rh > 0 else f"  剩余 {rm:02d}:{rs:02d}"
            self.speed_label.configure(text=speed_text)
        else:
            self.speed_label.configure(text="")
        # 更新操作描述（包含错误信息、输出路径和验证信息）
        action = t.current_action or ""
        if t.error:
            action += f"\n错误: {t.error}"
        if t.output_path:
            action += f"\n{t.output_path}"
        if t.verification and t.verification.get("verified"):
            v = t.verification
            parts = []
            if v.get("duration"):
                parts.append(f"时长 {v['duration']}")
            if v.get("resolution"):
                parts.append(v["resolution"])
            if v.get("video_codec"):
                parts.append(v["video_codec"])
            if v.get("audio_codec"):
                parts.append(v["audio_codec"])
            if parts:
                action += f"\n验证: {' | '.join(parts)}"
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
        self._dnd_available = False
        self.title("m3u8-dl-hls-gui v0.28")
        self.geometry("930x620")
        self.minsize(750, 500)
        self.configure(fg_color=COLORS["bg"])

        self.config_data = load_config(CONFIG_FILE)      # 加载用户配置
        self.tasks = _load_tasks()            # 加载历史任务
        self.task_cards = {}                  # task_id → TaskCard 映射
        self._active_context_menu = None      # 当前打开的右键菜单 (menu, owner_card)
        self._dismiss_pending = None           # 延迟关闭的 after ID
        self._queue_timer = None              # 队列调度定时器

        self._build_ui()
        self._refresh_task_list()
        self._poll_progress()  # 启动定时刷新
        self._start_clipboard_monitor()  # 启动剪贴板监控
        self._setup_drop()  # 启用拖拽
        self._schedule_queue()  # 启动任务队列调度
        self._check_update()  # 启动时检查更新
        # 全局点击/失焦监听：关闭右键菜单
        self.bind("<Button-1>", self._dismiss_context_menu)
        self.bind("<FocusOut>", self._dismiss_context_menu)

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
        url_frame = ctk.CTkFrame(form, fg_color="transparent")
        url_frame.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.url_var = ctk.StringVar()
        ctk.CTkEntry(url_frame, textvariable=self.url_var, placeholder_text="https://example.com/video.m3u8 / .mp4", **ek).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(url_frame, text="批量", width=48, height=34, font=("", 11), corner_radius=6, fg_color=COLORS["border"], text_color=COLORS["accent"], command=self._batch_import).pack(side="left", padx=(6, 0)); r += 1

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

        # 开始下载按钮 + 分析按钮 + 设置按钮
        btn_frame = ctk.CTkFrame(form, fg_color="transparent")
        btn_frame.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self._download_btn = ctk.CTkButton(btn_frame, text="开始下载", height=38, font=("", 13, "bold"), corner_radius=8, fg_color=COLORS["grad1"], hover_color=COLORS["grad2"], command=self._start_download)
        self._download_btn.pack(side="left", fill="x", expand=True)
        self._analyze_btn = ctk.CTkButton(btn_frame, text="分析", width=50, height=38, font=("", 12), corner_radius=8, fg_color=COLORS["border"], text_color=COLORS["accent"], command=self._analyze_url)
        self._analyze_btn.pack(side="left", padx=(6, 0))
        self._settings_btn = ctk.CTkButton(btn_frame, text="⚙", width=36, height=38, font=("", 14), corner_radius=8, fg_color=COLORS["border"], text_color=COLORS["text2"], command=self._show_settings)
        self._settings_btn.pack(side="left", padx=(6, 0))

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
        # 保留 config 中已有的隐藏选项（如 ffmpeg_concurrency），避免被覆盖
        self.config_data["workers"] = workers
        self.config_data["proxy"] = self.proxy_var.get().strip()
        self.config_data["headers"] = self.referer_var.get().strip()
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

    def _batch_import(self):
        """批量导入：支持 TXT 文件导入和手动粘贴多个链接"""
        dialog = ctk.CTkToplevel(self)
        dialog.title("批量导入")
        dialog.geometry("520x420")
        dialog.configure(fg_color=COLORS["bg"])
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="批量导入链接", font=("", 14, "bold"), text_color=COLORS["text"]).pack(padx=20, pady=(16, 8))
        ctk.CTkLabel(dialog, text="每行一个链接，支持 M3U8 和 MP4 格式", font=("", 11), text_color=COLORS["text2"]).pack(padx=20, pady=(0, 8))

        text框 = ctk.CTkTextbox(dialog, fg_color=COLORS["input"], text_color=COLORS["text"], font=("Consolas", 11), corner_radius=8, border_width=1, border_color=COLORS["border"])
        text框.pack(fill="both", expand=True, padx=20, pady=(0, 12))

        def load_txt():
            path = filedialog.askopenfilename(filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
            if path:
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    text框.delete("1.0", "end")
                    text框.insert("1.0", content)
                except Exception as e:
                    messagebox.showerror("错误", f"读取文件失败: {e}")

        def confirm():
            raw = text框.get("1.0", "end").strip()
            if not raw:
                dialog.destroy()
                return
            urls = [line.strip() for line in raw.splitlines() if line.strip() and not line.strip().startswith("#")]
            if not urls:
                messagebox.showwarning("提示", "未找到有效链接", parent=dialog)
                return
            # 用当前设置批量创建任务
            referer = self.referer_var.get().strip()
            output_dir = self.dir_var.get().strip() or get_default_output_dir()
            try:
                workers = max(1, min(100, int(self.workers_var.get())))
            except ValueError:
                workers = 20
            added = 0
            for url in urls:
                # 跳过已存在的 URL
                if any(t.url == url for t in self.tasks.values()):
                    continue
                # 从 URL 提取文件名
                from urllib.parse import urlparse
                parsed = urlparse(url)
                name_part = os.path.basename(parsed.path) or "output"
                name_part = os.path.splitext(name_part)[0]
                for ch in '<>:"/\\|?*\n\r\t':
                    name_part = name_part.replace(ch, '_')
                name_part = name_part.strip('. ') or "output"
                output_name = name_part + ".mp4"
                task_id = datetime.now().strftime("%Y%m%d%H%M%S%f") + f"_{added}"
                task = DownloadTask(task_id=task_id, url=url, output_name=output_name, output_dir=output_dir, workers=workers, proxy=self.proxy_var.get().strip(), custom_headers={})
                task.resolution = "最高分辨率"
                task.available_resolutions = ["最高分辨率"]
                task.available_audio_tracks = ["默认"]
                if referer:
                    task.custom_headers["Referer"] = referer
                self.tasks[task_id] = task
                added += 1
            _save_tasks(self.tasks)
            self._refresh_task_list()
            dialog.destroy()
            self._show_toast(f"已添加 {added} 个任务")

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkButton(btn_frame, text="导入 TXT", width=90, height=34, font=("", 11), corner_radius=6, fg_color=COLORS["border"], command=load_txt).pack(side="left")
        ctk.CTkButton(btn_frame, text="确认添加", height=34, font=("", 12, "bold"), corner_radius=6, fg_color=COLORS["grad1"], hover_color=COLORS["grad2"], command=confirm).pack(side="right")

    def _analyze_url(self):
        """分析链接：预解析 M3U8 显示清晰度/音轨/预计大小"""
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("警告", "请输入视频链接地址")
            return

        from urllib.parse import urlparse
        _parsed = urlparse(url)
        if _parsed.path.rstrip('/').lower().endswith('.mp4'):
            messagebox.showinfo("提示", "MP4 直链无需分析，可直接下载")
            return

        self._analyze_btn.configure(state="disabled", text="分析中...")

        def do_analyze():
            try:
                is_local = os.path.isfile(url)
                if is_local:
                    with open(url, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    base = get_base_url(url.replace("\\", "/"))
                else:
                    headers = {}
                    referer = self.referer_var.get().strip()
                    if referer:
                        headers['Referer'] = referer
                    content = fetch_m3u8(url, headers, self.proxy_var.get().strip())
                    base = get_base_url(url)
                playlist = parse_m3u8(content, base)

                # 如果是 Master Playlist，递归获取子播放列表信息
                stream_details = []
                if playlist.is_master and playlist.streams:
                    for s in playlist.streams:
                        detail = {"name": s.name, "bandwidth": s.bandwidth, "resolution": s.resolution}
                        # 尝试获取子播放列表的分片数和时长
                        try:
                            sub_url = s.url
                            if not sub_url.startswith("http"):
                                from urllib.parse import urljoin
                                sub_url = urljoin(base, sub_url)
                            sub_content = fetch_m3u8(sub_url, {}, self.proxy_var.get().strip())
                            sub_base = get_base_url(sub_url)
                            sub_playlist = parse_m3u8(sub_content, sub_base)
                            detail["segments"] = len(sub_playlist.segments)
                            detail["duration"] = sub_playlist.total_duration
                            detail["encrypted"] = any(seg.encryption_method for seg in sub_playlist.segments)
                        except Exception:
                            detail["segments"] = "?"
                            detail["duration"] = 0
                            detail["encrypted"] = False
                        stream_details.append(detail)
                else:
                    # Media Playlist
                    detail = {
                        "name": "当前流",
                        "bandwidth": 0,
                        "resolution": "",
                        "segments": len(playlist.segments),
                        "duration": playlist.total_duration,
                        "encrypted": any(seg.encryption_method for seg in playlist.segments),
                    }
                    stream_details.append(detail)

                self.after(0, lambda: self._show_analyze_result(stream_details, playlist))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("分析失败", str(e)))
            finally:
                self.after(0, lambda: self._analyze_btn.configure(state="normal", text="分析"))

        import threading
        threading.Thread(target=do_analyze, daemon=True).start()

    def _show_analyze_result(self, stream_details, playlist):
        """显示分析结果对话框"""
        dialog = ctk.CTkToplevel(self)
        dialog.title("分析结果")
        dialog.geometry("500x400")
        dialog.configure(fg_color=COLORS["bg"])
        dialog.transient(self)

        ctk.CTkLabel(dialog, text="视频分析结果", font=("", 14, "bold"), text_color=COLORS["text"]).pack(padx=20, pady=(16, 8))

        scroll = ctk.CTkScrollableFrame(dialog, fg_color=COLORS["card"], corner_radius=8)
        scroll.pack(fill="both", expand=True, padx=20, pady=(0, 12))

        for i, d in enumerate(stream_details):
            card = ctk.CTkFrame(scroll, fg_color=COLORS["input"], corner_radius=8)
            card.pack(fill="x", pady=(0, 8))

            name = d.get("name", f"流 {i+1}")
            res = d.get("resolution", "")
            bw = d.get("bandwidth", 0)
            segs = d.get("segments", "?")
            dur = d.get("duration", 0)
            enc = d.get("encrypted", False)

            # 标题行
            title = name
            if res:
                title += f" ({res})"
            if bw > 0:
                mbps = bw / 1000000
                title += f" - {mbps:.1f} Mbps"
            ctk.CTkLabel(card, text=title, font=("", 12, "bold"), text_color=COLORS["accent"], anchor="w").pack(fill="x", padx=12, pady=(8, 2))

            # 详情行
            info_parts = []
            if dur > 0:
                h = int(dur // 3600)
                m = int((dur % 3600) // 60)
                s = int(dur % 60)
                info_parts.append(f"时长 {h:02d}:{m:02d}:{s:02d}" if h > 0 else f"时长 {m:02d}:{s:02d}")
            if segs != "?":
                info_parts.append(f"{segs} 个分片")
            if enc:
                info_parts.append("AES 加密")
            if info_parts:
                ctk.CTkLabel(card, text=" | ".join(info_parts), font=("", 11), text_color=COLORS["text2"], anchor="w").pack(fill="x", padx=12, pady=(0, 8))

        # 底部信息
        total_dur = sum(d.get("duration", 0) for d in stream_details if isinstance(d.get("duration"), (int, float)))
        if total_dur > 0:
            h = int(total_dur // 3600)
            m = int((total_dur % 3600) // 60)
            s = int(total_dur % 60)
            dur_str = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
            ctk.CTkLabel(dialog, text=f"共 {len(stream_details)} 个流 | 总时长 {dur_str}", font=("", 11), text_color=COLORS["text2"]).pack(padx=20, pady=(0, 12))

        ctk.CTkButton(dialog, text="关闭", width=80, height=32, font=("", 11), corner_radius=6, fg_color=COLORS["border"], command=dialog.destroy).pack(pady=(0, 16))

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

        # 判断是否为 MP4 链接（仅当 URL 路径以 .mp4 结尾，排除路径中间含 .mp4 的 M3U8）
        from urllib.parse import urlparse
        _parsed = urlparse(url)
        is_mp4 = _parsed.path.rstrip('/').lower().endswith('.mp4')

        if is_mp4:
            # MP4 直接下载，无需解析 M3U8
            self._create_task(url, is_mp4=True)
        else:
            # M3U8：在子线程中解析获取分辨率列表
            def parse_and_create():
                try:
                    # 检测本地文件路径
                    is_local = os.path.isfile(url)
                    if is_local:
                        with open(url, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                        base = get_base_url(url.replace("\\", "/"))
                        # 存储本地内容供下载时使用
                        self._local_m3u8_content = content
                        self._local_m3u8_base = base
                    else:
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
        # 本地 M3U8 文件内容
        if hasattr(self, '_local_m3u8_content') and self._local_m3u8_content:
            task.local_m3u8_content = self._local_m3u8_content
            task.local_m3u8_base = getattr(self, '_local_m3u8_base', "")
            self._local_m3u8_content = ""
            self._local_m3u8_base = ""
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

    def _get_max_concurrent(self):
        """获取最大并行下载数（隐藏配置项）"""
        return max(1, min(10, self.config_data.get("max_concurrent", 3)))

    def _count_running(self):
        """统计当前正在下载的任务数"""
        return sum(1 for t in self.tasks.values() if t.status == "downloading")

    def _start_all(self):
        """开始所有待处理/已停止/已暂停/已失败的任务（受并发限制）"""
        for task in self.tasks.values():
            if task.status in ("pending", "stopped", "paused", "failed"):
                if self._count_running() < self._get_max_concurrent():
                    self._resume_task(task.task_id)
                else:
                    break  # 达到并发上限，等调度器启动
        self._schedule_queue()

    def _schedule_queue(self):
        """定时检查队列，有空位时自动启动等待中的任务"""
        if self._queue_timer:
            self.after_cancel(self._queue_timer)
        max_c = self._get_max_concurrent()
        running = self._count_running()
        if running < max_c:
            # 找到第一个等待中的任务启动
            for task in self.tasks.values():
                if task.status in ("pending", "stopped", "paused", "failed"):
                    if running >= max_c:
                        break
                    self._resume_task(task.task_id)
                    running += 1
        # 每秒检查一次
        self._queue_timer = self.after(1000, self._schedule_queue)

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
        start_download(task, self.tasks, on_progress=lambda t: None, resolution=resolution)

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

    def _show_settings(self):
        """显示设置对话框（所有配置项）"""
        dialog = ctk.CTkToplevel(self)
        dialog.title("设置")
        dialog.geometry("420x480")
        dialog.configure(fg_color=COLORS["bg"])
        dialog.transient(self)
        dialog.grab_set()

        # 深色标题栏
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes
                dialog.update_idletasks()
                tk_id = dialog.winfo_id()
                GetAncestor = ctypes.windll.user32.GetAncestor
                GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
                GetAncestor.restype = wintypes.HWND
                hwnd = GetAncestor(tk_id, 2)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
                )
            except Exception:
                pass

        ctk.CTkLabel(dialog, text="高级设置", font=("", 14, "bold"), text_color=COLORS["text"]).pack(padx=20, pady=(16, 12))

        scroll = ctk.CTkScrollableFrame(dialog, fg_color=COLORS["card"], corner_radius=8)
        scroll.pack(fill="both", expand=True, padx=20, pady=(0, 12))

        lk = {"font": ("", 11), "text_color": COLORS["text2"]}
        ek = {"height": 30, "font": ("Consolas", 11), "fg_color": COLORS["input"], "border_color": COLORS["border"], "text_color": COLORS["text"], "corner_radius": 6}

        # 配置项定义：(key, label, default, description)
        settings = [
            ("max_concurrent", "最大并行下载数", "3", "同时下载的任务上限（1-10）"),
            ("parallel_max", "MP4 分片并行数", "16", "curl --parallel-max（1-32）"),
            ("ffmpeg_concurrency", "FFmpeg 并发数", "2", "同时合并的任务上限（1-16）"),
            ("auto_update_check", "启动时检查更新", "false", "true/false，检查 GitHub 新版本"),
        ]

        vars_dict = {}
        for key, label, default, desc in settings:
            ctk.CTkLabel(scroll, text=label, font=("", 11, "bold"), text_color=COLORS["text"], anchor="w").pack(fill="x", padx=12, pady=(8, 2))
            ctk.CTkLabel(scroll, text=desc, font=("", 10), text_color=COLORS["muted"], anchor="w").pack(fill="x", padx=12, pady=(0, 4))
            current_val = str(self.config_data.get(key, default))
            var = ctk.StringVar(value=current_val)
            vars_dict[key] = var
            ctk.CTkEntry(scroll, textvariable=var, **ek).pack(fill="x", padx=12, pady=(0, 4))

        def save_settings():
            for key, var in vars_dict.items():
                val = var.get().strip()
                # 尝试解析为数字
                try:
                    val = int(val)
                except ValueError:
                    if val.lower() == "true":
                        val = True
                    elif val.lower() == "false":
                        val = False
                self.config_data[key] = val
            save_config(self.config_data, CONFIG_FILE)
            self._show_toast("设置已保存")
            dialog.destroy()

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkButton(btn_frame, text="恢复默认", width=80, height=34, font=("", 11), corner_radius=6, fg_color=COLORS["border"], text_color=COLORS["warning"], command=lambda: self._reset_settings(vars_dict)).pack(side="left")
        ctk.CTkButton(btn_frame, text="保存", height=34, font=("", 12, "bold"), corner_radius=6, fg_color=COLORS["grad1"], hover_color=COLORS["grad2"], command=save_settings).pack(side="right")

    def _reset_settings(self, vars_dict):
        """恢复默认设置"""
        defaults = {"max_concurrent": "3", "parallel_max": "16", "ffmpeg_concurrency": "2", "auto_update_check": "false"}
        for key, var in vars_dict.items():
            var.set(defaults.get(key, ""))

    def _check_update(self):
        """启动时检查 GitHub 最新版本（仅当 config.json 中 auto_update_check 为 true 时执行）"""
        if not self.config_data.get("auto_update_check", False):
            return
        current = "v0.28"
        def do_check():
            result = check_for_update(current, timeout=5)
            if result and result.get("has_update"):
                latest = result["latest"]
                url = result["url"]
                self.after(0, lambda: self._show_update_dialog(latest, url))
        threading.Thread(target=do_check, daemon=True).start()

    def _show_update_dialog(self, latest, url):
        """显示更新提示对话框"""
        dialog = ctk.CTkToplevel(self)
        dialog.title("发现新版本")
        dialog.geometry("380x180")
        dialog.configure(fg_color=COLORS["bg"])
        dialog.transient(self)
        dialog.grab_set()
        ctk.CTkLabel(dialog, text=f"发现新版本 {latest}", font=("", 14, "bold"), text_color=COLORS["success"]).pack(padx=20, pady=(20, 8))
        ctk.CTkLabel(dialog, text="前往 GitHub 下载最新版本？", font=("", 12), text_color=COLORS["text2"]).pack(padx=20, pady=(0, 16))
        def open_url():
            import webbrowser
            webbrowser.open(url)
            dialog.destroy()
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkButton(btn_frame, text="前往下载", height=34, font=("", 12, "bold"), corner_radius=6, fg_color=COLORS["grad1"], hover_color=COLORS["grad2"], command=open_url).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(btn_frame, text="稍后", width=60, height=34, font=("", 11), corner_radius=6, fg_color=COLORS["border"], command=dialog.destroy).pack(side="left", padx=(8, 0))

    def _dismiss_context_menu(self, event=None):
        """关闭当前打开的右键菜单（延迟50ms，避免按钮命令被中断）"""
        self._dismiss_pending = self.after(150, self._do_dismiss_context_menu)

    def _do_dismiss_context_menu(self):
        """实际执行关闭右键菜单"""
        self._dismiss_pending = None
        if self._active_context_menu and isinstance(self._active_context_menu, tuple):
            menu, owner = self._active_context_menu
            try:
                menu.destroy()
            except Exception:
                pass
            self._active_context_menu = None

    # ── Ctrl+V 粘贴检测 ──

    def _setup_drop(self):
        """启用 Ctrl+V 粘贴检测（支持本地文件路径和 URL）"""
        self.bind("<Control-v>", self._on_paste)

    def _on_paste(self, event=None):
        """Ctrl+V 粘贴时检测 M3U8 链接或本地文件路径"""
        try:
            clipboard = self.clipboard_get().strip()
            if not clipboard:
                return
            # 检测 URL
            match = re.search(r'https?://\S+\.(m3u8|mp4)(\?\S*)?', clipboard, re.IGNORECASE)
            if match:
                self.url_var.set(match.group())
                self._show_toast("已填入视频链接")
                return "break"
            # 检测本地文件路径
            if os.path.isfile(clipboard):
                ext = os.path.splitext(clipboard)[1].lower()
                if ext in (".m3u8", ".mp4"):
                    self._load_local_file(clipboard)
                    return "break"
                else:
                    self._show_toast("仅支持 .m3u8 和 .mp4 文件")
                    return "break"
        except Exception:
            pass

    def _load_local_file(self, filepath):
        """加载本地 .m3u8 或 .mp4 文件"""
        ext = os.path.splitext(filepath)[1].lower()
        if ext == ".mp4":
            self.url_var.set(filepath)
            self.name_var.set(os.path.basename(filepath))
            self._show_toast(f"已加载本地 MP4: {os.path.basename(filepath)}")
        elif ext == ".m3u8":
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
            except UnicodeDecodeError:
                with open(filepath, "r", encoding="latin-1") as f:
                    content = f.read()
            if "#EXTM3U" not in content:
                self._show_toast("不是有效的 M3U8 文件")
                return
            # 将文件内容作为自定义 M3U8 内容存入，用文件所在目录作为 base URL
            file_dir = os.path.dirname(os.path.abspath(filepath)).replace("\\", "/") + "/"
            self.url_var.set(file_dir + os.path.basename(filepath))
            self.name_var.set(os.path.basename(filepath))
            # 存储本地 M3U8 内容，供下载时使用
            self._local_m3u8_content = content
            self._local_m3u8_base = file_dir
            self._show_toast(f"已加载本地 M3U8: {os.path.basename(filepath)}")
        self._download_btn.configure(state="normal")



if __name__ == "__main__":
    setup_logging()
    app = App()
    app.mainloop()
