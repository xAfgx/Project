from __future__ import annotations

import secrets
import shutil
import tempfile
import time
from pathlib import Path

from manual_profile_probe import (
    COOKIE_A,
    WorkerClient,
    _assert_cookie,
    _assert_session_cookie,
    _cookie,
    _next_request,
    _start_server,
)

RESTORE_PATH = "/restore-target"


def _wait_for_restored_session(
    active: WorkerClient,
    *,
    target_url: str,
    cookie_name: str,
    cookie_value: str,
    request_id_prefix: str,
    timeout: float = 12,
) -> dict:
    deadline = time.time() + timeout
    last_inspected: dict = {}
    attempt = 0
    while time.time() < deadline:
        request_id = f"{request_id_prefix}-{attempt}"
        active.send({"type": "inspect-session", "requestId": request_id})
        inspected = active.wait("session-inspection", request_id, 15)
        last_inspected = inspected
        if inspected.get("url") == target_url:
            try:
                _assert_session_cookie(inspected.get("cookies") or [], cookie_name, cookie_value)
                return inspected
            except AssertionError:
                pass
        attempt += 1
        time.sleep(0.25)

    raise AssertionError(
        "Reopened SeleniumBase profile did not restore the active page and cookie "
        f"within the settle window: {last_inspected}"
    )


def _wait_for_cookie_request(
    recorder,
    *,
    expected_path: str,
    cookie_name: str,
    cookie_value: str,
    timeout: float = 12,
) -> dict:
    deadline = time.time() + timeout
    seen_headers: list[str] = []
    while time.time() < deadline:
        remaining = max(0.1, deadline - time.time())
        try:
            request = _next_request(recorder, expected_path, timeout=min(2.0, remaining))
        except TimeoutError:
            continue
        header = request.get("cookie", "")
        seen_headers.append(header)
        try:
            _assert_cookie(header, cookie_name, cookie_value)
            return request
        except AssertionError:
            continue

    raise AssertionError(
        f"Persistent cookie never reached {expected_path!r}; observed Cookie headers: {seen_headers!r}"
    )


def _run(profile_dir: Path) -> None:
    server, recorder, base_url = _start_server()
    token = secrets.token_hex(8)
    active: WorkerClient | None = None
    try:
        target_url = f"{base_url}{RESTORE_PATH}"
        active = WorkerClient(profile_dir, target_url, [_cookie(COOKIE_A, token)])
        initial = _next_request(recorder, RESTORE_PATH)
        _assert_cookie(initial["cookie"], COOKIE_A, token)
        active.close()
        active = None

        # Reopen twice without any explicit startUrl. Both launches must restore
        # the previous SeleniumBase-owned page as the active page and keep the
        # persistent cookie. Session inspection waits for the persisted cookie
        # store to settle before an explicit same-page request verifies the
        # outbound Cookie header. This avoids treating a transient Windows
        # startup request as evidence that persistence was lost.
        for index in range(2):
            active = WorkerClient(profile_dir, "")
            _wait_for_restored_session(
                active,
                target_url=target_url,
                cookie_name=COOKIE_A,
                cookie_value=token,
                request_id_prefix=f"inspect-{index}",
            )

            request_id = f"navigate-{index}"
            active.send({"type": "navigate", "requestId": request_id, "url": target_url})
            active.wait("navigated", request_id, 15)
            _wait_for_cookie_request(
                recorder,
                expected_path=RESTORE_PATH,
                cookie_name=COOKIE_A,
                cookie_value=token,
            )

            active.close()
            active = None
    finally:
        if active:
            try:
                active.close()
            except Exception:
                active.process.kill()
        server.shutdown()
        server.server_close()


def main() -> int:
    temporary = tempfile.mkdtemp(prefix="ares-sb-reopen-")
    profile_dir = Path(temporary) / "profile" / ".ares-seleniumbase-cdp"
    try:
        _run(profile_dir)
        print("SeleniumBase clean reopen restored the active page and persistent cookies twice.")
        return 0
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
