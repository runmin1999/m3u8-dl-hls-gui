"""工具函数模块：M3U8获取、配置管理、任务持久化"""

import os
import json
import time
import ssl
import logging
import subprocess
import requests

logger = logging.getLogger(__name__)


def get_base_dir():
    """获取应用基础目录，兼容 PyInstaller 打包"""
    import sys
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# 任务历史文件路径（供 app.py 和 downloader 模块共同使用）
BASE_DIR = get_base_dir()
TASKS_HISTORY_FILE = os.path.join(BASE_DIR, "tasks_history.json")


def fetch_m3u8(url, headers=None, proxy=""):
    """获取 m3u8 文件内容，带重试和SSL错误处理"""
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
        **(headers or {})
    }
    proxies = {"http": proxy, "https": proxy} if proxy else None

    session = requests.Session()
    session.headers.update(req_headers)
    if proxies:
        session.proxies.update(proxies)

    last_error = None
    for attempt in range(1, 6):
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            return resp.text
        except ssl.SSLError as e:
            last_error = e
            logger.warning(f"SSL错误 (第{attempt}次): {e}")
            if attempt < 5:
                time.sleep(attempt * 2)
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
    """从完整 URL 中提取基础 URL"""
    return url.rsplit("/", 1)[0] + "/"


def format_speed(bps):
    """将字节/秒格式化为可读的速度字符串"""
    if bps >= 1024 * 1024:
        return f"速度: {bps / (1024 * 1024):.2f} MB/s"
    elif bps >= 1024:
        return f"速度: {bps / 1024:.2f} KB/s"
    return f"速度: {bps} B/s"


def check_ffmpeg():
    """检查 FFmpeg 是否可用，支持常见安装路径"""
    import shutil

    # 方法1: PATH 中查找
    if shutil.which("ffmpeg"):
        return True

    # 方法2: 常见安装路径（Windows）
    common_paths = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", ""), "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg", "bin", "ffmpeg.exe"),
    ]
    for path in common_paths:
        if os.path.isfile(path):
            bin_dir = os.path.dirname(path)
            if bin_dir not in os.environ.get("PATH", ""):
                os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
            return True

    return False


def load_config(config_file):
    """从文件加载配置"""
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(config, config_file):
    """保存配置到文件"""
    try:
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存配置失败: {e}")


def save_tasks(tasks_dict, tasks_file):
    """保存任务列表到文件"""
    try:
        with open(tasks_file, "w", encoding="utf-8") as f:
            json.dump([t.to_dict() for t in tasks_dict.values()], f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存任务失败: {e}")


def load_tasks(tasks_file):
    """从文件加载任务列表"""
    tasks = {}
    if not os.path.exists(tasks_file):
        return tasks
    try:
        with open(tasks_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data  # 返回原始数据，由调用者构建 DownloadTask
    except Exception as e:
        logger.warning(f"加载任务失败: {e}")
    return tasks


def check_mp4_moov_position(file_path):
    """
    检测 MP4 文件的 MOOV atom 是否在文件头部。

    MP4 文件由一个个 box 组成，每个 box 格式：[4字节大小][4字节类型]
    常见顶层 box：ftyp, moov, mdat, free, widev

    返回: "faststart" (MOOV在前，正常) / "normal" (MOOV在后，播放可能卡死) / "corrupt" (文件无MOOV，损坏) / "unknown"
    """
    try:
        file_size = os.path.getsize(file_path)
        if file_size < 8:
            return "corrupt"

        with open(file_path, "rb") as f:
            # 读取头部用于 box 解析
            header = f.read(min(2048, file_size))

            # 首先在头部查找 moov
            pos = 0
            header_boxes = []
            moov_in_header = False
            mdat_in_header = False
            while pos + 8 <= len(header):
                box_size = int.from_bytes(header[pos:pos+4], 'big')
                box_type = header[pos+4:pos+8].decode('ascii', errors='ignore')

                if box_size < 8:
                    break

                header_boxes.append(box_type)
                if box_type == "moov":
                    moov_in_header = True
                if box_type == "mdat":
                    mdat_in_header = True

                pos += box_size

            if moov_in_header:
                return "faststart"

            # 头部没有 moov，检查文件尾部是否有 moov
            # 读取文件最后 4096 字节搜索 moov box
            tail_size = min(4096, file_size)
            f.seek(max(0, file_size - tail_size))
            tail = f.read()

            # 在尾部搜索 "moov" 字符串（简单启发式）
            tail_text = tail.decode('ascii', errors='ignore')
            if "moov" in tail_text:
                return "normal"

            # 头部有 mdat 但整个文件都没有 moov → 文件损坏/不完整
            if mdat_in_header or len(header_boxes) > 0:
                return "corrupt"

            return "unknown"
    except Exception:
        return "unknown"
