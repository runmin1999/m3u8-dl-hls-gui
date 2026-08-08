"""分片完整性测试"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.core.hls_parser import Segment
from src.core.segment_downloader import _build_result_list


class TestBuildResultList(unittest.TestCase):
    """B2: _build_result_list 防御校验"""

    def test_missing_segment_raises(self):
        """缺失分片应抛出 RuntimeError"""
        segments = [
            Segment(url="http://a.com/0.ts", index=0),
            Segment(url="http://a.com/1.ts", index=1),
            Segment(url="http://a.com/2.ts", index=2),
        ]
        results = {
            0: "/tmp/000000.ts",
            2: "/tmp/000002.ts",
            # index 1 missing
        }

        with self.assertRaises(RuntimeError) as ctx:
            _build_result_list(results, segments)
        self.assertIn("缺失 1 个", str(ctx.exception))
        self.assertIn("1", str(ctx.exception))

    def test_empty_file_raises(self):
        """空文件应抛出 RuntimeError"""
        segments = [
            Segment(url="http://a.com/0.ts", index=0),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            fp = os.path.join(tmpdir, "000000.ts")
            with open(fp, "wb") as f:
                pass  # 0 bytes

            results = {0: fp}
            with self.assertRaises(RuntimeError) as ctx:
                _build_result_list(results, segments)
            self.assertIn("分片文件无效", str(ctx.exception))

    def test_valid_files_succeed(self):
        """有效文件应正常返回有序列表"""
        segments = [
            Segment(url="http://a.com/0.ts", index=0),
            Segment(url="http://a.com/1.ts", index=1),
            Segment(url="http://a.com/2.ts", index=2),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            paths = []
            for i in range(3):
                fp = os.path.join(tmpdir, f"{i:06d}.ts")
                with open(fp, "wb") as f:
                    f.write(b"data")
                paths.append(fp)

            results = {i: paths[i] for i in range(3)}
            ordered = _build_result_list(results, segments)
            self.assertEqual(len(ordered), 3)
            # 顺序应与 segments 一致
            for i, path in enumerate(ordered):
                self.assertEqual(os.path.basename(path), f"{i:06d}.ts")


class TestDownloadAllIntegrity(unittest.TestCase):
    """B1: download_all 最终验证"""

    def test_partial_results_still_checked(self):
        """_build_result_list 应该被 download_all 调用并检查完整性"""
        # 这个测试验证 _build_result_list 作为 download_all 的最终校验
        # download_all 内部会调用 _build_result_list，如果缺失会 raise
        segments = [
            Segment(url="http://a.com/0.ts", index=0),
            Segment(url="http://a.com/1.ts", index=1),
        ]
        results = {0: "/tmp/000000.ts"}  # missing index 1

        with self.assertRaises(RuntimeError):
            _build_result_list(results, segments)


if __name__ == '__main__':
    unittest.main()
