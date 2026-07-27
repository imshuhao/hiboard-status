import json
import time
import unittest

from helpers import hh, TmpDataDirTest, read_state


class TestState(TmpDataDirTest):
    def test_update_project_creates_and_merges(self):
        hh.update_project("demo", {"status": "running", "updated_at": time.time()})
        state = hh.update_project("demo", {"status": "done"})
        self.assertEqual(state["projects"]["demo"]["status"], "done")
        self.assertIn("updated_at", state["projects"]["demo"])  # 旧字段保留

    def test_corrupted_state_recovers_with_backup(self):
        hh.data_dir().mkdir(parents=True, exist_ok=True)
        hh.state_path().write_text("{not json", encoding="utf-8")
        state = hh.update_project("demo", {"status": "running",
                                           "updated_at": time.time()})
        self.assertIn("demo", state["projects"])
        self.assertTrue(hh.state_path().with_suffix(".json.bak").exists())

    def test_prunes_entries_older_than_7_days(self):
        old = time.time() - hh.PRUNE_SECS - 60
        hh.update_project("ancient", {"status": "ended", "updated_at": old})
        state = hh.update_project("fresh", {"status": "running",
                                            "updated_at": time.time()})
        self.assertNotIn("ancient", state["projects"])
        self.assertIn("fresh", state["projects"])

    def test_state_survives_roundtrip(self):
        # updated_at 需为近期值，否则会被 7 天清理逻辑移除
        hh.update_project("demo", {"status": "running",
                                   "updated_at": time.time()})
        self.assertEqual(read_state()["projects"]["demo"]["status"], "running")


class TestSessionsMap(TmpDataDirTest):
    def test_stale_session_mappings_pruned(self):
        old = time.time() - hh.PRUNE_SECS - 60
        hh.mutate_state(lambda s: s.update(
            {"sessions": {"old-sid": {"proj": "x", "ts": old},
                          "new-sid": {"proj": "y", "ts": time.time()}}}))
        hh.mutate_state(lambda s: None)  # 任意一次写入都会触发清理
        state = read_state()
        self.assertNotIn("old-sid", state["sessions"])
        self.assertIn("new-sid", state["sessions"])


class TestFilePermissions(TmpDataDirTest):
    """state.json 含用户指令原文、push.log 可能含推送负载，均不应对同机他人可读。"""

    def test_state_and_log_are_owner_only(self):
        hh.update_project("demo", {"status": "running",
                                   "prompt": "正在处理：改数据库密码",
                                   "updated_at": time.time()})
        hh.log("something")
        for p in (hh.state_path(), hh.log_path()):
            mode = p.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600, f"{p.name} 权限应为 600，实际 {oct(mode)}")

    def test_data_dir_is_owner_only(self):
        hh.ensure_dir()
        mode = hh.data_dir().stat().st_mode & 0o777
        self.assertEqual(mode, 0o700)


class TestLogRotation(TmpDataDirTest):
    def test_log_rotates_over_512k(self):
        hh.ensure_dir()
        hh.log_path().write_text("x" * (513 * 1024), encoding="utf-8")
        hh.log("after-rotate")
        text = hh.log_path().read_text(encoding="utf-8")
        self.assertLess(len(text), 600 * 1024)
        self.assertIn("after-rotate", text)


if __name__ == "__main__":
    unittest.main()
