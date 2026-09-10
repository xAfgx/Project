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
TASK_ID = "runtime-nested-oopif-grid-e2e-seed-v1"
EXPECTED = [1, 4, 7]


class VisionHandler(BaseHTTPRequestHandler):
    calls = 0
    last_payload: Dict[str, Any] = {}
    token = "runtime-nested-e2e-token"
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
        body = json.dumps({"ready": True, "model": "runtime-nested-e2e"}).encode("utf-8")
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
            "model": "runtime-nested-e2e",
            "target": "fahrräder",
            "threshold": 0.5,
            "selectionPolicy": "runtime-nested-e2e",
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class FrameHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path.startswith("/decoy/"):
            body = b"<!doctype html><html><body><main><p>Hilfsframe</p><button>Weiter</button></main></body></html>"
        elif path == "/puzzle":
            # Deliberately NOT nine direct children. The runtime must recover the
            # nine clickable buttons from nested image descendants/ancestor structure.
            pixel = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='96' height='96'%3E%3Crect width='96' height='96' fill='%23999'/%3E%3C/svg%3E"
            rows = "".join(
                "<div class='row'>" + "".join(
                    f"<button class='choice' type='button' data-index='{r * 3 + c}'><span class='inner'><img alt='tile-{r}-{c}' src=\"{pixel}\"></span></button>"
                    for c in range(3)
                ) + "</div>"
                for r in range(3)
            )
            body = f"""<!doctype html><html><head><meta charset='utf-8'><style>
html,body{{margin:0;padding:0;background:#fff;font-family:Arial,sans-serif}}
main{{padding:28px;width:420px}} p{{font-size:20px;margin:0 0 18px}}
#grid{{display:flex;flex-direction:column;gap:10px;width:max-content}}
.row{{display:flex;gap:10px}}
.choice{{width:112px;height:112px;padding:7px;border:1px solid #888;border-radius:8px;background:#eee;cursor:pointer}}
.inner,.choice img{{display:block;width:96px;height:96px}}
.choice[data-selected='1']{{outline:5px solid #111;outline-offset:-5px}}
#submit{{margin-top:18px;width:150px;height:48px;font-size:17px}}
strong{{display:block;margin-top:18px;font-size:20px}}
</style></head><body><main>
<p>Wähle alle Bilder mit Fahrrädern aus</p><div id='grid'>{rows}</div>
<button id='submit' type='submit'>Bestätigen</button>
</main><script>
(() => {{
 const expected=[1,4,7], selected=new Set(); let trustedClicks=0;
 const tiles=Array.from(document.querySelectorAll('.choice'));
 tiles.forEach((tile,index)=>tile.addEventListener('click',event=>{{
   if(!event.isTrusted)return; trustedClicks+=1;
   if(selected.has(index))selected.delete(index);else selected.add(index);
   tile.dataset.selected=selected.has(index)?'1':'0';
 }}));
 document.querySelector('#submit').addEventListener('click',event=>{{
   if(!event.isTrusted)return; trustedClicks+=1;
   const actual=Array.from(selected).sort((a,b)=>a-b);
   if(JSON.stringify(actual)!==JSON.stringify(expected))return;
   const success=document.createElement('strong'); success.textContent='Erfolgreich verifiziert';
   document.querySelector('main').appendChild(success);
   window.parent.postMessage({{type:'ARES_RUNTIME_NESTED_E2E_SOLVED',selected:actual,trustedClicks}},'*');
 }});
}})();
</script></body></html>""".encode("utf-8")
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class ParentHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/":
            self.send_error(404)
            return
        frame_base = str(getattr(self.server, "frame_base"))
        body = f"""<!doctype html><html><head><meta charset='utf-8'><title>ARES nested OOPIF runtime proof</title></head>
<body><main>Produktseite bereit</main>
<script>window.addEventListener('message',event=>{{const d=event.data||{{}};if(d.type==='ARES_RUNTIME_NESTED_E2E_SOLVED'){{window.__aresRuntimeNestedE2E=d;location.hash='solved';}}}});</script>
<iframe src='{frame_base}/decoy/1' style='width:180px;height:90px'></iframe>
<iframe src='{frame_base}/decoy/2' style='width:180px;height:90px'></iframe>
<iframe src='{frame_base}/decoy/3' style='width:180px;height:90px'></iframe>
<iframe src='{frame_base}/puzzle' style='display:block;width:650px;height:650px;border:1px solid #ccc'></iframe>
</body></html>""".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def stdout_reader(process: subprocess.Popen[str], target: queue.Queue[Dict[str, Any]]) -> None:
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


def stderr_reader(process: subprocess.Popen[str], target: Deque[str]) -> None:
    assert process.stderr is not None
    for line in process.stderr:
        target.append(line.rstrip())


def send_command(process: subprocess.Popen[str], payload: Dict[str, Any]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    process.stdin.flush()


def wait_message(messages: queue.Queue[Dict[str, Any]], request_id: str, expected_type: str, *, timeout: float, process: subprocess.Popen[str], stderr_lines: Deque[str]) -> Dict[str, Any]:
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


def page_state(process: subprocess.Popen[str], messages: queue.Queue[Dict[str, Any]], stderr_lines: Deque[str]) -> Dict[str, Any]:
    request_id = uuid.uuid4().hex
    send_command(process, {"type": "rpc", "requestId": request_id, "action": "page-state"})
    message = wait_message(messages, request_id, "rpc-result", timeout=10.0, process=process, stderr_lines=stderr_lines)
    return message.get("result") if isinstance(message.get("result"), dict) else {}


def read_trace(path: Path) -> list[Dict[str, Any]]:
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
        print("RUNTIME_NESTED_OOPIF_GRID_E2E_SKIPPED platform=" + sys.platform)
        return 0

    VisionHandler.calls = 0
    VisionHandler.last_payload = {}
    vision = ThreadingHTTPServer(("127.0.0.1", 0), VisionHandler)
    frames = ThreadingHTTPServer(("127.0.0.1", 0), FrameHandler)
    parent = ThreadingHTTPServer(("127.0.0.1", 0), ParentHandler)
    setattr(parent, "frame_base", f"http://localhost:{frames.server_port}")
    for server in (vision, frames, parent):
        threading.Thread(target=server.serve_forever, daemon=True).start()

    parent_url = f"http://127.0.0.1:{parent.server_port}/"
    vision_url = f"http://127.0.0.1:{vision.server_port}"
    try:
        with tempfile.TemporaryDirectory(prefix="ares-runtime-nested-oopif-e2e-", ignore_cleanup_errors=True) as profile_dir:
            env = {**os.environ}
            runtime_dir = str(WORKER.parent)
            env["PYTHONPATH"] = runtime_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            env["ARES_VISION_SERVICE_URL"] = vision_url
            env["ARES_VISION_SERVICE_TOKEN"] = VisionHandler.token
            env["ARES_VISION_OFFLINE"] = "1"
            process = subprocess.Popen([sys.executable, "-u", str(WORKER)], cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1, env=env)
            messages: queue.Queue[Dict[str, Any]] = queue.Queue()
            stderr_lines: Deque[str] = deque(maxlen=500)
            threading.Thread(target=stdout_reader, args=(process, messages), daemon=True).start()
            threading.Thread(target=stderr_reader, args=(process, stderr_lines), daemon=True).start()

            start_id = uuid.uuid4().hex
            send_command(process, {"type":"start","requestId":start_id,"taskId":TASK_ID,"profileDir":profile_dir,"headless":False,"browserArgs":["--site-per-process","--window-size=1280,1000","--force-device-scale-factor=1"]})
            ready = wait_message(messages, start_id, "ready", timeout=40.0, process=process, stderr_lines=stderr_lines)
            assert ready.get("ok") is True, ready

            navigate_id = uuid.uuid4().hex
            send_command(process, {"type":"navigate","requestId":navigate_id,"url":parent_url,"timeoutMs":15_000})
            wait_message(messages, navigate_id, "navigated", timeout=25.0, process=process, stderr_lines=stderr_lines)

            solved = False
            last_state: Dict[str, Any] = {}
            deadline = time.monotonic() + 35.0
            while time.monotonic() < deadline:
                last_state = page_state(process, messages, stderr_lines)
                frame_list = last_state.get("frames") if isinstance(last_state.get("frames"), list) else []
                if str(last_state.get("url") or "").endswith("#solved"):
                    solved = True
                    break
                assert len(frame_list) >= 4, f"expected >=4 discovered iframe paths: {last_state}"
                time.sleep(0.35)

            trace_path = Path(profile_dir) / ".ares-visual-trace.jsonl"
            trace = read_trace(trace_path)
            assert solved, {"lastState":last_state,"visionCalls":VisionHandler.calls,"traceTail":trace[-30:],"stderr":list(stderr_lines)[-80:]}

            screenshots = [row for row in trace if row.get("phase") == "grid-screenshot-captured"]
            assert screenshots, trace[-40:]
            state = screenshots[-1].get("state") if isinstance(screenshots[-1].get("state"), dict) else {}
            assert int(state.get("tileCount") or 0) == 9, state
            assert str(state.get("scope") or "").startswith("oopif:"), state
            sources = state.get("sources") if isinstance(state.get("sources"), list) else []
            marks = state.get("marks") if isinstance(state.get("marks"), list) else []
            assert len(sources) == 9, state
            assert len(marks) == 9, state
            # The fixture has three row wrappers, so no DOM node owns nine grid
            # buttons as direct children. Nine BUTTON marks therefore prove that
            # the nested visual-descendant/ancestor recovery path reached the
            # clickable tiles even if an intermediate state strips `origin`.
            assert all(
                isinstance(mark, dict)
                and str(mark.get("structuralKey") or "").startswith("grid-tile|BUTTON|")
                and isinstance(mark.get("visualBounds"), dict)
                for mark in marks
            ), marks

            diagnostics = [row for row in trace if row.get("phase") == "grid-diagnostic"]
            assert any(row.get("stage") == "VISION_CALLED" and int(row.get("sourceCount") or 0) == 9 for row in diagnostics), diagnostics[-30:]
            assert any(row.get("stage") == "MARK_IDS" and bool(row.get("completeMapping")) for row in diagnostics), diagnostics[-30:]
            assert any(row.get("stage") == "VERIFY" and bool(row.get("verified")) for row in diagnostics), diagnostics[-30:]

            clicks = [row for row in trace if row.get("phase") == "cursor-click"]
            assert len(clicks) >= 4, clicks
            assert all(bool(row.get("seeded")) for row in clicks), clicks
            assert all(str(row.get("provider") or "").endswith(":cdp") for row in clicks), clicks
            assert VisionHandler.calls >= 1, VisionHandler.last_payload

            close_id = uuid.uuid4().hex
            send_command(process, {"type":"close","requestId":close_id})
            wait_message(messages, close_id, "closed", timeout=15.0, process=process, stderr_lines=stderr_lines)
            process.wait(timeout=15.0)
            print(f"RUNTIME_NESTED_OOPIF_GRID_E2E_PASS pid={ready.get('pid')} frames>=4 nestedButtons=9 visionCalls={VisionHandler.calls} cursorClicks={len(clicks)}")
    finally:
        for server in (parent, frames, vision):
            server.shutdown()
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
