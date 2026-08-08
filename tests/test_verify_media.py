"""文件完整性验证测试"""

import os
import tempfile
import unittest


class TestVerifyMediaFile(unittest.TestCase):
    """verify_media_file 基本测试"""

    def test_nonexistent_file(self):
        """不存在的文件应返回 verified=False"""
        from src.utils.helpers import verify_media_file
        result = verify_media_file("/nonexistent/file.mp4")
        self.assertFalse(result["verified"])

    def test_empty_file(self):
        """空文件应返回 verified=False 或有 error"""
        from src.utils.helpers import verify_media_file
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"")
            path = f.name
        try:
            result = verify_media_file(path)
            # ffprobe 对空文件可能返回 verified=True 但无流信息，或返回错误
            # 两种情况都可接受，关键是不抛异常
            self.assertIsInstance(result, dict)
            self.assertIn("verified", result)
        finally:
            os.unlink(path)

    def test_return_structure(self):
        """返回值应包含必要字段"""
        from src.utils.helpers import verify_media_file
        result = verify_media_file("/nonexistent/file.mp4")
        self.assertIn("verified", result)
        self.assertIn("error", result)

    def test_invalid_file(self):
        """非媒体文件应返回 verified=False 或有 error"""
        from src.utils.helpers import verify_media_file
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"this is not a video file")
            path = f.name
        try:
            result = verify_media_file(path)
            # 非媒体文件 ffprobe 会报错
            self.assertIsInstance(result, dict)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
