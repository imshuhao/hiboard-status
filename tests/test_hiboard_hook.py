import json, os, re, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock

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
        hh.update_project("p", {"status": "running",
                                "updated_at": time.time()})
        hh.do_push()  # 不应抛异常
        self.assertFalse(hh.log_path().exists()
                         and "DRY_RUN" in hh.log_path().read_text(encoding="utf-8"))

    def test_do_push_dedupes_identical_content(self):
        _write_config()
        # updated_at 需为近期值，否则会被 7 天清理逻辑移除（同 Task 4 调整）
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
        state = json.loads(hh.state_path().read_text(encoding="utf-8"))
        self.assertNotIn("push_claim", state)
        self.assertIn("last_push_hash", state)


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
        hh.run_summarize("demo", str(p), time.time())
        e = json.loads(hh.state_path().read_text(
            encoding="utf-8"))["projects"]["demo"]
        self.assertNotIn("摘要生成中", e["summary"])
        self.assertLessEqual(hh.utf16_len(e["summary"]), 500)

    def test_run_summarize_empty_transcript(self):
        _write_config()
        hh.update_project("demo", {"status": "done",
                                   "summary": "（摘要生成中…）",
                                   "updated_at": time.time()})
        hh.run_summarize("demo", "/nonexistent/x.jsonl", time.time())
        e = json.loads(hh.state_path().read_text(
            encoding="utf-8"))["projects"]["demo"]
        self.assertEqual(e["summary"], "（本轮无文本输出）")

    def test_late_summary_lands_without_touching_status(self):
        # 语义重构核心：摘要生成期间用户提交了新指令，迟到摘要照常落入
        # summary（渲染为「上轮」行），但绝不碰 status/prompt/updated_at
        _write_config()
        before = time.time() - 5
        hh.update_project("demo", {"status": "running", "prompt": "新任务",
                                   "summary": hh.SUMMARY_PLACEHOLDER,
                                   "summary_ts": before,
                                   "updated_at": before})
        p = Path(self._tmp.name) / "t.jsonl"
        _write_transcript(p, ["上一轮的汇报"])
        hh.run_summarize("demo", str(p), before)
        e = json.loads(hh.state_path().read_text(
            encoding="utf-8"))["projects"]["demo"]
        self.assertEqual(e["status"], "running")
        self.assertEqual(e["prompt"], "新任务")
        self.assertIn("上一轮的汇报", e["summary"])
        self.assertEqual(e["updated_at"], before)  # 不影响排序与 stale 判定

    def test_out_of_order_summary_rejected(self):
        # 乱序防护：旧回合的摘要不得覆盖新回合的
        _write_config()
        newer = time.time()
        hh.update_project("demo", {"status": "done", "summary": "新回合摘要",
                                   "summary_ts": newer,
                                   "updated_at": newer})
        p = Path(self._tmp.name) / "t.jsonl"
        _write_transcript(p, ["旧回合的汇报"])
        hh.run_summarize("demo", str(p), newer - 100)
        e = json.loads(hh.state_path().read_text(
            encoding="utf-8"))["projects"]["demo"]
        self.assertEqual(e["summary"], "新回合摘要")


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
        with mock.patch.object(hh.urllib.request, "urlopen",
                               return_value=self._quota_response()):
            ok = hh.push_card(hh.load_config(), "t", "c")
        self.assertFalse(ok)
        state = json.loads(hh.state_path().read_text(encoding="utf-8"))
        self.assertGreater(state.get("quota_blocked_until", 0), time.time())

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


class TestSessionsMap(TmpDataDirTest):
    def test_stale_session_mappings_pruned(self):
        old = time.time() - hh.PRUNE_SECS - 60
        hh.mutate_state(lambda s: s.update(
            {"sessions": {"old-sid": {"proj": "x", "ts": old},
                          "new-sid": {"proj": "y", "ts": time.time()}}}))
        hh.mutate_state(lambda s: None)  # 任意一次写入都会触发清理
        state = json.loads(hh.state_path().read_text(encoding="utf-8"))
        self.assertNotIn("old-sid", state["sessions"])
        self.assertIn("new-sid", state["sessions"])


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


class TestRenderStatusBody(unittest.TestCase):
    """渲染层按状态决定正文：前缀只在 running 出现，ended 不显示瞬时指令。"""

    def test_running_prompt_gets_prefix(self):
        now = time.time()
        out = hh.render_content({"projects": {"p": {
            "status": "running", "prompt": "跑测试", "updated_at": now}}}, now=now)
        self.assertIn("正在处理：跑测试", out)

    def test_ended_hides_prompt(self):
        now = time.time()
        out = hh.render_content({"projects": {"p": {
            "status": "ended", "prompt": "跑测试", "updated_at": now}}}, now=now)
        self.assertNotIn("跑测试", out)
        self.assertIn("已结束", out)

    def test_done_falls_back_to_prompt_when_no_summary(self):
        now = time.time()
        out = hh.render_content({"projects": {"p": {
            "status": "done", "prompt": "跑测试", "updated_at": now}}}, now=now)
        self.assertIn("跑测试", out)
        self.assertNotIn("正在处理", out)

    def test_running_shows_last_summary_line(self):
        now = time.time()
        out = hh.render_content({"projects": {"p": {
            "status": "running", "prompt": "修复登录", "summary": "重构了 token 逻辑",
            "updated_at": now}}}, now=now)
        self.assertIn("正在处理：修复登录", out)
        self.assertIn("↳ 上轮：重构了 token 逻辑", out)

    def test_waiting_shows_last_summary_line(self):
        now = time.time()
        out = hh.render_content({"projects": {"p": {
            "status": "waiting", "prompt": "要继续吗", "summary": "跑完了测试",
            "updated_at": now}}}, now=now)
        self.assertIn("↳ 上轮：跑完了测试", out)

    def test_done_has_no_last_summary_line(self):
        # done 状态摘要即正文，不重复渲染「上轮」行
        now = time.time()
        out = hh.render_content({"projects": {"p": {
            "status": "done", "summary": "完成了重构", "updated_at": now}}}, now=now)
        self.assertIn("完成了重构", out)
        self.assertNotIn("上轮", out)

    def test_unknown_status_renders_as_stale_not_crash(self):
        # 手改 state.json 或版本回退可能出现未知状态值，渲染须兜底
        now = time.time()
        state = {"projects": {"p": {"status": "weird", "updated_at": now}}}
        self.assertIn("状态未知", hh.render_content(state, now=now))
        self.assertIn("状态未知", hh.render_summary(state, now=now))


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
        slots = json.loads(hh.state_path().read_text(
            encoding="utf-8"))["manual_slots"]
        self.assertEqual(set(slots), {"1", "2", "3"})

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
        ids = set(self._pushed_card_ids())
        self.assertEqual(len(ids), 2)

    def test_ascii_topic_slug_unchanged(self):
        # 纯 ASCII 主题保持原 slug，不破坏已存在的主题卡 ID
        self.assertEqual(hh.slugify_ascii("Daily Report"), "daily_report")

    def test_failed_push_restores_ring_slot(self):
        _write_config()
        # 先占满一个卡位建立基线
        hh.cmd_push(self._push_file(summary="占位", content="c"))
        before = json.loads(hh.state_path().read_text(
            encoding="utf-8"))["manual_slots"]
        # 关掉 DRY_RUN 并把端点指向不可达地址，制造真实推送失败
        os.environ.pop("HIBOARD_DRY_RUN", None)
        try:
            _write_config(pushServiceUrl="http://127.0.0.1:1/x")
            rc = hh.cmd_push(self._push_file(summary="必败", content="c"))
        finally:
            os.environ["HIBOARD_DRY_RUN"] = "1"
        self.assertEqual(rc, 1)
        after = json.loads(hh.state_path().read_text(
            encoding="utf-8"))["manual_slots"]
        self.assertEqual(after, before)  # 失败不烧轮转位置

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
            state = json.loads(hh.state_path().read_text(encoding="utf-8"))
            self.assertNotIn("manual_slots", state)
        else:
            self.assertFalse(hh.state_path().exists())

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
            [sys.executable, str(SCRIPTS / "hiboard_hook.py"), "--push",
             "/nonexistent.json"],
            capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1)  # --push 失败必须非零，区别于 hook 恒零

    def test_test_push_exit_codes(self):
        env = os.environ.copy()
        env["HIBOARD_DRY_RUN"] = "1"
        cmd = [sys.executable, str(SCRIPTS / "hiboard_hook.py"), "--test-push"]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1)  # 无配置：失败须非零（与 --push 同哲学）
        _write_config()
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
