from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Deque, Dict

PREFIX = "ARES_SB_TASK\t"
ROOT = Path(__file__).resolve().parents[2]
WORKER = Path(__file__).with_name("task_browser_worker_oopif.py")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = b"<!doctype html><html><head><title>ARES RPC Focus</title></head><body><main>ready</main></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _stdout_reader(process: subprocess.Popen[str], target: queue.Queue[Dict[str, Any]]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        if not line.startswith(PREFIX):
            continue
        try:
            value = json.loads(line[len(PREFIX):])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            target.put(value)


def _stderr_reader(process: subprocess.Popen[str], target: Deque[str]) -> None:
    assert process.stderr is not None
    for line in process.stderr:
        target.append(line.rstrip())


def _send(process: subprocess.Popen[str], payload: Dict[str, Any]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    process.stdin.flush()


def _wait(
    messages: queue.Queue[Dict[str, Any]],
    request_id: str,
    expected_type: str,
    *,
    timeout: float,
    process: subprocess.Popen[str],
    stderr_lines: Deque[str],
) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            message = messages.get(timeout=max(0.05, deadline - time.monotonic()))
        except queue.Empty:
            break
        if message.get("requestId") != request_id:
            continue
        if message.get("type") == "error" or message.get("ok") is False:
            raise AssertionError(f"Task RPC error: {message}")
        if message.get("type") == expected_type:
            return message
    stderr = "\n".join(stderr_lines)[-4000:]
    raise AssertionError(
        f"Timed out waiting for {expected_type} request={request_id}; "
        f"returncode={process.poll()}; stderr_tail={stderr!r}"
    )


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/"

    try:
        with tempfile.TemporaryDirectory(prefix="ares-rpc-focus-", ignore_cleanup_errors=True) as profile_dir:
            env = {**os.environ}
            runtime_dir = str(WORKER.parent)
            env["PYTHONPATH"] = runtime_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            process = subprocess.Popen(
                [sys.executable, "-u", str(WORKER)],
                cwd=str(ROOT),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )
            messages: queue.Queue[Dict[str, Any]] = queue.Queue()
            stderr_lines: Deque[str] = deque(maxlen=300)
            threading.Thread(target=_stdout_reader, args=(process, messages), daemon=True).start()
            threading.Thread(target=_stderr_reader, args=(process, stderr_lines), daemon=True).start()

            start_id = uuid.uuid4().hex
            _send(process, {
                "type": "start",
                "requestId": start_id,
                "taskId": "rpc-navigation-focus",
                "profileDir": profile_dir,
                "headless": True,
                "browserArgs": [],
            })
            ready = _wait(messages, start_id, "ready", timeout=35.0, process=process, stderr_lines=stderr_lines)
            assert ready.get("ok") is True, ready

            navigate_id = uuid.uuid4().hex
            started = time.monotonic()
            _send(process, {
                "type": "navigate",
                "requestId": navigate_id,
                "url": url,
                "timeoutMs": 10_000,
            })
            navigated = _wait(messages, navigate_id, "navigated", timeout=20.0, process=process, stderr_lines=stderr_lines)
            elapsed = time.monotonic() - started
            assert str(navigated.get("url") or "").startswith(url), navigated

            state_id = uuid.uuid4().hex
            _send(process, {"type": "rpc", "requestId": state_id, "action": "page-state"})
            state = _wait(messages, state_id, "rpc-result", timeout=8.0, process=process, stderr_lines=stderr_lines)
            result = state.get("result") if isinstance(state.get("result"), dict) else {}
            assert str(result.get("url") or "").startswith(url), state
            assert str(result.get("readyState") or "") in {"interactive", "complete"}, state

            close_id = uuid.uuid4().hex
            _send(process, {"type": "close", "requestId": close_id})
            _wait(messages, close_id, "closed", timeout=8.0, process=process, stderr_lines=stderr_lines)
            process.wait(timeout=8.0)
            print(f"TASK_RPC_NAVIGATION_PASS elapsed={elapsed:.3f}s pid={ready.get('pid')}")
    finally:
        server.shutdown()
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
