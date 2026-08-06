"""AES-128 解密模块：解密 AES-128 加密的 TS 分片"""

import os
import logging
import threading
from typing import List, Optional

import requests
from Crypto.Cipher import AES

from m3u8_parser import Segment

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# 密钥缓存：同一个 key_url 只请求一次，避免重复网络请求
_key_cache: dict = {}
_key_cache_lock = threading.Lock()  # 线程安全锁（多线程解密时使用）


def fetch_key(key_url: str, headers: dict = None, proxy: str = "") -> bytes:
    """
    获取 AES-128 解密密钥

    密钥通常是一个 16 字节的文件，通过 HTTP 请求获取。
    使用缓存避免对同一个密钥 URL 重复请求。

    Args:
        key_url: 密钥文件的下载地址
        headers: 自定义请求头
        proxy: 代理地址

    Returns:
        16 字节密钥

    Raises:
        ValueError: 密钥长度不是 16 字节
    """
    # 先查缓存
    if key_url in _key_cache:
        return _key_cache[key_url]

    req_headers = {**DEFAULT_HEADERS, **(headers or {})}
    proxies = {"http": proxy, "https": proxy} if proxy else None

    resp = requests.get(key_url, headers=req_headers, timeout=30, proxies=proxies)
    resp.raise_for_status()
    key = resp.content
    if len(key) != 16:
        raise ValueError(
            f"AES-128 密钥长度错误：期望 16 字节，实际 {len(key)} 字节"
        )

    # 写入缓存（线程安全）
    with _key_cache_lock:
        _key_cache[key_url] = key
    return key


def decrypt_segment(
    encrypted_data: bytes,
    key: bytes,
    iv: bytes,
    media_sequence: int = 0,
) -> bytes:
    """
    使用 AES-128-CBC 模式解密数据

    AES-128-CBC 加密特点：
    - 密钥长度 16 字节
    - 使用 PKCS7 填充（解密后需去除）
    - IV 缺失时使用 MEDIA-SEQUENCE 编码为 128-bit big-endian（HLS 规范）

    Args:
        encrypted_data: 加密的二进制数据
        key: 16 字节 AES 密钥
        iv: 16 字节初始化向量（为空则使用 MEDIA-SEQUENCE）
        media_sequence: EXT-X-MEDIA-SEQUENCE 值

    Returns:
        解密后的数据（已去除 PKCS7 填充）
    """
    if not iv:
        # HLS 规范：IV 缺失时使用 MEDIA-SEQUENCE 编码为 128-bit big-endian
        iv = media_sequence.to_bytes(16, byteorder='big')

    cipher = AES.new(key, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(encrypted_data)

    # 去除 PKCS7 填充
    return _unpad_pkcs7(decrypted)


def _unpad_pkcs7(data: bytes) -> bytes:
    """
    去除 PKCS7 填充

    PKCS7 填充规则：在数据末尾添加 N 个值为 N 的字节（N = 1~16）。
    例如：原始数据 14 字节 → 填充 2 个 0x02 → 总长 16 字节。

    Raises:
        ValueError: 填充无效
    """
    if not data:
        raise ValueError("解密结果为空")

    pad_len = data[-1]
    if pad_len < 1 or pad_len > 16:
        raise ValueError("PKCS7 填充长度无效")

    expected = bytes([pad_len]) * pad_len
    if data[-pad_len:] != expected:
        raise ValueError("PKCS7 填充内容无效")

    return data[:-pad_len]


def _extract_segment_index(filepath: str) -> int:
    """从文件名中提取分片序号（如 000042.ts → 42）"""
    stem = os.path.splitext(os.path.basename(filepath))[0]
    return int(stem)


def decrypt_files(
    file_paths: List[str],
    segments: List[Segment],
    headers: dict = None,
    proxy: str = "",
    media_sequence: int = 0,
) -> List[str]:
    """
    批量解密 TS 分片文件

    采用原地解密方式：读取加密文件 → 解密 → 覆盖写回同一文件。
    无需额外的磁盘空间。

    Args:
        file_paths: 已下载的 TS 分片文件路径列表
        segments: 对应的分片信息列表（包含加密方式、密钥 URL、IV）
        headers: 自定义请求头
        proxy: 代理地址
        media_sequence: EXT-X-MEDIA-SEQUENCE 值（用于生成默认 IV）

    Returns:
        解密后的文件路径列表（与输入相同）

    Raises:
        ValueError: 不支持的加密方式
        RuntimeError: 任一分片解密失败
    """
    # 按 index 构建文件映射（不再依赖 zip 的位置对应）
    file_by_index: dict[int, str] = {}
    for filepath in file_paths:
        idx = _extract_segment_index(filepath)
        if idx in file_by_index:
            raise RuntimeError(f"分片 {idx} 有重复文件：{file_by_index[idx]} 和 {filepath}")
        file_by_index[idx] = filepath

    # 验证每个 segment 都有对应文件
    for seg in segments:
        if seg.index not in file_by_index:
            raise RuntimeError(f"分片 {seg.index} 没有对应的下载文件")

    decrypted_paths = []

    for seg in segments:
        filepath = file_by_index[seg.index]

        if not seg.encryption_method or seg.encryption_method == "NONE":
            # 无需解密，直接保留
            decrypted_paths.append(filepath)
            continue

        # 检查加密方法
        method = seg.encryption_method.upper()
        if method not in ("", "NONE", "AES-128"):
            raise ValueError(f"暂不支持的加密方式：{method}")

        try:
            # 获取 AES 密钥（带缓存，长度校验在 fetch_key 中）
            key = fetch_key(seg.key_url, headers, proxy)
            # 读取加密数据
            with open(filepath, "rb") as f:
                encrypted_data = f.read()
            # 使用分片自己的 IV：显式 IV 优先，否则用分片自己的 media_sequence number
            iv = seg.iv if seg.iv else seg.index.to_bytes(16, byteorder='big')
            # AES-128-CBC 解密
            decrypted_data = decrypt_segment(encrypted_data, key, iv, media_sequence=seg.index)
            # 原子写入：先写临时文件，再 replace
            tmp_path = filepath + ".decrypting"
            try:
                with open(tmp_path, "wb") as f:
                    f.write(decrypted_data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, filepath)
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
            decrypted_paths.append(filepath)
            logger.debug(f"分片 {seg.index} 解密成功")
        except Exception as e:
            raise RuntimeError(f"分片 {seg.index} 解密失败：{e}") from e

    return decrypted_paths
