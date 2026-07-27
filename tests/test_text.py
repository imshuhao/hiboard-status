import unittest

from helpers import hh


class TestUtf16(unittest.TestCase):
    def test_utf16_len(self):
        self.assertEqual(hh.utf16_len("abc"), 3)
        self.assertEqual(hh.utf16_len("中文"), 2)
        self.assertEqual(hh.utf16_len("🚀"), 2)  # 非 BMP 占 2 码元

    def test_truncate_within_limit_unchanged(self):
        self.assertEqual(hh.truncate_utf16("hello", 10), "hello")

    def test_truncate_over_limit(self):
        out = hh.truncate_utf16("a" * 100, 10)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(hh.utf16_len(out), 10)

    def test_truncate_never_splits_surrogate_pair(self):
        out = hh.truncate_utf16("🚀" * 100, 11)  # 奇数限额落在代理对中间
        self.assertLessEqual(hh.utf16_len(out), 11)
        out.encode("utf-8")  # 若截出孤立代理项这里会抛 UnicodeEncodeError

    def test_truncate_zero_limit_returns_empty(self):
        self.assertEqual(hh.truncate_utf16("abcdef", 0), "")
        self.assertEqual(hh.truncate_utf16("abcdef", -1), "")


class TestSlugify(unittest.TestCase):
    def test_ascii_topic_slug_unchanged(self):
        # 纯 ASCII 主题保持原 slug，不破坏已存在的主题卡 ID
        self.assertEqual(hh.slugify_ascii("Daily Report"), "daily_report")

    def test_non_ascii_topics_get_distinct_slugs(self):
        a = hh.slugify_ascii("每日日报")
        b = hh.slugify_ascii("每周周报")
        self.assertNotEqual(a, b)

    def test_empty_falls_back(self):
        self.assertEqual(hh.slugify_ascii("---"), "topic")


if __name__ == "__main__":
    unittest.main()
