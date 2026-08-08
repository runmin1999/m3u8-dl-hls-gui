"""AES-128 解密模块测试"""

import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from Crypto.Cipher import AES

from src.core.decryptor import (
    fetch_key, decrypt_segment, _unpad_pkcs7,
    _extract_segment_index, decrypt_files,
)
from src.core.hls_parser import Segment


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    """PKCS7 填充辅助函数"""
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len]) * pad_len


def _encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-128-CBC 加密辅助函数"""
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.encrypt(_pkcs7_pad(data))


class TestKeyFetch(unittest.TestCase):
    """A1: AES key 长度校验"""

    @patch('src.core.decryptor.requests.get')
    def test_key_wrong_length_raises(self, mock_get):
        """密钥长度不是 16 字节时应抛出 ValueError"""
        mock_resp = MagicMock()
        mock_resp.content = b"short_key"  # 9 bytes
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        with self.assertRaises(ValueError) as ctx:
            fetch_key("http://example.com/key.bin")
        self.assertIn("16 字节", str(ctx.exception))

    @patch('src.core.decryptor.requests.get')
    def test_key_correct_length_caches(self, mock_get):
        """正确长度的密钥应被缓存"""
        mock_resp = MagicMock()
        mock_resp.content = b"0123456789abcdef"  # 16 bytes
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        from src.core.decryptor import _key_cache
        _key_cache.clear()

        key1 = fetch_key("http://example.com/key.bin")
        key2 = fetch_key("http://example.com/key.bin")
        self.assertEqual(key1, key2)
        self.assertEqual(mock_get.call_count, 1)  # 只请求了一次

        _key_cache.clear()


class TestUnsupportedEncryption(unittest.TestCase):
    """A2: 不支持的加密方式"""

    def test_sample_aes_raises(self):
        """SAMPLE-AES 应抛出 ValueError"""
        key = b"0123456789abcdef"
        iv = b"\x00" * 16
        encrypted = _encrypt(b"test data", key, iv)

        seg = Segment(
            url="http://example.com/seg.ts",
            index=0,
            encryption_method="SAMPLE-AES",
            key_url="http://example.com/key",
            iv=iv,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "000000.ts")
            with open(filepath, "wb") as f:
                f.write(encrypted)

            with self.assertRaises(ValueError) as ctx:
                decrypt_files(
                    [filepath], [seg],
                    media_sequence=0,
                )
            self.assertIn("SAMPLE-AES", str(ctx.exception))

    def test_none_method_passes_through(self):
        """NONE 方法不解密"""
        seg = Segment(
            url="http://example.com/seg.ts",
            index=0,
            encryption_method="NONE",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "000000.ts")
            original = b"plain data"
            with open(filepath, "wb") as f:
                f.write(original)

            result = decrypt_files([filepath], [seg], media_sequence=0)
            with open(result[0], "rb") as f:
                self.assertEqual(f.read(), original)


class TestImplicitIV(unittest.TestCase):
    """A3: 隐式 IV 应使用每个分片自己的 index"""

    def test_implicit_iv_uses_seg_index(self):
        """隐式 IV 应使用 seg.index 而非 media_sequence base"""
        key = b"0123456789abcdef"

        # 用不同 IV 加密两个分片
        iv1 = (100).to_bytes(16, 'big')
        iv2 = (101).to_bytes(16, 'big')
        data1 = b"segment one data"
        data2 = b"segment two data"

        enc1 = _encrypt(data1, key, iv1)
        enc2 = _encrypt(data2, key, iv2)

        segments = [
            Segment(url="http://a.com/1.ts", index=100, encryption_method="AES-128",
                    key_url="http://a.com/key", iv=b""),
            Segment(url="http://a.com/2.ts", index=101, encryption_method="AES-128",
                    key_url="http://a.com/key", iv=b""),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            fp1 = os.path.join(tmpdir, "000100.ts")
            fp2 = os.path.join(tmpdir, "000101.ts")
            with open(fp1, "wb") as f:
                f.write(enc1)
            with open(fp2, "wb") as f:
                f.write(enc2)

            with patch('src.core.decryptor.fetch_key', return_value=key):
                result = decrypt_files(
                    [fp1, fp2], segments,
                    media_sequence=0,  # base=0，但应使用 seg.index
                )

            with open(result[0], "rb") as f:
                self.assertEqual(f.read(), data1)
            with open(result[1], "rb") as f:
                self.assertEqual(f.read(), data2)


class TestPKCS7Validation(unittest.TestCase):
    """A4: PKCS7 填充校验"""

    def test_empty_data_raises(self):
        """空数据应抛出异常"""
        with self.assertRaises(ValueError):
            _unpad_pkcs7(b"")

    def test_invalid_pad_length_raises(self):
        """填充长度无效应抛出异常"""
        with self.assertRaises(ValueError):
            _unpad_pkcs7(b"\x00" * 15 + b"\x11")  # pad_len=17 > 16

    def test_invalid_pad_content_raises(self):
        """填充内容不一致应抛出异常"""
        # 最后字节是 0x03，但前面3个字节不全是 0x03
        with self.assertRaises(ValueError):
            _unpad_pkcs7(b"\x00" * 15 + b"\x03")

    def test_valid_pad_succeeds(self):
        """有效填充应正常去除"""
        data = b"hello"  # 5 bytes
        padded = _pkcs7_pad(data)
        result = _unpad_pkcs7(padded)
        self.assertEqual(result, data)


class TestFileIndexAlignment(unittest.TestCase):
    """A5: 文件与 Segment 按 index 对齐"""

    def test_duplicate_index_raises(self):
        """同一 index 有两个文件应失败"""
        seg = Segment(url="http://a.com/1.ts", index=1)
        with tempfile.TemporaryDirectory() as tmpdir:
            fp1 = os.path.join(tmpdir, "000001.ts")
            fp2 = os.path.join(tmpdir, "000001_dup.ts")
            # 模拟重复：两个文件名都解析为 index=1
            # 实际上文件名不同，但 index 相同
            with open(fp1, "wb") as f:
                f.write(b"data1")
            # 创建一个符号链接模拟同 index（或直接用文件名）
            # 简化测试：直接传入同一个 filepath 两次
            with self.assertRaises(RuntimeError):
                decrypt_files([fp1, fp1], [seg, seg], media_sequence=0)

    def test_missing_file_raises(self):
        """segment 没有对应文件应失败"""
        seg = Segment(url="http://a.com/1.ts", index=1)
        with tempfile.TemporaryDirectory() as tmpdir:
            fp = os.path.join(tmpdir, "000099.ts")  # index 99
            with open(fp, "wb") as f:
                f.write(b"data")
            with self.assertRaises(RuntimeError) as ctx:
                decrypt_files([fp], [seg], media_sequence=0)
            self.assertIn("分片 1 没有对应的下载文件", str(ctx.exception))


class TestAtomicWrite(unittest.TestCase):
    """A6: 解密写入使用临时文件"""

    def test_no_decrypting残留_on_success(self):
        """正常完成后不残留 .decrypting 文件"""
        key = b"0123456789abcdef"
        iv = b"\x00" * 16
        data = b"test data for atomic write"
        encrypted = _encrypt(data, key, iv)

        seg = Segment(
            url="http://a.com/1.ts", index=0,
            encryption_method="AES-128",
            key_url="http://a.com/key", iv=iv,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "000000.ts")
            with open(filepath, "wb") as f:
                f.write(encrypted)

            with patch('src.core.decryptor.fetch_key', return_value=key):
                decrypt_files([filepath], [seg], media_sequence=0)

            # 检查无残留
            decrypting_files = [f for f in os.listdir(tmpdir) if f.endswith('.decrypting')]
            self.assertEqual(len(decrypting_files), 0)


class TestDecryptionFailureTerminates(unittest.TestCase):
    """A7: 解密失败必须终止"""

    def test_corrupted_data_raises(self):
        """解密损坏数据应抛出异常"""
        seg = Segment(
            url="http://a.com/1.ts", index=0,
            encryption_method="AES-128",
            key_url="http://a.com/key", iv=b"\x00" * 16,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "000000.ts")
            with open(filepath, "wb") as f:
                f.write(b"not valid encrypted data at all!!")

            with patch('src.core.decryptor.fetch_key', return_value=b"0123456789abcdef"):
                with self.assertRaises(RuntimeError) as ctx:
                    decrypt_files([filepath], [seg], media_sequence=0)
                self.assertIn("解密失败", str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
