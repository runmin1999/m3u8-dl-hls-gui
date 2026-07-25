"""m3u8 解析模块：解析 master/media playlist，提取 TS 分片列表"""

import re
from urllib.parse import urljoin
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Segment:
    """TS 分片信息"""
    url: str
    duration: float = 0.0
    index: int = 0
    # AES-128 解密信息
    encryption_method: str = ""  # "AES-128" 或空
    key_url: str = ""
    iv: bytes = b""


@dataclass
class StreamInfo:
    """多码率流信息（master playlist 中的条目）"""
    bandwidth: int = 0
    resolution: str = ""
    url: str = ""
    name: str = ""


@dataclass
class M3U8Playlist:
    """m3u8 播放列表"""
    is_master: bool = False
    streams: List[StreamInfo] = field(default_factory=list)
    segments: List[Segment] = field(default_factory=list)
    target_duration: float = 0.0
    total_duration: float = 0.0
    # 当前生效的加密信息（会向下传递）
    encryption_method: str = ""
    key_url: str = ""
    iv: bytes = b""


def parse_m3u8(content: str, base_url: str = "") -> M3U8Playlist:
    """
    解析 m3u8 文本内容

    Args:
        content: m3u8 文件文本内容
        base_url: 用于拼接相对路径的基础 URL

    Returns:
        M3U8Playlist 对象
    """
    lines = content.strip().splitlines()
    if not lines or "#EXTM3U" not in lines[0]:
        raise ValueError("无效的 m3u8 文件：缺少 #EXTM3U 头")

    # 判断是 master 还是 media playlist
    is_master = any("#EXT-X-STREAM-INF" in line for line in lines)

    playlist = M3U8Playlist(is_master=is_master)

    if is_master:
        _parse_master(lines, base_url, playlist)
    else:
        _parse_media(lines, base_url, playlist)

    return playlist


def _parse_master(lines: list, base_url: str, playlist: M3U8Playlist):
    """解析 master playlist"""
    current_stream = None

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#EXTM3U"):
            continue

        if line.startswith("#EXT-X-STREAM-INF:"):
            current_stream = StreamInfo()
            attrs = _parse_attributes(line[len("#EXT-X-STREAM-INF:"):])
            current_stream.bandwidth = int(attrs.get("BANDWIDTH", "0"))
            current_stream.resolution = attrs.get("RESOLUTION", "")
            name = attrs.get("NAME", "")
            if name:
                current_stream.name = name
            elif current_stream.resolution:
                current_stream.name = current_stream.resolution
            else:
                current_stream.name = f"{current_stream.bandwidth}bps"

        elif line.startswith("#EXT-X-MEDIA:"):
            # 处理独立音频/字幕轨道，这里只取 NAME
            attrs = _parse_attributes(line[len("#EXT-X-MEDIA:"):])
            if attrs.get("TYPE") == "AUDIO" and "URI" in attrs:
                stream = StreamInfo()
                stream.name = attrs.get("NAME", "Audio")
                stream.url = _resolve_url(base_url, attrs["URI"])
                playlist.streams.append(stream)

        elif not line.startswith("#") and current_stream is not None:
            current_stream.url = _resolve_url(base_url, line)
            playlist.streams.append(current_stream)
            current_stream = None


def _parse_media(lines: list, base_url: str, playlist: M3U8Playlist):
    """解析 media playlist"""
    current_duration = 0.0
    seg_index = 0
    # 当前生效的加密参数（EXT-X-KEY 会向下传递）
    enc_method = ""
    key_url = ""
    iv = b""

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#EXTM3U"):
            continue

        if line.startswith("#EXT-X-TARGETDURATION:"):
            playlist.target_duration = float(line.split(":")[1])

        elif line.startswith("#EXT-X-KEY:"):
            attrs = _parse_attributes(line[len("#EXT-X-KEY:"):])
            enc_method = attrs.get("METHOD", "NONE")
            if enc_method == "NONE":
                enc_method = ""
                key_url = ""
                iv = b""
            else:
                key_url = _resolve_url(base_url, attrs.get("URI", ""))
                iv_str = attrs.get("IV", "")
                iv = _parse_iv(iv_str)

        elif line.startswith("#EXTINF:"):
            # 格式: #EXTINF:duration,title
            duration_part = line[len("#EXTINF:"):]
            current_duration = float(duration_part.split(",")[0])

        elif not line.startswith("#"):
            # 这是一个 TS 分片 URL
            seg = Segment(
                url=_resolve_url(base_url, line),
                duration=current_duration,
                index=seg_index,
                encryption_method=enc_method,
                key_url=key_url,
                iv=iv,
            )
            playlist.segments.append(seg)
            playlist.total_duration += current_duration
            seg_index += 1
            current_duration = 0.0

    playlist.encryption_method = enc_method
    playlist.key_url = key_url
    playlist.iv = iv


def _parse_attributes(attr_str: str) -> dict:
    """解析 m3u8 属性字符串，如 BANDWIDTH=800000,RESOLUTION=640x360"""
    result = {}
    # 匹配 KEY=VALUE 或 KEY="VALUE"
    pattern = re.compile(r'([A-Z0-9_-]+)=("([^"]*)"|([^,]*))')
    for m in pattern.finditer(attr_str):
        key = m.group(1)
        value = m.group(3) if m.group(3) is not None else m.group(4)
        result[key] = value
    return result


def _resolve_url(base_url: str, url: str) -> str:
    """将相对 URL 拼接为绝对 URL"""
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if base_url:
        return urljoin(base_url, url)
    return url


def _parse_iv(iv_str: str) -> bytes:
    """解析 IV 字段，如 0x1234... -> bytes"""
    if not iv_str:
        return b""
    if iv_str.startswith("0x") or iv_str.startswith("0X"):
        iv_str = iv_str[2:]
    return bytes.fromhex(iv_str)
