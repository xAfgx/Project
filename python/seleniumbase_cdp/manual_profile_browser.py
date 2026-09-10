from __future__ import annotations

import hashlib
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict

import psutil

from control_aware_seleniumbase_adapter import ControlAwareSeleniumBaseCdpAdapter

RESULT_PREFIX = "ARES_SB_MANUAL\t"
LAST_URL_FILENAME = ".ares-last-url"
SITE_ADAPTER_FILENAME = ".ares-site-adapter.json"
MANUAL_RUNTIME_HEARTBEAT_SECONDS = 1.0


def _emit(payload: Dict[str, Any]) -> None:
    print(f"{RESULT_PREFIX}{json.dumps(payload, ensure_ascii=False)}", flush=True)


def _read_first_command() -> Dict[str, Any]:
    line = sys.stdin.readline()
    if not line:
        raise RuntimeError("No SeleniumBase start command received on stdin")
    command = json.loads(line)
    if not isinstance(command, dict):
        raise TypeError("SeleniumBase command must be a JSON object")
    return command


def _command_reader(target: queue.Queue[Dict[str, Any]]) -> None:
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            command = json.loads(raw)
            if isinstance(command, dict):
                target.put(command)
        except Exception as exc:
            _emit({"type": "error", "error": f"Invalid worker command: {exc}"})


def _proxy_value(command: Dict[str, Any]) -> str | None:
    value = str(command.get("proxy") or "").strip()
    return value or None


def _manual_browser_headless(command: Dict[str, Any]) -> bool:
    if "headless" in command:
        return bool(command.get("headless"))
    if not sys.platform.startswith("win"):
        return False
    session_name = str(os.environ.get("SESSIONNAME") or "").strip().lower()
    non_interactive_service = session_name in {"service", "services"}
    github_actions = str(os.environ.get("GITHUB_ACTIONS") or "").strip().lower() == "true"
    return non_interactive_service or github_actions


def _site_adapter_overrides(command: Dict[str, Any], profile_dir: Path) -> Dict[str, str]:
    inline = command.get("siteAdapterOverrides")
    if isinstance(inline, dict):
        return {str(key): str(value) for key, value in inline.items()}

    path = profile_dir / SITE_ADAPTER_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid {SITE_ADAPTER_FILENAME}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{SITE_ADAPTER_FILENAME} must contain a JSON object")
    return {str(key): str(value) for key, value in raw.items()}


def _restorable_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _read_last_url(profile_dir: Path) -> str:
    try:
        value = (profile_dir / LAST_URL_FILENAME).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return value if _restorable_url(value) else ""


def _remember_last_url(profile_dir: Path, adapter: ControlAwareSeleniumBaseCdpAdapter, previous: str = "") -> str:
    try:
        value = str(
            adapter.passive_observation(
                lambda: adapter.execute_script("return window.location.href;")
            )
            or ""
        ).strip()
    except Exception:
        return previous
    if not _restorable_url(value) or value == previous:
        return previous

    target = profile_dir / LAST_URL_FILENAME
    temporary = profile_dir / f"{LAST_URL_FILENAME}.tmp"
    try:
        temporary.write_text(value, encoding="utf-8")
        temporary.replace(target)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return previous
    return value


def _active_target_id(adapter: ControlAwareSeleniumBaseCdpAdapter) -> str:
    tab = adapter._sb.get_active_tab()
    target_id = getattr(tab, "target_id", None)
    if target_id is None:
        target = getattr(tab, "target", None)
        target_id = getattr(target, "target_id", None)
    value = str(target_id or "").strip()
    if not value:
        raise RuntimeError("Active SeleniumBase CDP target id is unavailable")
    return value


def _append_oopif_trace(adapter: ControlAwareSeleniumBaseCdpAdapter, phase: str, payload: Dict[str, Any]) -> None:
    try:
        trace_path = adapter.profile_dir / ".ares-visual-trace.jsonl"
        record = {"ts": time.time(), "phase": str(phase), **payload}
        with trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _enable_oopif_runtime(adapter: ControlAwareSeleniumBaseCdpAdapter) -> bool:
    # Import the patched registry entry point, not the raw implementation. This
    # gives the manual profile browser the same Page.enable, frame lifecycle,
    # document epoch and robust objectId-based frame-owner resolution used by
    # the real task OOPIF worker.
    from task_browser_worker_oopif import FlatCdpTargetRegistry

    driver = getattr(adapter._sb, "driver", None)
    if driver is not None and hasattr(driver, "cdp_base"):
        driver = driver.cdp_base
    websocket_url = str(getattr(driver, "websocket_url", "") or "").strip()
    if not websocket_url:
        raise RuntimeError("Browser-level CDP websocket URL is unavailable for OOPIF routing")

    registry = FlatCdpTargetRegistry(websocket_url)
    setattr(adapter, "_ares_oopif_registry", registry)

    def discover() -> list[Dict[str, Any]]:
        try:
            frames = registry.discover(_active_target_id(adapter), limit=128)
            _append_oopif_trace(
                adapter,
                "oopif-discover",
                {
                    "frameCount": len(frames),
                    "paths": [
                        [str(value) for value in entry.get("path") or [] if str(value)]
                        for entry in frames[:16]
                        if isinstance(entry, dict)
                    ],
                    "source": "manual-profile-browser",
                },
            )
            return frames
        except Exception as exc:
            _append_oopif_trace(
                adapter,
                "oopif-discover-error",
                {
                    "errorType": type(exc).__name__,
                    "error": str(exc)[:600],
                    "source": "manual-profile-browser",
                },
            )
            raise

    def evaluate(frame_path: list[str], script: str, args: list[Any] | None = None) -> Dict[str, Any]:
        path = [str(value) for value in frame_path if str(value)]
        try:
            frame_id, offset_x, offset_y = registry.resolve_path(
                _active_target_id(adapter),
                path,
                include_offsets=True,
            )
            value = registry.evaluate(frame_id, script, args or [])
            route = registry._route(frame_id) or {}
            epoch_reader = getattr(registry, "document_epoch", None)
            document_epoch = int(epoch_reader(frame_id)) if callable(epoch_reader) else 0
            result = {
                "value": value,
                "offsetX": offset_x,
                "offsetY": offset_y,
                "frameId": frame_id,
                "documentEpoch": document_epoch,
                "sessionGeneration": int(route.get("generation") or 0),
            }
            value_state = value if isinstance(value, dict) else {}
            _append_oopif_trace(
                adapter,
                "oopif-evaluate",
                {
                    "path": path,
                    "frameId": frame_id,
                    "sessionId": str(route.get("sessionId") or ""),
                    "contextId": route.get("contextId"),
                    "sessionGeneration": int(route.get("generation") or 0),
                    "documentEpoch": document_epoch,
                    "kind": str(value_state.get("kind") or type(value).__name__),
                    "scope": str(value_state.get("scope") or ""),
                    "source": "manual-profile-browser",
                },
            )
            return result
        except Exception as exc:
            _append_oopif_trace(
                adapter,
                "oopif-evaluate-error",
                {
                    "path": path,
                    "errorType": type(exc).__name__,
                    "error": str(exc)[:600],
                    "source": "manual-profile-browser",
                },
            )
            raise

    setattr(adapter._sb, "ares_oopif_discover", discover)
    setattr(adapter._sb, "ares_oopif_evaluate", evaluate)
    return True


def _run_manual_runtime_heartbeat(adapter: ControlAwareSeleniumBaseCdpAdapter) -> None:
    """Run automatic interaction directly from the manual browser owner loop.

    The visible profile browser has no RPC page-state consumer that needs to gate
    automatic interaction behind PageObservationWatchdog. Running the existing
    orchestrator only when the command queue is idle keeps one serialized owner
    while preventing a complex page snapshot/OOPIF discovery from starving the
    actual visual runtime before poll_and_act() can even begin.
    """
    try:
        adapter._orchestrator.run_cycle(adapter._run_visual_auto, adapter._run_instruction_auto)
    except Exception as exc:
        _append_oopif_trace(
            adapter,
            "manual-runtime-heartbeat-error",
            {"errorType": type(exc).__name__, "error": str(exc)[:600]},
        )


def _close_oopif_runtime(adapter: ControlAwareSeleniumBaseCdpAdapter) -> None:
    registry = getattr(adapter, "_ares_oopif_registry", None)
    if registry is None:
        return
    try:
        registry.close()
    except Exception:
        pass


def _close_adapter(adapter: ControlAwareSeleniumBaseCdpAdapter) -> None:
    owned_pids = adapter._profile_browser_pids()
    chrome_pid = adapter.chrome_pid
    if chrome_pid and chrome_pid not in owned_pids:
        owned_pids.insert(0, chrome_pid)

    _close_oopif_runtime(adapter)
    adapter.quit()

    deadline = time.monotonic() + (12.0 if sys.platform.startswith("win") else 3.0)
    remaining: list[psutil.Process] = []
    for pid in dict.fromkeys(owned_pids):
        try:
            process = psutil.Process(int(pid))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):
            continue
        timeout = max(0.0, deadline - time.monotonic())
        if timeout <= 0:
            remaining.append(process)
            continue
        try:
            process.wait(timeout=timeout)
        except psutil.NoSuchProcess:
            continue
        except (psutil.TimeoutExpired, psutil.AccessDenied, psutil.Error):
            if process.is_running():
                remaining.append(process)

    if sys.platform.startswith("win"):
        for process in remaining:
            try:
                process.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):
                continue
        if remaining:
            try:
                _, alive = psutil.wait_procs(remaining, timeout=3.0)
            except psutil.Error:
                alive = []
            for process in alive:
                try:
                    process.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):
                    pass
            if alive:
                try:
                    psutil.wait_procs(alive, timeout=2.0)
                except psutil.Error:
                    pass


def _start(command: Dict[str, Any]) -> int:
    if str(command.get("type") or "") != "start":
        raise ValueError("First SeleniumBase command must be type='start'")

    profile_id = str(command.get("profileId") or "").strip()
    profile_dir = Path(str(command.get("profileDir") or "")).expanduser().resolve()
    if not profile_id:
        raise ValueError("profileId is required")
    if not str(profile_dir):
        raise ValueError("profileDir is required")

    request_id = str(command.get("requestId") or "")
    adapter = ControlAwareSeleniumBaseCdpAdapter(
        profile_dir=profile_dir,
        headless=_manual_browser_headless(command),
        proxy=_proxy_value(command),
        user_agent=str(command.get("userAgent") or "").strip() or None,
        site_adapter_overrides=_site_adapter_overrides(command, profile_dir),
        # No --restore-last-session: Chrome would load the last page before the
        # seeded fingerprint spoof is injected, letting the page JS run with the
        # raw machine fingerprint. ARES restores the last URL itself (via
        # .ares-last-url + goto AFTER injection), so nothing is lost.
        # DoH stays enabled (secure mode) so Chrome can fetch the HTTPS/SVCB
        # record and engage ECH; browserleaks.com/ech then reports Success=True.
        browser_args=[
            "--no-first-run", "--no-default-browser-check",
            "--dns-over-https-mode=secure",
            "--dns-over-https-templates=https://dns.google/dns-query",
        ],
    )
    oopif_enabled = _enable_oopif_runtime(adapter)
    # Same seeded device-fingerprint diversification as task-driven sessions.
    # The seed is derived from the profile id, so every profile exposes a stable
    # yet distinct hardware/GPU/screen/canvas identity instead of the raw
    # machine fingerprint shared by all profiles.
    try:
        profile_seed = int(hashlib.sha256(profile_id.encode("utf-8")).hexdigest()[:8], 16)
    except Exception:
        profile_seed = 1
    closed = False
    last_url = _read_last_url(profile_dir)

    try:
        initial_cookies = command.get("cookies")
        applied = adapter.set_snapshot_cookies(initial_cookies) if isinstance(initial_cookies, list) and initial_cookies else 0

        start_url = str(command.get("startUrl") or "").strip() or last_url
        if start_url:
            adapter.goto(start_url)
            last_url = _remember_last_url(profile_dir, adapter, last_url)
            # Inject AFTER the first navigation: the CDP session/frame routes are
            # stable now, so the injection does not disturb OOPIF discovery or
            # subsequent CDP clicks. runImmediately applies the spoof to the
            # already-loaded page right away - no reload needed.
            adapter.install_device_fingerprint(profile_seed, str(command.get("userAgent") or "").strip() or None)

        _emit({
            "type": "ready",
            "requestId": request_id,
            "profileId": profile_id,
            "profileDir": str(profile_dir),
            "pid": adapter.chrome_pid,
            "appliedCookieCount": applied,
            "runtimeMode": "oopif",
            "oopifRoutingEnabled": oopif_enabled,
            "siteAdapterEnabled": True,
            "gridActionsEnabled": True,
            "sliderActionsEnabled": True,
            "autoInteractionsEnabled": True,
            "semanticInteractionsEnabled": True,
            "outcomeVerificationEnabled": True,
        })

        commands: queue.Queue[Dict[str, Any]] = queue.Queue()
        threading.Thread(target=_command_reader, args=(commands,), daemon=True).start()
        next_url_capture = time.monotonic() + 2.0
        next_runtime_heartbeat = time.monotonic() + 0.25

        while True:
            if not adapter.is_running():
                _emit({"type": "browser-closed", "profileId": profile_id})
                break

            now = time.monotonic()
            if now >= next_url_capture:
                last_url = _remember_last_url(profile_dir, adapter, last_url)
                next_url_capture = now + 2.0

            try:
                next_command = commands.get(timeout=0.25)
            except queue.Empty:
                now = time.monotonic()
                if now >= next_runtime_heartbeat:
                    _run_manual_runtime_heartbeat(adapter)
                    next_runtime_heartbeat = time.monotonic() + MANUAL_RUNTIME_HEARTBEAT_SECONDS
                continue

            adapter.note_control_activity()
            command_type = str(next_command.get("type") or "")
            next_request_id = str(next_command.get("requestId") or "")
            try:
                if command_type == "close":
                    last_url = _remember_last_url(profile_dir, adapter, last_url)
                    _close_adapter(adapter)
                    closed = True
                    _emit({"type": "closed", "requestId": next_request_id, "profileId": profile_id})
                    break
                if command_type == "export-cookies":
                    _emit({"type": "cookies", "requestId": next_request_id, "profileId": profile_id, "cookies": adapter.get_snapshot_cookies()})
                    continue
                if command_type == "apply-cookies":
                    cookies = next_command.get("cookies")
                    if not isinstance(cookies, list):
                        raise ValueError("apply-cookies requires a cookies array")
                    _emit({"type": "cookies-applied", "requestId": next_request_id, "profileId": profile_id, "count": adapter.set_snapshot_cookies(cookies)})
                    continue
                if command_type == "navigate":
                    url = str(next_command.get("url") or "").strip()
                    if not url:
                        raise ValueError("navigate requires a URL")
                    adapter.goto(url)
                    last_url = _remember_last_url(profile_dir, adapter, last_url)
                    _emit({"type": "navigated", "requestId": next_request_id, "profileId": profile_id})
                    continue
                if command_type == "inspect-session":
                    _emit({"type": "session-inspection", "requestId": next_request_id, "profileId": profile_id, **adapter.inspect_session()})
                    continue
                if command_type == "challenge-state":
                    _emit({"type": "challenge-state", "requestId": next_request_id, "profileId": profile_id, "state": adapter.challenge_state()})
                    continue
                if command_type == "site-grid-state":
                    _emit({"type": "site-grid-state", "requestId": next_request_id, "profileId": profile_id, "state": adapter.site_grid_state()})
                    continue
                if command_type == "site-slider-state":
                    _emit({"type": "site-slider-state", "requestId": next_request_id, "profileId": profile_id, "state": adapter.site_slider_state()})
                    continue
                if command_type == "auto-interaction-state":
                    _emit({"type": "auto-interaction-state", "requestId": next_request_id, "profileId": profile_id, "state": adapter.auto_interaction_state()})
                    continue
                if command_type == "interaction-outcome-state":
                    _emit({"type": "interaction-outcome-state", "requestId": next_request_id, "profileId": profile_id, "state": adapter.interaction_outcome_state()})
                    continue
                if command_type == "observe-semantic-fields":
                    _emit({"type": "semantic-fields", "requestId": next_request_id, "profileId": profile_id, "fields": adapter.observe_semantic_fields()})
                    continue
                if command_type == "execute-semantic-plan":
                    plan = next_command.get("plan")
                    if not isinstance(plan, list):
                        raise ValueError("execute-semantic-plan requires a plan array")
                    result = adapter.execute_semantic_plan(plan)
                    _emit({"type": "semantic-plan-result", "requestId": next_request_id, "profileId": profile_id, **result})
                    continue
                if command_type == "apply-grid-selection":
                    indexes = next_command.get("indexes")
                    if not isinstance(indexes, list):
                        raise ValueError("apply-grid-selection requires an indexes array")
                    result = adapter.apply_grid_selection(indexes, submit=bool(next_command.get("submit", True)))
                    _emit({"type": "grid-selection-applied", "requestId": next_request_id, "profileId": profile_id, **result})
                    continue
                if command_type == "apply-slider":
                    target = float(next_command.get("targetFraction", 0.96))
                    result = adapter.apply_slider(target)
                    _emit({"type": "slider-applied", "requestId": next_request_id, "profileId": profile_id, **result})
                    continue
                if command_type == "status":
                    _emit({
                        "type": "status",
                        "requestId": next_request_id,
                        "profileId": profile_id,
                        "open": adapter.is_running(),
                        "runtimeMode": "oopif",
                        "oopifRoutingEnabled": oopif_enabled,
                        "siteAdapterEnabled": True,
                        "gridActionsEnabled": True,
                        "sliderActionsEnabled": True,
                        "autoInteractionsEnabled": True,
                        "semanticInteractionsEnabled": True,
                        "outcomeVerificationEnabled": True,
                    })
                    continue
                raise ValueError(f"Unsupported SeleniumBase command: {command_type!r}")
            except Exception as exc:
                _emit({
                    "type": "error",
                    "requestId": next_request_id,
                    "profileId": profile_id,
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                })
            finally:
                if not closed and adapter.is_running():
                    adapter.note_control_activity()
                    next_runtime_heartbeat = max(
                        next_runtime_heartbeat,
                        time.monotonic() + MANUAL_RUNTIME_HEARTBEAT_SECONDS,
                    )
    finally:
        if not closed:
            try:
                _remember_last_url(profile_dir, adapter, last_url)
                _close_adapter(adapter)
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