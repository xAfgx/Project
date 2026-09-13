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
        # child frameId -> parent frameId, recorded while resolving frame paths.
        self._frame_parents: Dict[str, str] = {}
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
            # waitForDebuggerOnStart pauses every new child until it is prepared:
            # workers receive the fingerprint script (Target.attachedToTarget
            # handler) before they run, iframes/pages are resumed right after
            # this initialization. Excluding service/shared workers keeps
            # browser-level targets out of the page-scoped pause.
            await self._send_command(
                "Target.setAutoAttach",
                {
                    "autoAttach": True,
                    "waitForDebuggerOnStart": True,
                    "flatten": True,
                    "filter": [
                        {"type": "worker", "exclude": False},
                        {"type": "iframe", "exclude": False},
                        {"type": "page", "exclude": False},
                        {"type": "service_worker", "exclude": True},
                        {"type": "shared_worker", "exclude": True},
                    ],
                },
                session_id=session_id,
            )
            # The session itself may be a paused child target; resume it now that
            # DOM/Runtime are enabled and its own children are wired up.
            try:
                await self._send_command("Runtime.runIfWaitingForDebugger", {}, session_id=session_id)
            except Exception:
                pass
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

    def frame_parent(self, frame_id: str) -> str:
        with self._lock:
            return str(self._frame_parents.get(str(frame_id)) or "")

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
            with self._lock:
                self._frame_parents[child_frame_id] = str(frame_id)
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
        self._scroll_profile: Dict[str, float] = {}
        driver = getattr(self.sb, "driver", None)
        if driver is not None and hasattr(driver, "cdp_base"):
            driver = driver.cdp_base
        websocket_url = str(getattr(driver, "websocket_url", "") or "")
        self._oopif_registry = FlatCdpTargetRegistry(websocket_url)
        atexit.register(self._oopif_registry.close)

    def set_scroll_profile(self, profile: Any) -> None:
        """Optional per-task scroll tuning; empty keeps the default motion.

        Modules opt in explicitly (e.g. a slower, followable wheel), so the
        shared scroll system stays byte-identical for every other module.
        """
        if not isinstance(profile, dict):
            self._scroll_profile = {}
            return
        clean: Dict[str, float] = {}
        for key in (
            "speedMin", "speedMax",
            "stepMin", "stepMax",
            "chunkMin", "chunkMax",
            "pauseMin", "pauseMax",
            "settleMin", "settleMax",
        ):
            try:
                value = float(profile.get(key))
            except (TypeError, ValueError):
                continue
            clean[key] = value
        self._scroll_profile = clean

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

    def add_init_script(self, script: str) -> Dict[str, Any]:
        """Register the init script on the navigation-surviving root session.

        SeleniumBase's own active-tab session is dropped and re-created by queue
        redirects / CDP session recovery, which also discards the
        Page.addScriptToEvaluateOnNewDocument registration. The page then falls
        back to the real hardware fingerprint while workers stay spoofed.
        Registering through the OOPIF registry's properly initialized root
        session keeps the script bound to the target across navigations and
        session rebuilds.
        """
        source = str(script or "")
        if not source.strip():
            return {"result": False}
        if str(base.os.environ.get("ARES_DISABLE_OOPIF_INIT_SCRIPT") or "").strip() == "1":
            return super().add_init_script(source)
        registry = getattr(self, "_oopif_registry", None)
        if registry is not None:
            try:
                if registry.inject_init_script(self._active_target_id(), source):
                    return {"result": True, "identifier": "oopif-root"}
            except Exception:
                pass
        return super().add_init_script(source)

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

    def _resolve_native_node(
        self,
        locator: Dict[str, Any],
        selector: str,
        nth: int,
        text_spec: Dict[str, str] | None,
    ) -> tuple[int, str]:
        if not selector:
            raise ValueError("locator selector is empty")
        if text_spec and text_spec.get("source") is not None:
            raise LookupError("text-filtered locator requires the JS path")
        path = [str(value) for value in locator.get("framePath") or [] if str(value)]
        frame_id, _, _ = self._oopif_registry.resolve_path(
            self._active_target_id(),
            path,
            include_offsets=False,
        )
        route = self._oopif_registry._wait_for_route(frame_id)
        session_id = str(route["sessionId"])
        # A same-process child frame shares its parent's CDP session, so the
        # session document is the parent. Resolve the child frame's own
        # contentDocument root before querying, otherwise fields inside the
        # credit-card iframe are never found.
        root_node_id = self._document_root_for_frame(session_id, frame_id)
        if root_node_id <= 0:
            document = self._oopif_registry.call(
                "DOM.getDocument",
                {"depth": 0, "pierce": True},
                session_id=session_id,
            )
            root = document.get("root") if isinstance(document.get("root"), dict) else {}
            root_node_id = int(root.get("nodeId") or 0)
        if root_node_id <= 0:
            raise RuntimeError("DOM root node is unavailable")
        if isinstance(nth, int) and nth >= 0:
            found = self._oopif_registry.call(
                "DOM.querySelectorAll",
                {"nodeId": root_node_id, "selector": selector},
                session_id=session_id,
            )
            node_ids = found.get("nodeIds") if isinstance(found.get("nodeIds"), list) else []
            node_id = int(node_ids[nth]) if nth < len(node_ids) else 0
        else:
            found = self._oopif_registry.call(
                "DOM.querySelector",
                {"nodeId": root_node_id, "selector": selector},
                session_id=session_id,
            )
            node_id = int(found.get("nodeId") or 0)
        if node_id <= 0:
            # Read-only fallback: resolve the node through the frame's own
            # execution context, then convert the remote object to a native
            # nodeId. Focus/typing stay native; only the lookup uses JS.
            try:
                selector_json = base.json.dumps(selector, ensure_ascii=False)
                response = self._oopif_registry._evaluate_expression(
                    frame_id,
                    f"document.querySelector({selector_json})",
                    return_by_value=False,
                )
                remote = response.get("result") if isinstance(response.get("result"), dict) else {}
                object_id = str(remote.get("objectId") or "")
                if object_id:
                    node_result = self._oopif_registry.call(
                        "DOM.requestNode",
                        {"objectId": object_id},
                        session_id=session_id,
                    )
                    node_id = int(node_result.get("nodeId") or 0)
            except Exception:
                node_id = 0
        self._wheel_log(
            f"native-node path={path} frame={str(frame_id)[:8]} root={root_node_id} "
            f"selector={selector} found={node_id}"
        )
        if node_id <= 0:
            raise LookupError(f"No element matched locator: {selector}")
        return node_id, session_id

    def _locator_op(self, action: str, locator: Dict[str, Any], command: Dict[str, Any]) -> Any:
        # Native, smooth scroll through CDP mouseWheel ticks. No JavaScript and
        # no instant jump: only scrolls when the field is outside the viewport,
        # then moves in small wheel steps like a human.
        if action == "scroll-into-view":
            try:
                selector = str(locator.get("selector") or "")
                nth = int(locator.get("nth")) if isinstance(locator.get("nth"), (int, float)) else -1
                text_spec = base.pattern_payload(locator.get("hasText"))
                frame_path = [str(value) for value in locator.get("framePath") or [] if str(value)]
                # Native wheel scroll (CDP mouseWheel), no JavaScript. The field
                # position is taken from the same source the click uses, so the
                # nested payment iframe can no longer be scrolled past.
                if not self._smooth_scroll_into_view(selector, nth, text_spec, frame_path):
                    node_id, session_id = self._resolve_native_node(locator, selector, nth, text_spec)
                    self._oopif_registry.call(
                        "DOM.scrollIntoViewIfNeeded",
                        {"nodeId": node_id},
                        session_id=session_id,
                    )
                return True
            except Exception:
                return super()._locator_op(action, locator, command)
        return super()._locator_op(action, locator, command)
        return super()._locator_op(action, locator, command)

    def _wheel_log(self, message: str) -> None:
        # Debug hook retained for local tracing; intentionally silent.
        return

    def _document_scroll_y(self) -> float:
        """Read the main document's scroll offset through CDP only (no JS)."""
        try:
            root_frame = self._oopif_registry.ensure_target(self._active_target_id())
            route = self._oopif_registry._wait_for_route(root_frame)
            session_id = str(route["sessionId"])
            metrics = self._oopif_registry.call("Page.getLayoutMetrics", {}, session_id=session_id)
        except Exception:
            return 0.0
        for key in ("visualViewport", "cssLayoutViewport"):
            viewport = metrics.get(key)
            if isinstance(viewport, dict):
                try:
                    return float(viewport.get("pageY") or 0.0)
                except (TypeError, ValueError):
                    continue
        return 0.0

    def _frame_parent_map(self) -> Dict[str, str]:
        """Build a global child->parent frame map from every session's tree.

        Page.frameAttached events are not guaranteed for frames that were
        created before Page.enable reached their session, which left nested
        OOPIF offsets incomplete (a nested payment field measured as
        permanently off-screen). Walking Page.getFrameTree on each live session
        reconstructs the full chain for same-process and OOPIF frames alike.
        """
        parents: Dict[str, str] = {}
        sessions = getattr(self._oopif_registry, "_sessions", None)
        if not isinstance(sessions, dict):
            return parents
        for session_id in list(sessions.keys()):
            try:
                tree = self._oopif_registry.call(
                    "Page.getFrameTree",
                    {},
                    session_id=str(session_id),
                    timeout=1.0,
                )
            except Exception:
                continue
            stack = [tree.get("frameTree")] if isinstance(tree.get("frameTree"), dict) else []
            while stack:
                node = stack.pop()
                if not isinstance(node, dict):
                    continue
                frame = node.get("frame") if isinstance(node.get("frame"), dict) else {}
                frame_id = str(frame.get("id") or "")
                parent_id = str(frame.get("parentId") or "")
                if frame_id and parent_id:
                    parents.setdefault(frame_id, parent_id)
                for child in node.get("childFrames") or []:
                    if isinstance(child, dict):
                        stack.append(child)
        return parents

    def _frame_offset_in_main(self, field_frame_id: str) -> tuple[float, float]:
        """Accumulate iframe offsets up the frame tree, natively.

        Page.getFrameTree builds the parent map, then DOM.getFrameOwner +
        DOM.getContentQuads run on each parent frame session. Both the field
        quads and the owner quads are viewport-relative, so the accumulated
        offset is in the main viewport's coordinate space (DOM.getBoxModel
        returns document coordinates and made iframe fields measure as
        permanently off-screen). No page JavaScript and no scroll side effects.
        """
        root_frame = self._oopif_registry.ensure_target(self._active_target_id())
        parents = self._frame_parent_map()
        self._wheel_log(
            f"parent-map size={len(parents)} field={str(field_frame_id)[:8]} root={str(root_frame)[:8]}"
        )
        offset_x = 0.0
        offset_y = 0.0
        current = str(field_frame_id)
        guard = 0
        while current and current != root_frame and guard < 12:
            parent_frame = parents.get(current) or self._oopif_registry.frame_parent(current)
            self._wheel_log(f"chain {current[:8]} -> {str(parent_frame)[:8] if parent_frame else '-'}")
            if not parent_frame:
                self._wheel_log(f"no-parent frame={current[:8]}")
                break
            parent_route = self._oopif_registry._wait_for_route(parent_frame)
            parent_session = str(parent_route["sessionId"])
            owner = self._oopif_registry.call(
                "DOM.getFrameOwner",
                {"frameId": current},
                session_id=parent_session,
            )
            owner_node = int(owner.get("nodeId") or 0)
            if owner_node <= 0:
                # Cross-process (OOPIF) frame owners are not always resolvable
                # through the DOM domain. Locate the hosting iframe element by
                # matching its frameId instead.
                owner_node = self._find_iframe_node_by_frame_id(parent_session, current)
            if owner_node <= 0:
                self._wheel_log(f"no-owner frame={current[:8]} parent={parent_frame[:8]}")
                break
            quads_reply = self._oopif_registry.call(
                "DOM.getContentQuads",
                {"nodeId": owner_node},
                session_id=parent_session,
            )
            quads = quads_reply.get("quads") if isinstance(quads_reply.get("quads"), list) else []
            if not quads or not isinstance(quads[0], list) or len(quads[0]) < 8:
                self._wheel_log(f"no-box frame={current[:8]}")
                break
            quad = [float(value) for value in quads[0][:8]]
            bxs = quad[0::2]
            bys = quad[1::2]
            offset_x += min(bxs)
            offset_y += min(bys)
            current = parent_frame
            guard += 1
        return offset_x, offset_y

    def _find_iframe_node_by_frame_id(self, session_id: str, frame_id: str) -> int:
        """Find the hosting <iframe>/<frame> node for a child frame, natively."""
        try:
            document = self._oopif_registry.call(
                "DOM.getDocument",
                {"depth": 0, "pierce": False},
                session_id=session_id,
            )
            root = document.get("root") if isinstance(document.get("root"), dict) else {}
            root_node = int(root.get("nodeId") or 0)
            if root_node <= 0:
                return 0
            found = self._oopif_registry.call(
                "DOM.querySelectorAll",
                {"nodeId": root_node, "selector": "iframe,frame"},
                session_id=session_id,
            )
            node_ids = found.get("nodeIds") if isinstance(found.get("nodeIds"), list) else []
            for raw_node in node_ids:
                node_id = int(raw_node)
                described = self._oopif_registry.call(
                    "DOM.describeNode",
                    {"nodeId": node_id},
                    session_id=session_id,
                )
                node = described.get("node") if isinstance(described.get("node"), dict) else {}
                if str(node.get("frameId") or "") == str(frame_id):
                    return node_id
        except Exception:
            return 0
        return 0

    def _document_root_for_frame(self, session_id: str, frame_id: str) -> int:
        """Return the contentDocument root node for a child frame, natively.

        Same-process child frames share the parent's session, so their nodes are
        not reachable from the session's own document root.
        """
        for attempt in range(4):
            try:
                owner = self._oopif_registry.call(
                    "DOM.getFrameOwner",
                    {"frameId": frame_id},
                    session_id=session_id,
                )
                owner_node = int(owner.get("nodeId") or 0)
                if owner_node > 0:
                    described = self._oopif_registry.call(
                        "DOM.describeNode",
                        {"nodeId": owner_node, "depth": 1, "pierce": True},
                        session_id=session_id,
                    )
                    node = described.get("node") if isinstance(described.get("node"), dict) else {}
                    content = node.get("contentDocument")
                    if isinstance(content, dict):
                        return int(content.get("nodeId") or 0)
            except Exception:
                pass
            if attempt < 3:
                time.sleep(0.2 * (attempt + 1))
        return 0

    def _smooth_scroll_into_view(self, selector: str, nth: int, text_spec: Dict[str, str] | None, frame_path: List[str]) -> bool:
        """Wheel-scroll so the field becomes visible, using native CDP mouseWheel.

        The position comes from the same source the click uses: the field's own
        getBoundingClientRect inside its frame plus the frame's offset in the
        main viewport. DOM.getContentQuads reports a different coordinate basis
        for nested iframes, and that mismatch made the wheel overshoot the field.

        Returns False when geometry cannot be resolved (caller falls back to
        DOM.scrollIntoViewIfNeeded).
        """
        try:
            root_frame = self._oopif_registry.ensure_target(self._active_target_id())
            root_route = self._oopif_registry._wait_for_route(root_frame)
            root_session = str(root_route["sessionId"])
            metrics = self._oopif_registry.call("Page.getLayoutMetrics", {}, session_id=root_session)
            viewport = metrics.get("visualViewport") if isinstance(metrics.get("visualViewport"), dict) else {}
            if not viewport:
                viewport = metrics.get("cssLayoutViewport") if isinstance(metrics.get("cssLayoutViewport"), dict) else {}
            width = float(viewport.get("clientWidth") or 0.0)
            height = float(viewport.get("clientHeight") or 0.0)
            if width <= 0 or height <= 0:
                self._wheel_log("no-viewport")
                return False

            for attempt in range(8):
                # Same position source as the click: the field's own
                # getBoundingClientRect inside its frame plus the frame offset in
                # the main viewport. DOM.getContentQuads uses a different
                # coordinate basis for nested iframes, which made the wheel
                # overshoot the field.
                scroll_y = self._document_scroll_y()
                offset_x, offset_y = self._frame_viewport_offset(frame_path)
                probe = self._execute_script_retry(base.locator_script(), selector, nth, text_spec, "bounding-box", {})
                if not isinstance(probe, dict):
                    self._wheel_log("no-box")
                    return False
                top = offset_y + float(probe.get("y") or 0.0)
                bottom = top + float(probe.get("height") or 0.0)
                center_x = offset_x + float(probe.get("x") or 0.0) + float(probe.get("width") or 0.0) / 2.0
                # Generous margin so the wheel scrolls the field well inside the
                # viewport and DOM.focus afterwards cannot move it again.
                margin = 110.0
                below = bottom > height - margin
                above = top < margin
                self._wheel_log(
                    f"try{attempt} scrollY={scroll_y:.0f} "
                    f"top={top:.0f} bottom={bottom:.0f} below={below} above={above}"
                )
                if not below and not above:
                    return True
                # A sticky/fixed header sits above the margin but is already
                # fully visible and the document cannot scroll up any further.
                # Treat it as in view instead of looping wheel gestures that
                # cannot move the page (this previously stalled the search field).
                if above and scroll_y <= 1.0 and top >= 0:
                    self._wheel_log(f"already-visible-top scrollY={scroll_y:.0f} top={top:.0f}")
                    return True
                # Minimal correction only: scroll just enough to bring the field
                # inside the viewport. Centering required a large scroll whose
                # frame-offset math could overshoot (and looked like scrolling
                # past the field), so the small re-measured correction is used
                # everywhere.
                scroll_amount = (bottom - (height - margin)) if below else (top - margin)
                # Fields already essentially centered are left alone; the tiny
                # correction is not worth a visible scroll.
                if abs(scroll_amount) < 40:
                    return True
                # A person scrolls in short flicks, not one long fling: cap each
                # gesture and let the loop cover the remaining distance with a
                # short pause in between. Defaults are the established motion;
                # a module may opt into its own profile via set-scroll-profile.
                scroll_profile = getattr(self, "_scroll_profile", {}) or {}
                max_step = base.random.uniform(
                    float(scroll_profile.get("stepMin", 550.0)),
                    float(scroll_profile.get("stepMax", 900.0)),
                )
                if abs(scroll_amount) > max_step:
                    scroll_amount = max_step if scroll_amount > 0 else -max_step
                # Native wheel events over the main-page margin (x=10) at the
                # viewport center: reliably over the main page, unlike the
                # element position which can sit on a non-scrolling footer.
                wheel_x = 10.0
                wheel_y = height * 0.5
                self._wheel_log(
                    f"wheel scroll={scroll_amount:.0f} x={wheel_x:.0f} y={wheel_y:.0f}"
                )
                input_domain = getattr(base, "mycdp", None)
                dispatch = getattr(getattr(input_domain, "input_", None), "dispatch_mouse_event", None)
                if not callable(dispatch):
                    self._wheel_log("no-dispatch")
                    return False
                tab = self.sb.get_active_tab()
                loop = self.sb.get_event_loop()
                # Preferred: momentum scroll through Input.synthesizeScrollGesture
                # (mouse source). Real input accelerates, glides and settles, so
                # wheel deltas vary naturally instead of arriving on a fixed
                # rhythm. Falls back to the irregular wheel ticks when the
                # gesture is unavailable or the document did not move.
                if self._synthesize_scroll_gesture(tab, loop, wheel_x, wheel_y, scroll_amount, scroll_y):
                    continue
                # Irregular human wheel: random chunk sizes and pauses instead
                # of a fixed rhythm.
                # Small, irregular wheel chunks so the movement is visible and
                # human-like instead of one instant jump.
                self._wheel_diag(f"wheel-fallback amount={scroll_amount:.0f}")
                remaining = scroll_amount
                guard = 0
                while abs(remaining) > 4 and guard < 30:
                    chunk = min(abs(remaining), base.random.uniform(
                        float(scroll_profile.get("chunkMin", 70.0)),
                        float(scroll_profile.get("chunkMax", 170.0)),
                    ))
                    if remaining < 0:
                        chunk = -chunk
                    chunk = int(chunk) or (1 if chunk > 0 else -1)
                    loop.run_until_complete(tab.send(dispatch(
                        type_="mouseWheel",
                        x=wheel_x,
                        y=wheel_y,
                        delta_x=0,
                        delta_y=chunk,
                        modifiers=0,
                    )))
                    remaining -= chunk
                    guard += 1
                    time.sleep(base.random.uniform(
                        float(scroll_profile.get("pauseMin", 0.04)),
                        float(scroll_profile.get("pauseMax", 0.13)),
                    ))
                time.sleep(float(scroll_profile.get("settleMin", 0.2)) + 0.05)
            return True
        except Exception as exc:
            self._wheel_log(f"error={type(exc).__name__}:{str(exc)[:200]}")
            return False

    def _synthesize_scroll_gesture(
        self,
        tab: Any,
        loop: Any,
        x: float,
        y: float,
        amount: float,
        before_scroll_y: float,
    ) -> bool:
        """Momentum scroll via CDP Input.synthesizeScrollGesture (mouse source).

        Real input accelerates, glides and settles, so wheel deltas vary
        naturally instead of arriving on a fixed rhythm. Returns True only when
        the gesture was accepted and the document actually moved, so the caller
        can fall back to the deterministic random wheel ticks otherwise.
        """
        try:
            from mycdp import input_ as cdp_input
        except Exception:
            return False
        synthesize = getattr(cdp_input, "synthesize_scroll_gesture", None)
        if not callable(synthesize):
            return False
        scroll_profile = getattr(self, "_scroll_profile", {}) or {}
        speed = int(base.random.uniform(
            float(scroll_profile.get("speedMin", 450.0)),
            float(scroll_profile.get("speedMax", 950.0)),
        ))
        try:
            loop.run_until_complete(tab.send(synthesize(
                x=float(x),
                y=float(y),
                # yDistance is positive to scroll up; `amount` is positive down.
                y_distance=-float(amount),
                # No fling: momentum kept overshooting the field and DOM.focus
                # then snapped back, which looked like a jump. The gesture still
                # accelerates/decelerates, so the wheel deltas stay varied.
                prevent_fling=True,
                speed=speed,
                gesture_source_type=cdp_input.GestureSourceType.MOUSE,
            )))
        except Exception as exc:
            self._wheel_diag(f"synthesize-failed {type(exc).__name__}:{str(exc)[:140]}")
            return False
        time.sleep(base.random.uniform(
            float(scroll_profile.get("settleMin", 0.12)),
            float(scroll_profile.get("settleMax", 0.28)),
        ))
        after_scroll_y = self._document_scroll_y()
        moved = abs(after_scroll_y - float(before_scroll_y)) >= 4.0
        self._wheel_diag(
            f"synthesize-{'ok' if moved else 'no-move'} amount={amount:.0f} speed={speed} "
            f"scrollY={before_scroll_y:.0f}->{after_scroll_y:.0f}"
        )
        return moved

    @staticmethod
    def _wheel_diag(message: str) -> None:
        try:
            import os
            import tempfile

            path = os.path.join(tempfile.gettempdir(), "ares-wheel-diag.log")
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(f"{base.time.time():.3f} {message}\n")
        except Exception:
            pass
    def _native_focus_locator(
        self,
        locator: Dict[str, Any],
        selector: str,
        nth: int,
        text_spec: Dict[str, str] | None,
    ) -> None:
        """Focus through the CDP DOM domain.

        DOM.focus is frame-local and reliably targets the intended field, unlike
        viewport-coordinate clicks inside the tall checkout iframe. The engine
        scrolls with DOM.scrollIntoViewIfNeeded first, so focus does not move.
        """
        try:
            node_id, session_id = self._resolve_native_node(locator, selector, nth, text_spec)
            self._oopif_registry.call("DOM.focus", {"nodeId": node_id}, session_id=session_id)
        except Exception as exc:
            self._wheel_log(f"focus-fallback selector={selector} err={type(exc).__name__}:{str(exc)[:160]}")
            super()._native_focus_locator(locator, selector, nth, text_spec)

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
