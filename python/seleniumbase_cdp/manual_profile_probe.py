from __future__ import annotations

import json
import os
import queue
import secrets
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List

RESULT_PREFIX = "ARES_SB_MANUAL\t"
COOKIE_A = "ares_sb_manual_a"
COOKIE_B = "ares_sb_manual_b"


class RequestRecorder:
    def __init__(self) -> None:
        self.requests: queue.Queue[Dict[str, str]] = queue.Queue()


class Handler(BaseHTTPRequestHandler):
    recorder: RequestRecorder

    def do_GET(self) -> None:  # noqa: N802
        self.recorder.requests.put({"path": self.path, "cookie": self.headers.get("Cookie", "")})
        body = b"<!doctype html><html><head><title>ARES SeleniumBase Manual Probe</title></head><body>probe</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def _worker_path() -> Path:
    return Path(__file__).resolve().with_name("manual_profile_browser.py")


def _start_server() -> tuple[socketserver.TCPServer, RequestRecorder, str]:
    recorder = RequestRecorder()

    class ProbeHandler(Handler):
        pass

    ProbeHandler.recorder = recorder
    server = socketserver.TCPServer(("127.0.0.1", 0), ProbeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, recorder, f"http://{host}:{port}"


def _cookie(name: str, value: str) -> Dict[str, Any]:
    return {
        "name": name,
        "value": value,
        "domain": "127.0.0.1",
        "path": "/",
        "expires": time.time() + 86400,
        "httpOnly": True,
        "secure": False,
        "sameSite": "Lax",
    }


class WorkerClient:
    def __init__(self, profile_dir: Path, start_url: str, cookies: List[Dict[str, Any]] | None = None) -> None:
        self.process = subprocess.Popen(
            [sys.executable, str(_worker_path())],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=dict(os.environ),
        )
        if self.process.stdin is None or self.process.stdout is None:
            self._terminate_process()
            raise RuntimeError("Could not open SeleniumBase worker stdio")
        self._stdout = self.process.stdout
        self._messages: queue.Queue[Dict[str, Any]] = queue.Queue()
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self.send(
            {
                "type": "start",
                "requestId": "start",
                "profileId": "probe-profile",
                "profileDir": str(profile_dir),
                "startUrl": start_url,
                "cookies": cookies or [],
            }
        )
        try:
            ready = self.wait("ready", "start", 60)
        except Exception:
            self._terminate_process()
            raise
        if not ready.get("profileDir"):
            self._terminate_process()
            raise AssertionError("SeleniumBase worker did not report profileDir")

    def _read_stdout(self) -> None:
        for line in self._stdout:
            if not line.startswith(RESULT_PREFIX):
                continue
            try:
                payload = json.loads(line[len(RESULT_PREFIX):])
            except Exception:
                continue
            if isinstance(payload, dict):
                self._messages.put(payload)

    def _terminate_process(self) -> None:
        if self.process.poll() is not None:
            return
        try:
            self.process.kill()
        finally:
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass

    def send(self, payload: Dict[str, Any]) -> None:
        if self.process.stdin is None:
            raise RuntimeError("SeleniumBase worker stdin unavailable")
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()

    def wait(self, message_type: str, request_id: str, timeout: float = 15) -> Dict[str, Any]:
        deadline = time.time() + timeout
        deferred: List[Dict[str, Any]] = []
        try:
            while time.time() < deadline:
                remaining = max(0.05, deadline - time.time())
                try:
                    message = self._messages.get(timeout=min(0.5, remaining))
                except queue.Empty:
                    if self.process.poll() is not None:
                        stderr = self.process.stderr.read() if self.process.stderr else ""
                        raise RuntimeError(f"SeleniumBase worker exited early: {stderr[-2000:]}")
                    continue
                if message.get("type") == "error" and message.get("requestId") in {request_id, None, ""}:
                    raise RuntimeError(str(message.get("error") or "SeleniumBase worker error"))
                if message.get("type") == message_type and message.get("requestId") == request_id:
                    return message
                deferred.append(message)
        finally:
            for message in deferred:
                self._messages.put(message)
        raise TimeoutError(f"Timed out waiting for {message_type}/{request_id}")

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        self.send({"type": "close", "requestId": "close"})
        self.wait("closed", "close", 20)
        self.process.wait(timeout=15)
        # Windows can report the worker as exited slightly before Chrome has
        # fully released/flushed its profile files. Give the profile store a
        # short deterministic settle window before an immediate reopen.
        time.sleep(1.5 if sys.platform.startswith("win") else 0.2)


def _next_request(recorder: RequestRecorder, expected_path: str, timeout: float = 15) -> Dict[str, str]:
    deadline = time.time() + timeout
    seen: List[str] = []
    while time.time() < deadline:
        remaining = max(0.05, deadline - time.time())
        try:
            request = recorder.requests.get(timeout=min(0.5, remaining))
        except queue.Empty:
            continue
        seen.append(request.get("path", ""))
        if request.get("path") == expected_path:
            return request
    raise TimeoutError(f"Timed out waiting for request {expected_path!r}; saw {seen!r}")


def _assert_cookie(header: str, name: str, value: str) -> None:
    expected = f"{name}={value}"
    if expected not in header:
        raise AssertionError(f"Missing {expected!r} in request Cookie header: {header!r}")


def _assert_session_cookie(cookies: List[Dict[str, Any]], name: str, value: str) -> None:
    cookie = next((item for item in cookies if item.get("name") == name), None)
    if not cookie or cookie.get("value") != value:
        raise AssertionError(f"SeleniumBase session inspection did not see {name}: {cookies}")
    if cookie.get("httpOnly") is not True:
        raise AssertionError(f"SeleniumBase session inspection lost HttpOnly for {name}: {cookie}")


def _run(profile_dir: Path) -> None:
    server, recorder, base_url = _start_server()
    token_a = secrets.token_hex(8)
    token_b = secrets.token_hex(8)
    first: WorkerClient | None = None
    second: WorkerClient | None = None
    try:
        first = WorkerClient(profile_dir, f"{base_url}/initial", [_cookie(COOKIE_A, token_a)])
        first_request = _next_request(recorder, "/initial")
        _assert_cookie(first_request["cookie"], COOKIE_A, token_a)

        first.send({"type": "apply-cookies", "requestId": "apply", "cookies": [_cookie(COOKIE_B, token_b)]})
        applied = first.wait("cookies-applied", "apply")
        if int(applied.get("count") or 0) != 1:
            raise AssertionError(f"Expected one applied cookie, got: {applied}")

        first.send({"type": "navigate", "requestId": "navigate", "url": f"{base_url}/same-session"})
        first.wait("navigated", "navigate")
        same_session_request = _next_request(recorder, "/same-session")
        _assert_cookie(same_session_request["cookie"], COOKIE_A, token_a)
        _assert_cookie(same_session_request["cookie"], COOKIE_B, token_b)

        first.send({"type": "export-cookies", "requestId": "export"})
        exported = first.wait("cookies", "export")
        cookies = exported.get("cookies") or []
        cookie_a = next((item for item in cookies if item.get("name") == COOKIE_A), None)
        cookie_b = next((item for item in cookies if item.get("name") == COOKIE_B), None)
        if not cookie_a or not cookie_b:
            raise AssertionError(f"Expected both cookies in export: {cookies}")
        if cookie_a.get("httpOnly") is not True or cookie_b.get("httpOnly") is not True:
            raise AssertionError(f"HttpOnly attribute was not preserved: {cookies}")

        first.send({"type": "inspect-session", "requestId": "inspect"})
        inspected = first.wait("session-inspection", "inspect", 15)
        if inspected.get("url") != f"{base_url}/same-session":
            raise AssertionError(f"SeleniumBase inspected wrong page: {inspected}")
        if inspected.get("title") != "ARES SeleniumBase Manual Probe":
            raise AssertionError(f"SeleniumBase title mismatch: {inspected}")
        inspected_cookies = inspected.get("cookies") or []
        _assert_session_cookie(inspected_cookies, COOKIE_A, token_a)
        _assert_session_cookie(inspected_cookies, COOKIE_B, token_b)

        first.close()
        first = None

        second = WorkerClient(profile_dir, f"{base_url}/restart")
        restart_request = _next_request(recorder, "/restart")
        _assert_cookie(restart_request["cookie"], COOKIE_A, token_a)
        _assert_cookie(restart_request["cookie"], COOKIE_B, token_b)
        second.close()
        second = None
    finally:
        if first:
            try:
                first.close()
            except Exception:
                first._terminate_process()
        if second:
            try:
                second.close()
            except Exception:
                second._terminate_process()
        server.shutdown()
        server.server_close()


def main() -> int:
    temporary = tempfile.mkdtemp(prefix="ares-sb-manual-")
    profile_dir = Path(temporary) / "profile" / ".ares-seleniumbase-cdp"
    try:
        _run(profile_dir)
        print("SeleniumBase Pure CDP process/session/cookie probe passed natively.")
        return 0
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
