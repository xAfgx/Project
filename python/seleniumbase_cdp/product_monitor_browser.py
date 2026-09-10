from __future__ import annotations

import json
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict

from control_aware_seleniumbase_adapter import ControlAwareSeleniumBaseCdpAdapter as SeleniumBaseCdpAdapter
from runtime_poll_scheduler import SingleOwnerRuntimeScheduler

RESULT_PREFIX = "ARES_MONITOR_BROWSER\t"
MAX_HTML_CHARS = 2_000_000
MAX_TEXT_CHARS = 250_000


def _emit(payload: Dict[str, Any]) -> None:
    print(f"{RESULT_PREFIX}{json.dumps(payload, ensure_ascii=False)}", flush=True)


def _startup_stage(stage: str, *, request_id: str, task_id: str, started: float) -> None:
    elapsed_ms = int((time.monotonic() - started) * 1000.0)
    print(
        f"ARES_MONITOR_STARTUP stage={stage} request={request_id} task={task_id} elapsedMs={elapsed_ms}",
        file=sys.stderr,
        flush=True,
    )


def _read_first_command() -> Dict[str, Any]:
    line = sys.stdin.readline()
    if not line:
        raise RuntimeError("No monitor browser start command received on stdin")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise TypeError("Monitor browser command must be a JSON object")
    return value


def _command_reader(target: queue.Queue[Dict[str, Any]]) -> None:
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            value = json.loads(raw)
            if isinstance(value, dict):
                target.put(value)
        except Exception as exc:
            _emit({"type": "error", "error": f"Invalid monitor browser command: {exc}"})


def _snapshot(adapter: SeleniumBaseCdpAdapter) -> Dict[str, Any]:
    value = adapter.passive_observation(
        lambda: adapter.execute_script(
            """
            (() => {
              const root = document.documentElement;
              const body = document.body;
              return {
                url: String(window.location.href || ''),
                title: String(document.title || ''),
                readyState: String(document.readyState || ''),
                html: root ? String(root.outerHTML || '') : '',
                text: body ? String(body.innerText || body.textContent || '') : ''
              };
            })()
            """
        )
    )
    if not isinstance(value, dict):
        value = {}
    return {
        "url": str(value.get("url") or ""),
        "title": str(value.get("title") or "")[:500],
        "readyState": str(value.get("readyState") or ""),
        "html": str(value.get("html") or "")[:MAX_HTML_CHARS],
        "text": str(value.get("text") or "")[:MAX_TEXT_CHARS],
    }


def _rendered_document(
    adapter: SeleniumBaseCdpAdapter,
    *,
    url: str,
    stable_ms: int,
    timeout_ms: int,
) -> Dict[str, Any]:
    if url:
        current = str(
            adapter.passive_observation(
                lambda: adapter.execute_script("String(window.location.href || '')")
            )
            or ""
        )
        if current != url:
            adapter.goto(url)

    stable_for = 0.0
    last_fingerprint: tuple[Any, ...] | None = None
    last_snapshot: Dict[str, Any] = {}
    started = time.monotonic()
    last_poll = 0.0

    while (time.monotonic() - started) * 1000.0 < timeout_ms:
        now = time.monotonic()
        if now - last_poll >= 0.4:
            adapter.poll_runtime()
            last_poll = now

        snapshot = _snapshot(adapter)
        last_snapshot = snapshot
        fingerprint = (
            snapshot.get("url"),
            snapshot.get("title"),
            snapshot.get("readyState"),
            len(str(snapshot.get("html") or "")),
            len(str(snapshot.get("text") or "")),
        )
        if fingerprint == last_fingerprint and snapshot.get("readyState") in {"interactive", "complete"}:
            stable_for += 100.0
        else:
            stable_for = 0.0
            last_fingerprint = fingerprint

        if stable_for >= stable_ms:
            return {**snapshot, "stable": True}
        time.sleep(0.1)

    return {**last_snapshot, "stable": False}


def _start(command: Dict[str, Any]) -> int:
    if str(command.get("type") or "") != "start":
        raise ValueError("First monitor browser command must be type='start'")

    request_id = str(command.get("requestId") or "")
    task_id = str(command.get("taskId") or "").strip()
    profile_dir = Path(str(command.get("profileDir") or "")).expanduser().resolve()
    if not task_id:
        raise ValueError("taskId is required")

    startup_started = time.monotonic()
    _startup_stage("adapter-create-start", request_id=request_id, task_id=task_id, started=startup_started)
    adapter = SeleniumBaseCdpAdapter(
        profile_dir=profile_dir,
        headless=bool(command.get("headless")),
        proxy=str(command.get("proxy") or "").strip() or None,
        user_agent=str(command.get("userAgent") or "").strip() or None,
    )
    _startup_stage("adapter-created", request_id=request_id, task_id=task_id, started=startup_started)
    closed = False
    try:
        start_url = str(command.get("startUrl") or "").strip()
        if start_url:
            _startup_stage("goto-start", request_id=request_id, task_id=task_id, started=startup_started)
            adapter.goto(start_url)
            _startup_stage("goto-complete", request_id=request_id, task_id=task_id, started=startup_started)

        _emit({
            "type": "ready",
            "requestId": request_id,
            "taskId": task_id,
            "pid": adapter.chrome_pid,
            "runtime": adapter.runtime_metadata(),
            "challengeRuntimeEnabled": True,
            "visualRuntimeEnabled": True,
            "semanticRuntimeEnabled": True,
        })

        commands: queue.Queue[Dict[str, Any]] = queue.Queue()
        threading.Thread(target=_command_reader, args=(commands,), daemon=True).start()
        scheduler = SingleOwnerRuntimeScheduler(adapter)

        while True:
            if not adapter.is_running():
                _emit({"type": "browser-closed", "taskId": task_id})
                break

            scheduler.poll_if_due()
            try:
                next_command = commands.get(timeout=scheduler.queue_timeout(0.35))
            except queue.Empty:
                scheduler.poll_if_due()
                continue

            adapter.note_control_activity()
            command_type = str(next_command.get("type") or "")
            next_request_id = str(next_command.get("requestId") or "")
            try:
                if command_type == "close":
                    adapter.quit()
                    closed = True
                    _emit({"type": "closed", "requestId": next_request_id, "taskId": task_id})
                    break
                if command_type == "status":
                    _emit({
                        "type": "status",
                        "requestId": next_request_id,
                        "taskId": task_id,
                        "open": adapter.is_running(),
                        "runtime": adapter.runtime_metadata(),
                    })
                    continue
                if command_type == "render":
                    target_url = str(next_command.get("url") or "").strip()
                    stable_ms = max(200, min(3_000, int(next_command.get("stableMs") or 650)))
                    timeout_ms = max(1_000, min(20_000, int(next_command.get("timeoutMs") or 9_000)))
                    document = _rendered_document(
                        adapter,
                        url=target_url,
                        stable_ms=stable_ms,
                        timeout_ms=timeout_ms,
                    )
                    _emit({
                        "type": "rendered-document",
                        "requestId": next_request_id,
                        "taskId": task_id,
                        **document,
                    })
                    continue
                raise ValueError(f"Unsupported monitor browser command: {command_type!r}")
            except Exception as exc:
                _emit({
                    "type": "error",
                    "requestId": next_request_id,
                    "taskId": task_id,
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                })
            finally:
                if not closed and adapter.is_running():
                    adapter.note_control_activity()
                    scheduler.poll_if_due()
    finally:
        if not closed:
            try:
                adapter.quit()
            except Exception:
                pass
    return 0


def main() -> int:
    try:
        return _start(_read_first_command())
    except Exception as exc:
        _emit({"type": "error", "errorType": type(exc).__name__, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
