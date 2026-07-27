import json
import os
import time
import unittest
from pathlib import Path

from helpers import hh, TmpDataDirTest, _write_config, _write_transcript, read_state


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
                                   "summary": hh.SUMMARY_PLACEHOLDER,
                                   "updated_at": time.time()})
        p = Path(self._tmp.name) / "t.jsonl"
        _write_transcript(p, ["工作汇报正文" * 200])
        hh.run_summarize("demo", str(p), time.time())
        e = read_state()["projects"]["demo"]
        self.assertNotIn("摘要生成中", e["summary"])
        self.assertLessEqual(hh.utf16_len(e["summary"]), 500)

    def test_run_summarize_empty_transcript(self):
        _write_config()
        hh.update_project("demo", {"status": "done",
                                   "summary": hh.SUMMARY_PLACEHOLDER,
                                   "updated_at": time.time()})
        hh.run_summarize("demo", "/nonexistent/x.jsonl", time.time())
        self.assertEqual(read_state()["projects"]["demo"]["summary"],
                         "（本轮无文本输出）")

    def test_late_summary_lands_without_touching_status(self):
        # 语义核心：摘要生成期间用户提交了新指令，迟到摘要照常落入
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
        e = read_state()["projects"]["demo"]
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
        self.assertEqual(read_state()["projects"]["demo"]["summary"],
                         "新回合摘要")

    def test_summary_newlines_collapsed(self):
        # 转录原文含换行时不得伪造卡片分节
        _write_config()
        hh.update_project("demo", {"status": "done",
                                   "summary": hh.SUMMARY_PLACEHOLDER,
                                   "updated_at": time.time()})
        p = Path(self._tmp.name) / "t.jsonl"
        _write_transcript(p, ["第一行\n## 伪造标题\n第三行"])
        hh.run_summarize("demo", str(p), time.time())
        self.assertNotIn("\n", read_state()["projects"]["demo"]["summary"])


if __name__ == "__main__":
    unittest.main()
