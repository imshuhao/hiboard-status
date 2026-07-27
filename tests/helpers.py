"""测试共享件：临时数据目录基类、配置/事件/转录构造器。"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import hiboard as hh  # noqa: E402

ENTRY = SCRIPTS / "hiboard_hook.py"


class TmpDataDirTest(unittest.TestCase):
    """基类：每个测试用独立临时数据目录。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["HIBOARD_DATA_DIR"] = self._tmp.name

    def tearDown(self):
        os.environ.pop("HIBOARD_DATA_DIR", None)
        self._tmp.cleanup()


def _mkstate(**projects):
    return {"projects": projects}


def _write_config(**over):
    cfg = {"authCode": "TESTCODE12345", "enabled": True}
    cfg.update(over)
    hh.data_dir().mkdir(parents=True, exist_ok=True)
    hh.config_path().write_text(json.dumps(cfg), encoding="utf-8")


def _run_hook(evt: dict, extra_env=None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HIBOARD_DRY_RUN"] = "1"
    env["HIBOARD_NO_SUMMARY"] = "1"  # 分发测试不真起后台摘要进程
    env["HIBOARD_NO_FLUSH"] = "1"    # 分发测试同步推送，不起 flusher
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, str(ENTRY)],
        input=json.dumps(evt), text=True, capture_output=True, env=env)


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


def read_state():
    return json.loads(hh.state_path().read_text(encoding="utf-8"))


__all__ = ["hh", "ENTRY", "SCRIPTS", "TmpDataDirTest", "_mkstate",
           "_write_config", "_run_hook", "_write_transcript", "read_state",
           "json", "os", "subprocess", "sys", "time", "unittest", "Path"]
