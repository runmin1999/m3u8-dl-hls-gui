"""下载任务数据模型"""

from datetime import datetime


def get_default_output_dir():
    """获取默认下载目录（应用目录下的 Downloads 文件夹）"""
    import os
    from src.utils.helpers import get_base_dir
    downloads = os.path.join(get_base_dir(), "Downloads")
    os.makedirs(downloads, exist_ok=True)
    return downloads


class DownloadTask:
    """下载任务数据模型，保存单个下载任务的所有状态"""

    def __init__(self, task_id, url, output_name, output_dir, workers, proxy, custom_headers):
        self.task_id = task_id
        self.url = url
        self.output_name = output_name or "output.mp4"
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
        self.resolution = ""
        self.started_at = None
        self.finished_at = None
        self._stop_flag = False
        self._pause_flag = False
        self._thread = None
        self._downloaded_indices = set()
        self._mp4_downloaded = 0
        self._download_speed = 0
        self._remaining_seconds = 0
        self._dl_id = 0
        self.available_resolutions = ["最高分辨率"]
        self.audio_track = ""
        self.available_audio_tracks = []
        self._audio_track_url = ""
        self.local_m3u8_content = ""
        self.local_m3u8_base = ""
        self.verification = None

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
            "mp4_downloaded": getattr(self, '_mp4_downloaded', 0),
            "available_resolutions": getattr(self, 'available_resolutions', ["最高分辨率"]),
            "resolution": getattr(self, 'resolution', "最高分辨率"),
            "audio_track": getattr(self, 'audio_track', ""),
            "available_audio_tracks": getattr(self, 'available_audio_tracks', []),
            "audio_track_url": getattr(self, '_audio_track_url', ""),
            "local_m3u8_content": getattr(self, 'local_m3u8_content', ""),
            "local_m3u8_base": getattr(self, 'local_m3u8_base', ""),
            "verification": getattr(self, 'verification', None),
        }
