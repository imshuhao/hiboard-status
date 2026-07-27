import json
import os
import time
import unittest
from unittest import mock

from helpers import hh, TmpDataDirTest, _write_config, read_state


class TestConfig(TmpDataDirTest):
    def test_load_config_missing_file(self):
        self.assertIsNone(hh.load_config())

    def test_load_config_disabled(self):
        _write_config(enabled=False)
        self.assertIsNone(hh.load_config())

    def test_load_config_no_authcode(self):
        _write_config(authCode="")
        self.assertIsNone(hh.load_config())

    def test_load_config_ok(self):
        _write_config()
        self.assertEqual(hh.load_config()["authCode"], "TESTCODE12345")


class TestPushCard(TmpDataDirTest):
    def setUp(self):
        super().setUp()
        os.environ["HIBOARD_DRY_RUN"] = "1"

    def tearDown(self):
        os.environ.pop("HIBOARD_DRY_RUN", None)
        super().tearDown()

    def test_push_card_dry_run_logs_payload(self):
        _write_config()
        ok = hh.push_card(hh.load_config(), "标题", "# 正文")
        self.assertTrue(ok)
        logtext = hh.log_path().read_text(encoding="utf-8")
        self.assertIn("DRY_RUN", logtext)
        self.assertIn(hh.CARD_ID, logtext)
        self.assertIn('"data"', logtext)  # 外层 data 包装存在

    def test_dry_run_log_masks_auth_code(self):
        hh.push_card({"authCode": "SECRET123456"}, "标题", "正文")
        logtext = hh.log_path().read_text(encoding="utf-8")
        self.assertNotIn("SECRET123456", logtext)
        self.assertIn('"authCode": "***"', logtext)


class TestDoPush(TmpDataDirTest):
    def setUp(self):
        super().setUp()
        os.environ["HIBOARD_DRY_RUN"] = "1"

    def tearDown(self):
        os.environ.pop("HIBOARD_DRY_RUN", None)
        super().tearDown()

    def test_do_push_without_config_is_noop(self):
        hh.update_project("p", {"status": "running",
                                "updated_at": time.time()})
        hh.do_push()  # 不应抛异常
        self.assertFalse(hh.log_path().exists()
                         and "DRY_RUN" in hh.log_path().read_text(encoding="utf-8"))

    def test_do_push_dedupes_identical_content(self):
        _write_config()
        hh.update_project("p", {"status": "done", "summary": "x",
                                "updated_at": time.time()})
        hh.do_push()
        hh.do_push()  # 内容未变，应跳过
        logtext = hh.log_path().read_text(encoding="utf-8")
        self.assertEqual(logtext.count("DRY_RUN"), 1)

    def test_do_push_clears_claim_after_success(self):
        _write_config()
        hh.update_project("p", {"status": "done", "summary": "x",
                                "updated_at": time.time()})
        hh.do_push()
        state = read_state()
        self.assertNotIn("push_claim", state)
        self.assertIn("last_push_hash", state)


class TestQuotaBreaker(TmpDataDirTest):
    """0000400001 触发熔断：午夜前自动推送不再发起必败请求。"""

    def _quota_response(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.read.return_value = json.dumps(
            {"code": "0000400001",
             "desc": "The count reached the upper limit"}).encode()
        return resp

    def test_quota_error_trips_breaker(self):
        _write_config()
        with mock.patch.object(hh.push.urllib.request, "urlopen",
                               return_value=self._quota_response()):
            ok = hh.push_card(hh.load_config(), "t", "c")
        self.assertFalse(ok)
        self.assertGreater(read_state().get("quota_blocked_until", 0),
                           time.time())

    def test_do_push_skips_during_breaker(self):
        _write_config()
        hh.mutate_state(lambda s: s.update(
            {"quota_blocked_until": time.time() + 3600}))
        hh.update_project("p", {"status": "running", "prompt": "x",
                                "updated_at": time.time()})
        os.environ["HIBOARD_DRY_RUN"] = "1"
        try:
            hh.do_push()
        finally:
            os.environ.pop("HIBOARD_DRY_RUN", None)
        logtext = (hh.log_path().read_text(encoding="utf-8")
                   if hh.log_path().exists() else "")
        self.assertNotIn("DRY_RUN", logtext)

    def test_expired_breaker_allows_push(self):
        _write_config()
        hh.mutate_state(lambda s: s.update(
            {"quota_blocked_until": time.time() - 60}))
        hh.update_project("p", {"status": "running", "prompt": "x",
                                "updated_at": time.time()})
        os.environ["HIBOARD_DRY_RUN"] = "1"
        try:
            hh.do_push()
        finally:
            os.environ.pop("HIBOARD_DRY_RUN", None)
        self.assertIn("DRY_RUN", hh.log_path().read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
