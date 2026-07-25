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
        logger.warning(f"密钥长度为 {len(key)} 字节，期望 16 字节")

    # 写入缓存（线程安全）
    with _key_cache_lock:
        _key_cache[key_url] = key
    return key


def decrypt_segment(
    encrypted_data: bytes,
    key: bytes,
    iv: bytes,
) -> bytes:
    """
    使用 AES-128-CBC 模式解密数据

    AES-128-CBC 加密特点：
    - 密钥长度 16 字节
    - 使用 PKCS7 填充（解密后需去除）
    - IV 为空时使用 16 字节 0x00 作为默认 IV

    Args:
        encrypted_data: 加密的二进制数据
        key: 16 字节 AES 密钥
        iv: 16 字节初始化向量（为空则使用默认 IV）

    Returns:
        解密后的数据（已去除 PKCS7 填充）
    """
    if not iv:
        # 默认 IV：16 字节全零
        iv = b'\x00' * 16

    cipher = AES.new(key, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(encrypted_data)

    # 去除 PKCS7 填充
    return _unpad_pkcs7(decrypted)


def _unpad_pkcs7(data: bytes) -> bytes:
    """
    去除 PKCS7 填充

    PKCS7 填充规则：在数据末尾添加 N 个值为 N 的字节（N = 1~16）。
    例如：原始数据 14 字节 → 填充 2 个 0x02 → 总长 16 字节。
    """
    if not data:
        return data
    pad_len = data[-1]
    # 填充长度必须在 1-16 范围内
    if pad_len < 1 or pad_len > 16:
        return data
    # 验证填充字节是否一致（防止误判）
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
    批量解密 TS 分片文件

    采用原地解密方式：读取加密文件 → 解密 → 覆盖写回同一文件。
    无需额外的磁盘空间。

    Args:
        file_paths: 已下载的 TS 分片文件路径列表
        segments: 对应的分片信息列表（包含加密方式、密钥 URL、IV）
        headers: 自定义请求头
        proxy: 代理地址

    Returns:
        解密后的文件路径列表（与输入相同）
    """
    decrypted_paths = []

    for filepath, seg in zip(file_paths, segments):
        if not seg.encryption_method or seg.encryption_method == "NONE":
            # 无需解密，直接保留
            decrypted_paths.append(filepath)
            continue

        try:
            # 获取 AES 密钥（带缓存）
            key = fetch_key(seg.key_url, headers, proxy)
            # 读取加密数据
            with open(filepath, "rb") as f:
                encrypted_data = f.read()
            # AES-128-CBC 解密
            decrypted_data = decrypt_segment(encrypted_data, key, seg.iv)
            # 覆盖写回原文件
            with open(filepath, "wb") as f:
                f.write(decrypted_data)
            decrypted_paths.append(filepath)
            logger.debug(f"分片 {seg.index} 解密成功")
        except Exception as e:
            logger.error(f"分片 {seg.index} 解密失败: {e}")
            decrypted_paths.append(filepath)  # 解密失败时保留原文件

    return decrypted_paths
