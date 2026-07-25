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

# 缓存已下载的 key，避免重复请求
_key_cache: dict = {}
_key_cache_lock = threading.Lock()


def fetch_key(key_url: str, headers: dict = None, proxy: str = "") -> bytes:
    """
    获取 AES-128 解密密钥

    Args:
        key_url: 密钥 URL
        headers: 自定义请求头
        proxy: 代理地址

    Returns:
        16 字节密钥
    """
    if key_url in _key_cache:
        return _key_cache[key_url]

    req_headers = {**DEFAULT_HEADERS, **(headers or {})}
    proxies = {"http": proxy, "https": proxy} if proxy else None

    resp = requests.get(key_url, headers=req_headers, timeout=30, proxies=proxies)
    resp.raise_for_status()
    key = resp.content
    if len(key) != 16:
        logger.warning(f"密钥长度为 {len(key)} 字节，期望 16 字节")

    with _key_cache_lock:
        _key_cache[key_url] = key
    return key


def decrypt_segment(
    encrypted_data: bytes,
    key: bytes,
    iv: bytes,
) -> bytes:
    """
    使用 AES-128-CBC 解密数据

    Args:
        encrypted_data: 加密数据
        key: 16 字节密钥
        iv: 16 字节初始化向量（为空则使用默认 IV）

    Returns:
        解密后的数据（已去除 PKCS7 填充）
    """
    if not iv:
        # 默认 IV：16 字节 0x00
        iv = b'\x00' * 16

    cipher = AES.new(key, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(encrypted_data)

    # 去除 PKCS7 填充
    return _unpad_pkcs7(decrypted)


def _unpad_pkcs7(data: bytes) -> bytes:
    """去除 PKCS7 填充"""
    if not data:
        return data
    pad_len = data[-1]
    if pad_len < 1 or pad_len > 16:
        return data
    # 验证填充是否有效
    if data[-pad_len:] != bytes([pad_len]) * pad_len:
        return data
    return data[:-pad_len]


def decrypt_files(
    file_paths: List[str],
    segments: List[Segment],
    headers: dict = None,
    proxy: str = "",
) -> List[str]:
    """
    批量解密 TS 分片文件（原地解密，覆盖原文件）

    Args:
        file_paths: 已下载的 TS 分片文件路径列表
        segments: 对应的分片信息列表
        headers: 自定义请求头
        proxy: 代理地址

    Returns:
        解密后的文件路径列表
    """
    decrypted_paths = []

    for filepath, seg in zip(file_paths, segments):
        if not seg.encryption_method or seg.encryption_method == "NONE":
            # 无需解密
            decrypted_paths.append(filepath)
            continue

        try:
            # 获取密钥
            key = fetch_key(seg.key_url, headers, proxy)
            # 读取加密数据
            with open(filepath, "rb") as f:
                encrypted_data = f.read()
            # 解密
            decrypted_data = decrypt_segment(encrypted_data, key, seg.iv)
            # 覆盖写入
            with open(filepath, "wb") as f:
                f.write(decrypted_data)
            decrypted_paths.append(filepath)
            logger.debug(f"分片 {seg.index} 解密成功")
        except Exception as e:
            logger.error(f"分片 {seg.index} 解密失败: {e}")
            decrypted_paths.append(filepath)  # 保留原文件

    return decrypted_paths
