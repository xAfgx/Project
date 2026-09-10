from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

from auto_interaction_controller import AutoInteractionController
from proximity_grid_action_executor import ProximityGridActionExecutor
from runtime_oopif_outcome_grid_site_adapter import ScopeLockedGridSiteAdapter
from visual_interaction_runtime import VisualInteractionRuntime


class _TracedProximityGridActionExecutor(ProximityGridActionExecutor):
    """Preserve the production cursor path and append auditable click telemetry."""

    def __init__(self, *args: Any, trace: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._trace = trace

    def _cdp_click_to(self, target: Tuple[float, float], state: Dict[str, Any]) -> bool:
        if self._cursor_point is None:
            viewport = state.get("viewport") or {}
            try:
                width = float(viewport.get("width") or 0.0)
                height = float(viewport.get("height") or 0.0)
            except (TypeError, ValueError):
                width = height = 0.0
            self._cursor_point = (
                width / 2.0 if width > 0 else target[0],
                height / 2.0 if height > 0 else target[1],
            )

        result = self._cursor.play_click(
            self._sb,
            self._cursor_point,
            target,
            preferred="ghost-cursor",
        )
        if self._trace is not None:
            try:
                self._trace.append(
                    "cursor-click",
                    {
                        "preferred": "ghost-cursor",
                        "provider": str(result.get("provider") or ""),
                        "pointCount": int(result.get("pointCount") or 0),
                        "clicked": bool(result.get("clicked")),
                        "error": str(result.get("error") or ""),
                        "seeded": bool(getattr(self._cursor, "_runtime_seeded", False)),
                        "target": {"x": float(target[0]), "y": float(target[1])},
                    },
                )
            except Exception:
                pass
        if bool(result.get("clicked")):
            self._cursor_point = target
            return True
        return False


class OopifVisualInteractionRuntime(VisualInteractionRuntime):
    """Production visual runtime with OOPIF-only direct-children grid discovery."""

    def __init__(
        self,
        seleniumbase_cdp: Any,
        *,
        profile_dir: str | Path,
        overrides: Dict[str, str] | None = None,
        capture: Any = None,
    ) -> None:
        super().__init__(
            seleniumbase_cdp,
            profile_dir=profile_dir,
            overrides=overrides,
            capture=capture,
        )
        self._grid = ScopeLockedGridSiteAdapter(self._sb, overrides=overrides or {})
        self._grid_actions = _TracedProximityGridActionExecutor(
            self._sb,
            self._grid,
            self._policy,
            self._paths,
            trace=self._trace,
        )
        self._controller = AutoInteractionController(
            self._grid,
            self._slider,
            self._grid_actions,
            self._slider_actions,
            self._vision,
            self._slider_grounder,
            self._trace,
        )
