from __future__ import annotations

import asyncio
import json
import math
import os
import random
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from mycdp import input_ as cdp_input

Point = Tuple[float, float]


class CursorPathProvider:
    """Plan and play smooth cursor paths through the existing CDP session.

    Every runtime instance owns a private in-memory RNG. Motion is generated
    parametrically per action instead of selecting from a small set of fixed
    profiles. A caller may supply its existing task/profile seed namespace so
    parallel tasks keep independent, reproducible motion streams.
    """

    def __init__(self, *, helper_path: str | Path | None = None, seed: str | int | None = None) -> None:
        self._helper = Path(helper_path).expanduser().resolve() if helper_path else Path(__file__).with_name("cursor_path_helper.cjs")
        self._node = os.environ.get("ARES_NODE_EXECUTABLE", "node").strip() or "node"
        runtime_seed = os.environ.get("ARES_INTERACTION_SEED", "").strip()
        effective_seed: str | int = runtime_seed if runtime_seed else (seed if seed is not None else int.from_bytes(os.urandom(32), "big"))
        self._runtime_seeded = bool(runtime_seed)
        self._rng = random.Random(effective_seed)
        self._movement_index = 0

    def plan(self, start: Point, end: Point, *, preferred: str = "ghost-cursor") -> Dict[str, Any]:
        start = (float(start[0]), float(start[1]))
        end = (float(end[0]), float(end[1]))
        # Prefer the external GhostCursor helper for its organic motion. The
        # task-scoped seed still keeps the Python Bezier fallback (and the
        # sessionized tremor) deterministic when the helper is unavailable.
        external = self._external(start, end, preferred=preferred)
        if external:
            external["points"] = self._sessionize_points(external.get("points") or [], start, end)
            return external
        points = self._sessionize_points(self._python_bezier(start, end), start, end)
        return {"provider": "python-bezier", "points": points}

    def _sessionize_points(self, values: Iterable[Any], start: Point, end: Point) -> List[Point]:
        points = self._clean_points(values)
        if len(points) < 3:
            return points

        self._movement_index += 1
        dx, dy = end[0] - start[0], end[1] - start[1]
        distance = max(1.0, math.hypot(dx, dy))
        nx, ny = -dy / distance, dx / distance
        amplitude = self._rng.uniform(0.35, 1.55)
        phase = self._rng.uniform(-0.55, 0.55)
        secondary_phase = self._rng.uniform(0.0, math.tau)
        result: List[Point] = [start]
        denominator = max(1, len(points) - 1)
        sigma = self._rng.uniform(0.55, 1.35)

        for index, (x, y) in enumerate(points[1:-1], start=1):
            t = index / denominator
            envelope = 4.0 * t * (1.0 - t)
            primary = math.sin(math.pi * t + phase)
            secondary = 0.22 * math.sin(math.tau * t + secondary_phase)
            # Gaussian tremor that shrinks as the cursor approaches the target.
            micro = self._rng.gauss(0.0, sigma * (1.0 - 0.75 * t))
            offset = envelope * (amplitude * (primary + secondary) + micro)
            result.append((x + nx * offset, y + ny * offset))

        result.append(end)
        return self._clean_points(result)

    def random_start(self, width: float = 1024.0, height: float = 768.0) -> Point:
        """Return a plausible off-target cursor origin inside the viewport."""
        return (
            self._rng.uniform(float(width) * 0.06, float(width) * 0.94),
            self._rng.uniform(float(height) * 0.08, float(height) * 0.92),
        )

    def scroll_random(self, seleniumbase_cdp: Any, *, max_steps: int = 2) -> None:
        """Emit small, human-looking mouseWheel scrolls via CDP before an action."""
        context = self._cdp_context(seleniumbase_cdp)
        if context is None:
            return
        tab, loop = context

        async def scroll() -> None:
            button = cdp_input.MouseButton("left")
            steps = self._rng.randint(1, max_steps)
            for _ in range(steps):
                x = self._rng.uniform(220.0, 900.0)
                y = self._rng.uniform(160.0, 620.0)
                await tab.send(cdp_input.dispatch_mouse_event("mouseMoved", x=x, y=y, button=button, buttons=0))
                await asyncio.sleep(self._rng.uniform(0.02, 0.05))
                delta_y = self._rng.randint(-90, 90)
                await tab.send(cdp_input.dispatch_mouse_event("mouseWheel", x=x, y=y, delta_x=0, delta_y=delta_y))
                await asyncio.sleep(self._rng.uniform(0.04, 0.10))

        try:
            loop.run_until_complete(scroll())
        except Exception:
            pass

    def play_click(self, seleniumbase_cdp: Any, start: Point, end: Point, *, preferred: str = "ghost-cursor") -> Dict[str, Any]:
        plan = self.plan(start, end, preferred=preferred)
        points = self._clean_points(plan.get("points") or [])
        provider = str(plan.get("provider") or "path")
        if not points:
            return {"clicked": False, "provider": provider, "pointCount": 0, "error": "no-points"}
        clicked, error = self._play_cdp_click(seleniumbase_cdp, points)
        if clicked:
            return {"clicked": True, "provider": f"{provider}:cdp", "pointCount": len(points)}
        return {"clicked": False, "provider": provider, "pointCount": len(points), "error": error}

    def _motion_parameters(self, *, end_hold_backtrack: bool) -> Dict[str, float]:
        """Generate one bounded, solve-safe motion recipe for a single drag."""
        return {
            "prePress": self._rng.uniform(0.026, 0.064),
            "postPress": self._rng.uniform(0.038, 0.082),
            "baseDelay": self._rng.uniform(0.0075, 0.0135),
            "acceleration": self._rng.uniform(0.004, 0.015),
            "wave": self._rng.uniform(0.0, 0.0045),
            "waveCycles": self._rng.uniform(0.75, 2.15),
            "endHold": self._rng.uniform(0.09, 0.18) if end_hold_backtrack else 0.0,
            "backtrackPx": self._rng.uniform(0.8, 2.2) if end_hold_backtrack else 0.0,
            "backtrackHold": self._rng.uniform(0.038, 0.078) if end_hold_backtrack else 0.0,
        }

    def play_drag(
        self,
        seleniumbase_cdp: Any,
        start: Point,
        end: Point,
        *,
        preferred: str = "ghost-cursor",
        gui_start: Point | None = None,
        gui_end: Point | None = None,
        end_hold_backtrack: bool = False,
    ) -> Dict[str, Any]:
        plan = self.plan(start, end, preferred=preferred)
        points = self._clean_points(plan.get("points") or [])
        provider = str(plan.get("provider") or "path")
        if len(points) < 2:
            return {"moved": False, "provider": provider, "pointCount": len(points)}

        motion = self._motion_parameters(end_hold_backtrack=end_hold_backtrack)
        motion_id = f"m{self._movement_index}-{self._rng.getrandbits(48):012x}"
        if self._play_cdp_drag(seleniumbase_cdp, points, motion=motion, end_hold_backtrack=end_hold_backtrack):
            return {
                "moved": True,
                "provider": f"{provider}:cdp",
                "pointCount": len(points),
                "motionId": motion_id,
                "motionParameters": {key: round(value, 6) for key, value in motion.items()},
                "endHoldBacktrack": bool(end_hold_backtrack),
            }
        if gui_start is not None and gui_end is not None:
            gui_plan = self.plan(gui_start, gui_end, preferred=preferred)
            gui_points = self._clean_points(gui_plan.get("points") or [])
            gui_provider = str(gui_plan.get("provider") or provider)
            if len(gui_points) >= 2 and self._play_pyautogui(gui_points):
                return {"moved": True, "provider": f"{gui_provider}:gui", "pointCount": len(gui_points)}
            try:
                seleniumbase_cdp.gui_drag_drop_points(int(round(gui_start[0])), int(round(gui_start[1])), int(round(gui_end[0])), int(round(gui_end[1])), timeframe=0.55)
                return {"moved": True, "provider": "seleniumbase-gui", "pointCount": 2}
            except Exception:
                pass
        return {"moved": False, "provider": provider, "pointCount": len(points)}

    @staticmethod
    def _cdp_context(seleniumbase_cdp: Any):
        get_tab = getattr(seleniumbase_cdp, "get_active_tab", None)
        get_loop = getattr(seleniumbase_cdp, "get_event_loop", None)
        if not callable(get_tab) or not callable(get_loop):
            return None
        try:
            tab, loop = get_tab(), get_loop()
        except Exception:
            return None
        return None if tab is None or loop is None else (tab, loop)

    def _play_cdp_click(self, seleniumbase_cdp: Any, points: List[Point]) -> Tuple[bool, str]:
        context = self._cdp_context(seleniumbase_cdp)
        if context is None:
            return False, "no-cdp-context"
        tab, loop = context
        # Keep a dense curve (>= ~18 points). Very few points create large pixel
        # jumps that anti-bot systems treat as teleportation and discard, and a
        # gentle logarithmic ease-out near the target lets the tile's hover
        # state fire before the press.
        click_points = self._shorten_points(points, 20)
        pre_press = self._rng.uniform(0.02, 0.055)
        press_hold = self._rng.uniform(0.045, 0.125)

        async def click() -> None:
            button = cdp_input.MouseButton("left")
            count = len(click_points)
            for index, (x, y) in enumerate(click_points):
                await tab.send(cdp_input.dispatch_mouse_event("mouseMoved", x=x, y=y, button=button, buttons=0, pointer_type="mouse"))
                t = index / max(1, count - 1)
                # Fast far from the target, decelerating into it (ease-out).
                await asyncio.sleep(0.008 + 0.024 * (t * t))
            x, y = click_points[-1]
            await asyncio.sleep(pre_press)
            await tab.send(cdp_input.dispatch_mouse_event("mousePressed", x=x, y=y, button=button, buttons=1, click_count=1, pointer_type="mouse"))
            await asyncio.sleep(press_hold)
            await tab.send(cdp_input.dispatch_mouse_event("mouseReleased", x=x, y=y, button=button, buttons=0, click_count=1, pointer_type="mouse"))

        try:
            loop.run_until_complete(asyncio.wait_for(click(), timeout=3.0))
            return True, ""
        except (asyncio.TimeoutError, TimeoutError):
            # A wait_for cancellation bypasses SeleniumBase's own aclose()
            # (CancelledError is BaseException, not Exception), leaving THIS
            # browser's websocket half-open so the next send is refused
            # (WinError 1225). Let the cancellation settle, then close ONLY this
            # connection. Each task owns its own port, so other browsers sharing
            # the loop are never touched; the next send reopens this one.
            try:
                loop.run_until_complete(asyncio.sleep(0))
            except Exception:
                pass
            self._close_connection(tab, loop)
            return False, "timeout"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _close_connection(tab: Any, loop: Any) -> None:
        """Hard-reset ONLY this tab's own CDP connection (its own port).

        A cancelled send leaves a stale in-flight transaction in `mapper` plus a
        half-open websocket. Reusing that makes the next send hang or be refused
        (WinError 1225), so the race stays unreliable. Clear the pending
        transactions, close the websocket and drop the websocket/listener
        references so the next send opens a brand-new connection instead of
        reusing the broken one. No other browser/task on the shared loop is
        touched.
        """
        try:
            mapper = getattr(tab, "mapper", None)
            if hasattr(mapper, "clear"):
                mapper.clear()
        except Exception:
            pass
        close = getattr(tab, "aclose", None)
        if callable(close):
            try:
                result = close()
                if asyncio.iscoroutine(result):
                    loop.run_until_complete(result)
            except Exception:
                pass
        for attribute in ("websocket", "listener"):
            try:
                if hasattr(tab, attribute):
                    setattr(tab, attribute, None)
            except Exception:
                pass

    @staticmethod
    def _shorten_points(points: List[Point], max_points: int) -> List[Point]:
        n = len(points)
        if n <= max_points:
            return points
        step = (n - 1) / float(max_points - 1)
        result: List[Point] = []
        for index in range(max_points):
            point = points[min(n - 1, int(round(index * step)))]
            if not result or result[-1] != point:
                result.append(point)
        return result

    @classmethod
    def _play_cdp_drag(
        cls,
        seleniumbase_cdp: Any,
        points: List[Point],
        *,
        motion: Dict[str, float],
        end_hold_backtrack: bool = False,
    ) -> bool:
        context = cls._cdp_context(seleniumbase_cdp)
        if context is None:
            return False
        tab, loop = context

        async def drag() -> None:
            button = cdp_input.MouseButton("left")
            sx, sy = points[0]
            await tab.send(cdp_input.dispatch_mouse_event("mouseMoved", x=sx, y=sy, button=button, buttons=0))
            await asyncio.sleep(motion["prePress"])
            await tab.send(cdp_input.dispatch_mouse_event("mousePressed", x=sx, y=sy, button=button, buttons=1, click_count=1))
            await asyncio.sleep(motion["postPress"])
            ex, ey = points[-1]
            try:
                count = max(1, len(points) - 1)
                for index, (x, y) in enumerate(points[1:], start=1):
                    t = index / count
                    delay = (
                        motion["baseDelay"]
                        + motion["acceleration"] * (t * t)
                        + motion["wave"] * abs(math.sin(math.pi * motion["waveCycles"] * t))
                    )
                    await tab.send(cdp_input.dispatch_mouse_event("mouseMoved", x=x, y=y, button=button, buttons=1))
                    await asyncio.sleep(delay)

                ex, ey = points[-1]
                if end_hold_backtrack:
                    await asyncio.sleep(motion["endHold"])
                    px, py = points[-2]
                    dx, dy = ex - px, ey - py
                    length = max(1e-6, math.hypot(dx, dy))
                    back = motion["backtrackPx"]
                    bx = ex - dx / length * back
                    by = ey - dy / length * back
                    await tab.send(cdp_input.dispatch_mouse_event("mouseMoved", x=bx, y=by, button=button, buttons=1))
                    await asyncio.sleep(motion["backtrackHold"])
                    ex, ey = bx, by
            finally:
                await tab.send(cdp_input.dispatch_mouse_event("mouseReleased", x=ex, y=ey, button=button, buttons=0, click_count=1))

        try:
            loop.run_until_complete(drag())
            return True
        except Exception:
            return False

    _play_cdp = _play_cdp_drag

    def _external(self, start: Point, end: Point, *, preferred: str):
        if not self._helper.exists():
            return None
        payload = json.dumps({"start": {"x": start[0], "y": start[1]}, "end": {"x": end[0], "y": end[1]}, "preferred": preferred, "steps": self._steps(start, end)})
        env = dict(os.environ)
        if env.get("ARES_NODE_RUN_AS_NODE", "").strip() == "1":
            env["ELECTRON_RUN_AS_NODE"] = "1"
        try:
            completed = subprocess.run([self._node, str(self._helper)], input=payload, text=True, capture_output=True, timeout=2.5, env=env, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0 and not completed.stdout.strip():
            return None
        try:
            value = json.loads(completed.stdout.strip() or "{}")
        except json.JSONDecodeError:
            return None
        points = self._clean_points(value.get("points") or [])
        return None if len(points) < 2 else {"provider": str(value.get("provider") or "external"), "points": points}

    @staticmethod
    def _play_pyautogui(points: List[Point]) -> bool:
        try:
            import pyautogui
        except Exception:
            return False
        try:
            pyautogui.PAUSE = 0
            pyautogui.moveTo(points[0][0], points[0][1], duration=0)
            pyautogui.mouseDown(button="left")
            try:
                duration = max(0.004, min(0.018, 0.42 / max(1, len(points) - 1)))
                for x, y in points[1:]:
                    pyautogui.moveTo(x, y, duration=duration)
            finally:
                pyautogui.mouseUp(button="left")
            return True
        except Exception:
            try:
                pyautogui.mouseUp(button="left")
            except Exception:
                pass
            return False

    @classmethod
    def _python_bezier(cls, start: Point, end: Point) -> List[Point]:
        steps = cls._steps(start, end)
        dx, dy = end[0] - start[0], end[1] - start[1]
        distance = max(1.0, math.hypot(dx, dy))
        nx, ny = -dy / distance, dx / distance
        bend = min(36.0, max(7.0, distance * 0.07))
        c1 = (start[0] + dx * 0.33 + nx * bend, start[1] + dy * 0.33 + ny * bend)
        c2 = (start[0] + dx * 0.72 + nx * bend * 0.45, start[1] + dy * 0.72 + ny * bend * 0.45)
        points = []
        for index in range(steps + 1):
            t = index / steps
            u = 1.0 - t
            points.append((u**3 * start[0] + 3*u*u*t*c1[0] + 3*u*t*t*c2[0] + t**3 * end[0], u**3 * start[1] + 3*u*u*t*c1[1] + 3*u*t*t*c2[1] + t**3 * end[1]))
        return points

    @staticmethod
    def _steps(start: Point, end: Point) -> int:
        return max(18, min(72, int(round(18 + math.hypot(end[0] - start[0], end[1] - start[1]) / 12.0))))

    @staticmethod
    def _clean_points(values: Iterable[Any]) -> List[Point]:
        result = []
        for value in values:
            try:
                point = (float(value["x"]), float(value["y"])) if isinstance(value, dict) else (float(value[0]), float(value[1]))
            except (KeyError, TypeError, ValueError, IndexError):
                continue
            if not result or math.hypot(result[-1][0] - point[0], result[-1][1] - point[1]) > 0.01:
                result.append(point)
        return result
