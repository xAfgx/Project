from __future__ import annotations

import atexit
import asyncio
import json
import threading
import time
from typing import Any, Dict, Iterable, List

import websockets

import task_browser_worker as base


_CONTEXT_ERRORS = (
    "execution context was destroyed",
    "cannot find context",
    "cannot find context with specified id",
    "inspected target navigated or closed",
    "target closed",
    "no frame with given id",
)


class FlatCdpTargetRegistry:
    """Independent flattened CDP session registry for page/OOPIF routing.

    The transport intentionally uses its own browser-level websocket instead of
    SeleniumBase's Connection mapper so ARES never shares transaction IDs or
    mutates SeleniumBase's listener state.
    """

    def __init__(self, websocket_url: str, *, autostart: bool = True) -> None:
        self.websocket_url = str(websocket_url or "").strip()
        if autostart and not self.websocket_url:
            raise RuntimeError("Browser-level CDP websocket URL is unavailable")
        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws: Any = None
        self._stop_event: asyncio.Event | None = None
        self._pending: Dict[int, asyncio.Future[Any]] = {}
        self._next_id = 0
        self._startup_error: BaseException | None = None
        self._generation = 0
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._root_sessions: Dict[str, str] = {}
        self._root_frames: Dict[str, str] = {}
        self._frame_routes: Dict[str, Dict[str, Any]] = {}
        self._frame_contexts: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._session_frames: Dict[str, set[str]] = {}
        if autostart:
            self._thread = threading.Thread(target=self._thread_main, name="ares-oopif-cdp", daemon=True)
            self._thread.start()
            if not self._ready.wait(6.0):
                raise TimeoutError("OOPIF CDP registry did not become ready")
            if self._startup_error is not None:
                raise RuntimeError(f"OOPIF CDP registry failed to start: {self._startup_error}")

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._async_main())
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            finally:
                loop.close()
                self._closed.set()

    async def _async_main(self) -> None:
        self._stop_event = asyncio.Event()
        async with websockets.connect(self.websocket_url, ping_timeout=30, max_size=2**24, compression=None) as ws:
            self._ws = ws
            reader = asyncio.create_task(self._reader_loop())
            self._ready.set()
            await self._stop_event.wait()
            await ws.close()
            try:
                await reader
            except BaseException:
                pass

    async def _reader_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    message = json.loads(raw)
                except Exception:
                    continue
                if "id" in message:
                    request_id = int(message.get("id") or 0)
                    future = self._pending.get(request_id)
                    if future is not None and not future.done():
                        future.set_result(message)
                    continue
                self._handle_event(message)
        finally:
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(RuntimeError("OOPIF CDP websocket closed"))

    def _handle_event(self, message: Dict[str, Any]) -> None:
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        envelope_session = str(message.get("sessionId") or "")
        if method == "Target.attachedToTarget":
            session_id = str(params.get("sessionId") or "")
            target_info = params.get("targetInfo") if isinstance(params.get("targetInfo"), dict) else {}
            if session_id:
                self._register_session(
                    session_id,
                    str(target_info.get("targetId") or ""),
                    str(target_info.get("type") or ""),
                    parent_session_id=envelope_session or None,
                )
                if str(target_info.get("type") or "") in {"page", "iframe"} and self._loop is not None:
                    asyncio.create_task(self._initialize_session(session_id))
            return
        if method == "Target.detachedFromTarget":
            session_id = str(params.get("sessionId") or "")
            if session_id:
                self._detach_session(session_id)
            return
        if method == "Runtime.executionContextCreated":
            context = params.get("context") if isinstance(params.get("context"), dict) else {}
            aux = context.get("auxData") if isinstance(context.get("auxData"), dict) else {}
            if aux.get("isDefault") is True:
                frame_id = str(aux.get("frameId") or "")
                context_id = context.get("id")
                if envelope_session and frame_id and isinstance(context_id, (int, float)):
                    self._record_context(envelope_session, frame_id, int(context_id))
            return
        if method == "Runtime.executionContextDestroyed":
            context_id = params.get("executionContextId")
            if envelope_session and isinstance(context_id, (int, float)):
                self._destroy_context(envelope_session, int(context_id))
            return
        if method == "Runtime.executionContextsCleared" and envelope_session:
            self._clear_session_contexts(envelope_session)

    def _register_session(
        self,
        session_id: str,
        target_id: str,
        target_type: str,
        *,
        parent_session_id: str | None = None,
        generation: int | None = None,
    ) -> int:
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                return int(existing["generation"])
            if generation is None:
                self._generation += 1
                generation = self._generation
            else:
                self._generation = max(self._generation, int(generation))
            self._sessions[session_id] = {
                "sessionId": session_id,
                "targetId": target_id,
                "targetType": target_type,
                "parentSessionId": parent_session_id,
                "generation": int(generation),
                "initializing": False,
                "initialized": False,
                "error": None,
            }
            self._session_frames.setdefault(session_id, set())
            return int(generation)

    def _select_frame_route(self, frame_id: str) -> None:
        candidates = self._frame_contexts.get(frame_id, {})
        if not candidates:
            self._frame_routes.pop(frame_id, None)
            return
        winner = max(candidates.values(), key=lambda item: int(item.get("generation") or 0))
        self._frame_routes[frame_id] = dict(winner)

    def _record_context(self, session_id: str, frame_id: str, context_id: int) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                generation = self._register_session(session_id, "", "")
                session = self._sessions[session_id]
                session["generation"] = generation
            generation = int(session.get("generation") or 0)
            candidate = {
                "sessionId": session_id,
                "contextId": int(context_id),
                "generation": generation,
                "targetId": str(session.get("targetId") or ""),
            }
            self._frame_contexts.setdefault(frame_id, {})[session_id] = candidate
            self._session_frames.setdefault(session_id, set()).add(frame_id)
            self._select_frame_route(frame_id)

    def _destroy_context(self, session_id: str, context_id: int) -> None:
        with self._lock:
            for frame_id in list(self._session_frames.get(session_id, set())):
                candidates = self._frame_contexts.get(frame_id, {})
                candidate = candidates.get(session_id)
                if candidate is None or int(candidate.get("contextId") or -1) != int(context_id):
                    continue
                candidates.pop(session_id, None)
                if not candidates:
                    self._frame_contexts.pop(frame_id, None)
                self._session_frames.get(session_id, set()).discard(frame_id)
                self._select_frame_route(frame_id)

    def _clear_session_contexts(self, session_id: str) -> None:
        with self._lock:
            for frame_id in list(self._session_frames.get(session_id, set())):
                candidates = self._frame_contexts.get(frame_id, {})
                candidates.pop(session_id, None)
                if not candidates:
                    self._frame_contexts.pop(frame_id, None)
                self._select_frame_route(frame_id)
            self._session_frames.get(session_id, set()).clear()

    def _detach_session(self, session_id: str) -> None:
        # IMPORTANT: each frame keeps all live session candidates. Detaching an
        # old RFH removes only that session's candidate; if Chromium already
        # attached a replacement session for the same frameId, that newer route
        # remains selected. If a frame moves back in-process, the still-live root
        # session candidate becomes the fallback instead of disappearing.
        with self._lock:
            for frame_id in list(self._session_frames.get(session_id, set())):
                candidates = self._frame_contexts.get(frame_id, {})
                candidates.pop(session_id, None)
                if not candidates:
                    self._frame_contexts.pop(frame_id, None)
                self._select_frame_route(frame_id)
            self._session_frames.pop(session_id, None)
            self._sessions.pop(session_id, None)
            for target_id, current_session in list(self._root_sessions.items()):
                if current_session == session_id:
                    self._root_sessions.pop(target_id, None)
                    self._root_frames.pop(target_id, None)

    async def _initialize_session(self, session_id: str) -> None:
        owner = False
        for _ in range(250):
            with self._lock:
                session = self._sessions.get(session_id)
                if session is None:
                    return
                if bool(session.get("initialized")):
                    return
                if not bool(session.get("initializing")):
                    session["initializing"] = True
                    owner = True
                    break
            await asyncio.sleep(0.01)
        if not owner:
            raise TimeoutError(f"CDP session initialization timed out: {session_id}")
        try:
            # CDP domains are session-local. Child targets do not inherit these.
            await self._send_command("DOM.enable", {}, session_id=session_id)
            await self._send_command("Runtime.enable", {}, session_id=session_id)
            # Auto-attach is direct-child scoped, therefore repeat it for every
            # page/iframe session so nested OOPIFs are attached recursively.
            await self._send_command(
                "Target.setAutoAttach",
                {
                    "autoAttach": True,
                    "waitForDebuggerOnStart": False,
                    "flatten": True,
                },
                session_id=session_id,
            )
            with self._lock:
                session = self._sessions.get(session_id)
                if session is not None:
                    session["initialized"] = True
                    session["error"] = None
        except BaseException as exc:
            with self._lock:
                session = self._sessions.get(session_id)
                if session is not None:
                    session["error"] = str(exc)
            raise
        finally:
            with self._lock:
                session = self._sessions.get(session_id)
                if session is not None:
                    session["initializing"] = False

    async def _send_command(
        self,
        method: str,
        params: Dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("OOPIF CDP websocket is not connected")
        self._next_id += 1
        request_id = self._next_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = future
        payload: Dict[str, Any] = {"id": request_id, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        await self._ws.send(json.dumps(payload, separators=(",", ":")))
        try:
            message = await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(request_id, None)
        if isinstance(message, dict) and isinstance(message.get("error"), dict):
            error = message["error"]
            raise RuntimeError(f"{error.get('message') or 'CDP command failed'} [code: {error.get('code')}]")
        result = message.get("result") if isinstance(message, dict) else None
        return result if isinstance(result, dict) else {}

    def call(
        self,
        method: str,
        params: Dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        if self._startup_error is not None:
            raise RuntimeError(f"OOPIF CDP registry is unavailable: {self._startup_error}")
        if not self._ready.is_set() or self._loop is None:
            raise RuntimeError("OOPIF CDP registry is not ready")
        future = asyncio.run_coroutine_threadsafe(
            self._send_command(method, params, session_id=session_id, timeout=timeout),
            self._loop,
        )
        return future.result(timeout=timeout + 1.0)

    def _initialize_session_sync(self, session_id: str, timeout: float = 5.0) -> None:
        if self._loop is None:
            raise RuntimeError("OOPIF CDP registry loop is unavailable")
        future = asyncio.run_coroutine_threadsafe(self._initialize_session(session_id), self._loop)
        future.result(timeout=timeout + 1.0)

    def ensure_target(self, target_id: str) -> str:
        target_id = str(target_id or "").strip()
        if not target_id:
            raise RuntimeError("Active CDP target id is unavailable")
        with self._lock:
            session_id = self._root_sessions.get(target_id)
            if session_id and session_id in self._sessions:
                root_frame = self._root_frames.get(target_id)
                if root_frame:
                    return root_frame
            else:
                session_id = None
        if not session_id:
            result = self.call("Target.attachToTarget", {"targetId": target_id, "flatten": True})
            session_id = str(result.get("sessionId") or "")
            if not session_id:
                raise RuntimeError(f"Failed to attach flattened CDP session to target {target_id}")
            self._register_session(session_id, target_id, "page")
            with self._lock:
                self._root_sessions[target_id] = session_id
        self._initialize_session_sync(session_id)
        frame_tree = self.call("Page.getFrameTree", {}, session_id=session_id)
        frame = frame_tree.get("frameTree", {}).get("frame", {}) if isinstance(frame_tree.get("frameTree"), dict) else {}
        frame_id = str(frame.get("id") or "") if isinstance(frame, dict) else ""
        if not frame_id:
            raise RuntimeError(f"Root frame id is unavailable for target {target_id}")
        with self._lock:
            self._root_frames[target_id] = frame_id
        self._wait_for_route(frame_id, timeout=2.0)
        return frame_id

    def inject_init_script(self, target_id: str, source: str) -> bool:
        """Register a Page.addScriptToEvaluateOnNewDocument on the properly
        initialized, navigation-surviving root session for a target.

        Returns True when the script was registered, False otherwise.
        """
        target_id = str(target_id or "").strip()
        source = str(source or "").strip()
        if not target_id or not source:
            return False
        try:
            self.ensure_target(target_id)
            with self._lock:
                session_id = self._root_sessions.get(target_id)
            if not session_id:
                return False
            self.call(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": source, "runImmediately": True},
                session_id=session_id,
            )
            return True
        except Exception:
            return False

    def _route(self, frame_id: str) -> Dict[str, Any] | None:
        with self._lock:
            route = self._frame_routes.get(str(frame_id))
            return dict(route) if route is not None else None

    def _wait_for_route(self, frame_id: str, *, timeout: float = 1.5) -> Dict[str, Any]:
        deadline = time.monotonic() + max(0.05, timeout)
        while time.monotonic() < deadline:
            route = self._route(frame_id)
            if route is not None:
                return route
            time.sleep(0.01)
        raise RuntimeError(f"No live execution context is registered for frame {frame_id}")

    def _evaluate_expression(
        self,
        frame_id: str,
        expression: str,
        *,
        return_by_value: bool,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        last: BaseException | None = None
        for attempt in range(3):
            route = self._wait_for_route(frame_id, timeout=min(1.5, timeout))
            try:
                response = self.call(
                    "Runtime.evaluate",
                    {
                        "expression": expression,
                        "contextId": int(route["contextId"]),
                        "returnByValue": bool(return_by_value),
                        "awaitPromise": True,
                    },
                    session_id=str(route["sessionId"]),
                    timeout=timeout,
                )
                if response.get("exceptionDetails"):
                    details = response.get("exceptionDetails")
                    raise RuntimeError(f"Runtime.evaluate failed: {details}")
                return response
            except BaseException as exc:
                last = exc
                if not any(marker in str(exc).lower() for marker in _CONTEXT_ERRORS) or attempt >= 2:
                    raise
                time.sleep(0.03 * (attempt + 1))
        if last is not None:
            raise last
        raise RuntimeError("Runtime.evaluate failed without an error")

    def evaluate(self, frame_id: str, script: str, args: Iterable[Any]) -> Any:
        encoded_args = json.dumps(list(args), ensure_ascii=False, separators=(",", ":"))
        expression = f"(function(){{{script}}}).apply(null,{encoded_args})"
        response = self._evaluate_expression(frame_id, expression, return_by_value=True)
        remote = response.get("result") if isinstance(response.get("result"), dict) else {}
        if "value" in remote:
            return remote.get("value")
        if remote.get("type") == "undefined":
            return None
        return remote.get("description")

    def _child_frame_id(self, frame_id: str, selector: str) -> str:
        selector_json = json.dumps(str(selector), ensure_ascii=False)
        response = self._evaluate_expression(
            frame_id,
            f"document.querySelector({selector_json})",
            return_by_value=False,
        )
        remote = response.get("result") if isinstance(response.get("result"), dict) else {}
        object_id = str(remote.get("objectId") or "")
        if not object_id:
            raise LookupError(f"Frame path no longer resolves at {selector}")
        route = self._wait_for_route(frame_id)
        session_id = str(route["sessionId"])
        try:
            node_result = self.call("DOM.requestNode", {"objectId": object_id}, session_id=session_id)
            node_id = node_result.get("nodeId")
            if not isinstance(node_id, (int, float)):
                raise LookupError(f"Frame node id is unavailable at {selector}")
            description = self.call(
                "DOM.describeNode",
                {"nodeId": int(node_id), "depth": 0, "pierce": True},
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

    def resolve_path(
        self,
        target_id: str,
        frame_path: Iterable[str],
        *,
        include_offsets: bool,
    ) -> tuple[str, float, float]:
        current_frame = self.ensure_target(target_id)
        offset_x = 0.0
        offset_y = 0.0
        for selector in [str(value) for value in frame_path if str(value)]:
            if include_offsets:
                geometry = self.evaluate(
                    current_frame,
                    """
const selector=String(arguments[0]||'');
const frame=document.querySelector(selector);
if(!frame) return {missing:true};
frame.scrollIntoView({block:'nearest',inline:'nearest'});
const r=frame.getBoundingClientRect();
return {x:r.left+Number(frame.clientLeft||0),y:r.top+Number(frame.clientTop||0),width:r.width,height:r.height};
""",
                    [selector],
                )
                if not isinstance(geometry, dict) or geometry.get("missing"):
                    raise LookupError(f"Frame path no longer resolves at {selector}")
                if float(geometry.get("width") or 0.0) <= 0 or float(geometry.get("height") or 0.0) <= 0:
                    raise LookupError(f"Frame is not visible for native click: {selector}")
                offset_x += float(geometry.get("x") or 0.0)
                offset_y += float(geometry.get("y") or 0.0)
            current_frame = self._child_frame_id(current_frame, selector)
        return current_frame, offset_x, offset_y

    def discover(self, target_id: str, *, limit: int) -> List[Dict[str, Any]]:
        root_frame = self.ensure_target(target_id)
        queue_items: List[tuple[List[str], str]] = [([], root_frame)]
        entries: Dict[tuple[str, ...], Dict[str, Any]] = {}
        visited: set[tuple[str, ...]] = set()
        while queue_items and len(entries) < limit:
            path, frame_id = queue_items.pop(0)
            key = tuple(path)
            if key in visited:
                continue
            visited.add(key)
            try:
                current_url = str(self.evaluate(frame_id, "return window.location.href;", []) or "")
                descriptors = self.evaluate(frame_id, base._FRAME_DESCRIPTORS_SCRIPT, [])
            except Exception:
                continue
            if path and key in entries and current_url:
                entries[key]["url"] = current_url
            for raw in descriptors if isinstance(descriptors, list) else []:
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
                if len(entries) >= limit:
                    continue
                try:
                    child_frame = self._child_frame_id(frame_id, selector)
                except Exception:
                    continue
                queue_items.append((child_path, child_frame))
        return list(entries.values())

    def close(self) -> None:
        if self._closed.is_set():
            return
        loop = self._loop
        if loop is not None and self._stop_event is not None and loop.is_running():
            def stop() -> None:
                if self._stop_event is not None:
                    self._stop_event.set()
            loop.call_soon_threadsafe(stop)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)


class OopifTaskRpcRuntime(base.TaskRpcRuntime):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        driver = getattr(self.sb, "driver", None)
        if driver is not None and hasattr(driver, "cdp_base"):
            driver = driver.cdp_base
        websocket_url = str(getattr(driver, "websocket_url", "") or "")
        self._oopif_registry = FlatCdpTargetRegistry(websocket_url)
        atexit.register(self._oopif_registry.close)

    def _active_target_id(self) -> str:
        tab = self.sb.get_active_tab()
        target_id = getattr(tab, "target_id", None)
        if target_id is None:
            target = getattr(tab, "target", None)
            target_id = getattr(target, "target_id", None)
        value = str(target_id or "").strip()
        if not value:
            raise RuntimeError("Active SeleniumBase CDP target id is unavailable")
        return value

    def _execute_script_in_frame_path(self, frame_path: Iterable[str], script: str, args: Iterable[Any]) -> Any:
        path = [str(value) for value in frame_path if str(value)]
        if not path:
            return super()._execute_script_in_frame_path(path, script, args)
        frame_id, _, _ = self._oopif_registry.resolve_path(
            self._active_target_id(),
            path,
            include_offsets=False,
        )
        return self._oopif_registry.evaluate(frame_id, script, args)

    def _frame_viewport_offset(self, frame_path: Iterable[str]) -> tuple[float, float]:
        path = [str(value) for value in frame_path if str(value)]
        if not path:
            return 0.0, 0.0
        _, x, y = self._oopif_registry.resolve_path(
            self._active_target_id(),
            path,
            include_offsets=True,
        )
        return x, y

    def _discover_frame_tree(self) -> List[Dict[str, Any]]:
        try:
            return self._oopif_registry.discover(
                self._active_target_id(),
                limit=base.MAX_DISCOVERED_FRAMES,
            )
        except Exception:
            # Preserve the existing fail-closed/same-origin discovery behavior
            # if the independent registry is transiently unavailable.
            return super()._discover_frame_tree()


def main() -> int:
    original = base.TaskRpcRuntime
    base.TaskRpcRuntime = OopifTaskRpcRuntime
    try:
        return base.main()
    finally:
        base.TaskRpcRuntime = original


if __name__ == "__main__":
    raise SystemExit(main())
