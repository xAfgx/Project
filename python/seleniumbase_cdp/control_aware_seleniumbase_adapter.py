from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Iterable, List

from oopif_visual_interaction_runtime import OopifVisualInteractionRuntime
from seleniumbase_adapter import SeleniumBaseCdpAdapter


class ControlAwareSeleniumBaseCdpAdapter(SeleniumBaseCdpAdapter):
    """Keep expensive automatic page work behind explicit control-plane traffic."""

    CONTROL_QUIET_SECONDS = 0.9
    MAX_CONTROL_DEFERRAL_SECONDS = 2.0
    POLL_SKIP_TRACE_INTERVAL_SECONDS = 1.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._control_quiet_until = 0.0
        self._control_deferral_started_at = 0.0
        self._deferred_navigation_auto = False
        self._passive_observation_depth = 0
        self._runtime_poll_attempts = 0
        self._runtime_poll_runs = 0
        self._last_poll_skip_trace_at = 0.0
        self._runtime_poll_in_progress = False
        super().__init__(*args, **kwargs)
        overrides = kwargs.get("site_adapter_overrides")
        self._visual_interactions = OopifVisualInteractionRuntime(
            self._sb,
            profile_dir=self.profile_dir,
            overrides=overrides if isinstance(overrides, dict) else {},
            capture=self._capture,
        )
        self.note_control_activity()

    def _append_runtime_poll_trace(self, decision: str, *, now: float, forced: bool = False) -> None:
        """Write compact passive scheduling diagnostics into the existing visual trace."""
        if decision == "poll-skipped":
            if now - self._last_poll_skip_trace_at < self.POLL_SKIP_TRACE_INTERVAL_SECONDS:
                return
            self._last_poll_skip_trace_at = now
        try:
            quiet_remaining = max(0.0, self._control_quiet_until - now)
            deferral_age = (
                max(0.0, now - self._control_deferral_started_at)
                if self._control_deferral_started_at > 0.0
                else 0.0
            )
            record = {
                "ts": time.time(),
                "phase": "runtime-poll",
                "decision": str(decision),
                "forced": bool(forced),
                "quietRemainingMs": round(quiet_remaining * 1000.0, 3),
                "controlDeferralMs": round(deferral_age * 1000.0, 3),
                "maxControlDeferralMs": round(self.MAX_CONTROL_DEFERRAL_SECONDS * 1000.0, 3),
                "deferredNavigation": bool(self._deferred_navigation_auto),
                "passiveObservationDepth": int(self._passive_observation_depth),
                "attempt": int(self._runtime_poll_attempts),
                "runCount": int(self._runtime_poll_runs),
            }
            trace_path = self.profile_dir / ".ares-visual-trace.jsonl"
            with trace_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception:
            pass

    def _append_goto_trace(self, stage: str, *, status: str, started: float | None = None, error: BaseException | None = None) -> None:
        """Trace the synchronous navigation pipeline before runtime polling can begin."""
        try:
            record: Dict[str, Any] = {
                "ts": time.time(),
                "phase": "runtime-goto",
                "stage": str(stage),
                "status": str(status),
            }
            if started is not None:
                record["elapsedMs"] = round((time.monotonic() - started) * 1000.0, 3)
            if error is not None:
                record["errorType"] = type(error).__name__
                record["error"] = str(error)[:1000]
            trace_path = self.profile_dir / ".ares-visual-trace.jsonl"
            with trace_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception:
            pass

    def note_control_activity(self) -> None:
        if self._passive_observation_depth > 0 or self._runtime_poll_in_progress:
            return
        now = time.monotonic()

        # The worker normally polls from idle/page-state/network opportunities.
        # Under a continuously saturated RPC queue none of those opportunities
        # is guaranteed to occur. Once the existing starvation ceiling is hit,
        # create that opportunity here on the same owner thread before extending
        # the control quiet window again.
        if (
            self._control_deferral_started_at > 0.0
            and now < self._control_quiet_until
            and now - self._control_deferral_started_at >= self.MAX_CONTROL_DEFERRAL_SECONDS
        ):
            self.poll_runtime()
            now = time.monotonic()

        if self._control_deferral_started_at <= 0.0 or now >= self._control_quiet_until:
            self._control_deferral_started_at = now
        self._control_quiet_until = max(
            self._control_quiet_until,
            now + self.CONTROL_QUIET_SECONDS,
        )

    def passive_observation(self, action: Callable[[], Any]) -> Any:
        """Run read-only telemetry without masquerading as user/control traffic."""
        self._passive_observation_depth += 1
        try:
            return action()
        finally:
            self._passive_observation_depth = max(0, self._passive_observation_depth - 1)

    def poll_runtime(self) -> None:
        if self._runtime_poll_in_progress:
            return
        self._runtime_poll_in_progress = True
        try:
            now = time.monotonic()
            self._runtime_poll_attempts += 1
            quiet_active = now < self._control_quiet_until
            deferral_age = (
                max(0.0, now - self._control_deferral_started_at)
                if self._control_deferral_started_at > 0.0
                else 0.0
            )
            forced_after_starvation = (
                quiet_active
                and self._control_deferral_started_at > 0.0
                and deferral_age >= self.MAX_CONTROL_DEFERRAL_SECONDS
            )
            if quiet_active and not forced_after_starvation:
                self._append_runtime_poll_trace("poll-skipped", now=now)
                return

            self._runtime_poll_runs += 1
            self._append_runtime_poll_trace(
                "forced-after-control-starvation" if forced_after_starvation else "poll-run",
                now=now,
                forced=forced_after_starvation,
            )
            self._control_deferral_started_at = 0.0

            if self._deferred_navigation_auto:
                self._deferred_navigation_auto = False
                self._poll_observation_watchdog(force=True)
                return
            super().poll_runtime()
        finally:
            self._runtime_poll_in_progress = False

    def goto(self, url: str) -> None:
        """Navigate synchronously, but defer expensive automatic visual work."""
        self.note_control_activity()
        pipeline = (
            ("sb-goto", lambda: self._sb.goto(url)),
            ("challenge-stability", self._challenge_tracker.wait_for_stable_challenge),
            ("seleniumbase-solve-captcha", self._sb.solve_captcha),
        )
        for stage, action in pipeline:
            started = time.monotonic()
            self._append_goto_trace(stage, status="enter")
            try:
                action()
            except BaseException as exc:
                self._append_goto_trace(stage, status="error", started=started, error=exc)
                raise
            self._append_goto_trace(stage, status="exit", started=started)

        started = time.monotonic()
        self._append_goto_trace("watchdog-baseline", status="enter")
        try:
            self._watchdog.reset()
            initial = self._watchdog.poll()
            self._last_watchdog_state = initial
        except BaseException as exc:
            self._append_goto_trace("watchdog-baseline", status="error", started=started, error=exc)
            raise
        self._append_goto_trace("watchdog-baseline", status="exit", started=started)

        started = time.monotonic()
        self._append_goto_trace("page-load-capture", status="enter")
        try:
            self._capture_debug(
                "page-load",
                generation=int(initial.get("generation") or 0),
                force=True,
            )
        except BaseException as exc:
            self._append_goto_trace("page-load-capture", status="error", started=started, error=exc)
            raise
        self._append_goto_trace("page-load-capture", status="exit", started=started)

        self._deferred_navigation_auto = True
        self._append_goto_trace("navigation-complete", status="exit")
        self.note_control_activity()

    def execute_script(self, script: str, *args: Any) -> Any:
        self.note_control_activity()
        result = super().execute_script(script, *args)
        self.note_control_activity()
        return result

    def challenge_state(self) -> Dict[str, Any]:
        self.note_control_activity()
        return super().challenge_state()

    def site_grid_state(self) -> Dict[str, Any]:
        self.note_control_activity()
        return super().site_grid_state()

    def site_slider_state(self) -> Dict[str, Any]:
        self.note_control_activity()
        return super().site_slider_state()

    def slider_target_state(self) -> Dict[str, Any]:
        self.note_control_activity()
        return super().slider_target_state()

    def auto_interaction_state(self) -> Dict[str, Any]:
        self.note_control_activity()
        return super().auto_interaction_state()

    def interaction_outcome_state(self) -> Dict[str, Any]:
        self.note_control_activity()
        return super().interaction_outcome_state()

    def observe_semantic_fields(self) -> List[Dict[str, Any]]:
        self.note_control_activity()
        return super().observe_semantic_fields()

    def execute_semantic_plan(self, plan: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        self.note_control_activity()
        result = super().execute_semantic_plan(plan)
        self.note_control_activity()
        return result

    def apply_grid_selection(self, indexes: Iterable[int], *, submit: bool = True) -> Dict[str, Any]:
        self.note_control_activity()
        result = super().apply_grid_selection(indexes, submit=submit)
        self.note_control_activity()
        return result

    def apply_slider(self, target_fraction: float = 0.96) -> Dict[str, Any]:
        self.note_control_activity()
        result = super().apply_slider(target_fraction)
        self.note_control_activity()
        return result

    def inspect_session(self) -> Dict[str, Any]:
        self.note_control_activity()
        return super().inspect_session()

    def set_snapshot_cookies(self, cookies: Iterable[Dict[str, Any]]) -> int:
        self.note_control_activity()
        result = super().set_snapshot_cookies(cookies)
        self.note_control_activity()
        return result

    def get_snapshot_cookies(self) -> List[Dict[str, Any]]:
        self.note_control_activity()
        return super().get_snapshot_cookies()
