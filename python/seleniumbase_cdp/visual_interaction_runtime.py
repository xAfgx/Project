from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Iterable

from auto_interaction_controller import AutoInteractionController
from composite_slider_grounder import CompositeSliderGrounder
from consent_popup_handler import ConsentPopupHandler
from cursor_path_provider import CursorPathProvider
from interaction_policy import InteractionPolicy
from interaction_trace import InteractionTrace
from observation_capture import ObservationCapture
from proximity_grid_action_executor import ProximityGridActionExecutor
from scope_locked_grid_site_adapter import ScopeLockedGridSiteAdapter
from screenshot_grid_tile_provider import ScreenshotGridTileProvider
from site_slider_adapter import SliderSiteAdapter
from slider_action_executor import SliderActionExecutor
from vision_grid_classifier import VisionGridClassifier


class VisualInteractionRuntime:
    """Single ARES observation → grounding → action → re-observation boundary."""

    def __init__(
        self,
        seleniumbase_cdp: Any,
        *,
        profile_dir: str | Path,
        overrides: Dict[str, str] | None = None,
        capture: ObservationCapture | None = None,
    ) -> None:
        self._sb = seleniumbase_cdp
        self._profile_dir = Path(profile_dir).expanduser().resolve()
        self._policy = InteractionPolicy.from_profile(self._profile_dir)
        self._capture = capture or ObservationCapture(self._sb, profile_dir=self._profile_dir, policy=self._policy)
        self._grid = ScopeLockedGridSiteAdapter(self._sb, overrides=overrides or {})
        self._slider = SliderSiteAdapter(self._sb, overrides=overrides or {})
        self._paths = CursorPathProvider(seed=str(self._profile_dir))
        self._grid_actions = ProximityGridActionExecutor(self._sb, self._grid, self._policy, self._paths)
        self._slider_actions = SliderActionExecutor(self._sb, self._slider, self._paths)
        self._vision = VisionGridClassifier()
        self._screenshot_tiles = ScreenshotGridTileProvider()
        self._slider_grounder = CompositeSliderGrounder(self._sb, profile_dir=self._profile_dir)
        self._trace = InteractionTrace(self._profile_dir)
        self._popup_handler = ConsentPopupHandler(self._sb)
        self._controller = AutoInteractionController(
            self._grid,
            self._slider,
            self._grid_actions,
            self._slider_actions,
            self._vision,
            self._slider_grounder,
            self._trace,
        )
        self._last_grid_debug_signature = ""

    def poll_and_act(self) -> Dict[str, Any]:
        self._trace.append("runtime-stage-enter", {"stage": "popup-dismiss"})
        started = time.monotonic()
        popup = self._bounded_popup_dismiss(3.0)
        self._trace.append("runtime-stage", {
            "stage": "popup-dismiss",
            "elapsedMs": round((time.monotonic() - started) * 1000.0, 3),
            "result": popup,
        })
        popup_dismissed = bool(popup.get("dismissed"))
        if popup_dismissed:
            self._trace.append("popup-action", popup)
            # Do not spend the only available runtime opportunity on consent.
            # Give the DOM a short settle window, then continue through checkout
            # and visual discovery in this same serialized owner cycle.
            time.sleep(0.12)

        self._trace.append("runtime-stage-enter", {
            "stage": "checkout-progress",
            "afterPopupDismiss": popup_dismissed,
        })
        started = time.monotonic()
        checkout = self._popup_handler.advance_checkout_once()
        self._trace.append("runtime-stage", {
            "stage": "checkout-progress",
            "elapsedMs": round((time.monotonic() - started) * 1000.0, 3),
            "result": checkout,
        })
        if checkout.get("advanced"):
            self._trace.append("checkout-action", checkout)
            return {"acted": True, "kind": "checkout", "result": checkout}

        self._trace.append("runtime-stage-enter", {
            "stage": "grid-prefetch",
            "afterPopupDismiss": popup_dismissed,
        })
        started = time.monotonic()
        grid_state = self._grid.poll()
        self._trace.append("runtime-stage", {
            "stage": "grid-prefetch",
            "elapsedMs": round((time.monotonic() - started) * 1000.0, 3),
            "kind": str(grid_state.get("kind") or "none"),
            "scope": str(grid_state.get("scope") or ""),
        })
        if grid_state.get("kind") == "image-grid":
            signature = str(grid_state.get("signature") or "")
            if signature and signature != self._last_grid_debug_signature:
                captured = self._capture.capture(
                    "grid-candidate",
                    generation=int(grid_state.get("generation") or 0),
                    force=True,
                )
                self._trace.append(
                    "grid-screenshot-captured",
                    {
                        "kind": "image-grid",
                        "state": grid_state,
                        "capture": captured,
                    },
                )
                if bool(captured.get("captured")):
                    screenshot_result = self.poll_and_act_from_screenshot(
                        str(captured.get("path") or ""),
                        state=grid_state,
                    )
                    screenshot_result = self._finalize_interaction(screenshot_result)
                    if screenshot_result.get("verified") is True:
                        self._last_grid_debug_signature = signature
                    self._trace.append(
                        "grid-screenshot-result",
                        {
                            "kind": "image-grid",
                            "capture": captured,
                            "result": screenshot_result,
                        },
                    )
                    return {
                        **screenshot_result,
                        "debugScreenshot": captured,
                        "screenshotFirst": True,
                    }

                self._trace.append(
                    "grid-screenshot-unavailable",
                    {
                        "kind": "image-grid",
                        "state": grid_state,
                        "capture": captured,
                    },
                )

        self._trace.append("runtime-stage-enter", {
            "stage": "controller",
            "afterPopupDismiss": popup_dismissed,
        })
        started = time.monotonic()
        primary = self._finalize_interaction(self._controller.poll_and_act())
        self._trace.append("runtime-stage", {
            "stage": "controller",
            "elapsedMs": round((time.monotonic() - started) * 1000.0, 3),
            "kind": str(primary.get("kind") or "none"),
            "acted": bool(primary.get("acted")),
            "verified": primary.get("verified"),
        })
        if primary.get("kind") != "image-grid" or bool(primary.get("acted")):
            if popup_dismissed and not bool(primary.get("acted")) and str(primary.get("kind") or "none") == "none":
                return {
                    "acted": True,
                    "kind": "popup",
                    "result": popup,
                    "continued": True,
                    "nextResult": primary,
                }
            return primary

        state = primary.get("state") if isinstance(primary.get("state"), dict) else grid_state
        signature = str(state.get("signature") or "")
        return {
            **primary,
            "screenshotFirst": bool(signature and signature == self._last_grid_debug_signature),
            "screenshotFallback": {
                "attempted": bool(signature),
                "reason": "capture-or-crop-unavailable" if signature else "missing-grid-signature",
            },
        }

    def poll_and_act_from_screenshot(
        self,
        screenshot_path: str | Path,
        *,
        state: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        observed = dict(state) if isinstance(state, dict) else self._grid.poll()
        if observed.get("kind") != "image-grid":
            return {"acted": False, "kind": "none", "reason": "no-image-grid", "state": observed}

        current = self._grid.poll()
        observed_signature = str(observed.get("signature") or "")
        current_signature = str(current.get("signature") or "")
        if not observed_signature or current_signature != observed_signature:
            return {
                "acted": False,
                "verified": False,
                "kind": "image-grid",
                "reason": "screenshot-state-stale",
                "state": observed,
                "currentState": current,
                "invariants": {
                    "observedSignature": observed_signature,
                    "currentSignature": current_signature,
                    "sameVisualState": False,
                },
            }

        tile_count = int(observed.get("tileCount") or 0)
        marks = [
            mark for mark in observed.get("marks") or []
            if isinstance(mark, dict) and mark.get("role") == "grid-tile"
        ]
        provided = self._screenshot_tiles.sources(screenshot_path, observed)
        sources = list(provided.get("sources") or [])
        readable = int(provided.get("readable") or 0)
        crop_count = int(provided.get("cropCount") or 0)
        geometry_ready = tile_count > 0 and len(marks) == tile_count
        crops_ready = len(sources) == tile_count and readable == tile_count and crop_count == tile_count
        if not geometry_ready or not crops_ready:
            return {
                "acted": False,
                "verified": False,
                "kind": "image-grid",
                "reason": "screenshot-grid-unavailable",
                "state": observed,
                "screenshot": provided,
                "invariants": {
                    "tileCount": tile_count,
                    "markCount": len(marks),
                    "cropCount": crop_count,
                    "readable": readable,
                    "geometryReady": geometry_ready,
                    "cropsReady": crops_ready,
                    "observedSignature": observed_signature,
                    "currentSignature": current_signature,
                    "sameVisualState": True,
                },
            }

        result = self._controller.act_grid_from_sources(
            observed,
            sources,
            source="screenshot-crops",
            reference_source=str(provided.get("referenceSource") or ""),
        )
        return {
            **result,
            "screenshot": provided,
            "invariants": {
                "tileCount": tile_count,
                "markCount": len(marks),
                "cropCount": crop_count,
                "readable": readable,
                "geometryReady": True,
                "cropsReady": True,
                "observedSignature": observed_signature,
                "currentSignature": current_signature,
                "sameVisualState": True,
            },
        }

    def _finalize_interaction(self, result: Dict[str, Any]) -> Dict[str, Any]:
        kind = str(result.get("kind") or "")
        verified = bool(result.get("verified"))

        if kind == "image-grid" and not verified:
            reason = str(result.get("reason") or "")
            attempt = int(result.get("attempt") or 0)
            max_attempts = int(result.get("maxAttempts") or 3)
            retryable = reason not in {"max-attempts-reached", "explicit-failure"} and attempt < max_attempts
            if retryable:
                self._trace.append(
                    "grid-retry-scheduled",
                    {
                        "attempt": attempt,
                        "nextAttempt": attempt + 1 if attempt else 1,
                        "maxAttempts": max_attempts,
                        "reason": reason,
                    },
                )

        if verified and kind in {"image-grid", "slider"}:
            progress = self._advance_after_success()
            return {**result, "postSuccess": progress}
        return result

    def _bounded_popup_dismiss(self, timeout: float) -> Dict[str, Any]:
        """Run dismiss_once with a hard timeout so a hung CDP call can never
        block the runtime loop from reaching grid detection."""
        import threading

        result: Dict[str, Any] = {"dismissed": False, "reason": "bounded-timeout"}
        holder: list[Any] = []

        def run() -> None:
            try:
                holder.append(self._popup_handler.dismiss_once())
            except Exception as exc:
                holder.append({"dismissed": False, "reason": f"error:{type(exc).__name__}"})

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(timeout)
        if holder:
            result = holder[0]
        return result

    def _advance_after_success(self) -> Dict[str, Any]:
        """Bounded post-success chain for confirm/continue/checkout progression."""
        deadline = time.monotonic() + 1.6
        dismissed = []
        while time.monotonic() < deadline:
            popup = self._popup_handler.dismiss_once()
            if popup.get("dismissed"):
                dismissed.append(popup)
                self._trace.append("post-success-popup", popup)
                time.sleep(0.08)
                continue

            progress = self._popup_handler.advance_progress_once()
            if progress.get("advanced"):
                payload = {
                    **progress,
                    "verifiedSource": "post-success-explicit-control",
                    "dismissedPopups": dismissed,
                }
                self._trace.append("post-success-progress", payload)
                return payload
            time.sleep(0.10)

        return {
            "advanced": False,
            "reason": "no-post-success-progress-control",
            "dismissedPopups": dismissed,
        }

    def status(self) -> Dict[str, Any]:
        return {
            **self._controller.status(),
            "runtime": "visual-interaction-runtime",
            "markIdentity": "structural+semantic-visual",
            "gridGeometry": "dynamic-2x2-through-8x8",
            "gridClickOrder": "nearest-neighbour",
            "gridCropInvariant": "one-mark-one-readable-crop",
            "gridStateInvariant": "capture-classify-click-same-visual-state",
            "gridTiming": {
                "clickDelaySeconds": self._policy.grid_click_delay_seconds,
                "submitDelaySeconds": self._policy.grid_submit_delay_seconds,
            },
            "popupAutoProgress": True,
            "checkoutAutoProgress": True,
            "postSuccessAutoProgress": True,
            "maxGridAttempts": 3,
            "freshScreenshotPerRetry": True,
            "screenshotGridFallback": True,
            "screenshotFirstForGrid": True,
            "debugScreenshotRoot": str(self._capture.root),
            "sliderProviders": self._slider_grounder.status(),
        }

    def grid_state(self) -> Dict[str, Any]:
        return self._grid.poll()

    def slider_state(self) -> Dict[str, Any]:
        return self._slider.poll()

    def slider_target_state(self) -> Dict[str, Any]:
        return self._slider_grounder.ground(self._slider.poll())

    def apply_grid_selection(self, indexes: Iterable[int], *, submit: bool = True) -> Dict[str, Any]:
        return self._grid_actions.apply(indexes, submit=submit)

    def apply_slider(self, target_fraction: float) -> Dict[str, Any]:
        return self._slider_actions.apply(target_fraction)