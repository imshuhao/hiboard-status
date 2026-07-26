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


def _mkstate(**projects):
    return {"projects": projects}


class TestRender(unittest.TestCase):
    def test_render_content_orders_by_updated_desc(self):
        now = time.time()
        state = _mkstate(
            older={"status": "done", "summary": "旧的", "updated_at": now - 600},
            newer={"status": "running", "prompt": "新的", "updated_at": now},
        )
        out = hh.render_content(state, now=now)
        self.assertLess(out.index("newer"), out.index("older"))
        self.assertIn("🟢 newer — 运行中", out)
        self.assertIn("✅ older — 本轮完成", out)

    def test_render_content_prefers_summary_over_prompt(self):
        now = time.time()
        state = _mkstate(p={"status": "done", "summary": "摘要文本",
                            "prompt": "指令文本", "updated_at": now})
        out = hh.render_content(state, now=now)
        self.assertIn("摘要文本", out)
        self.assertNotIn("指令文本", out)

    def test_render_content_truncates_long_project(self):
        now = time.time()
        state = _mkstate(p={"status": "done", "summary": "长" * 5000,
                            "updated_at": now})
        out = hh.render_content(state, now=now)
        self.assertLessEqual(hh.utf16_len(out), hh.MAX_CARD_UTF16)
        self.assertIn("…", out)

    def test_render_content_empty_state(self):
        self.assertIn("暂无会话", hh.render_content({"projects": {}}))

    def test_render_summary_counts_running_and_latest(self):
        now = time.time()
        state = _mkstate(
            a={"status": "running", "updated_at": now - 10},
            b={"status": "done", "updated_at": now},
        )
        s = hh.render_summary(state, now=now)
        self.assertIn("1 个会话运行中", s)
        self.assertIn("b", s)

    def test_render_summary_empty(self):
        self.assertEqual(hh.render_summary({"projects": {}}), "Claude Code")


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
        on_disk = json.loads(hh.state_path().read_text(encoding="utf-8"))
        self.assertEqual(on_disk["projects"]["demo"]["status"], "running")


def _write_config(**over):
    cfg = {"authCode": "TESTCODE12345", "enabled": True}
    cfg.update(over)
    hh.data_dir().mkdir(parents=True, exist_ok=True)
    hh.config_path().write_text(json.dumps(cfg), encoding="utf-8")


class TestConfigAndPush(TmpDataDirTest):
    def setUp(self):
        super().setUp()
        os.environ["HIBOARD_DRY_RUN"] = "1"

    def tearDown(self):
        os.environ.pop("HIBOARD_DRY_RUN", None)
        super().tearDown()

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

    def test_push_card_dry_run_logs_payload(self):
        _write_config()
        ok = hh.push_card(hh.load_config(), "标题", "# 正文")
        self.assertTrue(ok)
        logtext = hh.log_path().read_text(encoding="utf-8")
        self.assertIn("DRY_RUN", logtext)
        self.assertIn(hh.CARD_ID, logtext)
        self.assertIn('"data"', logtext)  # 外层 data 包装存在

    def test_do_push_without_config_is_noop(self):
        state = hh.update_project("p", {"status": "running",
                                        "updated_at": time.time()})
        hh.do_push(state)  # 不应抛异常
        self.assertFalse(hh.log_path().exists()
                         and "DRY_RUN" in hh.log_path().read_text(encoding="utf-8"))

    def test_do_push_dedupes_identical_content(self):
        _write_config()
        # updated_at 需为近期值，否则会被 7 天清理逻辑移除（同 Task 4 调整）
        state = hh.update_project("p", {"status": "done", "summary": "x",
                                        "updated_at": time.time()})
        hh.do_push(state)
        state = json.loads(hh.state_path().read_text(encoding="utf-8"))
        hh.do_push(state)  # 内容未变，应跳过
        logtext = hh.log_path().read_text(encoding="utf-8")
        self.assertEqual(logtext.count("DRY_RUN"), 1)


def _run_hook(evt: dict, extra_env=None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HIBOARD_DRY_RUN"] = "1"
    env["HIBOARD_NO_SUMMARY"] = "1"  # 分发测试不真起后台摘要进程
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "hiboard_hook.py")],
        input=json.dumps(evt), text=True, capture_output=True, env=env)


class TestDispatch(TmpDataDirTest):
    def _state(self):
        return json.loads(hh.state_path().read_text(encoding="utf-8"))

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
        self.assertLessEqual(hh.utf16_len(e["prompt"]),
                             hh.MAX_PROMPT_UTF16 + len("正在处理："))

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
        _run_hook({"hook_event_name": "SessionEnd", "session_id": "s1",
                   "cwd": "/tmp/myproj"})
        self.assertEqual(self._state()["projects"]["myproj"]["status"], "ended")

    def test_garbage_stdin_exits_zero(self):
        env = os.environ.copy()
        r = subprocess.run([sys.executable, str(SCRIPTS / "hiboard_hook.py")],
                           input="not json", text=True,
                           capture_output=True, env=env)
        self.assertEqual(r.returncode, 0)

    def test_unknown_event_exits_zero(self):
        r = _run_hook({"hook_event_name": "SomethingNew", "cwd": "/tmp/x"})
        self.assertEqual(r.returncode, 0)


def _write_transcript(path: Path, texts):
    """构造 Claude Code transcript JSONL：texts 为 assistant 文本列表。"""
    lines = []
    for t in texts:
        lines.append(json.dumps({"type": "user",
                                 "message": {"role": "user", "content": "q"}}))
        lines.append(json.dumps({
            "type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": t}]}}))
    path.write_text("\n".join(lines), encoding="utf-8")


class TestSummarize(TmpDataDirTest):
    def setUp(self):
        super().setUp()
        os.environ["HIBOARD_DRY_RUN"] = "1"
        os.environ["HIBOARD_NO_LLM"] = "1"  # 强制走降级路径

    def tearDown(self):
        os.environ.pop("HIBOARD_DRY_RUN", None)
        os.environ.pop("HIBOARD_NO_LLM", None)
        super().tearDown()

    def test_last_assistant_text_picks_last(self):
        p = Path(self._tmp.name) / "t.jsonl"
        _write_transcript(p, ["第一条", "最后一条"])
        self.assertEqual(hh.last_assistant_text(str(p)), "最后一条")

    def test_last_assistant_text_string_content(self):
        p = Path(self._tmp.name) / "t.jsonl"
        p.write_text(json.dumps({"type": "assistant",
                                 "message": {"content": "纯字符串"}}),
                     encoding="utf-8")
        self.assertEqual(hh.last_assistant_text(str(p)), "纯字符串")

    def test_last_assistant_text_missing_file(self):
        self.assertEqual(hh.last_assistant_text("/nonexistent/x.jsonl"), "")

    def test_last_assistant_text_skips_garbage_lines(self):
        p = Path(self._tmp.name) / "t.jsonl"
        p.write_text("garbage\n" + json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "ok"}]}}),
            encoding="utf-8")
        self.assertEqual(hh.last_assistant_text(str(p)), "ok")

    def test_run_summarize_fallback_truncation(self):
        _write_config()
        hh.update_project("demo", {"status": "done",
                                   "summary": "（摘要生成中…）",
                                   "updated_at": time.time()})
        p = Path(self._tmp.name) / "t.jsonl"
        _write_transcript(p, ["工作汇报正文" * 200])
        hh.run_summarize("demo", str(p))
        e = json.loads(hh.state_path().read_text(
            encoding="utf-8"))["projects"]["demo"]
        self.assertNotIn("摘要生成中", e["summary"])
        self.assertLessEqual(hh.utf16_len(e["summary"]), 500)

    def test_run_summarize_empty_transcript(self):
        _write_config()
        hh.update_project("demo", {"status": "done",
                                   "summary": "（摘要生成中…）",
                                   "updated_at": time.time()})
        hh.run_summarize("demo", "/nonexistent/x.jsonl")
        e = json.loads(hh.state_path().read_text(
            encoding="utf-8"))["projects"]["demo"]
        self.assertEqual(e["summary"], "（本轮无文本输出）")


class TestSecurity(TmpDataDirTest):
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

    def test_dry_run_log_masks_auth_code(self):
        os.environ["HIBOARD_DRY_RUN"] = "1"
        try:
            hh.push_card({"authCode": "SECRET123456"}, "标题", "正文")
            logtext = hh.log_path().read_text(encoding="utf-8")
            self.assertNotIn("SECRET123456", logtext)
            self.assertIn('"authCode": "***"', logtext)
        finally:
            os.environ.pop("HIBOARD_DRY_RUN", None)

    def test_truncate_zero_limit_returns_empty(self):
        self.assertEqual(hh.truncate_utf16("abcdef", 0), "")
        self.assertEqual(hh.truncate_utf16("abcdef", -1), "")


if __name__ == "__main__":
    unittest.main()
