import json
import os
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

from helpers import ENTRY, hh, TmpDataDirTest, _write_config, read_state


class TestOnDemandPush(TmpDataDirTest):
    """--push 子模式：轮转卡位、主题卡、校验、退出码。"""

    def setUp(self):
        super().setUp()
        os.environ["HIBOARD_DRY_RUN"] = "1"

    def tearDown(self):
        os.environ.pop("HIBOARD_DRY_RUN", None)
        super().tearDown()

    def _push_file(self, **data):
        p = Path(self._tmp.name) / f"push_{time.time_ns()}.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return str(p)

    def _pushed_card_ids(self):
        logtext = hh.log_path().read_text(encoding="utf-8")
        return re.findall(r'"scheduleTaskId": "(claude_code_[a-z0-9_]+)"', logtext)

    def test_push_without_config_fails(self):
        rc = hh.cmd_push(self._push_file(summary="t", content="c"))
        self.assertEqual(rc, 1)

    def test_push_missing_fields_fails(self):
        _write_config()
        self.assertEqual(hh.cmd_push(self._push_file(summary="只有标题")), 1)
        self.assertEqual(hh.cmd_push(self._push_file(content="只有正文")), 1)

    def test_push_oversized_content_fails(self):
        _write_config()
        rc = hh.cmd_push(self._push_file(summary="t",
                                         content="长" * (hh.MAX_CARD_UTF16 + 1)))
        self.assertEqual(rc, 1)

    def test_ring_rotation_reuses_oldest_slot(self):
        _write_config()
        for i in range(4):  # 默认 3 槽，第 4 次应复用槽 1
            rc = hh.cmd_push(self._push_file(summary=f"第{i}条", content="# 内容"))
            self.assertEqual(rc, 0)
        ids = self._pushed_card_ids()
        self.assertEqual(ids, ["claude_code_manual_1", "claude_code_manual_2",
                               "claude_code_manual_3", "claude_code_manual_1"])
        self.assertEqual(set(read_state()["manual_slots"]), {"1", "2", "3"})

    def test_manual_slots_config_respected(self):
        _write_config(manualSlots=2)
        for i in range(3):
            hh.cmd_push(self._push_file(summary=f"第{i}条", content="c"))
        ids = self._pushed_card_ids()
        self.assertEqual(ids, ["claude_code_manual_1", "claude_code_manual_2",
                               "claude_code_manual_1"])

    def test_chinese_topics_get_distinct_ids(self):
        # 纯中文主题若全部塌缩成同一 slug，主题卡会互相覆盖且永久存在
        _write_config()
        hh.cmd_push(self._push_file(summary="日报", content="c", topic="每日日报"))
        hh.cmd_push(self._push_file(summary="周报", content="c", topic="每周周报"))
        self.assertEqual(len(set(self._pushed_card_ids())), 2)

    def test_failed_push_restores_ring_slot(self):
        _write_config()
        # 先占满一个卡位建立基线
        hh.cmd_push(self._push_file(summary="占位", content="c"))
        before = read_state()["manual_slots"]
        # 关掉 DRY_RUN 并把端点指向不可达地址，制造真实推送失败
        os.environ.pop("HIBOARD_DRY_RUN", None)
        try:
            _write_config(pushServiceUrl="http://127.0.0.1:1/x")
            rc = hh.cmd_push(self._push_file(summary="必败", content="c"))
        finally:
            os.environ["HIBOARD_DRY_RUN"] = "1"
        self.assertEqual(rc, 1)
        self.assertEqual(read_state()["manual_slots"], before)  # 失败不烧轮转位置

    def test_topic_uses_stable_id_and_skips_ring(self):
        _write_config()
        for _ in range(2):
            rc = hh.cmd_push(self._push_file(summary="日报", content="c",
                                             topic="每日日报 Daily"))
            self.assertEqual(rc, 0)
        ids = self._pushed_card_ids()
        self.assertEqual(len(set(ids)), 1)
        self.assertTrue(ids[0].startswith("claude_code_topic_daily"))
        # 主题卡完全不触碰轮转状态——state.json 根本不会被创建
        if hh.state_path().exists():
            self.assertNotIn("manual_slots", read_state())

    def test_push_custom_fields_land_in_payload(self):
        _write_config()
        hh.cmd_push(self._push_file(summary="标题", content="c",
                                    source="日报", result="已生成"))
        logtext = hh.log_path().read_text(encoding="utf-8")
        self.assertIn('"source": "日报"', logtext)
        self.assertIn('"result": "已生成"', logtext)

    def test_push_cli_exit_codes(self):
        env = os.environ.copy()
        env["HIBOARD_DRY_RUN"] = "1"
        r = subprocess.run(
            [sys.executable, str(ENTRY), "--push", "/nonexistent.json"],
            capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1)  # --push 失败必须非零，区别于 hook 恒零

    def test_test_push_exit_codes(self):
        env = os.environ.copy()
        env["HIBOARD_DRY_RUN"] = "1"
        cmd = [sys.executable, str(ENTRY), "--test-push"]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1)  # 无配置：失败须非零（与 --push 同哲学）
        _write_config()
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
