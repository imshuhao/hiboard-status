import time
import unittest

from helpers import hh


class TestTimeAndStatus(unittest.TestCase):
    def test_fmt_time_today_vs_older(self):
        now = time.time()
        self.assertNotIn("-", hh.fmt_time(now, now=now))
        self.assertIn("-", hh.fmt_time(now - 2 * 86400, now=now))


def _cells_entry(summary="", now=None, **cells):
    now = time.time() if now is None else now
    return {"cells": {sid: dict(c, updated_at=c.get("updated_at", now))
                      for sid, c in cells.items()},
            "summary": summary, "updated_at": now}


class TestCellsRender(unittest.TestCase):
    """格子模型渲染：分行、优先级、排序、截断。"""

    def test_render_content_orders_projects_by_updated_desc(self):
        now = time.time()
        state = {"projects": {
            "older": _cells_entry(A={"status": "done", "prompt": "x"},
                                  summary="旧的", now=now - 600),
            "newer": _cells_entry(B={"status": "running", "prompt": "新的"},
                                  now=now),
        }}
        out = hh.render_content(state, now=now)
        self.assertLess(out.index("newer"), out.index("older"))

    def test_render_content_truncates_long_summary(self):
        now = time.time()
        state = {"projects": {"p": _cells_entry(
            A={"status": "done", "prompt": "x"}, summary="长" * 5000, now=now)}}
        out = hh.render_content(state, now=now)
        self.assertLessEqual(hh.utf16_len(out), hh.MAX_CARD_UTF16)
        self.assertIn("…", out)

    def test_render_content_empty_state(self):
        self.assertIn("暂无会话", hh.render_content({"projects": {}}))

    def test_render_summary_empty(self):
        self.assertEqual(hh.render_summary({"projects": {}}), "Claude Code")

    def test_unknown_cell_status_treated_dead_not_crash(self):
        now = time.time()
        state = {"projects": {"p": _cells_entry(
            A={"status": "weird", "prompt": "x"}, summary="留底", now=now)}}
        self.assertIn("已结束", hh.render_content(state, now=now))
        self.assertIn("已结束", hh.render_summary(state, now=now))

    def test_single_running_cell_matches_classic_format(self):
        now = time.time()
        state = {"projects": {"p": _cells_entry(
            A={"status": "running", "prompt": "修复登录"}, now=now)}}
        out = hh.render_content(state, now=now)
        self.assertIn("🟢 p — 运行中", out)
        self.assertIn("正在处理：修复登录", out)
        self.assertIn("`自 ", out)

    def test_waiting_outranks_running_in_header(self):
        now = time.time()
        state = {"projects": {"p": _cells_entry(
            A={"status": "running", "prompt": "跑任务"},
            B={"status": "waiting", "prompt": "要继续吗"}, now=now)}}
        out = hh.render_content(state, now=now)
        self.assertIn("🟡 p — 2 个会话", out)
        # waiting 行排在 running 行前面，多格行首带各自 emoji
        self.assertLess(out.index("要继续吗"), out.index("跑任务"))
        self.assertIn("🟡 要继续吗", out)
        self.assertIn("🟢 正在处理：跑任务", out)

    def test_done_cell_single_shows_project_summary(self):
        now = time.time()
        state = {"projects": {"p": _cells_entry(
            A={"status": "done", "prompt": "旧指令"},
            summary="完成了重构", now=now)}}
        out = hh.render_content(state, now=now)
        self.assertIn("✅ p — 本轮完成", out)
        self.assertIn("完成了重构", out)
        self.assertNotIn("上轮", out)  # 摘要即正文，不重复

    def test_no_live_cells_decays_to_ended_with_summary(self):
        now = time.time()
        state = {"projects": {"p": _cells_entry(summary="上次的总结", now=now)}}
        out = hh.render_content(state, now=now)
        self.assertIn("⚪ p — 已结束", out)
        self.assertIn("上次的总结", out)

    def test_stale_cell_excluded(self):
        now = time.time()
        state = {"projects": {"p": _cells_entry(
            A={"status": "running", "prompt": "早死了",
               "updated_at": now - 3 * 3600},
            summary="留下的总结", now=now)}}
        out = hh.render_content(state, now=now)
        self.assertNotIn("早死了", out)
        self.assertIn("已结束", out)

    def test_last_summary_line_on_running_with_cells(self):
        now = time.time()
        state = {"projects": {"p": _cells_entry(
            A={"status": "running", "prompt": "新任务"},
            summary="上一轮干了活", now=now)}}
        out = hh.render_content(state, now=now)
        self.assertIn("↳ 上轮：上一轮干了活", out)

    def test_render_summary_counts_running_cells_across_projects(self):
        now = time.time()
        state = {"projects": {
            "p1": _cells_entry(A={"status": "running", "prompt": "x"},
                               B={"status": "running", "prompt": "y"}, now=now),
            "p2": _cells_entry(C={"status": "waiting", "prompt": "z"},
                               now=now - 10),
        }}
        s = hh.render_summary(state, now=now)
        self.assertIn("2 个会话运行中", s)

    def test_done_cell_without_summary_falls_back_to_prompt(self):
        now = time.time()
        state = {"projects": {"p": _cells_entry(
            A={"status": "done", "prompt": "跑测试"}, now=now)}}
        out = hh.render_content(state, now=now)
        self.assertIn("跑测试", out)
        self.assertNotIn("正在处理", out)

    def test_waiting_cell_shows_last_summary_line(self):
        now = time.time()
        state = {"projects": {"p": _cells_entry(
            A={"status": "waiting", "prompt": "要继续吗"},
            summary="跑完了测试", now=now)}}
        self.assertIn("↳ 上轮：跑完了测试", hh.render_content(state, now=now))



if __name__ == "__main__":
    unittest.main()
