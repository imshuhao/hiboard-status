import json, os, subprocess, sys, tempfile, time, unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import hiboard_hook as hh


class TmpDataDirTest(unittest.TestCase):
    """基类：每个测试用独立临时数据目录。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["HIBOARD_DATA_DIR"] = self._tmp.name

    def tearDown(self):
        os.environ.pop("HIBOARD_DATA_DIR", None)
        self._tmp.cleanup()


class TestPureHelpers(unittest.TestCase):
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

    def test_fmt_time_today_vs_older(self):
        now = time.time()
        self.assertNotIn("-", hh.fmt_time(now, now=now))
        self.assertIn("-", hh.fmt_time(now - 2 * 86400, now=now))

    def test_effective_status_stale(self):
        now = time.time()
        fresh = {"status": "running", "updated_at": now}
        old = {"status": "running", "updated_at": now - 3 * 3600}
        done_old = {"status": "done", "updated_at": now - 3 * 3600}
        self.assertEqual(hh.effective_status(fresh, now), "running")
        self.assertEqual(hh.effective_status(old, now), "stale")
        self.assertEqual(hh.effective_status(done_old, now), "done")  # 非活跃态不降级


if __name__ == "__main__":
    unittest.main()
