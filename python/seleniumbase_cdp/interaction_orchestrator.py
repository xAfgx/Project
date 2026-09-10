from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict


class InteractionOrchestrator:
    """Serialize all page interactions for one SeleniumBase profile.

    Only one action may own the browser at a time. Auto interaction follows the
    fixed chain visual -> instruction and stops immediately when one layer acts.
    Explicit semantic/grid/slider actions use the same lock.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = "idle"
        self._owner = "none"
        self._generation = 0
        self._last_transition = time.monotonic()
        self._last_result: Dict[str, Any] = {"acted": False, "kind": "none"}

    def run_cycle(
        self,
        visual: Callable[[], Dict[str, Any]],
        instruction: Callable[[], Dict[str, Any]],
    ) -> Dict[str, Any]:
        with self._lock:
            self._begin("auto-cycle")
            try:
                self._set_state("observing")
                visual_result = self._run_stage("visual", visual)
                if bool(visual_result.get("acted")):
                    return self._finish(visual_result)

                instruction_result = self._run_stage("instruction", instruction)
                return self._finish(instruction_result)
            except Exception as exc:
                return self._finish({
                    "acted": False,
                    "kind": "orchestrator-error",
                    "error": str(exc),
                })

    def run_action(self, owner: str, action: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
        with self._lock:
            self._begin(owner)
            try:
                result = self._run_stage(owner, action)
                return self._finish(result)
            except Exception:
                self._set_state("idle")
                self._owner = "none"
                raise

    def status(self) -> Dict[str, Any]:
        return {
            "state": self._state,
            "owner": self._owner,
            "generation": self._generation,
            "lastResult": dict(self._last_result),
            "singleOwner": True,
        }

    def _run_stage(self, owner: str, action: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
        self._owner = owner
        self._set_state("planning")
        self._set_state("executing")
        result = action()
        if not isinstance(result, dict):
            result = {"acted": False, "kind": owner, "error": "invalid-result"}
        self._set_state("verifying")
        return result

    def _begin(self, owner: str) -> None:
        self._generation += 1
        self._owner = owner
        self._set_state("observing")

    def _finish(self, result: Dict[str, Any]) -> Dict[str, Any]:
        self._last_result = dict(result)
        self._set_state("reobserve")
        self._owner = "none"
        self._set_state("idle")
        return result

    def _set_state(self, state: str) -> None:
        self._state = state
        self._last_transition = time.monotonic()
