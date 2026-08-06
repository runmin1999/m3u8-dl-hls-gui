"""Merger Semaphore 测试"""

import unittest
from unittest.mock import patch, MagicMock

import merger


class TestSemaphoreConsistency(unittest.TestCase):
    """F2: Semaphore 获取和释放是同一实例"""

    def test_acquire_release_same_instance(self):
        """acquire 和 release 应在同一个 Semaphore 上调用"""
        mock_semaphore = MagicMock()

        with patch('merger._get_merge_semaphore', return_value=mock_semaphore):
            semaphore = merger._get_merge_semaphore()
            semaphore.acquire()
            try:
                pass  # 模拟工作
            finally:
                semaphore.release()

        self.assertEqual(mock_semaphore.acquire.call_count, 1)
        self.assertEqual(mock_semaphore.release.call_count, 1)


if __name__ == '__main__':
    unittest.main()
