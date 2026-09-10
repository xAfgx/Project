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
TASK_ID = "runtime-oopif-grid-e2e-seed-v1"
EXPECTED = [1, 4, 7]


class _VisionHandler(BaseHTTPRequestHandler):
    calls = 0
    last_payload: Dict[str, Any] = {}
    token = "runtime-e2e-token"
    lock = threading.Lock()

    def _authorized(self) -> bool:
        return self.headers.get("Authorization", "") == f"Bearer {self.token}"

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.send_error(404)
            return
        if not self._authorized():
            self.send_error(401)
            return
        body = json.dumps({"ready": True, "model": "runtime-e2e-shared-service"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/classify":
            self.send_error(404)
            return
        if not self._authorized():
            self.send_error(401)
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
        instruction = str(payload.get("instruction") or "")
        assert len(sources) == 9, payload
        assert all(str(source).startswith("data:image/png;base64,") for source in sources), payload
        assert "fahrr" in instruction.casefold(), payload
        with self.lock:
            type(self).calls += 1
            type(self).last_payload = payload
        body = json.dumps({
            "selectedIndexes": EXPECTED,
            "scores": [0.1, 0.9, 0.1, 0.1, 0.95, 0.1, 0.1, 0.92, 0.1],
            "rawLogits": [-2.0, 2.0, -2.0, -2.0, 2.2, -2.0, -2.0, 2.1, -2.0],
            "model": "runtime-e2e-shared-service",
            "target": "fahrräder",
            "threshold": 0.5,
            "selectionPolicy": "runtime-e2e",
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _ChildHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/frame":
            self.send_error(404)
            return
        body = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Runtime OOPIF Grid</title>
<style>
  html,body{margin:0;padding:0;background:#fff;font-family:Arial,sans-serif}
  main{padding:28px;width:420px}
  p{font-size:20px;margin:0 0 18px 0}
  section{display:grid;grid-template-columns:repeat(3,112px);gap:10px}
  section>div{width:112px;height:112px;border-radius:8px;box-shadow:inset 0 0 0 1px #999;cursor:pointer}
  section>div:nth-child(1){background:linear-gradient(135deg,#d9d9d9,#9f9f9f)}
  section>div:nth-child(2){background:linear-gradient(135deg,#2f7d32,#9ccc65)}
  section>div:nth-child(3){background:linear-gradient(135deg,#b0bec5,#607d8b)}
  section>div:nth-child(4){background:linear-gradient(135deg,#ffcc80,#ef6c00)}
  section>div:nth-child(5){background:linear-gradient(135deg,#388e3c,#c5e1a5)}
  section>div:nth-child(6){background:linear-gradient(135deg,#90caf9,#1565c0)}
  section>div:nth-child(7){background:linear-gradient(135deg,#ce93d8,#7b1fa2)}
  section>div:nth-child(8){background:linear-gradient(135deg,#43a047,#dcedc8)}
  section>div:nth-child(9){background:linear-gradient(135deg,#ef9a9a,#c62828)}
  section>div[data-selected="1"]{outline:5px solid #111;outline-offset:-5px}
  button{margin-top:18px;width:150px;height:48px;font-size:17px}
  strong{display:block;margin-top:18px;font-size:20px}
</style>
</head>
<body>
<main>
  <p>Wähle alle Bilder mit Fahrrädern aus</p>
  <section>
    <div></div><div></div><div></div>
    <div></div><div></div><div></div>
    <div></div><div></div><div></div>
  </section>
  <button type="submit">Bestätigen</button>
</main>
<script>
(() => {
  const expected = [1,4,7];
  const selected = new Set();
  let trustedClicks = 0;
  const tiles = Array.from(document.querySelectorAll('section > div'));
  tiles.forEach((tile, index) => {
    tile.addEventListener('click', event => {
      if (!event.isTrusted) return;
      trustedClicks += 1;
      if (selected.has(index)) selected.delete(index); else selected.add(index);
      tile.dataset.selected = selected.has(index) ? '1' : '0';
    });
  });
  document.querySelector('button').addEventListener('click', event => {
    if (!event.isTrusted) return;
    trustedClicks += 1;
    const actual = Array.from(selected).sort((a,b)=>a-b);
    if (JSON.stringify(actual) !== JSON.stringify(expected)) return;
    const success = document.createElement('strong');
    success.textContent = 'Erfolgreich verifiziert';
    document.querySelector('main').appendChild(success);
    window.parent.postMessage({
      type: 'ARES_RUNTIME_E2E_SOLVED',
      selected: actual,
      trustedClicks
    }, '*');
  });
})();
</script>
</body>
</html>""".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _ParentHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/":
            self.send_error(404)
            return
        child_url = str(getattr(self.server, "child_url"))
        body = f"""<!doctype html>
<html>
<head><meta charset="utf-8"><title>ARES Runtime E2E</title></head>
<body style="margin:20px;background:#f5f5f5">
<script>
window.addEventListener('message', event => {{
  const data = event.data || {{}};
  if (data.type !== 'ARES_RUNTIME_E2E_SOLVED') return;
  window.__aresRuntimeE2E = data;
  location.hash = 'solved';
}});
</script>
<iframe src="{child_url}" style="width:650px;height:650px;border:1px solid #ccc"></iframe>
</body>
</html>""".encode("utf-8")
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


def _wait(messages: queue.Queue[Dict[str, Any]], request_id: str, expected_type: str, *, timeout: float, process: subprocess.Popen[str], stderr_lines: Deque[str]) -> Dict[str, Any]:
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
    stderr = "\n".join(stderr_lines)[-6000:]
    raise AssertionError(f"Timed out waiting for {expected_type} request={request_id}; returncode={process.poll()}; stderr_tail={stderr!r}")


def _rpc_page_state(process: subprocess.Popen[str], messages: queue.Queue[Dict[str, Any]], stderr_lines: Deque[str]) -> Dict[str, Any]:
    request_id = uuid.uuid4().hex
    _send(process, {"type": "rpc", "requestId": request_id, "action": "page-state"})
    message = _wait(messages, request_id, "rpc-result", timeout=10.0, process=process, stderr_lines=stderr_lines)
    return message.get("result") if isinstance(message.get("result"), dict) else {}


def _read_trace(path: Path) -> list[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def main() -> int:
    if not sys.platform.startswith("win"):
        print("RUNTIME_OOPIF_GRID_E2E_SKIPPED platform=" + sys.platform)
        return 0

    vision = ThreadingHTTPServer(("127.0.0.1", 0), _VisionHandler)
    child = ThreadingHTTPServer(("127.0.0.1", 0), _ChildHandler)
    parent = ThreadingHTTPServer(("127.0.0.1", 0), _ParentHandler)
    setattr(parent, "child_url", f"http://localhost:{child.server_port}/frame")
    for server in (vision, child, parent):
        threading.Thread(target=server.serve_forever, daemon=True).start()

    parent_url = f"http://127.0.0.1:{parent.server_port}/"
    vision_url = f"http://127.0.0.1:{vision.server_port}"

    try:
        with tempfile.TemporaryDirectory(prefix="ares-runtime-oopif-e2e-", ignore_cleanup_errors=True) as profile_dir:
            env = {**os.environ}
            runtime_dir = str(WORKER.parent)
            env["PYTHONPATH"] = runtime_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            env["ARES_VISION_SERVICE_URL"] = vision_url
            env["ARES_VISION_SERVICE_TOKEN"] = _VisionHandler.token
            env["ARES_VISION_OFFLINE"] = "1"
            process = subprocess.Popen([sys.executable, "-u", str(WORKER)], cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1, env=env)
            messages: queue.Queue[Dict[str, Any]] = queue.Queue()
            stderr_lines: Deque[str] = deque(maxlen=500)
            threading.Thread(target=_stdout_reader, args=(process, messages), daemon=True).start()
            threading.Thread(target=_stderr_reader, args=(process, stderr_lines), daemon=True).start()

            start_id = uuid.uuid4().hex
            _send(process, {"type": "start", "requestId": start_id, "taskId": TASK_ID, "profileDir": profile_dir, "headless": False, "browserArgs": ["--site-per-process", "--window-size=1280,900", "--force-device-scale-factor=1"]})
            ready = _wait(messages, start_id, "ready", timeout=40.0, process=process, stderr_lines=stderr_lines)
            assert ready.get("ok") is True, ready

            navigate_id = uuid.uuid4().hex
            _send(process, {"type": "navigate", "requestId": navigate_id, "url": parent_url, "timeoutMs": 15_000})
            navigated = _wait(messages, navigate_id, "navigated", timeout=25.0, process=process, stderr_lines=stderr_lines)
            assert str(navigated.get("url") or "").startswith(parent_url), navigated

            solved = False
            last_state: Dict[str, Any] = {}
            deadline = time.monotonic() + 28.0
            while time.monotonic() < deadline:
                last_state = _rpc_page_state(process, messages, stderr_lines)
                current_url = str(last_state.get("url") or "")
                frames = last_state.get("frames") if isinstance(last_state.get("frames"), list) else []
                if current_url.endswith("#solved"):
                    solved = True
                    break
                assert frames, f"iframe was not generically discovered: {last_state}"
                time.sleep(0.35)

            trace_path = Path(profile_dir) / ".ares-visual-trace.jsonl"
            trace = _read_trace(trace_path)
            assert solved, {"lastState": last_state, "visionCalls": _VisionHandler.calls, "traceTail": trace[-20:], "stderr": list(stderr_lines)[-80:]}

            decisions = [row for row in trace if row.get("phase") == "decision" and isinstance(row.get("decision"), dict)]
            assert any(list((row.get("decision") or {}).get("selectedIndexes") or []) == EXPECTED for row in decisions), decisions[-5:]

            screenshots = [row for row in trace if row.get("phase") == "grid-screenshot-captured"]
            assert screenshots, trace[-30:]
            screenshot_state = screenshots[-1].get("state") if isinstance(screenshots[-1].get("state"), dict) else {}
            assert int(screenshot_state.get("tileCount") or 0) == 9, screenshot_state
            assert str(screenshot_state.get("scope") or "").startswith("oopif:"), screenshot_state
            assert str(screenshot_state.get("origin") or "") == "direct-children", screenshot_state

            oopif_discovery = [row for row in trace if row.get("phase") == "oopif-discover"]
            assert any(int(row.get("frameCount") or 0) >= 1 for row in oopif_discovery), oopif_discovery[-5:]

            cursor_clicks = [row for row in trace if row.get("phase") == "cursor-click"]
            assert len(cursor_clicks) >= 4, cursor_clicks
            assert all(bool(row.get("seeded")) for row in cursor_clicks), cursor_clicks
            assert all(str(row.get("provider") or "").endswith(":cdp") for row in cursor_clicks), cursor_clicks
            assert all(int(row.get("pointCount") or 0) >= 2 for row in cursor_clicks), cursor_clicks
            assert all(str(row.get("preferred") or "") == "ghost-cursor" for row in cursor_clicks), cursor_clicks
            assert any(str(row.get("provider") or "").startswith("python-bezier:") for row in cursor_clicks), cursor_clicks

            actions = [row for row in trace if row.get("phase") == "action" and row.get("kind") == "image-grid"]
            assert any(bool((row.get("verification") or {}).get("verified")) for row in actions), actions[-5:]
            assert _VisionHandler.calls >= 1, _VisionHandler.last_payload

            close_id = uuid.uuid4().hex
            _send(process, {"type": "close", "requestId": close_id})
            _wait(messages, close_id, "closed", timeout=10.0, process=process, stderr_lines=stderr_lines)
            process.wait(timeout=10.0)
            print(f"RUNTIME_OOPIF_GRID_E2E_PASS pid={ready.get('pid')} visionCalls={_VisionHandler.calls} cursorClicks={len(cursor_clicks)} provider={cursor_clicks[0].get('provider')}")
    finally:
        for server in (parent, child, vision):
            server.shutdown()
            server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
