"""hiboard-status 公共 API 门面。

`import hiboard as hh` 即可访问全部公共名；子模块经 hh.push / hh.store 等可达
（测试 mock 用）。入口脚本 scripts/hiboard_hook.py 只 import 本门面。
"""

from . import const, events, ondemand, push, render, status, store, summarize, text  # noqa: F401
from .const import (CARD_ID, DEBOUNCE_SECS, FLUSH_CLAIM_SECS,  # noqa: F401
                    MAX_CARD_UTF16, MAX_LAST_SUMMARY_UTF16,
                    MAX_PROJECT_UTF16, MAX_PROMPT_UTF16, PRUNE_SECS,
                    STALE_SECS, SUMMARY_MIN_UTF16, SUMMARY_PLACEHOLDER,
                    VERSION)
from .events import (handle_event, project_name, request_push,  # noqa: F401
                     resolve_project, spawn_summarizer)
from .ondemand import cmd_push, cmd_test_push  # noqa: F401
from .push import (CP_HINTS, ERR_HINTS, do_push, load_config,  # noqa: F401
                   next_midnight, push_card, run_flush, status_card_id)
from .render import (STATUS_META, display_status, effective_status,  # noqa: F401
                     fmt_time, live_cells, render_content, render_summary)
from .status import cmd_status  # noqa: F401
from .store import (chmod_600, config_path, data_dir, ensure_dir, log,  # noqa: F401
                    log_path, mutate_state, state_path, update_project)
from .summarize import (last_assistant_text, last_turn, llm_summarize,  # noqa: F401
                        run_summarize)
from .text import slugify_ascii, truncate_utf16, utf16_len  # noqa: F401

# 测试与旧代码可能通过 hh.urllib 访问；push 模块内部使用的 urllib 以
# hh.push.urllib 为准（mock patch 请打到 hiboard.push.urllib.request）
