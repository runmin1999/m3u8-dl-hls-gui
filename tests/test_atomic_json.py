"""JSON 原子写入测试"""

import os
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from src.utils.helpers import _atomic_write_json, save_config, save_tasks, load_config


class TestAtomicWriteJson(unittest.TestCase):
    """E2: 原子 JSON 写入"""

    def test_normal_write(self):
        """正常写入后可加载"""
        data = {"key": "value", "number": 42}
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.json")
            _atomic_write_json(path, data)

            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertEqual(loaded, data)

    def test_no_tmp残留(self):
        """正常完成后无 .tmp 残留"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.json")
            _atomic_write_json(path, {"a": 1})

            files = os.listdir(tmpdir)
            tmp_files = [f for f in files if f.endswith('.tmp')]
            self.assertEqual(len(tmp_files), 0)

    def test_concurrent_writes(self):
        """多线程连续写入后仍是合法 JSON"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.json")
            errors = []

            def writer(thread_id):
                try:
                    for i in range(50):
                        _atomic_write_json(path, {"thread": thread_id, "iter": i})
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [])
            # 验证最终文件是合法 JSON
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertIsInstance(loaded, dict)

    def test_replace_failure_preserves_old(self):
        """os.replace 失败时旧文件不被清空"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.json")
            # 先写一个有效文件
            _atomic_write_json(path, {"version": 1})
            with open(path, "r") as f:
                old_data = json.load(f)
            self.assertEqual(old_data, {"version": 1})

            # mock os.replace 让它失败
            with patch('src.utils.helpers.os.replace', side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    _atomic_write_json(path, {"version": 2})

            # 旧文件应该还在且内容不变
            with open(path, "r") as f:
                current = json.load(f)
            self.assertEqual(current, {"version": 1})


class TestSaveConfig(unittest.TestCase):
    """E3: save_config 使用原子写入"""

    def test_save_and_load(self):
        """保存后可正常加载"""
        config = {"workers": 20, "proxy": ""}
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "config.json")
            save_config(config, path)
            loaded = load_config(path)
            self.assertEqual(loaded, config)


if __name__ == '__main__':
    unittest.main()
