from __future__ import annotations

import argparse
import http.server
import json
import secrets
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Tuple
from urllib.parse import parse_qs, urlsplit

RESULT_PREFIX = "ARES_SB_RESULT\t"
WORKER_PATH = Path(__file__).with_name("worker.py")
COOKIE_NAME = "ares_sb_profile_probe"
WORKER_TIMEOUT_SECONDS = 90
KILL_TIMEOUT_SECONDS = 10


class QuietHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        body = b"<!doctype html><html><body>ARES SeleniumBase CDP persistence probe</body></html>"
        self.send_response(200)
        token = parse_qs(urlsplit(self.path).query).get("ares_seed_cookie", [""])[0]
        if token:
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}={token}; Path=/; Max-Age=31536000; SameSite=Lax",
            )
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def _start_server() -> Tuple[ReusableTCPServer, threading.Thread, str]:
    server = ReusableTCPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, f"http://{host}:{port}/"


def _parse_result(stdout: str) -> Dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        if line.startswith(RESULT_PREFIX):
            payload = json.loads(line[len(RESULT_PREFIX):])
            if not isinstance(payload, dict):
                raise RuntimeError("Worker result was not a JSON object")
            return payload
    raise RuntimeError(f"Worker emitted no ARES result marker. Output tail: {stdout[-1200:]}")


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return

    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=KILL_TIMEOUT_SECONDS,
                check=False,
            )
        except Exception:
            process.kill()
    else:
        process.kill()

    try:
        process.wait(timeout=KILL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()


def _run_worker(
    *,
    action: str,
    profile_dir: Path,
    url: str,
    headless: bool,
    token: str = "",
) -> Dict[str, Any]:
    command = {
        "action": action,
        "profile_dir": str(profile_dir),
        "url": url,
        "headless": headless,
        "token": token,
    }

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    print(f"[seleniumbase-persistence] starting {action}", flush=True)
    process = subprocess.Popen(
        [sys.executable, str(WORKER_PATH)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creationflags,
    )

    try:
        stdout, stderr = process.communicate(
            input=json.dumps(command) + "\n",
            timeout=WORKER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        print(
            f"[seleniumbase-persistence] {action} exceeded {WORKER_TIMEOUT_SECONDS}s; killing worker tree",
            flush=True,
        )
        _kill_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except Exception:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
        raise TimeoutError(
            f"SeleniumBase worker {action!r} exceeded {WORKER_TIMEOUT_SECONDS}s and was terminated"
        ) from exc

    result = _parse_result(stdout)
    if process.returncode != 0 or not result.get("ok"):
        raise RuntimeError(
            "SeleniumBase worker failed: "
            f"returncode={process.returncode}, result={result}, stderr={stderr[-1200:]}"
        )

    print(f"[seleniumbase-persistence] completed {action}", flush=True)
    return result


def _assert_persisted(result: Dict[str, Any], token: str) -> None:
    cookies = result.get("cookies")
    if not isinstance(cookies, list):
        raise AssertionError(f"Worker did not return a cookie list: {cookies!r}")
    match = next((item for item in cookies if item.get("name") == COOKIE_NAME), None)
    if not match or match.get("value") != token:
        raise AssertionError(f"Persistent cookie missing after restart: {cookies!r}")
    storage_value = result.get("storage_value")
    if storage_value != token:
        raise AssertionError(
            f"LocalStorage missing after restart: expected {token!r}, got {storage_value!r}"
        )


def _run_probe(profile_dir: Path, headed: bool) -> None:
    server, thread, url = _start_server()
    token = secrets.token_hex(16)
    try:
        first = _run_worker(
            action="seed-persistence",
            profile_dir=profile_dir,
            url=url,
            headless=not headed,
            token=token,
        )
        _assert_persisted(first, token)

        # This is intentionally a second Python process. The only shared browser
        # state is SeleniumBase's own user_data_dir on disk.
        second = _run_worker(
            action="read-persistence",
            profile_dir=profile_dir,
            url=url,
            headless=not headed,
        )
        _assert_persisted(second, token)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify SeleniumBase pure-CDP profile persistence across two isolated Python processes."
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        help="Optional dedicated SeleniumBase profile directory. A temporary directory is used by default.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run Chrome headed instead of headless.",
    )
    args = parser.parse_args()

    if args.profile_dir:
        profile_dir = args.profile_dir.expanduser().resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)
        _run_probe(profile_dir, args.headed)
        print(f"PASS: SeleniumBase CDP persistence verified in {profile_dir}")
        return 0

    with tempfile.TemporaryDirectory(prefix="ares-seleniumbase-cdp-") as temp_root:
        profile_dir = Path(temp_root) / "profile"
        _run_probe(profile_dir, args.headed)
        print("PASS: SeleniumBase CDP persistence verified across two isolated Python processes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
