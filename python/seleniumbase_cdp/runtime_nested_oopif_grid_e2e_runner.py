from __future__ import annotations

import uuid
from typing import Any, Deque, Dict
import queue
import subprocess

import runtime_nested_oopif_grid_e2e_probe as probe


# page-state is intentionally a runtime opportunity in the production worker:
# TaskRpcRuntime.page_state -> scheduler.poll_if_due() -> adapter.poll_runtime().
# With several OOPIF routes that synchronous poll can legitimately outlive the
# probe's old 10s RPC-only budget. Keep the production path unchanged and give
# this representative harness the same 35s budget as its outer runtime proof.
def _page_state_with_runtime_budget(
    process: subprocess.Popen[str],
    messages: queue.Queue[Dict[str, Any]],
    stderr_lines: Deque[str],
) -> Dict[str, Any]:
    request_id = uuid.uuid4().hex
    probe.send_command(process, {"type": "rpc", "requestId": request_id, "action": "page-state"})
    message = probe.wait_message(
        messages,
        request_id,
        "rpc-result",
        timeout=35.0,
        process=process,
        stderr_lines=stderr_lines,
    )
    return message.get("result") if isinstance(message.get("result"), dict) else {}


probe.page_state = _page_state_with_runtime_budget

if __name__ == "__main__":
    raise SystemExit(probe.main())
