"""BYTERANGE 解析测试"""

import unittest
from m3u8_parser import parse_m3u8, _parse_byterange_value


class TestParseByterangeValue(unittest.TestCase):
    """C2: BYTERANGE 值解析"""

    def test_explicit_offset(self):
        length, offset = _parse_byterange_value("1000@500")
        self.assertEqual(length, 1000)
        self.assertEqual(offset, 500)

    def test_no_offset(self):
        length, offset = _parse_byterange_value("1000")
        self.assertEqual(length, 1000)
        self.assertIsNone(offset)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            _parse_byterange_value("")

    def test_negative_length_raises(self):
        with self.assertRaises(ValueError):
            _parse_byterange_value("-1@0")

    def test_negative_offset_raises(self):
        with self.assertRaises(ValueError):
            _parse_byterange_value("100@-1")


class TestByterangeConsecutive(unittest.TestCase):
    """C3: 连续隐式偏移"""

    def test_consecutive_implicit_offset(self):
        """4@0, 4, 4 应解析为 4@0, 4@4, 4@8"""
        m3u8_content = """#EXTM3U
#EXT-X-TARGETDURATION:10
#EXT-X-BYTERANGE:4@0
file.bin
#EXT-X-BYTERANGE:4
file.bin
#EXT-X-BYTERANGE:4
file.bin
"""
        playlist = parse_m3u8(m3u8_content, "http://example.com/")
        self.assertEqual(len(playlist.segments), 3)
        self.assertEqual(playlist.segments[0].byterange, "4@0")
        self.assertEqual(playlist.segments[1].byterange, "4@4")
        self.assertEqual(playlist.segments[2].byterange, "4@8")

    def test_uri_change_with_implicit_offset_raises(self):
        """换了 URI 的隐式偏移应失败"""
        m3u8_content = """#EXTM3U
#EXT-X-TARGETDURATION:10
#EXT-X-BYTERANGE:4@0
a.bin
#EXT-X-BYTERANGE:4
b.bin
"""
        with self.assertRaises(ValueError) as ctx:
            parse_m3u8(m3u8_content, "http://example.com/")
        self.assertIn("URI", str(ctx.exception))

    def test_explicit_offset_updates_last_range(self):
        """显式 offset 应更新 last_range_end"""
        m3u8_content = """#EXTM3U
#EXT-X-TARGETDURATION:10
#EXT-X-BYTERANGE:100@0
file.bin
#EXT-X-BYTERANGE:200@500
file.bin
#EXT-X-BYTERANGE:50
file.bin
"""
        playlist = parse_m3u8(m3u8_content, "http://example.com/")
        self.assertEqual(playlist.segments[0].byterange, "100@0")
        self.assertEqual(playlist.segments[1].byterange, "200@500")
        # 上一个 end = 500+200 = 700
        self.assertEqual(playlist.segments[2].byterange, "50@700")


class TestByterangeUrl(unittest.TestCase):
    """C3: BYTERANGE URL 处理"""

    def test_url_always_resolved_from_line(self):
        """即使有 BYTERANGE，URL 仍从当前行解析"""
        m3u8_content = """#EXTM3U
#EXT-X-TARGETDURATION:10
#EXT-X-BYTERANGE:100@0
video.mp4
"""
        playlist = parse_m3u8(m3u8_content, "http://cdn.example.com/path/")
        self.assertEqual(playlist.segments[0].url, "http://cdn.example.com/path/video.mp4")


if __name__ == '__main__':
    unittest.main()
