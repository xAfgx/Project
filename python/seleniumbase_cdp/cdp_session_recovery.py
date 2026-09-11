"""CDP session recovery guard for same-tab navigations.

SeleniumBase's sync CDP wrapper can keep a stale ``page`` handle after a
same-tab navigation. Native CDP events then stop flowing because the old
session no longer receives commands. This module verifies the live document
channel and re-binds it to the freshly loaded target of the same tab.

Works with both SeleniumBase CDP entry points:

- ``sb_cdp.Chrome`` ("Pure CDP"): no ``is_connected()`` / ``reconnect()``
  methods exist, so the guard probes the document and re-binds ``sb.page``.
- ``SB(uc=True)`` + CDP Mode: ``sb.is_connected()`` / ``sb.reconnect()`` are
  used when present, with the same CDP probe as the fallback.

Usage after a navigation::

    from cdp_session_recovery import ensure_live_cdp_session

    sb.goto(next_url)
    state = ensure_live_cdp_session(sb, expected_url=next_url)
    if not state["cdpConnected"]:
        raise AssertionError(state)
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Tuple

import mycdp

DEFAULT_SETTLE_SECONDS = 0.2
DEFAULT_READY_SETTLE_SECONDS = 2.0
EVENT_DOMAINS = ("page", "runtime", "network")
_READY_STATES = ("interactive", "complete")


def verify_cdp_connection(seleniumbase_cdp: Any) -> Dict[str, Any]:
    """Report the CDP/document state without changing it.

    ``driverReportedConnected`` mirrors ``sb.is_connected()`` (or the
    underlying driver's equivalent). In CDP Mode that flag can be ``False``
    while CDP is still fully usable, so ``cdpConnected`` is derived from a
    lightweight ``Runtime.evaluate`` probe on the active tab instead.
    """
    sb = seleniumbase_cdp
    driver = _unwrap_driver(sb)
    probe_ok, probe_reason = _probe_document(sb)
    return {
        "cdpConnected": bool(probe_ok),
        "driverReportedConnected": _reported_connection(sb, driver),
        "probe": probe_reason,
        "url": _current_url(sb, ""),
        "targetId": _target_id(_active_tab(sb)),
    }


def ensure_live_cdp_session(
    seleniumbase_cdp: Any,
    *,
    expected_url: Optional[str] = None,
    attempts: int = 3,
    settle_seconds: float = DEFAULT_SETTLE_SECONDS,
    reset_frame_context: bool = True,
    force_reconnect: bool = False,
    on_recovered: Optional[Callable[[Any], Any]] = None,
) -> Dict[str, Any]:
    """Verify and, if needed, restore a live CDP session on the active tab.

    Steps:
      1. Probe ``sb.is_connected()`` when available and run a CDP document
         probe on the active tab to detect a detached or stale session.
      2. Reconnect via ``sb.reconnect()`` when available; otherwise re-bind
         ``sb.page`` to the live target of the same tab, re-open the CDP
         websocket and re-enable the Page/Runtime/Network event domains.
      3. Reset the iframe context via ``switch_to_default_content`` when
         available so later interactions address the new top-level document.

    Args:
        seleniumbase_cdp: The SeleniumBase CDP object (``sb_cdp.Chrome`` or
            the CDP-enabled ``SB`` instance).
        expected_url: Optional URL of the newly loaded document. Used to pick
            the correct live tab when the old target was replaced.
        attempts: Maximum recovery rounds before giving up.
        settle_seconds: Pause after each reconnect before the next probe.
        reset_frame_context: Reset to the main frame after recovery.
        force_reconnect: Reconnect even when the CDP probe already succeeds.
        on_recovered: Optional hook called after a successful recovery, e.g.
            to re-inject stealth scripts or re-arm the CDP Fetch bridge for a
            replaced target.

    Returns:
        A status dict with camelCase keys: ``cdpConnected``, ``recovered``,
        ``driverReportedConnected``, ``reconnectMethod``, ``frameContext``,
        ``attempts``, ``targetId``, ``url``, ``probe`` and ``error``.
    """
    sb = seleniumbase_cdp
    driver = _unwrap_driver(sb)
    total_attempts = max(1, int(attempts))

    probe_ok, probe_reason = _probe_document(sb)
    result: Dict[str, Any] = {
        "recovered": False,
        "cdpConnected": bool(probe_ok),
        "driverReportedConnected": _reported_connection(sb, driver),
        "reconnectMethod": None,
        "frameContext": "not-run",
        "attempts": 0,
        "probe": probe_reason,
        "targetId": _target_id(_active_tab(sb)),
        "url": _current_url(sb, expected_url),
        "error": None,
    }

    if probe_ok and not force_reconnect:
        if reset_frame_context:
            result["frameContext"] = _reset_frame_context(sb, driver)
        result["targetId"] = _target_id(_active_tab(sb))
        result["url"] = _current_url(sb, expected_url)
        return result

    last_error: Optional[str] = None
    for attempt in range(1, total_attempts + 1):
        result["attempts"] = attempt
        result["reconnectMethod"] = _run_reconnect(sb, driver, expected_url)
        time.sleep(max(0.0, float(settle_seconds)))
        probe_ok, probe_reason = _probe_document(sb)
        result["probe"] = probe_reason
        result["cdpConnected"] = bool(probe_ok)
        last_error = probe_reason
        if probe_ok:
            break

    if not result["cdpConnected"]:
        result["error"] = f"cdp-recovery-failed:{last_error or 'unknown'}"
        if reset_frame_context:
            result["frameContext"] = _reset_frame_context(sb, driver)
        result["targetId"] = _target_id(_active_tab(sb))
        result["url"] = _current_url(sb, expected_url)
        return result

    if callable(on_recovered):
        try:
            on_recovered(sb)
        except Exception as exc:
            result["error"] = f"on-recovered:{type(exc).__name__}:{exc}"
    if reset_frame_context:
        result["frameContext"] = _reset_frame_context(sb, driver)
    result["recovered"] = True
    result["targetId"] = _target_id(_active_tab(sb))
    result["url"] = _current_url(sb, expected_url)
    return result


def wait_for_document_ready(
    seleniumbase_cdp: Any,
    *,
    settle_seconds: float = DEFAULT_READY_SETTLE_SECONDS,
    timeout_seconds: float = 15.0,
    poll_seconds: float = 0.1,
) -> Dict[str, Any]:
    """Wait for the freshly loaded document before the next UI interaction.

    Prefers ``sb.wait_for_ready_state_complete()`` when available (SB/UC mode)
    and otherwise polls ``document.readyState`` on the live CDP document until
    it reaches ``interactive`` or ``complete``. In both cases a fixed
    ``settle_seconds`` pause follows so embedded iframes and late-registering
    execution contexts can attach.
    """
    sb = seleniumbase_cdp
    settle = max(0.0, float(settle_seconds))
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    last_state: Optional[str] = None
    error: Optional[str] = None

    waiter = getattr(sb, "wait_for_ready_state_complete", None)
    if callable(waiter):
        method = "seleniumbase-ready-state"
        try:
            waiter()
        except Exception as exc:
            error = f"{type(exc).__name__}:{exc}"
    else:
        method = "cdp-ready-state-probe"
        while time.monotonic() <= deadline:
            last_state = _probe_ready_state(sb)
            if last_state is None or last_state in _READY_STATES:
                break
            time.sleep(max(0.01, float(poll_seconds)))

    ready = True if method == "seleniumbase-ready-state" else last_state in _READY_STATES
    time.sleep(settle)
    return {
        "ready": bool(ready),
        "readyState": last_state,
        "method": method,
        "settleSeconds": settle,
        "error": error,
    }


def captcha_frame_recovery(
    seleniumbase_cdp: Any,
    *,
    frame: str = "iframe",
    retry: bool = True,
) -> Dict[str, Any]:
    """Re-focus the CAPTCHA iframe and force the click after navigation.

    Uses the integrated ``uc_gui_click_rc(frame="iframe", retry=True)`` method
    when the driver exposes it (UC Mode). Pure CDP sessions fall back to the
    built-in ``solve_captcha()``, which re-discovers frames via CDP itself.
    """
    sb = seleniumbase_cdp
    uc_click = getattr(sb, "uc_gui_click_rc", None)
    if callable(uc_click):
        try:
            result = uc_click(frame=frame, retry=retry)
            return {"method": "uc_gui_click_rc", "acted": bool(result), "error": None}
        except Exception as exc:
            return {
                "method": "uc_gui_click_rc",
                "acted": False,
                "error": f"{type(exc).__name__}:{exc}",
            }

    solver = getattr(sb, "solve_captcha", None)
    if callable(solver):
        try:
            result = solver()
            return {"method": "solve_captcha", "acted": bool(result), "error": None}
        except Exception as exc:
            return {
                "method": "solve_captcha",
                "acted": False,
                "error": f"{type(exc).__name__}:{exc}",
            }

    return {"method": "none", "acted": False, "error": None}


def _run_reconnect(sb: Any, driver: Any, expected_url: Optional[str]) -> Optional[str]:
    """Run the best available reconnect strategy for this SeleniumBase setup."""
    reconnect = getattr(sb, "reconnect", None)
    if callable(reconnect):
        try:
            reconnect()
            return "seleniumbase-reconnect"
        except Exception:
            pass

    driver_reconnect = getattr(driver, "reconnect", None)
    if callable(driver_reconnect):
        try:
            driver_reconnect()
            return "driver-reconnect"
        except Exception:
            pass

    tab = _rebind_active_tab(sb, driver, expected_url)
    if tab is not None:
        return "cdp-tab-rebind"
    return None


def _rebind_active_tab(sb: Any, driver: Any, expected_url: Optional[str]) -> Any:
    """Re-bind ``sb.page`` to the live page target of the same tab."""
    loop = _event_loop(sb)
    current = _active_tab(sb)

    if driver is not None:
        update = getattr(driver, "update_targets", None)
        if callable(update) and loop is not None:
            try:
                loop.run_until_complete(update())
            except Exception:
                pass

    tab = _select_live_tab(driver, current, expected_url)
    if tab is None:
        return None

    try:
        setattr(sb, "page", tab)
    except Exception:
        return None
    if driver is not None and hasattr(driver, "page"):
        try:
            setattr(driver, "page", tab)
        except Exception:
            pass

    aopen = getattr(tab, "aopen", None)
    if callable(aopen) and loop is not None:
        try:
            loop.run_until_complete(aopen())
        except Exception:
            return None

    _reenable_event_domains(loop, tab)
    return tab


def _reenable_event_domains(loop: Any, tab: Any) -> None:
    """Re-arm the CDP event domains after a websocket re-open or target swap."""
    if loop is None or tab is None:
        return
    for name in EVENT_DOMAINS:
        domain = getattr(mycdp, name, None)
        enable = getattr(domain, "enable", None)
        if not callable(enable):
            continue
        try:
            loop.run_until_complete(tab.send(enable()))
        except Exception:
            continue


def _reset_frame_context(sb: Any, driver: Any) -> str:
    """Exit all iframe contexts so interactions target the top-level document."""
    method = getattr(sb, "switch_to_default_content", None)
    if callable(method):
        try:
            method()
            return "seleniumbase-default-content"
        except Exception as exc:
            return f"error:{type(exc).__name__}"

    switch_to = getattr(driver, "switch_to", None)
    default_content = getattr(switch_to, "default_content", None)
    if callable(default_content):
        try:
            default_content()
            return "driver-default-content"
        except Exception as exc:
            return f"error:{type(exc).__name__}"

    return "not-applicable"


def _probe_document(sb: Any) -> Tuple[bool, str]:
    evaluator = getattr(sb, "evaluate", None)
    if not callable(evaluator):
        return True, "no-evaluator"
    try:
        value = evaluator("1")
    except Exception as exc:
        return False, f"probe-exception:{type(exc).__name__}"
    if value is None:
        return False, "probe-null"
    return True, "probe-ok"


def _probe_ready_state(sb: Any) -> Optional[str]:
    evaluator = getattr(sb, "evaluate", None)
    if callable(evaluator):
        try:
            value = evaluator("document.readyState")
        except Exception:
            return None
    else:
        executor = getattr(sb, "execute_script", None)
        if not callable(executor):
            return None
        try:
            value = executor("return document.readyState;")
        except Exception:
            return None
    state = str(value or "").strip().lower()
    if state in {"loading", "interactive", "complete"}:
        return state
    return None


def _reported_connection(sb: Any, driver: Any) -> Optional[bool]:
    for candidate in (sb, driver):
        if candidate is None:
            continue
        checker = getattr(candidate, "is_connected", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
    return None


def _unwrap_driver(sb: Any) -> Any:
    driver = getattr(sb, "driver", None)
    if driver is not None and hasattr(driver, "cdp_base"):
        return driver.cdp_base
    return driver


def _event_loop(sb: Any) -> Any:
    getter = getattr(sb, "get_event_loop", None)
    if callable(getter):
        try:
            loop = getter()
            if loop is not None:
                return loop
        except Exception:
            pass
    direct = getattr(sb, "loop", None)
    if direct is not None:
        return direct
    driver = _unwrap_driver(sb)
    cdp = getattr(driver, "cdp", None)
    return getattr(cdp, "loop", None)


def _active_tab(sb: Any) -> Any:
    getter = getattr(sb, "get_active_tab", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            pass
    return getattr(sb, "page", None)


def _select_live_tab(driver: Any, current: Any, expected_url: Optional[str]) -> Any:
    tabs = list(getattr(driver, "tabs", None) or []) if driver is not None else []
    current_id = _target_id(current)

    if current_id:
        for tab in tabs:
            if _target_id(tab) == current_id:
                return tab

    if current is not None and not tabs:
        return current

    if expected_url:
        wanted = _normalize_url(expected_url)
        for tab in tabs:
            candidate = _normalize_url(_tab_url(tab))
            if candidate and (
                candidate == wanted
                or candidate.startswith(wanted)
                or wanted.startswith(candidate)
            ):
                return tab

    if tabs:
        return tabs[-1]
    return current


def _target_id(tab: Any) -> str:
    if tab is None:
        return ""
    value = getattr(tab, "target_id", None)
    if value is None:
        target = getattr(tab, "target", None)
        value = getattr(target, "target_id", None)
    return str(value or "")


def _tab_url(tab: Any) -> str:
    if tab is None:
        return ""
    value = getattr(tab, "url", None)
    if not value:
        target = getattr(tab, "target", None)
        value = getattr(target, "url", None)
    return str(value or "")


def _normalize_url(url: str) -> str:
    value = str(url or "").strip()
    while value.endswith("/"):
        value = value[:-1]
    return value


def _current_url(sb: Any, fallback: Optional[str]) -> str:
    getter = getattr(sb, "get_current_url", None)
    if callable(getter):
        try:
            value = getter()
            if value:
                return str(value)
        except Exception:
            pass
    return str(fallback or "")
