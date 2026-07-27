import time
import unittest

from helpers import hh, _mkstate


class TestTimeAndStatus(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
