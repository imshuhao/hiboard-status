"""数据目录、日志与状态文件。

state/log 含用户指令原文，权限一律 600/700。
mutate_state 是唯一的状态写入通道：flock 串行化 + 原子写回 + 过期清理。
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

from .const import PRUNE_SECS

try:
    import fcntl

    def _lock(f):
        fcntl.flock(f, fcntl.LOCK_EX)

    def _unlock(f):
        fcntl.flock(f, fcntl.LOCK_UN)
except ImportError:  # Windows（尽力而为，未经真机验证）
    import msvcrt

    def _lock(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock(f):
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


def data_dir() -> Path:
    return Path(os.environ.get("HIBOARD_DATA_DIR",
                               str(Path.home() / ".claude" / "hiboard")))


def state_path() -> Path:
    return data_dir() / "state.json"


def config_path() -> Path:
    return data_dir() / "config.json"


def log_path() -> Path:
    return data_dir() / "push.log"


def ensure_dir() -> Path:
    """创建数据目录，权限 0700。state/log 含用户指令原文，不应对同机他人可读。"""
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        d.chmod(0o700)  # 目录已存在时 mkdir 的 mode 不生效，显式补一次
    except OSError:
        pass
    return d


def chmod_600(p: Path) -> None:
    try:
        p.chmod(0o600)
    except OSError:
        pass


def log(msg: str) -> None:
    try:
        ensure_dir()
        p = log_path()
        try:
            if p.stat().st_size > 512 * 1024:  # 轮转：只留最近 500 行
                lines = p.read_text(encoding="utf-8").splitlines()[-500:]
                p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{stamp} {msg}\n")
        chmod_600(p)
    except Exception:
        pass  # 日志失败不影响任何流程


def mutate_state(mutator) -> dict:
    """flock 串行化：加载 → mutator 就地修改 → 清理过期 → 原子写回。"""
    ensure_dir()
    with open(data_dir() / ".lock", "w") as lock:
        _lock(lock)
        try:
            try:
                state = json.loads(state_path().read_text(encoding="utf-8"))
                if not isinstance(state, dict):
                    raise ValueError("state 不是对象")
            except FileNotFoundError:
                state = {}
            except (ValueError, json.JSONDecodeError):
                state_path().rename(state_path().with_suffix(".json.bak"))
                log("WARN state.json 损坏，已备份并重建")
                state = {}
            state.setdefault("projects", {})
            mutator(state)
            now = time.time()
            state["projects"] = {
                n: e for n, e in state["projects"].items()
                if now - e.get("updated_at", 0) <= PRUNE_SECS
            }
            state["sessions"] = {
                sid: s for sid, s in state.get("sessions", {}).items()
                if now - s.get("ts", 0) <= PRUNE_SECS
            }
            tmp = state_path().with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            chmod_600(tmp)  # rename 前设权限，避免出现可读窗口
            tmp.rename(state_path())
            return state
        finally:
            _unlock(lock)


def update_project(proj: str, fields: dict) -> dict:
    def m(state):
        state["projects"].setdefault(proj, {}).update(fields)
    return mutate_state(m)
