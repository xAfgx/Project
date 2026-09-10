from __future__ import annotations

import os
from typing import Any

import task_browser_worker_oopif_impl as impl
from control_aware_seleniumbase_adapter import ControlAwareSeleniumBaseCdpAdapter
from runtime_poll_scheduler import SingleOwnerRuntimeScheduler


def _child_frame_id(self: Any, frame_id: str, selector: str) -> str:
    """Resolve a frame owner without creating a transient frontend nodeId.

    Runtime objectIds and DOM frontend nodeIds are session-scoped and may become
    stale independently during RFH/DOM transitions. Keep the object and DOM
    lookup on the same live route, and let DOM.describeNode consume the objectId
    directly so no ephemeral nodeId crosses commands.
    """
    selector_json = impl.json.dumps(str(selector), ensure_ascii=False)
    last: BaseException | None = None
    for attempt in range(3):
        route = self._wait_for_route(frame_id, timeout=1.5)
        session_id = str(route["sessionId"])
        try:
            response = self.call(
                "Runtime.evaluate",
                {
                    "expression": f"document.querySelector({selector_json})",
                    "contextId": int(route["contextId"]),
                    "returnByValue": False,
                    "awaitPromise": True,
                },
                session_id=session_id,
            )
            if response.get("exceptionDetails"):
                raise RuntimeError(f"Runtime.evaluate failed: {response.get('exceptionDetails')}")
            remote = response.get("result") if isinstance(response.get("result"), dict) else {}
            object_id = str(remote.get("objectId") or "")
            if not object_id:
                raise LookupError(f"Frame path no longer resolves at {selector}")
            try:
                description = self.call(
                    "DOM.describeNode",
                    {"objectId": object_id, "depth": 0, "pierce": True},
                    session_id=session_id,
                )
                node = description.get("node") if isinstance(description.get("node"), dict) else {}
                child_frame_id = str(node.get("frameId") or "")
                if not child_frame_id:
                    raise LookupError(f"CDP frameId is unavailable at {selector}")
                self._wait_for_route(child_frame_id, timeout=2.0)
                return child_frame_id
            finally:
                try:
                    self.call("Runtime.releaseObject", {"objectId": object_id}, session_id=session_id, timeout=1.0)
                except Exception:
                    pass
        except BaseException as exc:
            last = exc
            text = str(exc).lower()
            stale = (
                "could not find node" in text
                or "cannot find context" in text
                or "execution context was destroyed" in text
                or "inspected target navigated or closed" in text
                or "no frame with given id" in text
            )
            if not stale or attempt >= 2:
                raise
            impl.time.sleep(0.03 * (attempt + 1))
    if last is not None:
        raise last
    raise RuntimeError("Frame owner resolution failed without an error")


def _left_button(runtime: Any) -> Any:
    input_domain = getattr(impl.base.mycdp, "input_", None)
    mouse_button = getattr(input_domain, "MouseButton", None)
    left = getattr(mouse_button, "LEFT", None)
    if left is None:
        raise RuntimeError("CDP left mouse button enum is unavailable")
    return left


def _pointer_mouse(self: Any, action: str, command: dict[str, Any]) -> bool:
    """Drive generic pointer actions through the existing top-level CDP input.

    Coordinates are viewport coordinates. The pressed state is kept only so
    mouseMoved carries the correct CDP buttons bitmask while a drag is active.
    """
    if action in {"mouse-move", "mouse-click"}:
        x = float(command.get("x") or 0)
        y = float(command.get("y") or 0)
        self._pointer_x = x
        self._pointer_y = y
    else:
        x = float(getattr(self, "_pointer_x", 0.0))
        y = float(getattr(self, "_pointer_y", 0.0))

    if action != "mouse-up":
        hit = self._execute_script_retry_for_path(
            [],
            "return !!document.elementFromPoint(Number(arguments[0]), Number(arguments[1]));",
            x,
            y,
        )
        if not hit:
            return False

    if action == "mouse-move":
        self._dispatch_mouse_event(
            "mouseMoved",
            x,
            y,
            buttons=1 if bool(getattr(self, "_pointer_pressed", False)) else 0,
        )
        return True
    if action == "mouse-down":
        self._dispatch_mouse_event("mousePressed", x, y, button=_left_button(self), buttons=1, click_count=1)
        self._pointer_pressed = True
        return True
    if action == "mouse-up":
        try:
            self._dispatch_mouse_event("mouseReleased", x, y, button=_left_button(self), buttons=0, click_count=1)
        finally:
            self._pointer_pressed = False
        return True
    if action == "mouse-click":
        self._dispatch_native_click(x, y)
        self._sync_newest_target()
        return True
    raise ValueError(f"Unsupported pointer action: {action!r}")


_original_rpc = impl.base.TaskRpcRuntime.rpc
_original_network_events = impl.base.TaskRpcRuntime.network_events
_original_page_state = impl.base.TaskRpcRuntime.page_state
_original_registry_handle_event = impl.FlatCdpTargetRegistry._handle_event
_original_registry_initialize_session = impl.FlatCdpTargetRegistry._initialize_session
_original_oopif_runtime_init = impl.OopifTaskRpcRuntime.__init__
_original_run = impl.base.run


def _passive_queue_dom(self: Any) -> dict[str, Any]:
    """Read queue DOM state with native CDP DOM commands only."""
    registry = self._oopif_registry
    root_frame_id = registry.ensure_target(self._active_target_id())
    route = registry._route(root_frame_id) or {}
    session_id = str(route.get("sessionId") or "")
    if not session_id:
        raise RuntimeError("Passive queue DOM route is unavailable")

    document = registry.call("DOM.getDocument", {"depth": 0, "pierce": True}, session_id=session_id)
    root = document.get("root") if isinstance(document.get("root"), dict) else {}
    root_node_id = int(root.get("nodeId") or 0)
    if root_node_id <= 0:
        raise RuntimeError("Passive queue DOM root node is unavailable")

    def outer_html(selector: str) -> str:
        found = registry.call(
            "DOM.querySelector",
            {"nodeId": root_node_id, "selector": selector},
            session_id=session_id,
        )
        node_id = int(found.get("nodeId") or 0)
        if node_id <= 0:
            return ""
        html = registry.call("DOM.getOuterHTML", {"nodeId": node_id}, session_id=session_id)
        return str(html.get("outerHTML") or "")[:8192]

    queue_position = outer_html("#queue-position")
    position = queue_position or outer_html("#position")
    status = outer_html("#status")
    try:
        url = str(self.sb.get_current_url() or "")
    except Exception:
        url = ""
    return {
        "result": {
            "hasQueuePosition": bool(queue_position),
            "hasPosition": bool(position),
            "positionText": position,
            "statusText": status,
            "url": url,
        },
        "url": url,
    }


def _control_aware_rpc(self: Any, command: dict[str, Any]) -> dict[str, Any]:
    action = str(command.get("action") or "")
    passive = action in {"page-state", "passive-queue-dom"}
    if not passive:
        self.adapter.note_control_activity()
    try:
        if action == "passive-queue-dom":
            return _passive_queue_dom(self)
        if action in {"mouse-down", "mouse-up"}:
            self._sync_newest_target()
            return {"result": self._mouse(action, command)}
        return _original_rpc(self, command)
    finally:
        if not passive:
            self.adapter.note_control_activity()


def _runtime_scheduler(self: Any) -> SingleOwnerRuntimeScheduler | None:
    scheduler = getattr(self, "_ares_runtime_scheduler", None)
    return scheduler if isinstance(scheduler, SingleOwnerRuntimeScheduler) else None


def _network_events_with_runtime_opportunity(self: Any) -> dict[str, Any]:
    result = _original_network_events(self)
    scheduler = _runtime_scheduler(self)
    if scheduler is not None:
        scheduler.poll_if_due()
    return result


def _page_state_with_passive_runtime_opportunity(self: Any) -> dict[str, Any]:
    observer = getattr(self.adapter, "passive_observation", None)
    if callable(observer):
        result = observer(lambda: _original_page_state(self))
    else:
        result = _original_page_state(self)
    scheduler = _runtime_scheduler(self)
    if scheduler is not None:
        scheduler.poll_if_due()
    return result


def _epoch_map(registry: Any) -> dict[str, int]:
    epochs = getattr(registry, "_ares_document_epochs", None)
    if not isinstance(epochs, dict):
        epochs = {}
        setattr(registry, "_ares_document_epochs", epochs)
    return epochs


def _document_epoch(self: Any, frame_id: str) -> int:
    return int(_epoch_map(self).get(str(frame_id), 0))


def _bump_document_epoch(registry: Any, frame_id: str) -> int:
    frame_id = str(frame_id or "")
    if not frame_id:
        return 0
    epochs = _epoch_map(registry)
    epochs[frame_id] = int(epochs.get(frame_id, 0)) + 1
    return int(epochs[frame_id])


def _handle_event_with_frame_lifecycle(self: Any, message: dict[str, Any]) -> None:
    """Extend the existing flattened registry with document identity events."""
    _original_registry_handle_event(self, message)
    method = str(message.get("method") or "")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}

    if method == "Page.frameAttached":
        frame_id = str(params.get("frameId") or "")
        if frame_id:
            _epoch_map(self).setdefault(frame_id, 0)
        return
    if method == "Page.frameNavigated":
        frame = params.get("frame") if isinstance(params.get("frame"), dict) else {}
        _bump_document_epoch(self, str(frame.get("id") or ""))
        return
    if method == "Page.navigatedWithinDocument":
        _bump_document_epoch(self, str(params.get("frameId") or ""))
        return
    if method == "Page.frameDetached":
        frame_id = str(params.get("frameId") or "")
        if frame_id:
            _epoch_map(self).pop(frame_id, None)


async def _initialize_session_with_page(self: Any, session_id: str) -> None:
    await _original_registry_initialize_session(self, session_id)
    await self._send_command("Page.enable", {}, session_id=session_id)


def _append_oopif_trace(runtime: Any, phase: str, payload: dict[str, Any]) -> None:
    """Append passive routing diagnostics beside the existing visual trace."""
    try:
        profile_dir = getattr(runtime.adapter, "profile_dir", None)
        if profile_dir is None:
            return
        trace_path = profile_dir / ".ares-visual-trace.jsonl"
        record = {"ts": impl.time.time(), "phase": str(phase), **payload}
        with trace_path.open("a", encoding="utf-8") as handle:
            handle.write(impl.json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _oopif_runtime_init(self: Any, *args: Any, **kwargs: Any) -> None:
    _original_oopif_runtime_init(self, *args, **kwargs)
    self._ares_runtime_scheduler = SingleOwnerRuntimeScheduler(self.adapter)
    registry = self._oopif_registry

    def discover() -> list[dict[str, Any]]:
        try:
            frames = registry.discover(self._active_target_id(), limit=impl.base.MAX_DISCOVERED_FRAMES)
            _append_oopif_trace(
                self,
                "oopif-discover",
                {
                    "frameCount": len(frames),
                    "paths": [
                        [str(value) for value in entry.get("path") or [] if str(value)]
                        for entry in frames[:16]
                        if isinstance(entry, dict)
                    ],
                },
            )
            return frames
        except Exception as exc:
            _append_oopif_trace(
                self,
                "oopif-discover-error",
                {"errorType": type(exc).__name__, "error": str(exc)[:600]},
            )
            raise

    def evaluate(frame_path: list[str], script: str, args: list[Any] | None = None) -> dict[str, Any]:
        path = [str(value) for value in frame_path if str(value)]
        try:
            frame_id, offset_x, offset_y = registry.resolve_path(
                self._active_target_id(),
                path,
                include_offsets=True,
            )
            value = registry.evaluate(frame_id, script, args or [])
            route = registry._route(frame_id) or {}
            result = {
                "value": value,
                "offsetX": offset_x,
                "offsetY": offset_y,
                "frameId": frame_id,
                "documentEpoch": registry.document_epoch(frame_id),
                "sessionGeneration": int(route.get("generation") or 0),
            }
            value_state = value if isinstance(value, dict) else {}
            _append_oopif_trace(
                self,
                "oopif-evaluate",
                {
                    "path": path,
                    "frameId": frame_id,
                    "sessionId": str(route.get("sessionId") or ""),
                    "contextId": route.get("contextId"),
                    "sessionGeneration": int(route.get("generation") or 0),
                    "documentEpoch": registry.document_epoch(frame_id),
                    "kind": str(value_state.get("kind") or type(value).__name__),
                    "scope": str(value_state.get("scope") or ""),
                    "complete": bool(value_state.get("complete")),
                    "failed": bool(value_state.get("failed")),
                },
            )
            return result
        except Exception as exc:
            _append_oopif_trace(
                self,
                "oopif-evaluate-error",
                {
                    "path": path,
                    "errorType": type(exc).__name__,
                    "error": str(exc)[:600],
                },
            )
            raise

    setattr(self.sb, "ares_oopif_discover", discover)
    setattr(self.sb, "ares_oopif_evaluate", evaluate)


def _seeded_run(start: dict[str, Any]) -> int:
    """Bind the existing cursor RNG namespace to the owning task id."""
    previous = os.environ.get("ARES_INTERACTION_SEED")
    task_id = str(start.get("taskId") or "").strip()
    if task_id:
        os.environ["ARES_INTERACTION_SEED"] = task_id
    try:
        return _original_run(start)
    finally:
        if previous is None:
            os.environ.pop("ARES_INTERACTION_SEED", None)
        else:
            os.environ["ARES_INTERACTION_SEED"] = previous


# The default OOPIF task worker and the manual validation worker use the same
# control-aware adapter contract: explicit RPC/control traffic gets a short
# priority window before expensive automatic visual inference is allowed to start.
impl.base.SeleniumBaseCdpAdapter = ControlAwareSeleniumBaseCdpAdapter
impl.FlatCdpTargetRegistry._child_frame_id = _child_frame_id
impl.FlatCdpTargetRegistry._handle_event = _handle_event_with_frame_lifecycle
impl.FlatCdpTargetRegistry._initialize_session = _initialize_session_with_page
impl.FlatCdpTargetRegistry.document_epoch = _document_epoch
impl.OopifTaskRpcRuntime.__init__ = _oopif_runtime_init
impl.base.TaskRpcRuntime._mouse = _pointer_mouse
impl.base.TaskRpcRuntime.rpc = _control_aware_rpc
impl.base.TaskRpcRuntime.network_events = _network_events_with_runtime_opportunity
impl.base.TaskRpcRuntime.page_state = _page_state_with_passive_runtime_opportunity
impl.base.run = _seeded_run

FlatCdpTargetRegistry = impl.FlatCdpTargetRegistry
main = impl.main


if __name__ == "__main__":
    raise SystemExit(main())
