from __future__ import annotations

import re
import time
from typing import Any, Dict, Iterable


_ACTION_TERMS = re.compile(
    r"(?i)(select|click|choose|mark|verify|verification|drag|slide|move|hold|swipe|pull|push|"
    r"wähl|klick|markier|prüf|bestät|zieh|schieb|verschieb|beweg|regler|gedrückt|"
    r"nach\s+rechts|nach\s+links|bis\s+zum\s+ende)"
)
_MAX_GRID_ATTEMPTS = 3


class AutoInteractionController:
    """Coordinate observation, grounding, actions, and passive re-evaluation."""

    def __init__(
        self,
        grid_adapter: Any,
        slider_adapter: Any,
        grid_actions: Any,
        slider_actions: Any,
        vision: Any,
        slider_grounder: Any = None,
        trace: Any = None,
    ) -> None:
        self._grid_adapter = grid_adapter
        self._slider_adapter = slider_adapter
        self._grid_actions = grid_actions
        self._slider_actions = slider_actions
        self._vision = vision
        self._slider_grounder = slider_grounder
        self._trace = trace
        self._last_grid_signature = ""
        self._last_slider_signature = ""
        self._last_action_at = 0.0
        self._grid_attempts: Dict[str, int] = {}

    def poll_and_act(self) -> Dict[str, Any]:
        grid = self._grid_adapter.poll()
        slider = self._slider_adapter.poll()
        candidate = grid if int(grid.get("score") or 0) >= int(slider.get("score") or 0) else slider
        self._record("observation", {"kind": candidate.get("kind"), "state": candidate})
        if candidate.get("kind") == "image-grid":
            return self._handle_grid(candidate)
        if candidate.get("kind") == "slider":
            return self._handle_slider(candidate)
        return {"acted": False, "kind": "none", "state": candidate}

    def act_grid_from_sources(
        self,
        state: Dict[str, Any],
        sources: Iterable[str],
        *,
        source: str = "screenshot",
        reference_source: str = "",
    ) -> Dict[str, Any]:
        if state.get("kind") != "image-grid":
            return {"acted": False, "kind": "none", "state": state, "reason": "not-image-grid"}
        result = self._handle_grid(
            state,
            source_override=list(sources),
            decision_source=source,
            force_actionable=True,
            reference_source=reference_source,
        )
        return {**result, "decisionSource": source}

    def status(self) -> Dict[str, Any]:
        status = {
            "enabled": True,
            "vision": self._vision.status(),
            "gridSignature": self._last_grid_signature,
            "sliderSignature": self._last_slider_signature,
            "maxGridAttempts": _MAX_GRID_ATTEMPTS,
            "gridAttempts": dict(self._grid_attempts),
        }
        trace_path = getattr(self._trace, "path", None)
        if trace_path:
            status["tracePath"] = str(trace_path)
        return status

    def _handle_grid(
        self,
        state: Dict[str, Any],
        *,
        source_override: list[str] | None = None,
        decision_source: str = "structural-source",
        force_actionable: bool = False,
        reference_source: str = "",
    ) -> Dict[str, Any]:
        signature = str(state.get("signature") or "")
        actionable = self._actionable(state) or force_actionable
        if not actionable or not signature:
            return {
                "acted": False,
                "kind": "image-grid",
                "state": state,
                "decisionSource": decision_source,
                "reason": "not-actionable",
            }
        if signature == self._last_grid_signature:
            return {
                "acted": False,
                "verified": True,
                "kind": "image-grid",
                "state": state,
                "decisionSource": decision_source,
                "reason": "already-handled",
            }

        previous_attempts = int(self._grid_attempts.get(signature) or 0)
        if previous_attempts >= _MAX_GRID_ATTEMPTS:
            return {
                "acted": False,
                "verified": False,
                "kind": "image-grid",
                "state": state,
                "decisionSource": decision_source,
                "attempt": previous_attempts,
                "maxAttempts": _MAX_GRID_ATTEMPTS,
                "reason": "max-attempts-reached",
            }

        sources = source_override if source_override is not None else list(state.get("sources") or [])
        instruction = str(state.get("instruction") or "")
        use_reference = bool(reference_source)
        self._record("grid-diagnostic", {
            "stage": "VISION_CALLED",
            "signature": signature,
            "decisionSource": decision_source,
            "instruction": instruction,
            "sourceCount": len(sources),
            "tileCount": int(state.get("tileCount") or 0),
            "attemptBefore": previous_attempts,
            "referenceMode": use_reference,
        })
        if use_reference:
            decision = self._vision.classify_reference(reference_source, sources)
        else:
            decision = self._vision.classify(instruction, sources)
        self._record("grid-diagnostic", {
            "stage": "VISION_RESULT",
            "signature": signature,
            "error": str(decision.get("error") or ""),
            "target": decision.get("target"),
            "selectedIndexesRaw": list(decision.get("selectedIndexes") or []),
            "scores": list(decision.get("scores") or []),
            "model": decision.get("model"),
            "threshold": decision.get("threshold"),
            "selectionPolicy": decision.get("selectionPolicy"),
        })
        if str(decision.get("error") or "").strip():
            decision = {
                **decision,
                "selectedIndexes": [],
                "selectedMarkIds": [],
                "source": decision_source,
                "attempt": previous_attempts,
                "maxAttempts": _MAX_GRID_ATTEMPTS,
            }
            self._record("decision", {"kind": "image-grid", "decision": decision})
            self._record("grid-diagnostic", {
                "stage": "VERIFY",
                "verified": False,
                "reason": "vision-error-retry",
                "attempt": previous_attempts,
            })
            return {
                "acted": False,
                "verified": False,
                "kind": "image-grid",
                "state": state,
                "decision": decision,
                "decisionSource": decision_source,
                "attempt": previous_attempts,
                "maxAttempts": _MAX_GRID_ATTEMPTS,
                "reason": "vision-error-retry",
            }

        attempt = previous_attempts + 1
        self._grid_attempts[signature] = attempt
        selected = self._selected_indexes(decision.get("selectedIndexes") or [], int(state.get("tileCount") or 0))
        tile_marks = [
            mark for mark in state.get("marks") or []
            if isinstance(mark, dict) and mark.get("role") == "grid-tile"
        ]
        selected_mark_ids = [
            str(tile_marks[index].get("markId") or "")
            for index in selected
            if index < len(tile_marks) and tile_marks[index].get("markId")
        ]
        decision = {
            **decision,
            "selectedIndexes": selected,
            "selectedMarkIds": selected_mark_ids,
            "source": decision_source,
            "attempt": attempt,
            "maxAttempts": _MAX_GRID_ATTEMPTS,
        }
        self._record("decision", {"kind": "image-grid", "decision": decision})
        self._record("grid-diagnostic", {
            "stage": "MARK_IDS",
            "signature": signature,
            "selectedIndexes": selected,
            "selectedMarkIds": selected_mark_ids,
            "tileMarkCount": len(tile_marks),
            "completeMapping": bool(selected) and len(selected_mark_ids) == len(selected),
            "attempt": attempt,
        })
        if not selected or len(selected_mark_ids) != len(selected):
            if not selected:
                # 0 matches: do not idle into the timeout. Click the captcha's
                # reload/refresh control so the next cycle classifies a clean
                # image instead of waiting on an unclear/blank puzzle.
                reload_result = None
                reload_fn = getattr(self._grid_actions, "reload_challenge", None)
                if callable(reload_fn):
                    reload_result = reload_fn(state)
                reloaded = bool((reload_result or {}).get("reloaded"))
                reason = "empty-selection-reloaded" if reloaded else (
                    "max-attempts-reached" if attempt >= _MAX_GRID_ATTEMPTS else "no-selection-retry"
                )
                self._record("grid-diagnostic", {
                    "stage": "VERIFY",
                    "verified": False,
                    "reason": reason,
                    "attempt": attempt,
                    "reload": reload_result,
                })
                return {
                    "acted": reloaded,
                    "verified": False,
                    "kind": "image-grid",
                    "state": state,
                    "decision": decision,
                    "decisionSource": decision_source,
                    "attempt": attempt,
                    "maxAttempts": _MAX_GRID_ATTEMPTS,
                    "reason": reason,
                    "reload": reload_result,
                }

            reason = "max-attempts-reached" if attempt >= _MAX_GRID_ATTEMPTS else "no-selection-retry"
            self._record("grid-diagnostic", {
                "stage": "VERIFY",
                "verified": False,
                "reason": reason,
                "attempt": attempt,
            })
            return {
                "acted": False,
                "verified": False,
                "kind": "image-grid",
                "state": state,
                "decision": decision,
                "decisionSource": decision_source,
                "attempt": attempt,
                "maxAttempts": _MAX_GRID_ATTEMPTS,
                "reason": reason,
            }

        self._last_action_at = time.monotonic()
        apply_marks = getattr(self._grid_actions, "apply_marks", None)
        self._record("grid-diagnostic", {
            "stage": "APPLY_MARKS_ENTER",
            "signature": signature,
            "selectedIndexes": selected,
            "selectedMarkIds": selected_mark_ids,
            "submit": True,
            "executor": "apply_marks" if selected_mark_ids and callable(apply_marks) else "apply_indexes",
            "attempt": attempt,
        })
        if selected_mark_ids and callable(apply_marks):
            result = apply_marks(selected_mark_ids, submit=True, expected_state=state)
        else:
            result = self._grid_actions.apply(selected, submit=True)
        self._record("grid-diagnostic", {
            "stage": "APPLY_MARKS_RESULT",
            "signature": signature,
            "clickedIndexes": list((result or {}).get("clickedIndexes") or []) if isinstance(result, dict) else [],
            "clickedMarkIds": list((result or {}).get("clickedMarkIds") or []) if isinstance(result, dict) else [],
            "submitted": bool((result or {}).get("submitted")) if isinstance(result, dict) else False,
            "reason": str((result or {}).get("reason") or "") if isinstance(result, dict) else "invalid-result",
            "attempt": attempt,
        })

        clicked = [int(value) for value in (result.get("clickedIndexes") or [])] if isinstance(result, dict) else []
        clicked_mark_ids = [str(value) for value in (result.get("clickedMarkIds") or [])] if isinstance(result, dict) else []
        all_clicked = (
            len(clicked) == len(selected)
            and set(clicked_mark_ids) == set(selected_mark_ids)
        )
        submitted = bool(result.get("submitted")) if isinstance(result, dict) else False
        if not all_clicked or not submitted:
            verification = {
                "verified": False,
                "reason": str((result or {}).get("reason") or ("incomplete-click-set" if not all_clicked else "submit-not-confirmed")),
            }
            self._record("action", {"kind": "image-grid", "decision": decision, "result": result, "verification": verification})
            reason = verification["reason"]
            if attempt >= _MAX_GRID_ATTEMPTS and reason not in {"stale-grid-during-selection", "stale-grid-before-selection"}:
                reason = "max-attempts-reached"
            self._record("grid-diagnostic", {
                "stage": "VERIFY",
                "verified": False,
                "reason": reason,
                "allClicked": all_clicked,
                "submitted": submitted,
                "attempt": attempt,
            })
            return {
                "acted": bool(clicked),
                "verified": False,
                "kind": "image-grid",
                "state": state,
                "decision": decision,
                "decisionSource": decision_source,
                "result": result,
                "verification": verification,
                "attempt": attempt,
                "maxAttempts": _MAX_GRID_ATTEMPTS,
                "reason": reason,
            }

        if bool(result.get("challengeStillPresent")):
            # reCAPTCHA kept the puzzle ("Versuche es bitte erneut"): re-run the
            # recognition/click loop on the (freshly reloaded) image instead of
            # declaring success or hanging. Capped by _MAX_GRID_ATTEMPTS.
            risk_flagged = bool(result.get("riskFlagged"))
            reloaded = bool(result.get("reloaded"))
            still_reason = "max-attempts-reached" if attempt >= _MAX_GRID_ATTEMPTS else (
                "risk-flagged-reloaded" if risk_flagged and reloaded else "post-submit-retry"
            )
            self._record("grid-diagnostic", {
                "stage": "VERIFY",
                "verified": False,
                "reason": still_reason,
                "attempt": attempt,
                "riskFlagged": risk_flagged,
                "reloaded": reloaded,
            })
            return {
                "acted": True,
                "verified": False,
                "kind": "image-grid",
                "state": state,
                "decision": decision,
                "decisionSource": decision_source,
                "result": result,
                "attempt": attempt,
                "maxAttempts": _MAX_GRID_ATTEMPTS,
                "reason": still_reason,
            }

        verification = self._verify_grid(signature, result.get("state") if isinstance(result, dict) else None)
        verified = bool(verification.get("verified"))
        if verified:
            self._last_grid_signature = signature
            self._grid_attempts.pop(signature, None)
        explicit_failure = str(verification.get("reason") or "") == "explicit-failure"
        reason = "verified" if verified else (
            "explicit-failure" if explicit_failure else (
                "max-attempts-reached" if attempt >= _MAX_GRID_ATTEMPTS else "verification-retry"
            )
        )
        self._record("action", {"kind": "image-grid", "decision": decision, "result": result, "verification": verification})
        self._record("grid-diagnostic", {
            "stage": "VERIFY",
            "verified": verified,
            "reason": str(verification.get("reason") or reason),
            "finalReason": reason,
            "attempt": attempt,
        })
        return {
            "acted": True,
            "verified": verified,
            "kind": "image-grid",
            "state": state,
            "decision": decision,
            "decisionSource": decision_source,
            "result": result,
            "verification": verification,
            "attempt": attempt,
            "maxAttempts": _MAX_GRID_ATTEMPTS,
            "reason": reason,
        }

    def _handle_slider(self, state: Dict[str, Any]) -> Dict[str, Any]:
        signature_fn = getattr(self._slider_actions, "calibration_signature", None)
        signature = str(signature_fn(state) if callable(signature_fn) else (state.get("signature") or ""))
        if bool(state.get("complete")):
            self._last_slider_signature = signature or self._last_slider_signature
            return {"acted": False, "verified": True, "kind": "slider", "state": state, "reason": "complete"}
        if bool(state.get("failed")):
            self._last_slider_signature = signature or self._last_slider_signature
            return {"acted": False, "verified": False, "kind": "slider", "state": state, "reason": "failed"}
        if not self._actionable(state) or not signature or signature == self._last_slider_signature:
            return {"acted": False, "kind": "slider", "state": state}

        target = self._ground_slider(state)
        self._record("decision", {"kind": "slider", "target": target, "state": state})
        if not bool(target.get("grounded")) or float(target.get("confidence") or 0.0) < 0.40:
            return {"acted": False, "kind": "slider", "state": state, "target": target}

        target_fraction = float(target.get("targetFraction"))
        self._last_action_at = time.monotonic()
        # DOM/instruction targets are exact (e.g. DataDome .sliderTarget): drag
        # straight there in one clean motion instead of overshoot+backtrack.
        exact_target = str(target.get("source") or "") in {"dom-target", "instruction-percent", "instruction-value"}
        try:
            result = self._slider_actions.apply(target_fraction, state=state, exact_target=exact_target)
        except TypeError:
            # Action executor without the exact_target extension.
            result = self._slider_actions.apply(target_fraction, state=state)
        verification = self._verify_slider(
            state,
            target_fraction,
            result.get("state") if isinstance(result, dict) else None,
        )

        acted = bool(result.get("moved")) if isinstance(result, dict) else False
        if acted:
            self._last_slider_signature = str(result.get("calibrationSignature") or signature)
        payload = {
            "acted": acted,
            "verified": bool(verification.get("verified")),
            "kind": "slider",
            "target": target,
            "result": result,
            "verification": verification,
        }
        self._record("action", payload)
        return payload

    def _ground_slider(self, state: Dict[str, Any]) -> Dict[str, Any]:
        if self._slider_grounder is not None:
            try:
                return self._slider_grounder.ground(state)
            except Exception as exc:
                return {"grounded": False, "targetFraction": None, "confidence": 0.0, "source": "error", "error": str(exc)}
        return {"grounded": True, "targetFraction": 0.96, "confidence": 0.40, "source": "legacy-directional", "markId": "fallback"}

    def _verify_grid(self, before_signature: str, initial: Any) -> Dict[str, Any]:
        state = initial if isinstance(initial, dict) else self._grid_adapter.poll()
        deadline = time.monotonic() + 2.5
        transition_seen = False
        while time.monotonic() < deadline:
            if bool(state.get("complete")):
                return {"verified": True, "reason": "explicit-complete", "state": state}
            if bool(state.get("failed")):
                return {"verified": False, "reason": "explicit-failure", "state": state}
            signature = str(state.get("signature") or "")
            if state.get("kind") != "image-grid" or (signature and signature != before_signature):
                transition_seen = True
            time.sleep(0.08)
            state = self._grid_adapter.poll()
        return {
            "verified": False,
            "reason": "transition-without-explicit-success" if transition_seen else "no-explicit-success",
            "state": state,
        }

    def _verify_slider(self, before: Dict[str, Any], target: float, initial: Any) -> Dict[str, Any]:
        state = initial if isinstance(initial, dict) else self._slider_adapter.poll()
        deadline = time.monotonic() + 1.25
        while time.monotonic() < deadline:
            if bool(state.get("complete")):
                return {"verified": True, "reason": "explicit-complete", "state": state}
            if bool(state.get("failed")):
                return {"verified": False, "reason": "explicit-failure", "state": state}
            if state.get("kind") == "slider":
                current = float(state.get("fraction") or 0.0)
                current_distance = abs(current - target)
                if current_distance <= 0.02:
                    return {
                        "verified": True,
                        "reason": "target-within-tolerance",
                        "distance": current_distance,
                        "state": state,
                    }
            time.sleep(0.06)
            state = self._slider_adapter.poll()
        return {
            "verified": False,
            "reason": "no-explicit-success-or-target-tolerance",
            "state": state,
        }

    def _record(self, phase: str, payload: Dict[str, Any]) -> None:
        if self._trace is None:
            return
        try:
            self._trace.append(phase, payload)
        except Exception:
            pass

    @staticmethod
    def _selected_indexes(values: Any, count: int) -> list[int]:
        selected = set()
        for value in values:
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= index < count:
                selected.add(index)
        return sorted(selected)

    @staticmethod
    def _actionable(state: Dict[str, Any]) -> bool:
        if bool(state.get("override")):
            return True
        instruction = str(state.get("instruction") or "")
        score = int(state.get("score") or 0)
        return score >= 70 and bool(_ACTION_TERMS.search(instruction))
