import os
import subprocess
import sys
import unittest

from helpers import (ENTRY, hh, TmpDataDirTest, _run_hook, _write_config,
                     read_state)


class TestDispatch(TmpDataDirTest):
    def _state(self):
        return read_state()

    def test_session_start_registers_running(self):
        _write_config()
        r = _run_hook({"hook_event_name": "SessionStart",
                       "session_id": "s1", "cwd": "/tmp/myproj"})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self._state()["projects"]["myproj"]["status"], "running")

    def test_user_prompt_truncates_and_sets_running(self):
        _write_config()
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                   "cwd": "/tmp/myproj", "prompt": "改" * 200})
        e = self._state()["projects"]["myproj"]
        self.assertEqual(e["status"], "running")
        self.assertLessEqual(hh.utf16_len(e["prompt"]), hh.MAX_PROMPT_UTF16)

    def test_stop_sets_done_with_placeholder(self):
        _write_config()
        _run_hook({"hook_event_name": "Stop", "session_id": "s1",
                   "cwd": "/tmp/myproj", "transcript_path": "/nonexistent"})
        e = self._state()["projects"]["myproj"]
        self.assertEqual(e["status"], "done")
        self.assertIn("摘要生成中", e["summary"])

    def test_notification_sets_waiting_keeps_prompt(self):
        _write_config()
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                   "cwd": "/tmp/myproj", "prompt": "跑测试"})
        _run_hook({"hook_event_name": "Notification", "session_id": "s1",
                   "cwd": "/tmp/myproj"})
        e = self._state()["projects"]["myproj"]
        self.assertEqual(e["status"], "waiting")
        self.assertIn("跑测试", e["prompt"])  # Notification 不清空既有内容

    def test_session_end_sets_ended(self):
        _write_config()
        _run_hook({"hook_event_name": "SessionStart", "session_id": "s1",
                   "cwd": "/tmp/myproj"})
        _run_hook({"hook_event_name": "SessionEnd", "session_id": "s1",
                   "cwd": "/tmp/myproj"})
        self.assertEqual(self._state()["projects"]["myproj"]["status"], "ended")

    def test_session_end_from_other_session_is_ignored(self):
        # 幽灵会话守卫：非记录在案的 session 不得把项目翻成「已结束」
        _write_config()
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                   "cwd": "/tmp/myproj", "prompt": "干活"})
        _run_hook({"hook_event_name": "SessionEnd", "session_id": "ghost",
                   "cwd": "/tmp/myproj"})
        self.assertEqual(self._state()["projects"]["myproj"]["status"], "running")

    def test_ghost_start_end_sequence_cannot_take_over(self):
        # 审查复现的 Critical：幽灵会话 SessionStart+SessionEnd 连击。
        # SessionStart 不得接管他人条目，其 SessionEnd 也因此被守卫拦截
        _write_config()
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                   "cwd": "/tmp/myproj", "prompt": "干活"})
        _run_hook({"hook_event_name": "SessionStart", "session_id": "ghost",
                   "cwd": "/tmp/myproj"})
        _run_hook({"hook_event_name": "SessionEnd", "session_id": "ghost",
                   "cwd": "/tmp/myproj"})
        e = self._state()["projects"]["myproj"]
        self.assertEqual(e["status"], "running")
        self.assertIn("干活", e["prompt"])
        self.assertEqual(e["session_id"], "s1")

    def test_session_end_unknown_project_creates_nothing(self):
        _write_config()
        _run_hook({"hook_event_name": "SessionEnd", "session_id": "ghost",
                   "cwd": "/tmp/neverseen"})
        self.assertNotIn("neverseen", self._state()["projects"])

    def test_project_pinned_at_session_start_despite_cd(self):
        # 归属钉死在启动目录：会话中途 cd 到别的目录，事件仍记到原项目
        _write_config()
        _run_hook({"hook_event_name": "SessionStart", "session_id": "s1",
                   "cwd": "/tmp/projA"})
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                   "cwd": "/tmp/projB", "prompt": "改代码"})
        projects = self._state()["projects"]
        self.assertIn("改代码", projects["projA"]["prompt"])
        self.assertNotIn("projB", projects)

    def test_unmapped_session_falls_back_to_cwd(self):
        # 插件启用前已开始的会话没有映射，退回按 cwd 推断
        _write_config()
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "old",
                   "cwd": "/tmp/legacy", "prompt": "继续"})
        self.assertIn("legacy", self._state()["projects"])

    def test_new_prompt_and_session_start_preserve_summary(self):
        # summary 是「最近完成回合的总结」，新指令与新会话都不清空它
        _write_config()
        _run_hook({"hook_event_name": "Stop", "session_id": "s1",
                   "cwd": "/tmp/myproj", "transcript_path": "/nonexistent"})
        _run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                   "cwd": "/tmp/myproj", "prompt": "下一件事"})
        e = self._state()["projects"]["myproj"]
        self.assertEqual(e["status"], "running")
        self.assertEqual(e["summary"], hh.SUMMARY_PLACEHOLDER)
        _run_hook({"hook_event_name": "SessionStart", "session_id": "s2",
                   "cwd": "/tmp/myproj"})
        self.assertEqual(self._state()["projects"]["myproj"]["summary"],
                         hh.SUMMARY_PLACEHOLDER)

    def test_garbage_stdin_exits_zero(self):
        env = os.environ.copy()
        r = subprocess.run([sys.executable, str(ENTRY)],
                           input="not json", text=True,
                           capture_output=True, env=env)
        self.assertEqual(r.returncode, 0)

    def test_unknown_event_exits_zero(self):
        r = _run_hook({"hook_event_name": "SomethingNew", "cwd": "/tmp/x"})
        self.assertEqual(r.returncode, 0)


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
        # 不写 config —— 设计 §9：未配置时插件零副作用
        r = _run_hook({"hook_event_name": "SessionStart", "session_id": "s1",
                       "cwd": "/tmp/myproj"})
        self.assertEqual(r.returncode, 0)
        self.assertFalse(hh.state_path().exists())


class TestEntryPath(unittest.TestCase):
    def test_spawn_entry_is_the_hook_script(self):
        # 拆包后自我重启必须指回入口脚本，而非包内模块的 __file__
        self.assertEqual(hh.events.ENTRY, ENTRY)
        self.assertTrue(ENTRY.exists())


class TestRequestPush(TmpDataDirTest):
    """后台推送调度：认领去重，一个合并窗口只起一个 flusher。"""

    def test_flusher_spawned_once_per_window(self):
        from unittest import mock
        _write_config()
        with mock.patch.object(hh.events.subprocess, "Popen") as popen:
            hh.request_push()
            hh.request_push()  # 认领仍在，有 flusher 在途，不重复 spawn
        self.assertEqual(popen.call_count, 1)
        self.assertIn("flush_claim", read_state())
        args = popen.call_args[0][0]
        self.assertIn("--flush", args)

    def test_expired_claim_allows_new_flusher(self):
        from unittest import mock
        _write_config()
        hh.mutate_state(lambda s: s.update(
            {"flush_claim": {"ts": 0}}))  # 早已过期（进程被杀场景）
        with mock.patch.object(hh.events.subprocess, "Popen") as popen:
            hh.request_push()
        self.assertEqual(popen.call_count, 1)

    def test_no_flush_env_pushes_synchronously(self):
        import os
        _write_config()
        hh.update_project("p", {"status": "done", "summary": "x",
                                "updated_at": __import__("time").time()})
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
