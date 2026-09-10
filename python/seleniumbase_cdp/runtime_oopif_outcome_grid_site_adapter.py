from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List

from runtime_oopif_grid_site_adapter import ScopeLockedGridSiteAdapter as _DirectChildrenGridSiteAdapter


_OUTCOME_SCRIPT = r"""
return (() => {
  const visible = el => {
    if (!el?.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0
      && s.display !== 'none'
      && s.visibility !== 'hidden'
      && Number(s.opacity || 1) > 0;
  };
  const values = [...(document.querySelectorAll?.('strong,b,[role="status"],[role="alert"],p,h1,h2,h3,h4,div,span') || [])]
    .filter(visible)
    .map(el => String(el.innerText || el.textContent || '').trim().replace(/\s+/g, ' '))
    .filter(Boolean)
    .slice(0, 1200);
  const text = values.join('\n').slice(0, 20000);
  const success = /(?:^|\b)(success|successful|correct|passed|verified|erfolgreich|richtig|bestanden)(?:\b|$)/i.test(text);
  const failure = /(?:^|\b)(failed|failure|incorrect|wrong|error|fehlgeschlagen|falsch|nicht korrekt)(?:\b|$)/i.test(text);
  return {complete: !!success && !failure, failed: !!failure && !success};
})();
"""


class ScopeLockedGridSiteAdapter(_DirectChildrenGridSiteAdapter):
    """Add generic OOPIF outcome detection and preserve path-level grid diagnostics."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._last_oopif_path_debug: List[Dict[str, Any]] = []
        super().__init__(*args, **kwargs)

    def _candidate_plausibility_debug(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        marks = [
            mark for mark in candidate.get("marks") or []
            if isinstance(mark, dict) and mark.get("role") == "grid-tile"
        ]
        result: Dict[str, Any] = {
            "plausible": False,
            "markCount": len(marks),
            "reason": "not-image-grid",
        }
        if candidate.get("kind") != "image-grid":
            return result
        if bool(candidate.get("override")):
            return {**result, "plausible": True, "reason": "override"}
        if len(marks) < self.MIN_COUNT:
            return {**result, "reason": "marks-too-few", "requiredMarks": self.MIN_COUNT}

        viewport_known = 0
        in_viewport = 0
        for mark in marks:
            bounds = mark.get("visualBounds")
            if not isinstance(bounds, dict):
                continue
            try:
                x = float(bounds.get("x") or 0.0)
                y = float(bounds.get("y") or 0.0)
                width = float(bounds.get("width") or 0.0)
                height = float(bounds.get("height") or 0.0)
            except (TypeError, ValueError):
                continue
            if width <= 0 or height <= 0:
                continue
            viewport = mark.get("viewport")
            if not isinstance(viewport, dict):
                continue
            try:
                vw = float(viewport.get("width") or 0.0)
                vh = float(viewport.get("height") or 0.0)
            except (TypeError, ValueError):
                continue
            if vw <= 0 or vh <= 0:
                continue
            viewport_known += 1
            if x + width > 0 and y + height > 0 and x < vw and y < vh:
                in_viewport += 1

        required = max(self.MIN_COUNT, math.ceil(viewport_known * self.MIN_VIEWPORT_RATIO)) if viewport_known else 0
        result.update({
            "viewportKnown": viewport_known,
            "inViewport": in_viewport,
            "requiredInViewport": required,
        })
        if viewport_known and in_viewport < required:
            result["reason"] = "viewport-coverage"
            return result
        result.update({"plausible": True, "reason": "accepted"})
        return result

    def _snapshot_oopif_frames(self) -> Dict[str, Any]:
        discover = getattr(self._sb, "ares_oopif_discover", None)
        if not callable(discover):
            self._last_oopif_path_debug = [{"reason": "oopif-discover-unavailable"}]
            return self._empty("oopif")
        try:
            entries = [entry for entry in (discover() or []) if isinstance(entry, dict)]
        except Exception as exc:
            self._last_oopif_path_debug = [{"reason": "oopif-discover-error", "error": str(exc)[:500]}]
            return self._empty("oopif")

        path_debug: List[Dict[str, Any]] = []
        best = self._empty("oopif")
        best_rank = float("-inf")
        for entry in entries:
            path = [str(value) for value in entry.get("path") or [] if str(value)]
            if not path:
                continue
            try:
                candidate = self._snapshot_oopif_path(path)
            except Exception as exc:
                path_debug.append({"path": path, "reason": "snapshot-error", "error": str(exc)[:500]})
                continue

            plausibility = self._candidate_plausibility_debug(candidate)
            item: Dict[str, Any] = {
                "path": path,
                "kind": str(candidate.get("kind") or "none"),
                "scope": str(candidate.get("scope") or ""),
                "score": int(candidate.get("score") or 0),
                "rows": int(candidate.get("rows") or 0),
                "columns": int(candidate.get("columns") or 0),
                "tileCount": int(candidate.get("tileCount") or 0),
                "frameId": str(candidate.get("frameId") or ""),
                "plausibility": plausibility,
            }
            oopif_debug = candidate.get("oopifDebug")
            if isinstance(oopif_debug, dict):
                item["detector"] = oopif_debug
            path_debug.append(item)

            if candidate.get("kind") != "image-grid" or not bool(plausibility.get("plausible")):
                continue
            rank = self._candidate_rank(candidate)
            if rank > best_rank:
                best = candidate
                best_rank = rank

        self._last_oopif_path_debug = path_debug[:32]
        return best

    def _discover_global(self) -> Dict[str, Any]:
        state = super()._discover_global()
        debug = [item for item in state.get("discoveryDebug") or [] if isinstance(item, dict)]
        if self._last_oopif_path_debug:
            debug.append({
                "producer": "oopif-path-diagnostics",
                "count": len(self._last_oopif_path_debug),
                "paths": self._last_oopif_path_debug[:16],
            })
        return {**state, "discoveryDebug": debug}

    def _snapshot_oopif_path(self, path: Iterable[str]) -> Dict[str, Any]:
        state = super()._snapshot_oopif_path(path)
        if bool(state.get("complete")) or bool(state.get("failed")):
            return state

        clean_path = [str(value) for value in path if str(value)]
        evaluate = getattr(self._sb, "ares_oopif_evaluate", None)
        if not clean_path or not callable(evaluate):
            return state
        try:
            evaluated = evaluate(clean_path, _OUTCOME_SCRIPT, [])
        except Exception:
            return state
        if not isinstance(evaluated, dict):
            return state
        value = evaluated.get("value")
        if not isinstance(value, dict):
            return state
        complete = bool(value.get("complete"))
        failed = bool(value.get("failed"))
        if not complete and not failed:
            return state
        return {**state, "complete": complete, "failed": failed}
