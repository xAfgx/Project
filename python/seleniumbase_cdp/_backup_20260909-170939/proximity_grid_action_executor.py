from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, List, Tuple

from authorized_grid_action_executor import AuthorizedGridActionExecutor
from cursor_path_provider import CursorPathProvider
from interaction_policy import InteractionPolicy


_CONFIRM_TEXT = (
    "ok",
    "yes",
    "ja",
    "verify",
    "bestätigen",
    "bestaetigen",
    "bestätigung",
    "weiter",
    "continue",
    "accept",
    "accept all",
    "alles akzeptieren",
    "akzeptieren",
    "allow",
    "zulassen",
    "prüfen",
    "pruefen",
    "überprüfen",
    "ueberpruefen",
    "absenden",
    "senden",
    "fertig",
    "abschließen",
    "abschliessen",
    "submit",
    "confirm",
    "check",
    "done",
    "next",
)


class ProximityGridActionExecutor(AuthorizedGridActionExecutor):
    """Execute grid selections through the existing CDP cursor path only."""

    def __init__(
        self,
        seleniumbase_cdp: Any,
        site_adapter: Any,
        policy: InteractionPolicy | None = None,
        cursor: CursorPathProvider | None = None,
    ) -> None:
        super().__init__(seleniumbase_cdp, site_adapter)
        self._policy = policy or InteractionPolicy()
        self._cursor = cursor or CursorPathProvider()
        self._cursor_point: Tuple[float, float] | None = None

    def apply(self, indexes: Iterable[int], *, submit: bool = True) -> Dict[str, Any]:
        state = self._site_adapter.poll()
        selected = self._ordered_indexes(
            state,
            self._clean_indexes(indexes, int(state.get("tileCount") or 0)),
        )
        marks = self._grid_marks(state)
        mark_ids = [
            str(marks[index].get("markId") or "")
            for index in selected
            if 0 <= index < len(marks) and marks[index].get("markId")
        ]
        if len(mark_ids) == len(selected) and mark_ids:
            result = self.apply_marks(mark_ids, submit=submit, expected_state=state)
        else:
            result = self._apply_state(state, selected, submit=submit)
        result["clickOrder"] = list(result.get("clickedIndexes") or selected)
        return result

    def apply_marks(
        self,
        mark_ids: Iterable[str],
        *,
        submit: bool = True,
        expected_state: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        observed = dict(expected_state) if isinstance(expected_state, dict) else self._site_adapter.poll()
        requested = {str(value) for value in mark_ids if str(value)}
        observed_marks = self._grid_marks(observed)
        selected = [
            index for index, mark in enumerate(observed_marks)
            if str(mark.get("markId") or "") in requested
        ]
        selected = self._ordered_indexes(observed, selected)
        ordered_mark_ids = [
            str(observed_marks[index].get("markId") or "")
            for index in selected
            if 0 <= index < len(observed_marks) and observed_marks[index].get("markId")
        ]
        if observed.get("kind") != "image-grid" or len(ordered_mark_ids) != len(requested):
            return {
                "clickedIndexes": [],
                "clickedMarkIds": [],
                "requestedMarkIds": sorted(requested),
                "submitted": False,
                "reason": "stale-grid-before-selection",
                "state": self._site_adapter.poll(),
            }

        expected_visual = self._visual_fingerprint(observed)
        current = self._site_adapter.poll()
        if current.get("kind") != "image-grid" or self._visual_fingerprint(current) != expected_visual:
            return {
                "clickedIndexes": [],
                "clickedMarkIds": [],
                "requestedMarkIds": sorted(requested),
                "submitted": False,
                "reason": "stale-grid-before-selection",
                "state": current,
            }

        clicked_indexes: List[int] = []
        clicked_mark_ids: List[str] = []
        for position, mark_id in enumerate(ordered_mark_ids):
            current = self._site_adapter.poll()
            if current.get("kind") != "image-grid":
                return {
                    "clickedIndexes": clicked_indexes,
                    "clickedMarkIds": clicked_mark_ids,
                    "requestedMarkIds": sorted(requested),
                    "submitted": False,
                    "reason": "stale-grid-during-selection",
                    "state": current,
                    "clickOrder": list(clicked_indexes),
                }

            current_marks = self._grid_marks(current)
            resolved_index = next(
                (
                    index for index, mark in enumerate(current_marks)
                    if str(mark.get("markId") or "") == mark_id
                ),
                -1,
            )
            if resolved_index < 0:
                return {
                    "clickedIndexes": clicked_indexes,
                    "clickedMarkIds": clicked_mark_ids,
                    "requestedMarkIds": sorted(requested),
                    "submitted": False,
                    "reason": "stale-grid-during-selection",
                    "state": current,
                    "clickOrder": list(clicked_indexes),
                }

            target = self._mark_center(current_marks[resolved_index])
            if target is None or not self._cdp_click_to(target, current):
                return {
                    "clickedIndexes": clicked_indexes,
                    "clickedMarkIds": clicked_mark_ids,
                    "requestedMarkIds": sorted(requested),
                    "submitted": False,
                    "reason": "click-unresolved",
                    "state": current,
                    "clickOrder": list(clicked_indexes),
                }
            clicked_indexes.append(resolved_index)
            clicked_mark_ids.append(mark_id)
            if position < len(ordered_mark_ids) - 1:
                self._sleep_between_clicks()

        submitted = False
        reason = ""
        current = self._site_adapter.poll()
        if submit:
            if current.get("kind") != "image-grid":
                reason = "stale-grid-before-submit"
            else:
                delay = self._policy.grid_submit_delay_seconds
                if delay > 0:
                    time.sleep(delay)
                submit_point = self._submit_center(current) or self._confirmation_center()
                if submit_point is not None:
                    submitted = self._cdp_click_to(submit_point, current)
                if not submitted:
                    reason = "submit-not-confirmed"

        final_state = self._site_adapter.poll()
        return {
            "clickedIndexes": clicked_indexes,
            "clickedMarkIds": clicked_mark_ids,
            "requestedMarkIds": sorted(requested),
            "submitted": submitted,
            "reason": reason,
            "state": final_state,
            "clickOrder": list(clicked_indexes),
        }

    def _apply_state(self, state: Dict[str, Any], selected: List[int], *, submit: bool) -> Dict[str, Any]:
        selected = self._clean_indexes(selected, int(state.get("tileCount") or 0))
        if state.get("kind") != "image-grid" or not selected:
            return {"clickedIndexes": [], "clickedMarkIds": [], "submitted": False, "state": state}

        marks = self._grid_marks(state)
        clicked: List[int] = []
        clicked_mark_ids: List[str] = []

        for position, index in enumerate(selected):
            latest = self._site_adapter.poll()
            latest_marks = self._grid_marks(latest)
            target = self._mark_center(latest_marks[index]) if 0 <= index < len(latest_marks) else None
            if target is None:
                continue
            if self._cdp_click_to(target, latest):
                clicked.append(index)
                mark_id = str(latest_marks[index].get("markId") or "")
                if mark_id:
                    clicked_mark_ids.append(mark_id)
            if position < len(selected) - 1:
                self._sleep_between_clicks()

        submitted = False
        if submit and len(clicked) == len(selected):
            latest = self._site_adapter.poll()
            delay = self._policy.grid_submit_delay_seconds
            if delay > 0:
                time.sleep(delay)
            submit_point = self._submit_center(latest) or self._confirmation_center()
            if submit_point is not None:
                submitted = self._cdp_click_to(submit_point, latest)

        return {
            "clickedIndexes": clicked,
            "clickedMarkIds": clicked_mark_ids,
            "submitted": submitted,
            "state": self._site_adapter.poll(),
        }

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
        if bool(result.get("clicked")):
            self._cursor_point = target
            return True
        return False

    def _confirmation_center(self) -> Tuple[float, float] | None:
        overrides = getattr(self._site_adapter, "_overrides", {})
        selector = overrides.get("submit") or 'button[type="submit"],input[type="submit"],button,[role="button"]'
        script = f"""
        (() => {{
          const selector = {json.dumps(selector)};
          const accepted = new Set({json.dumps(list(_CONFIRM_TEXT))});
          const norm = value => String(value || '').trim().toLowerCase().replace(/\\s+/g, ' ');
          const confirmMatch = value => {{
            if (accepted.has(value)) return true;
            for (const word of accepted) {{
              if (word.length >= 3 && value.includes(word)) return true;
            }}
            return false;
          }};
          const visible = el => {{
            if (!el?.getBoundingClientRect) return false;
            const r = el.getBoundingClientRect(), s = getComputedStyle(el);
            return r.width >= 20 && r.height >= 20 &&
              s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
          }};
          const roots = [];
          const walk = (doc, offsetX, offsetY) => {{
            if (!doc) return;
            roots.push({{doc, offsetX, offsetY}});
            for (const frame of doc.querySelectorAll?.('iframe') || []) {{
              try {{
                if (!frame.contentDocument) continue;
                const r = frame.getBoundingClientRect();
                walk(frame.contentDocument, offsetX + r.left, offsetY + r.top);
              }} catch (_) {{}}
            }}
          }};
          walk(document, 0, 0);

          const candidates = [];
          for (const root of roots) {{
            for (const el of root.doc.querySelectorAll?.(selector) || []) {{
              if (!visible(el)) continue;
              const text = norm(el.innerText || el.textContent || el.value || el.getAttribute('aria-label'));
              if (!confirmMatch(text) && !{str(bool(overrides.get("submit"))).lower()}) continue;
              const r = el.getBoundingClientRect();
              candidates.push({{
                x: root.offsetX + r.left + r.width / 2,
                y: root.offsetY + r.top + r.height / 2,
                preferred: confirmMatch(text)
              }});
            }}
          }}
          candidates.sort((a, b) => Number(b.preferred) - Number(a.preferred));
          return candidates[0] || null;
        }})()
        """
        try:
            value = self._evaluate(script)
            if not isinstance(value, dict):
                return None
            x = float(value.get("x"))
            y = float(value.get("y"))
            if x < 0 or y < 0:
                return None
            return (x, y)
        except Exception:
            return None

    @staticmethod
    def _grid_marks(state: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            mark for mark in state.get("marks") or []
            if isinstance(mark, dict) and mark.get("role") == "grid-tile"
        ]

    @classmethod
    def _visual_fingerprint(cls, state: Dict[str, Any]) -> Tuple[str, ...]:
        return tuple(
            str(mark.get("source") or mark.get("semanticVisualSignature") or "")
            for mark in cls._grid_marks(state)
        )

    @staticmethod
    def _mark_center(mark: Dict[str, Any]) -> Tuple[float, float] | None:
        bounds = mark.get("visualBounds")
        if not isinstance(bounds, dict):
            return None
        try:
            x = float(bounds.get("x") or 0.0)
            y = float(bounds.get("y") or 0.0)
            width = float(bounds.get("width") or 0.0)
            height = float(bounds.get("height") or 0.0)
        except (TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None
        return (x + width / 2.0, y + height / 2.0)

    @staticmethod
    def _submit_center(state: Dict[str, Any]) -> Tuple[float, float] | None:
        bounds = state.get("submitBounds")
        if not isinstance(bounds, dict):
            return None
        try:
            x = float(bounds.get("x") or 0.0)
            y = float(bounds.get("y") or 0.0)
            width = float(bounds.get("width") or 0.0)
            height = float(bounds.get("height") or 0.0)
        except (TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None
        return (x + width / 2.0, y + height / 2.0)

    def _sleep_between_clicks(self) -> None:
        delay = self._policy.grid_click_delay_seconds
        if delay > 0:
            time.sleep(delay)

    @staticmethod
    def _clean_indexes(values: Iterable[int], count: int) -> List[int]:
        clean = set()
        for value in values:
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= index < count:
                clean.add(index)
        return sorted(clean)

    @classmethod
    def _ordered_indexes(cls, state: Dict[str, Any], selected: List[int]) -> List[int]:
        if len(selected) <= 1:
            return list(selected)

        marks = cls._grid_marks(state)
        centers: Dict[int, Tuple[float, float]] = {}
        for index in selected:
            if not (0 <= index < len(marks)):
                continue
            center = cls._mark_center(marks[index])
            if center is not None:
                centers[index] = center

        if len(centers) != len(selected):
            return list(selected)

        remaining = set(selected)
        current = min(remaining, key=lambda index: (centers[index][1], centers[index][0], index))
        order = [current]
        remaining.remove(current)

        while remaining:
            cx, cy = centers[current]
            current = min(
                remaining,
                key=lambda index: (
                    (centers[index][0] - cx) ** 2 + (centers[index][1] - cy) ** 2,
                    centers[index][1],
                    centers[index][0],
                    index,
                ),
            )
            order.append(current)
            remaining.remove(current)

        return order
