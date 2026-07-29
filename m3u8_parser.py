"""m3u8 解析模块：解析 master/media playlist，提取 TS 分片列表"""

import re
from urllib.parse import urljoin
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Segment:
    """TS 分片信息"""
    url: str                    # 分片下载地址
    duration: float = 0.0       # 分片时长（秒）
    index: int = 0              # 分片序号（从 0 开始）
    # AES-128 解密信息
    encryption_method: str = "" # 加密方式："AES-128" 或空（无加密）
    key_url: str = ""           # 密钥下载地址
    iv: bytes = b""             # 初始化向量（16 字节）


@dataclass
class StreamInfo:
    """多码率流信息（master playlist 中的条目）"""
    bandwidth: int = 0      # 码率（bps）
    resolution: str = ""    # 分辨率（如 "1920x1080"）
    url: str = ""           # 对应 media playlist 的 URL
    name: str = ""          # 流名称（用于 UI 显示）


@dataclass
class M3U8Playlist:
    """m3u8 播放列表"""
    is_master: bool = False             # 是否为 Master Playlist（多码率）
    streams: List[StreamInfo] = field(default_factory=list)  # 码率流列表（仅 master）
    segments: List[Segment] = field(default_factory=list)     # TS 分片列表（仅 media）
    target_duration: float = 0.0        # 单个分片目标时长
    total_duration: float = 0.0         # 视频总时长（秒）
    # 当前生效的加密信息（EXT-X-KEY 会向下传递给后续分片）
    encryption_method: str = ""
    key_url: str = ""
    iv: bytes = b""
    media_sequence: int = 0  # EXT-X-MEDIA-SEQUENCE 值（分片起始序号）


def parse_m3u8(content: str, base_url: str = "") -> M3U8Playlist:
    """
    解析 m3u8 文本内容

    M3U8 有两种类型：
    1. Master Playlist：包含多个码率的流，每个流指向一个 Media Playlist
    2. Media Playlist：包含实际的 TS 分片下载地址

    Args:
        content: m3u8 文件文本内容
        base_url: 用于拼接相对路径的基础 URL

    Returns:
        M3U8Playlist 对象
    """
    lines = content.strip().splitlines()
    if not lines or "#EXTM3U" not in lines[0]:
        raise ValueError("无效的 m3u8 文件：缺少 #EXTM3U 头")

    # 通过检测 #EXT-X-STREAM-INF 判断是否为 Master Playlist
    is_master = any("#EXT-X-STREAM-INF" in line for line in lines)

    playlist = M3U8Playlist(is_master=is_master)

    if is_master:
        _parse_master(lines, base_url, playlist)
    else:
        _parse_media(lines, base_url, playlist)

    return playlist


def _parse_master(lines: list, base_url: str, playlist: M3U8Playlist):
    """
    解析 Master Playlist

    Master Playlist 包含多个码率流的入口，格式示例：
    #EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
    360p.m3u8
    #EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720
    720p.m3u8
    """
    current_stream = None

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#EXTM3U"):
            continue

        if line.startswith("#EXT-X-STREAM-INF:"):
            # 解析码率流属性
            current_stream = StreamInfo()
            attrs = _parse_attributes(line[len("#EXT-X-STREAM-INF:"):])
            current_stream.bandwidth = int(attrs.get("BANDWIDTH", "0"))
            current_stream.resolution = attrs.get("RESOLUTION", "")
            # 优先使用 NAME，否则用分辨率或码率作为显示名称
            name = attrs.get("NAME", "")
            if name:
                current_stream.name = name
            elif current_stream.resolution:
                current_stream.name = current_stream.resolution
            else:
                current_stream.name = f"{current_stream.bandwidth}bps"

        elif line.startswith("#EXT-X-MEDIA:"):
            # 处理独立音频/字幕轨道，这里只提取音频轨道
            attrs = _parse_attributes(line[len("#EXT-X-MEDIA:"):])
            if attrs.get("TYPE") == "AUDIO" and "URI" in attrs:
                stream = StreamInfo()
                stream.name = attrs.get("NAME", "Audio")
                stream.url = _resolve_url(base_url, attrs["URI"])
                playlist.streams.append(stream)

        elif not line.startswith("#") and current_stream is not None:
            # 非注释行 = 码率流对应的 Media Playlist URL
            current_stream.url = _resolve_url(base_url, line)
            playlist.streams.append(current_stream)
            current_stream = None


def _parse_media(lines: list, base_url: str, playlist: M3U8Playlist):
    """
    解析 Media Playlist

    Media Playlist 包含实际的 TS 分片信息，格式示例：
    #EXT-X-TARGETDURATION:10
    #EXT-X-KEY:METHOD=AES-128,URI="key.bin",IV=0x1234...
    #EXTINF:9.009,
    segment000.ts
    #EXTINF:9.009,
    segment001.ts
    """
    current_duration = 0.0
    seg_index = playlist.media_sequence
    # 当前生效的加密参数（EXT-X-KEY 会向下传递给后续分片，直到遇到新的 EXT-X-KEY）
    enc_method = ""
    key_url = ""
    iv = b""

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#EXTM3U"):
            continue

        if line.startswith("#EXT-X-TARGETDURATION:"):
            # 每个分片的目标最大时长（秒）
            playlist.target_duration = float(line.split(":")[1])

        elif line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            # 分片起始序号（直播流/时移流使用）
            playlist.media_sequence = int(line.split(":")[1])
            seg_index = playlist.media_sequence

        elif line.startswith("#EXT-X-KEY:"):
            # 解析加密信息：METHOD=AES-128, URI="key.bin", IV=0x...
            attrs = _parse_attributes(line[len("#EXT-X-KEY:"):])
            enc_method = attrs.get("METHOD", "NONE")
            if enc_method == "NONE":
                # METHOD=NONE 表示后续分片不加密
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
            # 非注释行 = TS 分片 URL，关联当前的时长和加密信息
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
    """
    解析 m3u8 属性字符串

    示例输入: BANDWIDTH=800000,RESOLUTION=640x360,CODECS="avc1.64001e,mp4a.40.2"
    示例输出: {"BANDWIDTH": "800000", "RESOLUTION": "640x360", "CODECS": "avc1.64001e,mp4a.40.2"}
    """
    result = {}
    # 正则匹配 KEY=VALUE 或 KEY="VALUE" 格式
    # ([A-Z0-9_-]+)  匹配属性名（大写字母、数字、下划线、连字符）
    # ("([^"]*)"|([^,]*))  匹配带引号的值或不带引号的值
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
    # 如果已经是绝对 URL，直接返回
    if url.startswith("http://") or url.startswith("https://"):
        return url
    # 使用 urljoin 拼接相对路径
    if base_url:
        return urljoin(base_url, url)
    return url


def _parse_iv(iv_str: str) -> bytes:
    """
    解析 IV 字段

    IV 格式: 0x1234567890ABCDEF1234567890ABCDEF
    转换为 16 字节的 bytes 对象
    """
    if not iv_str:
        return b""
    # 去掉 0x 前缀
    if iv_str.startswith("0x") or iv_str.startswith("0X"):
        iv_str = iv_str[2:]
    return bytes.fromhex(iv_str)
