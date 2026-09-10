from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any, Dict

from cursor_path_provider import CursorPathProvider


class SliderActionExecutor:
    """Move a detected slider with feedback-controlled, seeded drag corrections."""

    TARGET_TOLERANCE = 0.02
    MAX_CORRECTION_DRAGS = 3
    END_TARGET_THRESHOLD = 0.94
    MAX_COMMAND_FRACTION = 1.02

    def __init__(self, seleniumbase_cdp: Any, slider_adapter: Any, path_provider: CursorPathProvider | None = None) -> None:
        self._sb = seleniumbase_cdp
        self._slider_adapter = slider_adapter
        self._paths = path_provider or CursorPathProvider()
        self._gain_signature = ""
        self._gain: float | None = None

    def calibration_signature(self, state: Dict[str, Any]) -> str:
        marks = []
        for mark in state.get("marks") or []:
            if not isinstance(mark, dict) or mark.get("role") not in {"slider-handle", "slider-track", "slider-target"}:
                continue
            marks.append({
                "role": str(mark.get("role") or ""),
                "markId": str(mark.get("markId") or ""),
                "identitySignature": str(mark.get("identitySignature") or ""),
                "fraction": round(float(mark.get("fraction") or 0.0), 4) if mark.get("role") == "slider-target" else None,
            })
        payload = {
            "scope": str(state.get("scope") or ""),
            "documentEpoch": int(state.get("documentEpoch") or 0),
            "sessionGeneration": int(state.get("sessionGeneration") or 0),
            "orientation": str(state.get("orientation") or "horizontal"),
            "nativeRange": bool(state.get("nativeRange")),
            "instruction": str(state.get("instruction") or ""),
            "handleSelector": str(state.get("handleSelector") or ""),
            "trackSelector": str(state.get("trackSelector") or ""),
            "marks": marks,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8", errors="ignore")).hexdigest()

    def apply(
        self,
        target_fraction: float = 0.96,
        *,
        state: Dict[str, Any] | None = None,
        force_fallback: bool = False,
        exact_target: bool = False,
    ) -> Dict[str, Any]:
        state = state or self._slider_adapter.poll()
        if state.get("kind") != "slider":
            return {
                "moved": False,
                "verified": False,
                "targetFraction": 0.0,
                "mode": "none",
                "state": state,
                "attempts": [],
                "correctionDrags": 0,
            }

        target = self._clamp(float(target_fraction))
        calibration_signature = self.calibration_signature(state)
        if calibration_signature != self._gain_signature:
            self._gain_signature = calibration_signature
            self._gain = None

        current = self._fraction(state)
        if bool(state.get("complete")) or abs(target - current) <= self.TARGET_TOLERANCE:
            return {
                "moved": False,
                "verified": True,
                "targetFraction": target,
                "currentFraction": current,
                "targetError": abs(target - current),
                "mode": "none",
                "state": state,
                "attempts": [],
                "correctionDrags": 0,
                "gain": self._gain,
                "calibrationSignature": calibration_signature,
            }

        attempts = []
        total_points = 0
        moved_any = False
        last_mode = "none"
        last_motion_id = None
        last_motion_parameters: Dict[str, Any] = {}
        any_backtrack = False
        previous_error = target - current
        direction_changes = 0

        for drag_index in range(self.MAX_CORRECTION_DRAGS + 1):
            if state.get("kind") != "slider":
                break
            if bool(state.get("complete")):
                break
            if bool(state.get("failed")):
                break

            current = self._fraction(state)
            error = target - current
            if abs(error) <= self.TARGET_TOLERANCE:
                break

            if drag_index == 0:
                if exact_target:
                    # Exact DOM target (e.g. DataDome .sliderTarget): one clean
                    # drag straight to the target. Overshoot+backtrack is a bot
                    # tell for these motion-analyzed sliders.
                    command, dither = target, 0.0
                else:
                    command, dither = self._initial_overshoot_command(
                        current,
                        target,
                        overshoot_enabled=not bool(state.get("nativeRange")),
                    )
            else:
                command, dither = self._correction_command(current, target)

            command_delta = command - current
            if abs(command_delta) < 0.001:
                break

            crosses_target = (target - current) * (command - target) > 0
            end_slider_settle = str(state.get("orientation") or "horizontal") != "vertical" and command >= self.END_TARGET_THRESHOLD
            drag = self._perform_drag(
                state,
                command,
                force_fallback=force_fallback,
                end_hold_backtrack=False if exact_target else (crosses_target or end_slider_settle),
            )
            moved = bool(drag.get("moved"))
            moved_any = moved_any or moved
            mode = f"path:{drag.get('provider') or 'unknown'}" if drag.get("provider") else str(drag.get("mode") or "none")
            if moved:
                last_mode = mode
            total_points += int(drag.get("pointCount") or 0)
            last_motion_id = drag.get("motionId") or last_motion_id
            if isinstance(drag.get("motionParameters"), dict):
                last_motion_parameters = dict(drag.get("motionParameters") or {})
            any_backtrack = any_backtrack or bool(drag.get("endHoldBacktrack"))

            after = self._poll_after_drag(state) if moved else self._slider_adapter.poll()
            after_fraction = self._fraction(after) if after.get("kind") == "slider" else current
            observed_delta = after_fraction - current
            learned_gain = self._learn_gain(command_delta, observed_delta)

            new_error = target - after_fraction
            if self._direction(previous_error) and self._direction(new_error) and self._direction(previous_error) != self._direction(new_error):
                direction_changes += 1
            previous_error = new_error

            attempts.append({
                "index": drag_index + 1,
                "kind": "initial" if drag_index == 0 else "correction",
                "beforeFraction": round(current, 6),
                "commandFraction": round(command, 6),
                "afterFraction": round(after_fraction, 6),
                "error": round(new_error, 6),
                "ditherFraction": round(dither, 6),
                "gain": round(learned_gain, 6) if learned_gain is not None else None,
                "mode": mode,
                "endHoldBacktrack": bool(drag.get("endHoldBacktrack")),
            })
            state = after

            if not moved:
                break
            if bool(state.get("complete")) or bool(state.get("failed")):
                break
            if state.get("kind") == "slider" and abs(target - self._fraction(state)) <= self.TARGET_TOLERANCE:
                break

        current = self._fraction(state) if state.get("kind") == "slider" else current
        verified = bool(state.get("complete")) or (
            state.get("kind") == "slider"
            and not bool(state.get("failed"))
            and abs(target - current) <= self.TARGET_TOLERANCE
        )

        return {
            "moved": moved_any,
            "verified": verified,
            "targetFraction": target,
            "currentFraction": current,
            "targetError": abs(target - current),
            "mode": last_mode,
            "pointCount": total_points,
            "motionId": last_motion_id,
            "motionParameters": last_motion_parameters,
            "endHoldBacktrack": any_backtrack,
            "state": state,
            "attempts": attempts,
            "correctionDrags": max(0, len(attempts) - 1),
            "maxCorrectionDrags": self.MAX_CORRECTION_DRAGS,
            "gain": self._gain,
            "overshootDirectionChanges": direction_changes,
            "calibrationSignature": calibration_signature,
        }

    def _perform_drag(
        self,
        state: Dict[str, Any],
        command: float,
        *,
        force_fallback: bool,
        end_hold_backtrack: bool,
    ) -> Dict[str, Any]:
        if not force_fallback:
            planned = self._planned_drag(state, command, end_hold_backtrack=end_hold_backtrack)
            if planned.get("moved"):
                return planned

        moved = self._native_drag(state, command)
        if moved:
            return {"moved": True, "mode": "seleniumbase-native", "pointCount": 2}
        moved = self._apply_document(command, state)
        return {"moved": moved, "mode": "event-fallback" if moved else "none", "pointCount": 0}

    def _planned_drag(
        self,
        state: Dict[str, Any],
        target: float,
        *,
        end_hold_backtrack: bool,
    ) -> Dict[str, Any]:
        viewport = self._viewport_points(state, target)
        if viewport is None:
            return {"moved": False, "provider": "none", "pointCount": 0}
        gui = self._screen_points(state, target)
        viewport_start, viewport_end = viewport
        gui_start, gui_end = gui if gui is not None else (None, None)
        try:
            return self._paths.play_drag(
                self._sb,
                viewport_start,
                viewport_end,
                preferred="ghost-cursor",
                gui_start=gui_start,
                gui_end=gui_end,
                end_hold_backtrack=end_hold_backtrack,
            )
        except Exception:
            return {"moved": False, "provider": "none", "pointCount": 0}

    @staticmethod
    def _viewport_points(state: Dict[str, Any], target: float):
        track = state.get("trackRect") if isinstance(state.get("trackRect"), dict) else None
        handle = state.get("handleRect") if isinstance(state.get("handleRect"), dict) else None
        if not track or not handle:
            return None
        try:
            tx = float(track.get("x") or 0.0)
            ty = float(track.get("y") or 0.0)
            width = float(track.get("width") or 0.0)
            height = float(track.get("height") or 0.0)
            hx = float(handle.get("x") or 0.0)
            hy = float(handle.get("y") or 0.0)
            hw = float(handle.get("width") or 0.0)
            hh = float(handle.get("height") or 0.0)
            current = max(0.0, min(1.0, float(state.get("fraction") or 0.0)))
        except (TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None

        native = bool(state.get("nativeRange"))
        if state.get("orientation") == "vertical":
            start = (
                tx + width / 2.0 if native else hx + hw / 2.0,
                ty + height - height * current if native else hy + hh / 2.0,
            )
            end_target = max(0.0, min(1.0, target)) if native else target
            end = (tx + width / 2.0, ty + height - height * end_target)
        else:
            start = (
                tx + width * current if native else hx + hw / 2.0,
                ty + height / 2.0 if native else hy + hh / 2.0,
            )
            end_target = max(0.0, min(1.0, target)) if native else target
            end = (tx + width * end_target, ty + height / 2.0)
        return start, end

    def _screen_points(self, state: Dict[str, Any], target: float):
        handle_selector = str(state.get("handleSelector") or "")
        track_selector = str(state.get("trackSelector") or "")
        rect = state.get("trackRect") if isinstance(state.get("trackRect"), dict) else {}
        if not handle_selector or not track_selector or not rect:
            return None
        try:
            handle_x, handle_y = self._sb.get_gui_element_center(handle_selector)
            track_x, track_y = self._sb.get_gui_element_center(track_selector)
            width = float(rect.get("width") or 0)
            height = float(rect.get("height") or 0)
            current = max(0.0, min(1.0, float(state.get("fraction") or 0)))
            if width <= 0 or height <= 0:
                return None
            native = bool(state.get("nativeRange"))
            end_target = max(0.0, min(1.0, target)) if native else target
            if state.get("orientation") == "vertical":
                start_x = float(handle_x)
                start_y = float(track_y) + height / 2 - height * current if native else float(handle_y)
                end_x = float(track_x)
                end_y = float(track_y) + height / 2 - height * end_target
            else:
                start_x = float(track_x) - width / 2 + width * current if native else float(handle_x)
                start_y = float(handle_y)
                end_x = float(track_x) - width / 2 + width * end_target
                end_y = float(track_y)
            return (start_x, start_y), (end_x, end_y)
        except Exception:
            return None

    def _native_drag(self, state: Dict[str, Any], target: float) -> bool:
        points = self._screen_points(state, target)
        if points is None:
            return False
        start, end = points
        try:
            self._sb.gui_drag_drop_points(
                int(round(start[0])), int(round(start[1])),
                int(round(end[0])), int(round(end[1])),
                timeframe=0.55,
            )
            return True
        except Exception:
            return False

    def _apply_document(self, target: float, state: Dict[str, Any]) -> bool:
        overrides = getattr(self._slider_adapter, "_overrides", {})
        script = f"""
        (() => {{
          const target = {json.dumps(target)};
          const overrides = {json.dumps(overrides)};
          const visible = el => {{
            if (!el?.getBoundingClientRect) return false;
            const r = el.getBoundingClientRect(), s = getComputedStyle(el);
            return r.width >= 18 && r.height >= 12 && s.display !== 'none' && s.visibility !== 'hidden';
          }};
          const roots = [], seen = new Set();
          const walk = root => {{
            if (!root || seen.has(root)) return;
            seen.add(root); roots.push(root);
            for (const el of root.querySelectorAll?.('*') || []) if (el.shadowRoot) walk(el.shadowRoot);
            for (const frame of root.querySelectorAll?.('iframe') || []) {{
              try {{ if (frame.contentDocument) walk(frame.contentDocument); }} catch (_) {{}}
            }}
          }};
          walk(document);

          for (const root of roots) {{
            const scopedRoot = overrides.sliderRoot ? root.querySelector(overrides.sliderRoot) || root : root;
            let handles = overrides.sliderHandle ? [...scopedRoot.querySelectorAll(overrides.sliderHandle)].filter(visible) : [];
            if (!handles.length) handles = [...scopedRoot.querySelectorAll('input[type="range"],[role="slider"],[aria-valuenow]')].filter(visible);
            if (!handles.length) handles = [...scopedRoot.querySelectorAll('[class*="slider" i] [class*="thumb" i],[class*="slider" i] [class*="handle" i],[class*="drag" i] [class*="handle" i]')].filter(visible);
            for (const handle of handles) {{
              const nativeRange = handle.matches('input[type="range"]');
              let track = overrides.sliderTrack ? scopedRoot.querySelector(overrides.sliderTrack) : null;
              if (!track && nativeRange) track = handle;
              if (!track) track = handle.closest('[role="slider"]')?.parentElement || handle.closest('[class*="slider" i],[class*="track" i],[class*="drag" i]') || handle.parentElement;
              if (!track || !visible(track)) continue;

              if (nativeRange) {{
                const min = Number(handle.min || 0), max = Number(handle.max || 100);
                const bounded = Math.max(0, Math.min(1, target));
                handle.value = String(min + (max - min) * bounded);
                handle.dispatchEvent(new Event('input', {{bubbles:true}}));
                handle.dispatchEvent(new Event('change', {{bubbles:true}}));
                return true;
              }}

              const r = track.getBoundingClientRect();
              const h = handle.getBoundingClientRect();
              const horizontal = r.width >= r.height;
              const x = horizontal ? r.left + r.width * target : r.left + r.width / 2;
              const y = horizontal ? r.top + r.height / 2 : r.bottom - r.height * target;
              const sx = h.left + h.width / 2, sy = h.top + h.height / 2;
              const opts = {{bubbles:true, cancelable:true, pointerId:1, pointerType:'mouse', isPrimary:true}};
              handle.dispatchEvent(new PointerEvent('pointerdown', {{...opts, clientX:sx, clientY:sy, buttons:1}}));
              handle.dispatchEvent(new MouseEvent('mousedown', {{bubbles:true, cancelable:true, clientX:sx, clientY:sy, buttons:1}}));
              document.dispatchEvent(new PointerEvent('pointermove', {{...opts, clientX:x, clientY:y, buttons:1}}));
              document.dispatchEvent(new MouseEvent('mousemove', {{bubbles:true, cancelable:true, clientX:x, clientY:y, buttons:1}}));
              document.dispatchEvent(new PointerEvent('pointerup', {{...opts, clientX:x, clientY:y, buttons:0}}));
              document.dispatchEvent(new MouseEvent('mouseup', {{bubbles:true, cancelable:true, clientX:x, clientY:y, buttons:0}}));
              return true;
            }}
          }}
          return false;
        }})()
        """
        frame_path = [str(value) for value in state.get("framePath") or [] if str(value)]
        if frame_path:
            oopif_evaluate = getattr(self._sb, "ares_oopif_evaluate", None)
            if callable(oopif_evaluate):
                try:
                    evaluated = oopif_evaluate(frame_path, f"return {script};", [])
                    return bool(evaluated.get("value")) if isinstance(evaluated, dict) else bool(evaluated)
                except Exception:
                    return False
        try:
            return bool(self._evaluate(script))
        except Exception:
            return False

    def _initial_overshoot_command(
        self,
        current: float,
        target: float,
        *,
        overshoot_enabled: bool,
    ) -> tuple[float, float]:
        error = target - current
        direction = self._direction(error)
        if direction == 0 or not overshoot_enabled:
            return target, 0.0
        if direction > 0 and target >= self.END_TARGET_THRESHOLD:
            command = self.MAX_COMMAND_FRACTION
            return command, command - target
        overshoot = min(0.035, max(0.008, abs(error) * 0.08))
        command = max(-0.02, min(self.MAX_COMMAND_FRACTION, target + direction * overshoot))
        return command, command - target

    def _correction_command(self, current: float, target: float) -> tuple[float, float]:
        error = target - current
        gain = self._gain if self._gain is not None and 0.15 <= self._gain <= 2.5 else 1.0
        correction = error / gain
        dither = math.copysign(min(0.012, abs(error) * 0.18), error) if error else 0.0
        command = self._clamp(current + correction + dither)
        return command, dither

    def _learn_gain(self, command_delta: float, observed_delta: float) -> float | None:
        if abs(command_delta) < 0.005 or abs(observed_delta) < 0.001:
            return self._gain
        if command_delta * observed_delta <= 0:
            return self._gain
        measured = abs(observed_delta / command_delta)
        if not 0.08 <= measured <= 4.0:
            return self._gain
        measured = max(0.15, min(2.5, measured))
        self._gain = measured if self._gain is None else (self._gain * 0.6 + measured * 0.4)
        return self._gain

    def _poll_after_drag(self, before: Dict[str, Any]) -> Dict[str, Any]:
        before_fraction = self._fraction(before)
        latest = before
        for index in range(5):
            latest = self._slider_adapter.poll()
            if bool(latest.get("complete")) or bool(latest.get("failed")):
                return latest
            if latest.get("kind") != "slider":
                return latest
            if abs(self._fraction(latest) - before_fraction) >= 0.001:
                return latest
            if index < 4:
                time.sleep(0.03)
        return latest

    @staticmethod
    def _fraction(state: Dict[str, Any]) -> float:
        try:
            return max(0.0, min(1.0, float(state.get("fraction") or 0.0)))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _direction(value: float) -> int:
        if value > 1e-9:
            return 1
        if value < -1e-9:
            return -1
        return 0

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def _evaluate(self, script: str) -> Any:
        evaluator = getattr(self._sb, "evaluate", None)
        if callable(evaluator):
            return evaluator(script)
        return self._sb.execute_script(f"return {script};")
