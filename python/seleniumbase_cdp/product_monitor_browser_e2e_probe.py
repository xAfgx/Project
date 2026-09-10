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

PREFIX = "ARES_MONITOR_BROWSER\t"
ROOT = Path(__file__).resolve().parents[2]
WORKER = Path(__file__).with_name("product_monitor_browser.py")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/product"):
            body = """<!doctype html><html><head><title>Dynamic Product</title></head>
<body><main id='app'>Loading…</main><script>
setTimeout(() => {
  document.querySelector('#app').innerHTML = `
    <article itemscope itemtype="https://schema.org/Product">
      <h1 itemprop="name">ARES Stellar Booster</h1>
      <link itemprop="availability" href="https://schema.org/InStock">
      <button>Add to cart</button>
    </article>`;
}, 650);
</script></body></html>"""
        else:
            body = """<!doctype html><html><head><title>Dynamic Catalog</title></head>
<body><main id='app'>Catalog loading…</main><script>
setTimeout(() => {
  document.querySelector('#app').innerHTML = `
    <a href="/product"><strong>ARES Stellar Booster</strong></a>`;
}, 550);
</script></body></html>"""
        raw = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _reader(process: subprocess.Popen[str], target: queue.Queue[Dict[str, Any]]) -> None:
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
    """Continuously drain stderr so a verbose browser startup cannot block the worker."""
    assert process.stderr is not None
    for line in process.stderr:
        target.append(line.rstrip())


def _stderr_summary(lines: Deque[str], limit: int = 3000) -> str:
    text = "\n".join(lines)
    return text[-limit:] if text else ""


def _wait(
    target: queue.Queue[Dict[str, Any]],
    request_id: str,
    expected_type: str,
    timeout: float = 30.0,
    *,
    process: subprocess.Popen[str] | None = None,
    stderr_lines: Deque[str] | None = None,
) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout
    deferred: list[Dict[str, Any]] = []
    try:
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            try:
                message = target.get(timeout=remaining)
            except queue.Empty:
                break
            if message.get("type") == "error" and message.get("requestId") in {None, "", request_id}:
                raise AssertionError(f"Monitor worker error: {message}")
            if message.get("requestId") == request_id and message.get("type") == expected_type:
                return message
            deferred.append(message)
    finally:
        for message in deferred:
            target.put(message)

    details: list[str] = []
    if process is not None:
        details.append(f"returncode={process.poll()}")
    if stderr_lines is not None:
        stderr = _stderr_summary(stderr_lines)
        if stderr:
            details.append(f"stderr_tail={stderr!r}")
    suffix = f" ({'; '.join(details)})" if details else ""
    raise AssertionError(f"Timed out waiting for {expected_type} request={request_id}{suffix}")


def _send(process: subprocess.Popen[str], payload: Dict[str, Any]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    process.stdin.flush()


def _graceful_close(process: subprocess.Popen[str], messages: queue.Queue[Dict[str, Any]]) -> None:
    if process.poll() is not None:
        return
    close_id = uuid.uuid4().hex
    try:
        _send(process, {"type": "close", "requestId": close_id})
        _wait(messages, close_id, "closed", timeout=12.0)
        process.wait(timeout=12.0)
        # Windows can report the Python worker exited slightly before Chrome
        # releases its profile database handles.
        time.sleep(1.5 if sys.platform.startswith("win") else 0.2)
    except Exception:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5.0)
        time.sleep(1.5 if sys.platform.startswith("win") else 0.2)


def _run_mode(base_url: str, *, headless: bool) -> None:
    mode = "headless" if headless else "visible"
    with tempfile.TemporaryDirectory(prefix=f"ares-monitor-{mode}-e2e-", ignore_cleanup_errors=True) as profile_dir:
        env = {**os.environ}
        python_path = str(WORKER.parent)
        env["PYTHONPATH"] = python_path + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        process = subprocess.Popen(
            [sys.executable, str(WORKER)],
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        messages: queue.Queue[Dict[str, Any]] = queue.Queue()
        stderr_lines: Deque[str] = deque(maxlen=200)
        threading.Thread(target=_reader, args=(process, messages), daemon=True).start()
        threading.Thread(target=_stderr_reader, args=(process, stderr_lines), daemon=True).start()

        try:
            start_id = uuid.uuid4().hex
            _send(process, {
                "type": "start",
                "requestId": start_id,
                "taskId": f"monitor-e2e-{mode}",
                "profileDir": profile_dir,
                "startUrl": base_url,
                "headless": headless,
            })
            ready = _wait(
                messages,
                start_id,
                "ready",
                timeout=45.0,
                process=process,
                stderr_lines=stderr_lines,
            )
            assert ready.get("challengeRuntimeEnabled") is True
            assert ready.get("visualRuntimeEnabled") is True
            assert ready.get("semanticRuntimeEnabled") is True
            assert ready.get("pid"), ready

            catalog_id = uuid.uuid4().hex
            _send(process, {
                "type": "render",
                "requestId": catalog_id,
                "url": base_url,
                "stableMs": 700,
                "timeoutMs": 8_000,
            })
            catalog = _wait(messages, catalog_id, "rendered-document", timeout=20.0)
            assert "ARES Stellar Booster" in str(catalog.get("html") or ""), catalog
            assert "/product" in str(catalog.get("html") or ""), catalog

            product_id = uuid.uuid4().hex
            _send(process, {
                "type": "render",
                "requestId": product_id,
                "url": base_url + "product",
                "stableMs": 700,
                "timeoutMs": 8_000,
            })
            product = _wait(messages, product_id, "rendered-document", timeout=20.0)
            html = str(product.get("html") or "")
            assert "ARES Stellar Booster" in html, product
            assert "schema.org/InStock" in html, product
            assert "Add to cart" in html, product
            assert product.get("readyState") in {"interactive", "complete"}, product
            print(f"MONITOR_BROWSER_MODE_PASS mode={mode} pid={ready.get('pid')}")
        finally:
            _graceful_close(process, messages)

        if process.returncode not in {0, None}:
            stderr = _stderr_summary(stderr_lines)
            raise AssertionError(f"Monitor worker exited with {process.returncode}: {stderr}")


def _requested_modes() -> list[bool]:
    requested = os.environ.get("ARES_MONITOR_E2E_MODE", "both").strip().lower()
    if requested in {"", "both"}:
        return [False, True]
    if requested == "visible":
        return [False]
    if requested == "headless":
        return [True]
    raise ValueError("ARES_MONITOR_E2E_MODE must be one of: visible, headless, both")


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}/"

    modes = _requested_modes()
    try:
        for headless in modes:
            _run_mode(base_url, headless=headless)
    finally:
        server.shutdown()
        server.server_close()

    labels = ",".join("headless" if mode else "visible" for mode in modes)
    print(
        "PASS: real SeleniumBase Chromium rendered the JavaScript storefront "
        f"in requested mode(s)={labels}, with the existing challenge/visual/semantic runtime "
        "enabled and no profile requirement."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
