from __future__ import annotations

import queue
import shutil
import socketserver
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import parse_qs, urlparse

from seleniumbase_adapter import SeleniumBaseCdpAdapter


class Recorder:
    def __init__(self) -> None:
        self.hits: queue.Queue[Dict[str, str]] = queue.Queue()


class FixtureHandler(BaseHTTPRequestHandler):
    recorder: Recorder
    page: bytes

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/completed", "/failed"}:
            params = parse_qs(parsed.query)
            self.recorder.hits.put({
                "type": parsed.path.lstrip("/"),
                "fraction": (params.get("fraction") or [""])[0],
            })
            self._send(b"ok", "text/plain; charset=utf-8")
            return
        if parsed.path == "/":
            self._send(self.page, "text/html; charset=utf-8")
            return
        self.send_response(404)
        self.end_headers()

    def _send(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def _fixture() -> bytes:
    return b"""<!doctype html>
<html>
<head>
<meta charset='utf-8'>
<style>
html,body{margin:0;padding:0;background:#f3f3f3;color:#161616;font-family:Arial,sans-serif}
main{padding:24px}
#slider-fixture{width:430px;background:#fff;border:1px solid #ccc;padding:18px}
#instruction{font-size:18px;margin:0 0 12px}
#track{position:relative;width:320px;height:44px;background:#e9e9e9;border:1px solid #bdbdbd;border-radius:3px;user-select:none}
#handle{position:absolute;left:0;top:0;width:44px;height:44px;background:#d9d9d9;border-right:1px solid #aaa;cursor:grab;box-sizing:border-box}
#status{margin-top:9px;height:20px;font-size:13px;color:#555}
</style>
</head>
<body><main>
<div id='slider-fixture'>
  <div id='instruction'>Ziehe den Regler nach rechts bis zum Ende.</div>
  <div id='track' class='slider-track'>
    <div id='handle' class='slider-handle' role='slider' aria-valuemin='0' aria-valuemax='100' aria-valuenow='0'></div>
  </div>
  <div id='status'>Noch nicht abgeschlossen</div>
</div>
<script>
(() => {
  const track = document.getElementById('track');
  const handle = document.getElementById('handle');
  const status = document.getElementById('status');
  let dragging = false;
  let fraction = 0;
  const update = clientX => {
    const r = track.getBoundingClientRect();
    fraction = Math.max(0, Math.min(1, (clientX-r.left)/Math.max(1,r.width)));
    handle.style.left = Math.max(0, Math.min(r.width-handle.offsetWidth, fraction*r.width-handle.offsetWidth/2)) + 'px';
    handle.setAttribute('aria-valuenow', String(fraction*100));
  };
  handle.addEventListener('mousedown', event => { dragging=true; update(event.clientX); event.preventDefault(); });
  document.addEventListener('mousemove', event => { if(dragging) update(event.clientX); });
  document.addEventListener('mouseup', event => {
    if(!dragging) return;
    update(event.clientX); dragging=false;
    if(fraction >= 0.94) {
      status.textContent='Abgeschlossen';
      const done=document.createElement('div'); done.id='done'; done.hidden=true;
      document.getElementById('slider-fixture').appendChild(done);
      fetch('/completed?fraction='+encodeURIComponent(fraction.toFixed(6)));
    } else {
      status.textContent='Nicht abgeschlossen';
      fetch('/failed?fraction='+encodeURIComponent(fraction.toFixed(6)));
    }
  });
})();
</script>
</main></body>
</html>"""


def _start_server(page: bytes) -> Tuple[socketserver.TCPServer, Recorder, str]:
    recorder = Recorder()
    class Handler(FixtureHandler):
        pass
    Handler.recorder = recorder
    Handler.page = page
    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    return server, recorder, f"http://{host}:{port}/"


def _collect(recorder: Recorder, timeout: float = 3.0) -> List[Dict[str, str]]:
    hits: List[Dict[str, str]] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            hits.append(recorder.hits.get(timeout=0.2))
        except queue.Empty:
            if hits:
                break
    return hits


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="ares-slider-e2e-"))
    server, recorder, url = _start_server(_fixture())
    adapter: SeleniumBaseCdpAdapter | None = None
    try:
        adapter = SeleniumBaseCdpAdapter(
            profile_dir=root / "profile",
            headless=True,
            site_adapter_overrides={
                "sliderRoot": "#slider-fixture",
                "sliderHandle": "#handle",
                "sliderTrack": "#track",
                "sliderInstruction": "#instruction",
                "sliderComplete": "#done",
            },
        )
        adapter.goto(url)
        deadline = time.monotonic() + 8.0
        result: Dict[str, Any] = {}
        while time.monotonic() < deadline:
            state = adapter.auto_interaction_state()
            candidate = state.get("lastResult")
            if isinstance(candidate, dict):
                result = candidate
                if candidate.get("kind") == "slider" and candidate.get("acted"):
                    break
            adapter.poll_runtime()
            time.sleep(0.15)

        hits = _collect(recorder)
        completed = [hit for hit in hits if hit.get("type") == "completed"]
        failed = [hit for hit in hits if hit.get("type") == "failed"]
        target = result.get("target") if isinstance(result.get("target"), dict) else {}
        action = result.get("result") if isinstance(result.get("result"), dict) else {}
        actual_fraction = float(completed[-1].get("fraction") or -1.0) if completed else -1.0

        print(
            "SLIDER_E2E_DIAGNOSTIC "
            f"source={target.get('source')!r} groundedFraction={target.get('targetFraction')!r} "
            f"actionMode={action.get('mode')!r} "
            f"endHoldBacktrack={action.get('endHoldBacktrack')!r} "
            f"correctionDrags={action.get('correctionDrags')!r} "
            f"verified={action.get('verified')!r} serverFraction={actual_fraction:.6f} "
            f"failedHits={len(failed)}"
        )

        assert result.get("kind") == "slider", result
        assert float(target.get("targetFraction") or 0.0) >= 0.94, target
        assert result.get("acted") is True, result
        assert str(action.get("mode") or "").startswith("path:"), action
        assert str(action.get("mode") or "").endswith(":cdp"), action
        assert action.get("endHoldBacktrack") is True, action
        assert int(action.get("correctionDrags") or 0) <= 3, action
        assert action.get("verified") is True or result.get("verified") is True, result
        assert len(completed) == 1, hits
        assert len(failed) <= 3, hits
        assert actual_fraction >= 0.94, actual_fraction
        print("PASS: local end-slider completed through feedback-controlled ARES CDP drag path.")
        return 0
    finally:
        if adapter is not None:
            try:
                adapter.quit()
            except Exception:
                pass
        server.shutdown()
        server.server_close()
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
