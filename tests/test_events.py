import os
import subprocess
import sys
import time
import unittest
from unittest import mock

from helpers import (ENTRY, hh, TmpDataDirTest, _run_hook, _write_config,
                     read_state)


class TestDispatch(TmpDataDirTest):
    """格子模型：格子生于首次 UserPromptSubmit、死于 SessionEnd。"""

    def _state(self):
        return read_state()

    def _cell(self, proj="myproj", sid="s1"):
        return self._state()["projects"][proj]["cells"][sid]

    def test_session_start_registers_mapping_only(self):
        _write_config()
        r = _run_hook({"hook_event_name": "SessionStart",
                       "session_id": "s1", "cwd": "/tmp/myproj"})
        self.assertEqual(r.returncode, 0)
        state = self._state()
        self.assertEqual(state["sessions"]["s1"]["proj"], "myproj")
        self.assertNotIn("myproj", state["projects"])  # 无格子无条目

    def test_user_prompt_creates_running_cell(self):
        _write_config()
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                   "cwd": "/tmp/myproj", "prompt": "改" * 200})
        c = self._cell()
        self.assertEqual(c["status"], "running")
        self.assertLessEqual(hh.utf16_len(c["prompt"]), hh.MAX_PROMPT_UTF16)

    def test_stop_sets_cell_done_with_project_placeholder(self):
        _write_config()
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                   "cwd": "/tmp/myproj", "prompt": "跑测试"})
        _run_hook({"hook_event_name": "Stop", "session_id": "s1",
                   "cwd": "/tmp/myproj", "transcript_path": "/nonexistent"})
        e = self._state()["projects"]["myproj"]
        self.assertEqual(e["cells"]["s1"]["status"], "done")
        self.assertEqual(e["cells"]["s1"]["prompt"], "跑测试")  # 指令保留
        self.assertIn("摘要生成中", e["summary"])

    def test_notification_sets_cell_waiting(self):
        _write_config()
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                   "cwd": "/tmp/myproj", "prompt": "跑测试"})
        _run_hook({"hook_event_name": "Notification", "session_id": "s1",
                   "cwd": "/tmp/myproj",
                   "notification_type": "permission_prompt"})
        c = self._cell()
        self.assertEqual(c["status"], "waiting")
        self.assertIn("跑测试", c["prompt"])

    def test_notification_non_waiting_type_ignored(self):
        _write_config()
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                   "cwd": "/tmp/myproj", "prompt": "跑测试"})
        _run_hook({"hook_event_name": "Notification", "session_id": "s1",
                   "cwd": "/tmp/myproj", "notification_type": "auth_success"})
        self.assertEqual(self._cell()["status"], "running")  # 不受影响

    def test_notification_without_cell_ignored(self):
        _write_config()
        _run_hook({"hook_event_name": "Notification", "session_id": "sX",
                   "cwd": "/tmp/myproj"})
        self.assertNotIn("myproj", self._state()["projects"])

    def test_session_end_removes_own_cell_only(self):
        _write_config()
        for sid in ("s1", "s2"):
            _run_hook({"hook_event_name": "UserPromptSubmit",
                       "session_id": sid, "cwd": "/tmp/myproj",
                       "prompt": f"任务{sid}"})
        _run_hook({"hook_event_name": "SessionEnd", "session_id": "s1",
                   "cwd": "/tmp/myproj"})
        cells = self._state()["projects"]["myproj"]["cells"]
        self.assertNotIn("s1", cells)
        self.assertEqual(cells["s2"]["status"], "running")

    def test_ghost_start_end_leaves_zero_trace(self):
        # 幽灵会话（从不提交指令）：Start 只登记映射，End 无格可删
        _write_config()
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                   "cwd": "/tmp/myproj", "prompt": "干活"})
        _run_hook({"hook_event_name": "SessionStart", "session_id": "ghost",
                   "cwd": "/tmp/myproj"})
        _run_hook({"hook_event_name": "SessionEnd", "session_id": "ghost",
                   "cwd": "/tmp/myproj"})
        cells = self._state()["projects"]["myproj"]["cells"]
        self.assertEqual(list(cells), ["s1"])
        self.assertEqual(cells["s1"]["status"], "running")

    def test_concurrent_sessions_do_not_overwrite_each_other(self):
        # 核心场景：A 在跑、B 回合结束，卡片不得显示「本轮完成」
        _write_config()
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "A",
                   "cwd": "/tmp/myproj", "prompt": "修复登录"})
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "B",
                   "cwd": "/tmp/myproj", "prompt": "写文档"})
        _run_hook({"hook_event_name": "Stop", "session_id": "B",
                   "cwd": "/tmp/myproj", "transcript_path": "/nonexistent"})
        e = self._state()["projects"]["myproj"]
        self.assertEqual(e["cells"]["A"]["status"], "running")
        self.assertEqual(e["cells"]["B"]["status"], "done")
        self.assertEqual(hh.display_status(e), "running")

    def test_project_pinned_at_session_start_despite_cd(self):
        _write_config()
        _run_hook({"hook_event_name": "SessionStart", "session_id": "s1",
                   "cwd": "/tmp/projA"})
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                   "cwd": "/tmp/projB", "prompt": "改代码"})
        projects = self._state()["projects"]
        self.assertIn("改代码", projects["projA"]["cells"]["s1"]["prompt"])
        self.assertNotIn("projB", projects)

    def test_unmapped_session_falls_back_to_cwd(self):
        _write_config()
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "old",
                   "cwd": "/tmp/legacy", "prompt": "继续"})
        self.assertIn("legacy", self._state()["projects"])

    def test_summary_survives_new_prompt_and_session(self):
        # summary 仍是项目级「最近完成回合的总结」，新指令新会话不清空
        _write_config()
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                   "cwd": "/tmp/myproj", "prompt": "第一件事"})
        _run_hook({"hook_event_name": "Stop", "session_id": "s1",
                   "cwd": "/tmp/myproj", "transcript_path": "/nonexistent"})
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s2",
                   "cwd": "/tmp/myproj", "prompt": "第二件事"})
        e = self._state()["projects"]["myproj"]
        self.assertEqual(e["summary"], hh.SUMMARY_PLACEHOLDER)

    def test_legacy_flat_fields_cleared_on_first_cell(self):
        _write_config()
        hh.update_project("myproj", {"status": "ended", "prompt": "旧的",
                                     "session_id": "olds",
                                     "updated_at": time.time()})
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                   "cwd": "/tmp/myproj", "prompt": "新的"})
        e = self._state()["projects"]["myproj"]
        self.assertNotIn("status", e)
        self.assertNotIn("prompt", e)
        self.assertIn("s1", e["cells"])

    def test_garbage_stdin_exits_zero(self):
        env = os.environ.copy()
        r = subprocess.run([sys.executable, str(ENTRY)],
                           input="not json", text=True,
                           capture_output=True, env=env)
        self.assertEqual(r.returncode, 0)

    def test_unknown_event_exits_zero(self):
        r = _run_hook({"hook_event_name": "SomethingNew", "cwd": "/tmp/x"})
        self.assertEqual(r.returncode, 0)


class TestCellPruning(TmpDataDirTest):
    def test_prune_cells_by_age_and_registry(self):
        now = time.time()
        entry = {"cells": {
            "ancient": {"status": "done", "updated_at": now - hh.const.CELL_PRUNE_SECS - 1},
            "dead":    {"status": "running", "updated_at": now - 120},
            "fresh":   {"status": "running", "updated_at": now - 120},
            "newborn": {"status": "running", "updated_at": now - 5},
        }}
        hh.events.prune_cells(entry, now, live={"fresh"})
        # ancient 超龄删；dead 不在活表且过宽限期删；newborn 在宽限期内保留
        self.assertEqual(set(entry["cells"]), {"fresh", "newborn"})

    def test_prune_without_registry_keeps_by_age_only(self):
        now = time.time()
        entry = {"cells": {"x": {"status": "running", "updated_at": now - 120}}}
        hh.events.prune_cells(entry, now, live=None)
        self.assertIn("x", entry["cells"])


class TestSummarizerSpawn(TmpDataDirTest):
    def _evt(self, **over):
        evt = {"hook_event_name": "Stop", "session_id": "s1",
               "cwd": "/tmp/myproj"}
        evt.update(over)
        return evt

    def test_payload_text_goes_via_stdin(self):
        with mock.patch.object(hh.events.subprocess, "Popen") as popen:
            hh.spawn_summarizer(
                self._evt(last_assistant_message="汇报正文",
                          transcript_path="/tmp/t.jsonl"),
                "myproj", 123.0, "用户指令")
        args = popen.call_args[0][0]
        self.assertEqual(args[args.index("--summarize") + 1:],
                         ["myproj", "123.0", "用户指令", ""])  # 有载荷则不传 transcript
        popen.return_value.stdin.write.assert_called_once_with(
            "汇报正文".encode("utf-8"))

    def test_missing_payload_falls_back_to_transcript(self):
        with mock.patch.object(hh.events.subprocess, "Popen") as popen:
            hh.spawn_summarizer(self._evt(transcript_path="/tmp/t.jsonl"),
                                "myproj", 123.0, "")
        args = popen.call_args[0][0]
        self.assertEqual(args[-1], "/tmp/t.jsonl")


class TestRecursionAndGating(TmpDataDirTest):
    """摘要无头会话防递归 + 未配置零副作用。"""

    def test_summarizing_env_suppresses_hook(self):
        _write_config()
        r = _run_hook({"hook_event_name": "SessionStart", "session_id": "s1",
                       "cwd": "/tmp/myproj"},
                      extra_env={"HIBOARD_SUMMARIZING": "1"})
        self.assertEqual(r.returncode, 0)
        self.assertFalse(hh.state_path().exists(),
                         "递归守卫下不应有任何状态写入")

    def test_unconfigured_hook_has_zero_side_effects(self):
        r = _run_hook({"hook_event_name": "SessionStart", "session_id": "s1",
                       "cwd": "/tmp/myproj"})
        self.assertEqual(r.returncode, 0)
        self.assertFalse(hh.state_path().exists())


class TestEntryPath(unittest.TestCase):
    def test_spawn_entry_is_the_hook_script(self):
        self.assertEqual(hh.events.ENTRY, ENTRY)
        self.assertTrue(ENTRY.exists())


class TestRequestPush(TmpDataDirTest):
    """后台推送调度：认领去重，一个合并窗口只起一个 flusher。"""

    def test_flusher_spawned_once_per_window(self):
        _write_config()
        with mock.patch.object(hh.events.subprocess, "Popen") as popen:
            hh.request_push()
            hh.request_push()
        self.assertEqual(popen.call_count, 1)
        self.assertIn("flush_claim", read_state())
        self.assertIn("--flush", popen.call_args[0][0])

    def test_expired_claim_allows_new_flusher(self):
        _write_config()
        hh.mutate_state(lambda s: s.update({"flush_claim": {"ts": 0}}))
        with mock.patch.object(hh.events.subprocess, "Popen") as popen:
            hh.request_push()
        self.assertEqual(popen.call_count, 1)

    def test_no_flush_env_pushes_synchronously(self):
        _write_config()
        hh.update_project("p", {"status": "done", "summary": "x",
                                "updated_at": time.time()})
        os.environ["HIBOARD_NO_FLUSH"] = "1"
        os.environ["HIBOARD_DRY_RUN"] = "1"
        try:
            hh.request_push()
        finally:
            os.environ.pop("HIBOARD_NO_FLUSH", None)
            os.environ.pop("HIBOARD_DRY_RUN", None)
        self.assertIn("DRY_RUN", hh.log_path().read_text(encoding="utf-8"))
        self.assertNotIn("flush_claim", read_state())


if __name__ == "__main__":
    unittest.main()
