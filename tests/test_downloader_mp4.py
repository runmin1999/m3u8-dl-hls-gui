"""MP4 下载模块测试"""

import unittest
from downloader_mp4 import _is_download_complete, _find_curl, _build_curl_cmd


class TestIsDownloadComplete(unittest.TestCase):
    """D3: 下载完成判断"""

    def test_success_with_content_length(self):
        self.assertTrue(_is_download_complete(0, 100, 100))

    def test_success_oversized(self):
        self.assertTrue(_is_download_complete(0, 120, 100))

    def test_success_no_content_length(self):
        """无 Content-Length 时 curl 返回 0 且文件非空即完成"""
        self.assertTrue(_is_download_complete(0, 100, 0))

    def test_failure_zero_size_no_cl(self):
        """无 Content-Length 但文件为空"""
        self.assertFalse(_is_download_complete(0, 0, 0))

    def test_failure_nonzero_return(self):
        self.assertFalse(_is_download_complete(1, 100, 0))
        self.assertFalse(_is_download_complete(1, 100, 100))

    def test_failure_incomplete(self):
        """文件大小不足"""
        self.assertFalse(_is_download_complete(0, 80, 100))


class TestCurlLookup(unittest.TestCase):
    """D1: curl 跨平台查找"""

    def test_find_curl_returns_path_or_none(self):
        """应返回路径或 None，不应抛异常"""
        result = _find_curl()
        # 在测试环境中可能找不到 curl，但不应报错
        self.assertIsInstance(result, (str, type(None)))


class TestBuildCurlCmd(unittest.TestCase):
    """D2: curl 命令构建"""

    def _make_task(self, **kwargs):
        """创建模拟 task 对象"""
        task = type('Task', (), {
            'url': kwargs.get('url', 'http://example.com/video.mp4'),
            'proxy': kwargs.get('proxy', ''),
            'custom_headers': kwargs.get('custom_headers', {}),
        })()
        return task

    def test_has_parallel_params(self):
        """命令应包含 parallel 参数用于 HTTP/2 多路复用"""
        task = self._make_task()
        cmd = _build_curl_cmd(task, "/tmp/out.mp4", "curl")
        cmd_str = " ".join(cmd)
        self.assertIn("--parallel", cmd_str)
        self.assertIn("--parallel-max", cmd_str)
        self.assertIn("--parallel-immediate", cmd_str)

    def test_has_sS_and_fail(self):
        """应包含 -sS 和 --fail"""
        task = self._make_task()
        cmd = _build_curl_cmd(task, "/tmp/out.mp4", "curl")
        self.assertIn("-sS", cmd)
        self.assertIn("--fail", cmd)

    def test_resume_has_C_flag(self):
        """续传应包含 -C -"""
        import tempfile, os
        task = self._make_task()
        with tempfile.TemporaryDirectory() as tmpdir:
            fp = os.path.join(tmpdir, "test.mp4")
            with open(fp, "wb") as f:
                f.write(b"data")
            cmd = _build_curl_cmd(task, fp, "curl", resume=True)
            self.assertIn("-C", cmd)
            self.assertIn("-", cmd)

    def test_first_arg_is_curl_path(self):
        """命令首项应为传入的 curl 路径"""
        task = self._make_task()
        cmd = _build_curl_cmd(task, "/tmp/out.mp4", "/usr/local/bin/curl")
        self.assertEqual(cmd[0], "/usr/local/bin/curl")


if __name__ == '__main__':
    unittest.main()
