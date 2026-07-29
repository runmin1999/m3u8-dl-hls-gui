"""工具函数模块：M3U8获取、配置管理、任务持久化"""

import os
import json
import time
import ssl
import logging
import subprocess
import requests

logger = logging.getLogger(__name__)


def fetch_m3u8(url, headers=None, proxy=""):
    """获取 m3u8 文件内容，带重试和SSL错误处理"""
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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
    """检查 FFmpeg 是否可用"""
    try:
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            startupinfo=startupinfo,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
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
