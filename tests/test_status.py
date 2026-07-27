import os
import subprocess
import sys
import time
import unittest

from helpers import ENTRY, hh, TmpDataDirTest, _write_config


class TestStatusCli(TmpDataDirTest):
    def _run(self):
        return subprocess.run([sys.executable, str(ENTRY), "--status"],
                              capture_output=True, text=True,
                              env=os.environ.copy())

    def test_status_without_config(self):
        r = self._run()
        self.assertEqual(r.returncode, 0)
        self.assertIn(hh.VERSION, r.stdout)
        self.assertIn("未配置", r.stdout)

    def test_status_shows_projects_and_breaker(self):
        _write_config()
        hh.update_project("demo", {"status": "running", "prompt": "改代码",
                                   "updated_at": time.time()})
        hh.mutate_state(lambda s: s.update(
            {"quota_blocked_until": time.time() + 3600,
             "topics": {"claude_code_topic_x": {
                 "topic": "日报", "ts": time.time(), "count": 3}}}))
        r = self._run()
        self.assertEqual(r.returncode, 0)
        self.assertIn("demo", r.stdout)
        self.assertIn("运行中", r.stdout)
        self.assertIn("配额熔断中", r.stdout)
        self.assertIn("日报", r.stdout)
        self.assertIn("配置：OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
