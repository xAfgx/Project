from __future__ import annotations

import hashlib
import json
import queue
import re
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List

import mycdp
from seleniumbase_adapter import SeleniumBaseCdpAdapter

PREFIX = "ARES_SB_TASK\t"
MAX_RESPONSE_BODY = 256_000
MAX_NETWORK_EVENTS = 50
MAX_DISCOVERED_FRAMES = 128
_CONTEXT_RETRY_ERRORS = (
    "execution context was destroyed",
    "cannot find context",
    "inspected target navigated or closed",
    "target closed",
    "no frame with given id",
)
_QUEUE_URL_RE = re.compile(
    r"(?i)(queue[-_.]?it|waiting[-_.]?room|waitingroom|/queue(?:/|\?|$)|queue-token|queue/status|checkpoint|throttle)"
)
_TEXT_MIME_RE = re.compile(r"(?i)^(?:application/(?:json|[^;]+\+json)|text/(?:html|plain))(?:;|$)")
_FRAME_DESCRIPTORS_SCRIPT = r"""
const selectorFor = (element) => {
  const parts = [];
  let node = element;
  while (node && node.nodeType === 1) {
    if (node === document.documentElement) {
      parts.unshift('html');
      break;
    }
    const parent = node.parentElement;
    if (!parent) break;
    const index = Array.prototype.indexOf.call(parent.children, node) + 1;
    parts.unshift(`${node.tagName.toLowerCase()}:nth-child(${index})`);
    node = parent;
  }
  return parts.join(' > ');
};
return Array.from(document.querySelectorAll('iframe,frame')).map((element, ordinal) => ({
  selector: selectorFor(element),
  name: String(element.getAttribute('name') || element.getAttribute('id') || ''),
  src: String(element.getAttribute('src') || ''),
  ordinal,
}));
"""


def emit(payload: Dict[str, Any]) -> None:
    print(f"{PREFIX}{json.dumps(payload, ensure_ascii=False)}", flush=True)


def read_first() -> Dict[str, Any]:
    line = sys.stdin.readline()
    if not line:
        raise RuntimeError("No SeleniumBase task start command received")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise TypeError("Start command must be a JSON object")
    return value


def command_reader(target: queue.Queue[Dict[str, Any]]) -> None:
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            value = json.loads(raw)
            if isinstance(value, dict):
                target.put(value)
        except Exception as exc:
            emit({"type": "error", "error": f"Invalid RPC JSON: {exc}"})


def as_proxy(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def pattern_payload(value: Any) -> Dict[str, str] | None:
    if isinstance(value, str):
        return {"source": re.escape(value), "flags": "i"}
    if isinstance(value, dict):
        return {"source": str(value.get("source") or ""), "flags": str(value.get("flags") or "")}
    return None


def locator_script() -> str:
    return r"""
const selector = String(arguments[0] || '');
const nth = Number(arguments[1] ?? -1);
const textSpec = arguments[2];
const action = String(arguments[3] || '');
const payload = arguments[4] || {};
const visible = el => {
  if (!el || !el.getBoundingClientRect) return false;
  const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
};
let items = Array.from(document.querySelectorAll(selector));
if (textSpec && textSpec.source !== undefined) {
  let rx;
  try { rx = new RegExp(String(textSpec.source || ''), String(textSpec.flags || '').replace(/g/g, '')); }
  catch (_) { rx = new RegExp(String(textSpec.source || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i'); }
  items = items.filter(el => rx.test(String(el.innerText || el.textContent || el.getAttribute('value') || el.getAttribute('aria-label') || '')));
}
const selected = nth >= 0 ? (items[nth] ? [items[nth]] : []) : items;
const el = selected[0];
if (action === 'count') return items.length;
if (action === 'all-text-contents') return selected.map(node => String(node.textContent || ''));
if (!el) return { __aresMissing: true };
if (action === 'is-visible') return visible(el);
if (action === 'is-enabled') return !el.disabled && el.getAttribute('aria-disabled') !== 'true';
if (action === 'input-value') return String(el.value ?? '');
if (action === 'inner-text') return String(el.innerText ?? el.textContent ?? '');
if (action === 'bounding-box') { const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height}; }
if (action === 'scroll-into-view') { el.scrollIntoView({block:'center',inline:'nearest'}); return true; }
if (action === 'focus') { el.focus({preventScroll:true}); return document.activeElement === el; }
if (action === 'click') {
  el.scrollIntoView({block:'center',inline:'nearest'});
  const r = el.getBoundingClientRect();
  const x = r.left + r.width / 2;
  const y = r.top + r.height / 2;
  const hit = document.elementFromPoint(x, y);
  return {
    nativeClick: true,
    visible: visible(el),
    enabled: !el.disabled && el.getAttribute('aria-disabled') !== 'true',
    hit: !!hit && (hit === el || el.contains(hit)),
    x, y, width: r.width, height: r.height,
  };
}
if (action === 'fill') {
  const value = String(payload.value ?? '');
  el.scrollIntoView({block:'center',inline:'nearest'}); el.focus({preventScroll:true});
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
  if (descriptor?.set) descriptor.set.call(el, value); else el.value = value;
  el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));
  return {value:String(el.value ?? ''), verified:String(el.value ?? '') === value};
}
if (action === 'select-option') {
  const wanted = String(payload.value ?? ''), options = Array.from(el.options || []);
  const match = options.find(option => String(option.value) === wanted) || options.find(option => String(option.textContent || '').trim() === wanted);
  if (!match) return {selected:false};
  el.value = match.value; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));
  return {selected:true,value:String(el.value ?? '')};
}
return null;
"""


class TaskRpcRuntime:
    def __init__(self, adapter: SeleniumBaseCdpAdapter, *, user_agent: str | None = None, language: str | None = None) -> None:
        self.adapter = adapter
        self.sb = getattr(adapter, "_sb")
        self._network_lock = threading.Lock()
        self._network_events: Deque[Dict[str, Any]] = deque(maxlen=MAX_NETWORK_EVENTS)
        self._last_tab_count = 1
        self._active_frame_path: List[str] = []
        self._install_network_handler()
        self._install_dialog_handler()
        self._apply_user_agent_override(user_agent, language)

    def _install_network_handler(self) -> None:
        try:
            self.sb.add_handler(mycdp.network.ResponseReceived, self._on_response)
        except Exception:
            pass

    def _install_dialog_handler(self) -> None:
        event_type = getattr(getattr(mycdp, "page", None), "JavascriptDialogOpening", None)
        if event_type is None:
            return
        try:
            self.sb.add_handler(event_type, self._on_dialog)
        except Exception:
            pass

    async def _on_dialog(self, event: Any) -> None:
        try:
            tab = self.sb.get_active_tab()
            command = getattr(getattr(mycdp, "page", None), "handle_java_script_dialog", None)
            if callable(command):
                await tab.send(command(accept=True))
        except Exception:
            pass

    async def _on_response(self, event: mycdp.network.ResponseReceived) -> None:
        response = event.response
        url = str(response.url or "")
        mime = str(response.mime_type or "").split(";", 1)[0].strip().lower()
        if not _QUEUE_URL_RE.search(url):
            return
        if not _TEXT_MIME_RE.search(mime):
            return
        headers = {str(key).lower(): str(value) for key, value in dict(response.headers or {}).items()}
        with self._network_lock:
            self._network_events.append({
                "url": url,
                "headers": headers,
                "mimeType": mime,
                "requestId": event.request_id,
            })

    def _apply_user_agent_override(self, user_agent: str | None, language: str | None) -> None:
        if not user_agent:
            return
        command = getattr(getattr(mycdp, "network", None), "set_user_agent_override", None)
        if not callable(command):
            return
        try:
            tab = self.sb.get_active_tab()
            loop = self.sb.get_event_loop()
            kwargs: Dict[str, Any] = {"user_agent": str(user_agent)}
            if language:
                kwargs["accept_language"] = str(language)
            loop.run_until_complete(tab.send(command(**kwargs)))
        except Exception:
            pass

    def add_init_script(self, script: str) -> Dict[str, Any]:
        source = str(script or "")
        if not source.strip():
            return {"result": False}
        command = getattr(getattr(mycdp, "page", None), "add_script_to_evaluate_on_new_document", None)
        if not callable(command):
            raise RuntimeError("CDP Page.addScriptToEvaluateOnNewDocument is unavailable")
        tab = self.sb.get_active_tab()
        loop = self.sb.get_event_loop()
        result = loop.run_until_complete(tab.send(command(source=source)))
        return {"result": True, "identifier": str(result or "")}

    def install_stealth_spoof(self, seed: int, user_agent: str | None = None) -> Dict[str, Any]:
        """Diversify hardware/GPU/screen fingerprints per task to avoid cluster bans.

        Injected via Page.addScriptToEvaluateOnNewDocument so it runs before the
        page loads. The deterministic seed is derived from the task id, so the
        same task always produces the same fingerprint while different tasks and
        the wider runner pool look like independent consumer machines.

        All values come from the static real-device catalog. The GPU family is
        derived from the user agent OS, so UA, WebGL vendor/renderer, screen
        resolution and fonts never contradict each other.
        """
        script = self._stealth_spoof_script(int(seed) if seed is not None else 1, user_agent)
        return self.add_init_script(script)

    @staticmethod
    def _stealth_spoof_script(seed: int, user_agent: str | None = None) -> str:
        from device_fingerprint_catalog import build_device_fingerprint_script

        return build_device_fingerprint_script(int(seed) if seed else 1, user_agent)

    def _sync_newest_target(self) -> None:
        try:
            tabs = list(self.sb.get_tabs() or [])
        except Exception:
            tabs = []
        if len(tabs) > self._last_tab_count:
            try:
                self.sb.switch_to_tab(tabs[-1])
            except Exception:
                try:
                    self.sb.switch_to_newest_tab()
                except Exception:
                    pass
        self._last_tab_count = max(1, len(tabs)) if tabs else self._last_tab_count
        try:
            self.sb.switch_to_newest_window()
        except Exception:
            pass

    def navigate(self, command: Dict[str, Any]) -> Dict[str, Any]:
        url = str(command.get("url") or "").strip()
        if not url:
            raise ValueError("navigate requires url")
        self.adapter.goto(url)
        self._sync_newest_target()
        return {"url": str(self.sb.get_current_url() or url), "result": True}

    def network_events(self) -> Dict[str, Any]:
        with self._network_lock:
            items = list(self._network_events)
            self._network_events.clear()
        if not items:
            return {"events": [], "url": str(self.sb.get_current_url() or "")}

        tab = self.sb.get_active_tab()
        loop = self.sb.get_event_loop()
        output: List[Dict[str, Any]] = []
        for item in items:
            event = {"url": item["url"], "headers": item["headers"]}
            try:
                body, is_base64 = loop.run_until_complete(tab.send(mycdp.network.get_response_body(item["requestId"])))
                if not is_base64 and isinstance(body, str):
                    event["body"] = body[:MAX_RESPONSE_BODY]
            except Exception:
                pass
            output.append(event)
        return {"events": output, "url": str(self.sb.get_current_url() or "")}

    def page_state(self) -> Dict[str, Any]:
        try:
            url = str(self.sb.get_current_url() or "")
        except Exception:
            url = ""
        try:
            ready_state = str(self.adapter.execute_script("return document.readyState;") or "")
        except Exception:
            ready_state = ""
        return {
            "url": url,
            "readyState": ready_state,
            "frames": self._discover_frame_tree(),
        }

    def _discover_frame_tree(self) -> List[Dict[str, Any]]:
        queue_paths: Deque[List[str]] = deque([[]])
        entries: Dict[tuple[str, ...], Dict[str, Any]] = {}
        visited: set[tuple[str, ...]] = set()

        while queue_paths and len(entries) < MAX_DISCOVERED_FRAMES:
            path = queue_paths.popleft()
            key = tuple(path)
            if key in visited:
                continue
            visited.add(key)
            try:
                current_url, raw_descriptors = self._in_frames(
                    path,
                    lambda: (
                        str(self._execute_script_retry("return window.location.href;") or ""),
                        self._execute_script_retry(_FRAME_DESCRIPTORS_SCRIPT),
                    ),
                )
                descriptors = raw_descriptors if isinstance(raw_descriptors, list) else []
                if path and key in entries and current_url:
                    entries[key]["url"] = current_url
                for raw in descriptors:
                    if not isinstance(raw, dict):
                        continue
                    selector = str(raw.get("selector") or "").strip()
                    if not selector:
                        continue
                    child_path = [*path, selector]
                    child_key = tuple(child_path)
                    if child_key in entries:
                        continue
                    entries[child_key] = {
                        "path": child_path,
                        "url": str(raw.get("src") or ""),
                        "name": str(raw.get("name") or ""),
                        "depth": len(child_path),
                    }
                    if len(entries) < MAX_DISCOVERED_FRAMES:
                        queue_paths.append(child_path)
            except Exception:
                continue

        return list(entries.values())

    def rpc(self, command: Dict[str, Any]) -> Dict[str, Any]:
        self._sync_newest_target()
        action = str(command.get("action") or "")
        if action == "title":
            return {"result": str(self.sb.get_title() or ""), "url": str(self.sb.get_current_url() or "")}
        if action == "page-state":
            state = self.page_state()
            return {"result": state, "url": str(state.get("url") or "")}
        if action == "evaluate-page":
            result = self._evaluate_function(str(command.get("script") or ""), command.get("args") if isinstance(command.get("args"), list) else [])
            return {"result": result, "url": str(self.sb.get_current_url() or "")}
        if action == "wait-load-state":
            self._wait_ready(int(command.get("timeoutMs") or 15_000))
            return {"result": True, "url": str(self.sb.get_current_url() or "")}
        if action == "bring-to-front":
            bring = getattr(self.sb, "bring_active_window_to_front", None)
            if callable(bring):
                bring()
            return {"result": True}
        if action == "force-captcha-poll":
            return {"result": self.adapter.force_captcha_poll(), "url": str(self.sb.get_current_url() or "")}
        if action in {"mouse-move", "mouse-click"}:
            return {"result": self._mouse(action, command)}

        locator = command.get("locator")
        if not isinstance(locator, dict):
            raise ValueError(f"RPC action {action!r} requires locator")
        frame_path = [str(value) for value in locator.get("framePath") or [] if str(value)]
        return {
            "result": self._in_frames(frame_path, lambda: self._locator_op(action, locator, command)),
            "url": str(self.sb.get_current_url() or ""),
        }

    def _execute_script_in_frame_path(self, frame_path: Iterable[str], script: str, args: Iterable[Any]) -> Any:
        path = [str(value) for value in frame_path if str(value)]
        if not path:
            return self.adapter.execute_script(script, *list(args))
        wrapper = r"""
const framePath = Array.isArray(arguments[0]) ? arguments[0] : [];
const source = String(arguments[1] || '');
const callArgs = Array.isArray(arguments[2]) ? arguments[2] : [];
let targetWindow = window;
for (const rawSelector of framePath) {
  const selector = String(rawSelector || '');
  let frame;
  try {
    frame = targetWindow.document.querySelector(selector);
  } catch (error) {
    return {__aresFrameExecutionError:true, selector, message:String(error?.message || error)};
  }
  if (!frame || !frame.contentWindow) return {__aresFrameMissing:true, selector};
  targetWindow = frame.contentWindow;
}
try {
  const fn = targetWindow.Function(source);
  return fn.apply(targetWindow, callArgs);
} catch (error) {
  return {__aresFrameExecutionError:true, message:String(error?.message || error)};
}
"""
        value = self.adapter.execute_script(wrapper, path, script, list(args))
        if isinstance(value, dict) and value.get("__aresFrameMissing"):
            raise LookupError(f"Frame path no longer resolves at {value.get('selector')}")
        if isinstance(value, dict) and value.get("__aresFrameExecutionError"):
            raise RuntimeError(
                "Frame execution is not script-accessible; an attached OOPIF CDP session is required: "
                f"{value.get('message') or 'unknown frame execution error'}"
            )
        return value

    def _execute_script_retry_for_path(self, frame_path: Iterable[str], script: str, *args: Any) -> Any:
        last: Exception | None = None
        path = [str(value) for value in frame_path if str(value)]
        for attempt in range(3):
            try:
                return self._execute_script_in_frame_path(path, script, args)
            except Exception as exc:
                last = exc
                message = str(exc).lower()
                if not any(marker in message for marker in _CONTEXT_RETRY_ERRORS) or attempt >= 2:
                    raise
                time.sleep(0.10 * (attempt + 1))
                self._sync_newest_target()
                self._wait_ready(2_000)
        if last:
            raise last
        return None

    def _execute_script_retry(self, script: str, *args: Any) -> Any:
        return self._execute_script_retry_for_path(self._active_frame_path, script, *args)

    def _frame_viewport_offset(self, frame_path: Iterable[str]) -> tuple[float, float]:
        path = [str(value) for value in frame_path if str(value)]
        if not path:
            return 0.0, 0.0
        geometry = self._execute_script_retry_for_path(
            [],
            r"""
const framePath = Array.isArray(arguments[0]) ? arguments[0] : [];
let targetWindow = window;
let x = 0;
let y = 0;
for (const rawSelector of framePath) {
  const selector = String(rawSelector || '');
  let frame;
  try {
    frame = targetWindow.document.querySelector(selector);
  } catch (error) {
    return {__aresFrameExecutionError:true, selector, message:String(error?.message || error)};
  }
  if (!frame || !frame.contentWindow) return {__aresFrameMissing:true, selector};
  frame.scrollIntoView({block:'nearest', inline:'nearest'});
  const r = frame.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return {__aresFrameInvisible:true, selector};
  x += r.left + Number(frame.clientLeft || 0);
  y += r.top + Number(frame.clientTop || 0);
  targetWindow = frame.contentWindow;
}
return {x, y};
""",
            path,
        )
        if isinstance(geometry, dict) and geometry.get("__aresFrameMissing"):
            raise LookupError(f"Frame path no longer resolves at {geometry.get('selector')}")
        if isinstance(geometry, dict) and geometry.get("__aresFrameInvisible"):
            raise LookupError(f"Frame is not visible for native click: {geometry.get('selector')}")
        if isinstance(geometry, dict) and geometry.get("__aresFrameExecutionError"):
            raise RuntimeError(
                "Nested frame geometry is not script-accessible; an attached OOPIF CDP session is required: "
                f"{geometry.get('message') or 'unknown frame geometry error'}"
            )
        if not isinstance(geometry, dict):
            raise RuntimeError("Frame viewport offset could not be resolved")
        return float(geometry.get("x") or 0.0), float(geometry.get("y") or 0.0)

    @staticmethod
    def _assert_native_click_probe(probe: Any, selector: str) -> Dict[str, Any]:
        if isinstance(probe, dict) and probe.get("__aresMissing"):
            raise LookupError(f"No element matched locator: {selector}")
        if not isinstance(probe, dict) or not bool(probe.get("nativeClick")):
            raise RuntimeError(f"Native click geometry could not be resolved for {selector}")
        if not bool(probe.get("visible")):
            raise RuntimeError(f"Native click target is not visible: {selector}")
        if not bool(probe.get("enabled")):
            raise RuntimeError(f"Native click target is disabled: {selector}")
        if not bool(probe.get("hit")):
            raise RuntimeError(f"Native click hit-test failed: {selector}")
        if float(probe.get("width") or 0.0) <= 0 or float(probe.get("height") or 0.0) <= 0:
            raise RuntimeError(f"Native click target has no stable box: {selector}")
        return probe

    @staticmethod
    def _native_probe_stable(previous: Dict[str, Any], current: Dict[str, Any], tolerance: float = 1.5) -> bool:
        return all(
            abs(float(previous.get(key) or 0.0) - float(current.get(key) or 0.0)) <= tolerance
            for key in ("x", "y", "width", "height")
        )

    def _dispatch_mouse_event(
        self,
        event_type: str,
        x: float,
        y: float,
        *,
        button: Any = None,
        buttons: int | None = None,
        click_count: int | None = None,
    ) -> None:
        input_domain = getattr(mycdp, "input_", None)
        dispatch = getattr(input_domain, "dispatch_mouse_event", None)
        if not callable(dispatch):
            raise RuntimeError("CDP Input.dispatchMouseEvent is unavailable")
        kwargs: Dict[str, Any] = {
            "type_": event_type,
            "x": float(x),
            "y": float(y),
            "pointer_type": "mouse",
        }
        if button is not None:
            kwargs["button"] = button
        if buttons is not None:
            kwargs["buttons"] = int(buttons)
        if click_count is not None:
            kwargs["click_count"] = int(click_count)
        tab = self.sb.get_active_tab()
        loop = self.sb.get_event_loop()
        loop.run_until_complete(tab.send(dispatch(**kwargs)))

    def _dispatch_native_click(self, x: float, y: float) -> None:
        input_domain = getattr(mycdp, "input_", None)
        mouse_button = getattr(input_domain, "MouseButton", None)
        left = getattr(mouse_button, "LEFT", None)
        if left is None:
            raise RuntimeError("CDP left mouse button enum is unavailable")
        self._dispatch_mouse_event("mouseMoved", x, y, buttons=0)
        self._dispatch_mouse_event("mousePressed", x, y, button=left, buttons=1, click_count=1)
        self._dispatch_mouse_event("mouseReleased", x, y, button=left, buttons=0, click_count=1)

    def _native_locator_click(
        self,
        locator: Dict[str, Any],
        selector: str,
        nth: int,
        text_spec: Dict[str, str] | None,
    ) -> Dict[str, Any]:
        frame_path = [str(value) for value in locator.get("framePath") or [] if str(value)]
        offset_x, offset_y = self._frame_viewport_offset(frame_path)
        previous: Dict[str, Any] | None = None
        stable: Dict[str, Any] | None = None
        deadline = time.monotonic() + 0.45
        while time.monotonic() < deadline:
            probe = self._assert_native_click_probe(
                self._execute_script_retry(locator_script(), selector, nth, text_spec, "click", {}),
                selector,
            )
            if previous is not None and self._native_probe_stable(previous, probe):
                stable = probe
                break
            previous = probe
            time.sleep(0.05)
        if stable is None:
            raise RuntimeError(f"Native click target position did not stabilize: {selector}")

        x = offset_x + float(stable.get("x") or 0.0)
        y = offset_y + float(stable.get("y") or 0.0)
        self._dispatch_native_click(x, y)
        self._sync_newest_target()
        return {
            "clicked": True,
            "native": True,
            "inputMethod": "Input.dispatchMouseEvent",
            "x": x,
            "y": y,
        }

    def _locator_op(self, action: str, locator: Dict[str, Any], command: Dict[str, Any]) -> Any:
        selector = str(locator.get("selector") or "")
        if not selector:
            raise ValueError("locator selector is empty")
        nth = int(locator.get("nth")) if isinstance(locator.get("nth"), (int, float)) else -1
        text_spec = pattern_payload(locator.get("hasText"))
        if action == "wait-for":
            deadline = time.monotonic() + max(0.25, float(command.get("timeoutMs") or 15_000) / 1000.0)
            state = str(command.get("state") or "visible")
            while time.monotonic() < deadline:
                probe = "is-visible" if state == "visible" else "count"
                value = self._execute_script_retry(locator_script(), selector, nth, text_spec, probe, {})
                if (state == "visible" and value is True) or (state != "visible" and int(value or 0) > 0):
                    return True
                time.sleep(0.05)
            raise TimeoutError(f"Locator wait timed out: {selector}")
        if action in {"evaluate-one", "evaluate-all"}:
            return self._locator_evaluate(
                selector,
                nth,
                text_spec,
                str(command.get("script") or ""),
                command.get("args") if isinstance(command.get("args"), list) else [],
                all_items=action == "evaluate-all",
            )
        if action == "click":
            return self._native_locator_click(locator, selector, nth, text_spec)
        result = self._execute_script_retry(
            locator_script(),
            selector,
            nth,
            text_spec,
            action,
            {"value": command.get("value"), "options": command.get("options") or {}},
        )
        if isinstance(result, dict) and result.get("__aresMissing"):
            if action == "count":
                return 0
            if action in {"is-visible", "is-enabled"}:
                return False
            if action in {"input-value", "inner-text"}:
                return ""
            if action == "bounding-box":
                return None
            raise LookupError(f"No element matched locator: {selector}")
        if action == "fill" and isinstance(result, dict) and not bool(result.get("verified")):
            raise RuntimeError(f"fill readback verification failed for {selector}")
        return result

    def _locator_evaluate(
        self,
        selector: str,
        nth: int,
        text_spec: Dict[str, str] | None,
        function_source: str,
        args: List[Any],
        *,
        all_items: bool,
    ) -> Any:
        script = r"""
const selector=String(arguments[0]||''), nth=Number(arguments[1]??-1), spec=arguments[2], fnSource=String(arguments[3]||''), extra=Array.isArray(arguments[4])?arguments[4]:[];
let items=Array.from(document.querySelectorAll(selector));
if(spec&&spec.source!==undefined){const rx=new RegExp(String(spec.source||''),String(spec.flags||'').replace(/g/g,''));items=items.filter(el=>rx.test(String(el.innerText||el.textContent||el.getAttribute('value')||el.getAttribute('aria-label')||'')));}
const fn=(0,eval)(`(${fnSource})`); if(arguments[5]) return fn(items,...extra); const el=nth>=0?items[nth]:items[0]; if(!el)return {__aresMissing:true}; return fn(el,...extra);
"""
        value = self._execute_script_retry(script, selector, nth, text_spec, function_source, args, all_items)
        if isinstance(value, dict) and value.get("__aresMissing"):
            raise LookupError(f"No element matched locator: {selector}")
        return value

    def _evaluate_function(self, source: str, args: List[Any]) -> Any:
        if not source:
            return None
        if source.lstrip().startswith(("return ", "const ", "let ", "var ")) or ("function" not in source and "=>" not in source):
            return self._execute_script_retry(source, *args)
        return self._execute_script_retry(
            "const fn=(0,eval)(`(${arguments[0]})`); return fn(...arguments[1]);",
            source,
            args,
        )

    def _in_frames(self, frame_path: Iterable[str], action: Any) -> Any:
        previous_path = self._active_frame_path
        self._active_frame_path = [str(value) for value in frame_path if str(value)]
        try:
            return action()
        finally:
            self._active_frame_path = previous_path

    def _wait_ready(self, timeout_ms: int) -> None:
        deadline = time.monotonic() + max(0.25, timeout_ms / 1000.0)
        while time.monotonic() < deadline:
            try:
                if str(self.adapter.execute_script("return document.readyState;") or "") in {"interactive", "complete"}:
                    return
            except Exception:
                pass
            time.sleep(0.05)
        raise TimeoutError("document.readyState did not become interactive")

    def _mouse(self, action: str, command: Dict[str, Any]) -> bool:
        x, y = float(command.get("x") or 0), float(command.get("y") or 0)
        hit = self._execute_script_retry_for_path(
            [],
            "return !!document.elementFromPoint(Number(arguments[0]), Number(arguments[1]));",
            x,
            y,
        )
        if not hit:
            return False
        if action == "mouse-move":
            self._dispatch_mouse_event("mouseMoved", x, y, buttons=0)
            return True
        self._dispatch_native_click(x, y)
        self._sync_newest_target()
        return True


def run(start: Dict[str, Any]) -> int:
    if str(start.get("type") or "") != "start":
        raise ValueError("First command must be type='start'")
    profile_dir = Path(str(start.get("profileDir") or "")).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    user_agent = str(start.get("userAgent") or "").strip() or None
    language = str(start.get("locale") or "").strip() or None
    adapter = SeleniumBaseCdpAdapter(
        profile_dir=profile_dir,
        headless=bool(start.get("headless", False)),
        proxy=as_proxy(start.get("proxy")),
        user_agent=user_agent,
        browser_args=[str(v) for v in start.get("browserArgs") or []],
        language=language,
        timezone=str(start.get("timezoneId") or "").strip() or None,
    )
    runtime = TaskRpcRuntime(adapter, user_agent=user_agent, language=language)

    # Diversify hardware/GPU/screen fingerprint per task before any page loads,
    # so a pool of identical runners is not fingerprinted as one cluster.
    task_seed = str(start.get("taskId") or "").strip()
    if task_seed:
        try:
            runtime.install_stealth_spoof(int(hashlib.sha256(task_seed.encode("utf-8")).hexdigest()[:8], 16))
        except Exception:
            pass

    commands: queue.Queue[Dict[str, Any]] = queue.Queue()
    threading.Thread(target=command_reader, args=(commands,), daemon=True).start()
    emit({
        "type": "ready",
        "requestId": str(start.get("requestId") or ""),
        "ok": True,
        "pid": adapter.chrome_pid,
        "profileDir": str(profile_dir),
    })
    closed = False
    try:
        while True:
            if not adapter.is_running():
                break
            try:
                command = commands.get(timeout=0.25)
            except queue.Empty:
                adapter.poll_runtime()
                continue
            request_id = str(command.get("requestId") or "")
            command_type = str(command.get("type") or "")
            try:
                if command_type == "close":
                    adapter.quit()
                    closed = True
                    emit({"type": "closed", "requestId": request_id, "ok": True})
                    break
                if command_type == "apply-cookies":
                    cookies = command.get("cookies")
                    if not isinstance(cookies, list):
                        raise TypeError("apply-cookies requires an array")
                    emit({
                        "type": "cookies-applied",
                        "requestId": request_id,
                        "ok": True,
                        "result": adapter.set_snapshot_cookies(cookies),
                    })
                    continue
                if command_type == "add-init-script":
                    emit({
                        "type": "init-script-added",
                        "requestId": request_id,
                        "ok": True,
                        **runtime.add_init_script(str(command.get("script") or "")),
                    })
                    continue
                if command_type == "navigate":
                    emit({"type": "navigated", "requestId": request_id, "ok": True, **runtime.navigate(command)})
                    continue
                if command_type == "network-events":
                    emit({"type": "network-events", "requestId": request_id, "ok": True, **runtime.network_events()})
                    continue
                if command_type == "rpc":
                    emit({"type": "rpc-result", "requestId": request_id, "ok": True, **runtime.rpc(command)})
                    continue
                raise ValueError(f"Unsupported SeleniumBase task command: {command_type!r}")
            except Exception as exc:
                emit({
                    "type": "error",
                    "requestId": request_id,
                    "ok": False,
                    "error": str(exc),
                    "errorType": type(exc).__name__,
                })
    finally:
        if not closed:
            try:
                adapter.quit()
            except Exception:
                pass
    return 0


def main() -> int:
    try:
        return run(read_first())
    except Exception as exc:
        emit({"type": "error", "ok": False, "error": str(exc), "errorType": type(exc).__name__})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
