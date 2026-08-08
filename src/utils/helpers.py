"""通用工具函数：基础目录、URL处理、格式化、配置管理、任务持久化"""

import os
import sys
import json
import time
import ssl
import logging
import subprocess
import threading
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

# 模块级写锁，保护 JSON 文件写入
_json_write_lock = threading.Lock()


def get_base_dir():
    """获取应用基础目录，兼容 PyInstaller 打包"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# 任务历史文件路径
BASE_DIR = get_base_dir()
TASKS_HISTORY_FILE = os.path.join(BASE_DIR, "tasks_history.json")


def _atomic_write_json(path, data):
    """原子写入 JSON 文件：先写临时文件，fsync，再 replace"""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp_path = path + ".tmp"

    with _json_write_lock:
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


def fetch_m3u8(url, headers=None, proxy=""):
    """获取 m3u8 文件内容，带重试和SSL错误处理"""
    from urllib.parse import urljoin as _urljoin
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
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


def format_size(n):
    """格式化文件大小"""
    if n < 1024:
        return f"{n}B"
    elif n < 1024 * 1024:
        return f"{n/1024:.0f}KB"
    elif n < 1024 * 1024 * 1024:
        return f"{n/1024/1024:.1f}MB"
    else:
        return f"{n/1024/1024/1024:.2f}GB"


def check_ffmpeg():
    """检查 FFmpeg 是否可用，支持常见安装路径"""
    import shutil
    if shutil.which("ffmpeg"):
        return True
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


def verify_media_file(file_path):
    """用 ffprobe 检查媒体文件完整性，返回验证信息"""
    result = {"verified": False, "error": None}
    try:
        import shutil as _shutil
        ffprobe_path = _shutil.which("ffprobe")
        if not ffprobe_path:
            result["error"] = "未找到 ffprobe"
            return result
        cmd = [ffprobe_path, "-v", "quiet", "-print_format", "json",
               "-show_format", "-show_streams", file_path]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            result["error"] = "ffprobe 执行失败"
            return result
        data = json.loads(r.stdout)
        fmt = data.get("format", {})
        streams = data.get("streams", [])
        duration_sec = float(fmt.get("duration", 0))
        if duration_sec > 0:
            h = int(duration_sec // 3600)
            m = int((duration_sec % 3600) // 60)
            s = int(duration_sec % 60)
            result["duration"] = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
        for s in streams:
            codec_type = s.get("codec_type")
            if codec_type == "video" and "video_codec" not in result:
                result["video_codec"] = s.get("codec_name", "unknown").upper()
                w, h = s.get("width"), s.get("height")
                if w and h:
                    result["resolution"] = f"{w}x{h}"
            elif codec_type == "audio" and "audio_codec" not in result:
                result["audio_codec"] = s.get("codec_name", "unknown").upper()
        result["verified"] = True
    except FileNotFoundError:
        result["error"] = "未找到 ffprobe"
    except Exception as e:
        result["error"] = str(e)
    return result


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
    """保存配置到文件（原子写入）"""
    try:
        _atomic_write_json(config_file, config)
    except Exception as e:
        logger.warning(f"保存配置失败: {e}")


def save_tasks(tasks_dict, tasks_file):
    """保存任务列表到文件（原子写入，先快照再写）"""
    try:
        tasks = list(tasks_dict.values())
        snapshot = [task.to_dict() for task in tasks]
        _atomic_write_json(tasks_file, snapshot)
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
        return data
    except Exception as e:
        logger.warning(f"加载任务失败: {e}")
    return tasks


def check_for_update(current_version, timeout=5):
    """检查 GitHub 最新 Release 版本"""
    try:
        repo = "runmin1999/m3u8-dl-hls-gui"
        api_url = f"https://api.github.com/repos/{repo}/releases/latest"
        resp = requests.get(api_url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        latest_tag = data.get("tag_name", "")
        release_url = data.get("html_url", "")
        if not latest_tag:
            return None
        def _parse_ver(tag):
            tag = tag.lstrip("v")
            parts = []
            for p in tag.split("."):
                try:
                    parts.append(int(p))
                except ValueError:
                    parts.append(0)
            return parts
        cur = _parse_ver(current_version)
        lat = _parse_ver(latest_tag)
        return {"has_update": lat > cur, "latest": latest_tag, "url": release_url}
    except Exception:
        return None
