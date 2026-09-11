from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, List, Tuple

from authorized_grid_action_executor import AuthorizedGridActionExecutor
from cdp_challenge_observer import CdpChallengeObserver
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


_RELOAD_SELECTORS = (
    "#recaptcha-reload-button",
    "button#recaptcha-reload-button",
    ".rc-button-reload",
    "[id*='reload' i]",
    "button[title*='new challenge' i]",
    "button[title*='neue' i]",
    "button[title*='neu' i]",
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
        self._challenge_observer = CdpChallengeObserver(seleniumbase_cdp)

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
        if current.get("kind") != "image-grid":
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
        # Click directly from the OBSERVED marks (stable coordinates) instead of
        # re-resolving markIds after every click. Clicking a tile changes its
        # visual signature, which makes the markId lookup fail and aborts the
        # loop early - leaving remaining tiles and the submit unclicked.
        for position, selected_index in enumerate(selected):
            if not (0 <= selected_index < len(observed_marks)):
                continue
            mark = observed_marks[selected_index]
            mark_id = str(mark.get("markId") or "")
            target = self._mark_center(mark)
            if target is None:
                target = self._grid_center_for_index(observed, observed_marks, selected_index)
            if target is None:
                continue
            if not self._cdp_click_tile(target, mark, current):
                refreshed = self._site_adapter.poll()
                return {
                    "clickedIndexes": clicked_indexes,
                    "clickedMarkIds": clicked_mark_ids,
                    "requestedMarkIds": sorted(requested),
                    "submitted": False,
                    "reason": "stale-grid-during-selection" if self._grid_changed(observed, refreshed) else "click-unresolved",
                    "state": refreshed,
                    "clickOrder": list(clicked_indexes),
                }
            clicked_indexes.append(selected_index)
            clicked_mark_ids.append(mark_id)
            if position < len(selected) - 1:
                self._sleep_between_clicks()

        submitted = False
        still_present = False
        risk_flagged = False
        reloaded = False
        reason = ""
        # Do NOT re-poll the grid here. The OOPIF grid evaluate can block while
        # reCAPTCHA re-renders right after the last click; that hung the whole
        # step and the confirm button was never reached (task stalled until
        # timeout). The confirm button position is stable, so use the last known
        # state captured before the click loop.
        if submit:
            if current.get("kind") != "image-grid":
                reason = "stale-grid-before-submit"
            else:
                # Human-like pause after the last tile before moving to confirm
                # (simulates the eye switch to the button, avoids a timing flag).
                time.sleep(max(0.5, self._policy.grid_submit_delay_seconds))
                # Resolve the confirm button FRESH from the live DOM. reCAPTCHA
                # resizes the iframe and flips the button disabled->active once
                # tiles are selected, so the pre-click submitBounds miss the
                # moved/enabled button.
                submit_point = self._fresh_submit_point(current)
                if submit_point is not None:
                    # Exactly ONE clean click. Never poke the confirm button a
                    # second time: repeated clicks look like an attack and get
                    # the session risk-locked by Google.
                    submitted = self._cdp_click_to(submit_point, current)
                if not submitted:
                    reason = "submit-not-confirmed"
                else:
                    # Give the page time to process the answer, then check if
                    # the puzzle survived (reCAPTCHA "Versuche es bitte erneut").
                    time.sleep(2.0)
                    still_present = self._challenge_still_present(current)
                    if still_present and self._same_challenge(current):
                        # Identical puzzle after the answer => server-side risk
                        # lock (motion pattern too rigid). Do NOT click the
                        # confirm button again; request a completely fresh
                        # puzzle via the reload control instead.
                        risk_flagged = True
                        reload_result = self.reload_challenge(current)
                        reloaded = bool((reload_result or {}).get("reloaded"))

        return {
            "clickedIndexes": clicked_indexes,
            "clickedMarkIds": clicked_mark_ids,
            "requestedMarkIds": sorted(requested),
            "submitted": submitted,
            "challengeStillPresent": still_present,
            "riskFlagged": risk_flagged,
            "reloaded": reloaded,
            "reason": reason,
            "state": current,
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
            if self._cdp_click_with_retry(target, latest):
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
            challenge_present = self._challenge_active()
            window = self._puzzle_window(latest)
            submit_point = (
                self._preferred_submit_point(window)
                or self._confirmation_center(latest)
            )
            if submit_point is None and not challenge_present:
                submit_point = self._submit_center(latest)
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

    def _cdp_click_with_retry(
        self,
        target: Tuple[float, float],
        state: Dict[str, Any],
        attempts: int = 3,
    ) -> bool:
        """Retry a transient CDP click failure on the SAME stable coordinate.

        The cursor path can fail intermittently while the challenge iframe is
        re-rendering (the same target then succeeds on a later attempt). Aborting
        the whole selection on the first failure left tiles unclicked and the
        submit unreachable, so retry before giving up."""
        for attempt in range(max(1, attempts)):
            if self._cdp_click_to(target, state):
                return True
            if attempt < attempts - 1:
                time.sleep(0.25)
        return False

    def _cdp_click_tile(
        self,
        target: Tuple[float, float],
        mark: Dict[str, Any],
        state: Dict[str, Any],
        attempts: int = 3,
    ) -> bool:
        """Click a reCAPTCHA tile without ever toggling it back off.

        reCAPTCHA toggles a tile on every click, so a blind retry could deselect
        a tile whose first click actually registered. Before each retry the tile's
        live aria-checked state is read; if it is already selected the click is
        treated as successful and no second click is sent."""
        if self._cdp_click_to(target, state):
            return True
        if self._tile_selected(mark, state) is True:
            return True
        for attempt in range(1, max(1, attempts)):
            time.sleep(0.25)
            if self._cdp_click_to(target, state):
                return True
            if self._tile_selected(mark, state) is True:
                return True
        return False

    def _tile_selected(self, mark: Dict[str, Any], state: Dict[str, Any]) -> bool | None:
        """Read the live aria-checked state of a tile. None when unreadable."""
        selector = str(mark.get("selector") or "").strip()
        scope = str(state.get("scope") or "")
        if not selector or not scope.startswith("oopif:"):
            return None
        evaluate = getattr(self._sb, "ares_oopif_evaluate", None)
        if not callable(evaluate):
            return None
        frame_path = [seg for seg in scope[len("oopif:"):].split("/") if seg]
        if not frame_path:
            return None
        script = f"""
        return (() => {{
          const el = document.querySelector({json.dumps(selector)});
          if (!el) return null;
          const value = el.getAttribute('aria-checked');
          return value === null ? null : value === 'true';
        }})();
        """
        try:
            result = evaluate(frame_path, script, [])
        except Exception:
            return None
        if not isinstance(result, dict):
            return None
        value = result.get("value")
        return value if isinstance(value, bool) else None

    def _confirmation_center(self, state: Dict[str, Any] | None = None) -> Tuple[float, float] | None:
        """Locate a confirm/continue control via the passive DOM-domain walk.

        No JavaScript is injected for frame element discovery. The recursive
        walk uses DOM.describeNode -> contentDocument and DOM.getContentQuads,
        which already returns top-level viewport coordinates.
        """
        overrides = getattr(self._site_adapter, "_overrides", {})
        window = self._puzzle_window(state) if isinstance(state, dict) else None
        return self._challenge_observer.confirmation_center(
            window=window,
            accepted_texts=_CONFIRM_TEXT,
            override_selector=overrides.get("submit"),
            grid_in_frame=self._grid_in_frame(state),
        )

    @staticmethod
    def _grid_in_frame(state: Dict[str, Any] | None) -> bool | None:
        """True/False when the grid scope is known, None when undetectable."""
        if not isinstance(state, dict):
            return None
        scope = str(state.get("scope") or "")
        if not scope:
            return None
        if scope.startswith("oopif:") or "iframe" in scope:
            return True
        return False

    def _oopif_submit_center(self, state: Dict[str, Any]) -> Tuple[float, float] | None:
        """Find the submit button inside the OOPIF grid frame and return its
        viewport center. reCAPTCHA's confirm button ("Weiter"/"Senden") only
        appears AFTER tiles are clicked, and it lives inside the cross-origin
        iframe the main-document JS search cannot reach."""
        evaluate = getattr(self._sb, "ares_oopif_evaluate", None)
        if not callable(evaluate):
            return None
        scope = str(state.get("scope") or "")
        if not scope.startswith("oopif:"):
            return None
        frame_path = [seg for seg in scope[len("oopif:"):].split("/") if seg]
        if not frame_path:
            return None
        script = r"""
        return (() => {
          const rx = /(weiter|senden|bestätigen|bestaetigen|verify|submit|confirm|next)/i;
          const visible = el => {
            if (!el?.getBoundingClientRect) return false;
            const r = el.getBoundingClientRect(), s = getComputedStyle(el);
            return r.width >= 20 && r.height >= 20 && s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
          };
          const els = [...(document.querySelectorAll?.('button,input[type="submit"],[role="button"]') || [])].filter(visible);
          const preferred = els.find(el => el.matches?.('#recaptcha-verify-button, .rc-button-default')) || null;
          const match = preferred || els.find(el => rx.test((el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '').trim()));
          if (!match) return null;
          const r = match.getBoundingClientRect();
          return {x: r.left + r.width / 2, y: r.top + r.height / 2};
        })();
        """
        try:
            result = evaluate(frame_path, script, [])
        except Exception:
            return None
        if not isinstance(result, dict):
            return None
        value = result.get("value")
        if not isinstance(value, dict):
            return None
        try:
            x = float(value.get("x") or 0.0) + float(result.get("offsetX") or 0.0)
            y = float(value.get("y") or 0.0) + float(result.get("offsetY") or 0.0)
        except (TypeError, ValueError):
            return None
        if x < 0 or y < 0:
            return None
        return (x, y)

    @staticmethod
    def _grid_marks(state: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            mark for mark in state.get("marks") or []
            if isinstance(mark, dict) and mark.get("role") == "grid-tile"
        ]

    def reload_challenge(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Request a fresh captcha image by clicking its reload/refresh control.

        Used when the classifier finds no clear match (0 tiles): instead of
        idling into the timeout, refresh the puzzle and let the next cycle
        classify a clean grid."""
        target = self._oopif_control_center(state, _RELOAD_SELECTORS)
        if target is None:
            target = self._document_control_center(_RELOAD_SELECTORS)
        if target is None:
            return {"reloaded": False, "reason": "reload-control-not-found"}
        clicked = self._cdp_click_to(target, state)
        return {
            "reloaded": bool(clicked),
            "reason": "" if clicked else "reload-click-failed",
            "target": {"x": float(target[0]), "y": float(target[1])},
        }

    def _challenge_still_present(self, state: Dict[str, Any]) -> bool:
        """True when the captcha is still shown after an answer was submitted.

        Detects reCAPTCHA's retry state ("Versuche es bitte erneut") or a still
        visible image grid, so the caller re-runs recognition on the fresh
        puzzle instead of declaring success or hanging."""
        script = r"""
        (() => {
          const visible = el => {
            if (!el?.getBoundingClientRect) return false;
            const r = el.getBoundingClientRect(), s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
          };
          const roots = [];
          const walk = doc => {
            if (!doc) return;
            roots.push(doc);
            for (const frame of doc.querySelectorAll?.('iframe') || []) {
              try { if (frame.contentDocument) walk(frame.contentDocument); } catch (_) {}
            }
          };
          walk(document);
          for (const doc of roots) {
            const body = doc.body ? String(doc.body.innerText || doc.body.textContent || '') : '';
            if (/(versuche es bitte erneut|try again|erneut versuchen|probiere es erneut)/i.test(body)) return true;
            const grid = doc.querySelector('.rc-imageselect, table.rc-imageselect-table, .rc-imageselect-challenge');
            if (grid && visible(grid)) return true;
            const reload = doc.querySelector('#recaptcha-reload-button, .rc-button-reload');
            if (reload && visible(reload)) return true;
          }
          return false;
        })()
        """
        value = self._evaluate_in_scope(state, script)
        return bool(value) if isinstance(value, bool) else False

    def _evaluate_in_scope(self, state: Dict[str, Any], script: str) -> Any:
        """Run a script in the (OOPIF) captcha frame when possible, else in the
        main document (which also walks same-origin frames)."""
        scope = str(state.get("scope") or "")
        evaluate = getattr(self._sb, "ares_oopif_evaluate", None)
        if scope.startswith("oopif:") and callable(evaluate):
            frame_path = [seg for seg in scope[len("oopif:"):].split("/") if seg]
            if frame_path:
                try:
                    result = evaluate(frame_path, script, [])
                    if isinstance(result, dict):
                        return result.get("value")
                except Exception:
                    pass
        try:
            return self._evaluate(script)
        except Exception:
            return None

    def _same_challenge(self, state: Dict[str, Any]) -> bool:
        """True when the still-shown puzzle is the same one (no new image was
        dealt) — i.e. the confirm click likely missed instead of answering."""
        before = str(state.get("instruction") or "").strip()
        if not before:
            return False
        return self._challenge_instruction(state) == before

    def _challenge_instruction(self, state: Dict[str, Any]) -> str:
        script = r"""
        (() => {
          const el = document.querySelector('.rc-imageselect-instructions, .rc-imageselect-desc, .rc-imageselect-desc-wrapper');
          return el ? String(el.innerText || el.textContent || '').trim() : '';
        })()
        """
        value = self._evaluate_in_scope(state, script)
        return str(value or "").strip()

    def _oopif_control_center(
        self,
        state: Dict[str, Any],
        selectors: Iterable[str],
    ) -> Tuple[float, float] | None:
        evaluate = getattr(self._sb, "ares_oopif_evaluate", None)
        if not callable(evaluate):
            return None
        scope = str(state.get("scope") or "")
        if not scope.startswith("oopif:"):
            return None
        frame_path = [seg for seg in scope[len("oopif:"):].split("/") if seg]
        if not frame_path:
            return None
        script = f"""
        return (() => {{
          const selectors = {json.dumps(list(selectors))};
          const visible = el => {{
            if (!el?.getBoundingClientRect) return false;
            const r = el.getBoundingClientRect(), s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
          }};
          for (const selector of selectors) {{
            const el = [...document.querySelectorAll(selector)].find(visible);
            if (el) {{ const r = el.getBoundingClientRect(); return {{x: r.left + r.width/2, y: r.top + r.height/2}}; }}
          }}
          return null;
        }})();
        """
        try:
            result = evaluate(frame_path, script, [])
        except Exception:
            return None
        if not isinstance(result, dict):
            return None
        value = result.get("value")
        if not isinstance(value, dict):
            return None
        try:
            x = float(value.get("x") or 0.0) + float(result.get("offsetX") or 0.0)
            y = float(value.get("y") or 0.0) + float(result.get("offsetY") or 0.0)
        except (TypeError, ValueError):
            return None
        if x < 0 or y < 0:
            return None
        return (x, y)

    def _document_control_center(self, selectors: Iterable[str]) -> Tuple[float, float] | None:
        script = f"""
        (() => {{
          const selectors = {json.dumps(list(selectors))};
          const visible = el => {{
            if (!el?.getBoundingClientRect) return false;
            const r = el.getBoundingClientRect(), s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
          }};
          const roots = [];
          const walk = (doc, ox, oy) => {{
            if (!doc) return;
            roots.push({{doc, ox, oy}});
            for (const frame of doc.querySelectorAll?.('iframe') || []) {{
              try {{ if (frame.contentDocument) {{ const r = frame.getBoundingClientRect(); walk(frame.contentDocument, ox + r.left, oy + r.top); }} }} catch (_) {{}}
            }}
          }};
          walk(document, 0, 0);
          for (const root of roots) {{
            for (const selector of selectors) {{
              const el = [...root.doc.querySelectorAll(selector)].find(visible);
              if (el) {{ const r = el.getBoundingClientRect(); return {{x: root.ox + r.left + r.width/2, y: root.oy + r.top + r.height/2}}; }}
            }}
          }}
          return null;
        }})()
        """
        try:
            value = self._evaluate(script)
        except Exception:
            return None
        if not isinstance(value, dict):
            return None
        try:
            x = float(value.get("x"))
            y = float(value.get("y"))
        except (TypeError, ValueError):
            return None
        if x < 0 or y < 0:
            return None
        return (x, y)

    @classmethod
    def _grid_changed(cls, before: Dict[str, Any], after: Dict[str, Any]) -> bool:
        """True when the challenge grid was replaced/refreshed mid-selection.

        A hung CDP click usually means reCAPTCHA already swapped the challenge
        (e.g. after a wrong tile). Reporting that as a refresh lets the caller
        re-classify the NEW grid instead of aborting on a stale coordinate."""
        if not isinstance(after, dict) or after.get("kind") != "image-grid":
            return True
        before_sig = str(before.get("signature") or "")
        after_sig = str(after.get("signature") or "")
        if before_sig and after_sig:
            return before_sig != after_sig
        return cls._visual_fingerprint(before) != cls._visual_fingerprint(after)

    @classmethod
    def _visual_fingerprint(cls, state: Dict[str, Any]) -> Tuple[str, ...]:
        return tuple(
            str(mark.get("source") or mark.get("semanticVisualSignature") or "")
            for mark in cls._grid_marks(state)
        )

    @classmethod
    def _mark_center(cls, mark: Dict[str, Any]) -> Tuple[float, float] | None:
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

    @classmethod
    def _grid_center_for_index(cls, state: Dict[str, Any], marks: List[Dict[str, Any]], index: int) -> Tuple[float, float] | None:
        """Fall back to grid geometry when a mark is missing visualBounds.

        reCAPTCHA tiles are a regular square grid. Derive origin + tile size from
        the marks that DO have bounds, then compute the center of index from its
        row/column position. This keeps every selected tile clickable even when
        individual visualBounds are absent."""
        centers: List[Tuple[float, float] | None] = []
        for mark in marks:
            centers.append(cls._mark_center(mark))
        if not (0 <= index < len(marks)):
            return None
        direct = centers[index]
        if direct is not None:
            return direct

        columns = int(state.get("columns") or 0)
        rows = int(state.get("rows") or 0)
        known = [c for c in centers if c is not None]
        if columns < 2 or rows < 2 or not known:
            return None

        xs = [c[0] for c in known]
        ys = [c[1] for c in known]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        # Tile pitch: the step between adjacent tile centers.
        span_x = max_x - min_x
        span_y = max_y - min_y
        pitch_x = span_x / (columns - 1) if columns > 1 else 0.0
        pitch_y = span_y / (rows - 1) if rows > 1 else 0.0
        if pitch_x <= 0 or pitch_y <= 0:
            return None

        row = index // columns
        col = index % columns
        # Anchor at the top-left most known center (nearest to (min_x, min_y)).
        return (min_x + col * pitch_x, min_y + row * pitch_y)

    def _fresh_submit_point(self, state: Dict[str, Any]) -> Tuple[float, float] | None:
        """Resolve the confirm button from the live DOM, scoped to the puzzle.

        Prefer a fresh lookup (passive DOM-domain query / OOPIF evaluate) over
        the pre-click submitBounds, because reCAPTCHA shifts the button after
        the tiles are selected and only then flips it from disabled to active.
        While a challenge window is visible, main-page controls outside the
        puzzle window are never used.
        """
        challenge_present = self._challenge_active()
        window = self._puzzle_window(state)
        point = (
            self._preferred_submit_point(window)
            or self._oopif_submit_center(state)
            or self._confirmation_center(state)
        )
        if point is None and challenge_present:
            return None
        return point or self._submit_center(state)

    def _preferred_submit_point(
        self,
        window: Dict[str, float] | None,
    ) -> Tuple[float, float] | None:
        """Provider verify button via the passive CDP DOM observer (no JS)."""
        return self._challenge_observer.preferred_submit_center(window=window)

    def _challenge_active(self) -> bool:
        """True while a CAPTCHA challenge window is visibly present on screen.

        Passive DOM-domain detection only; no Runtime.evaluate JavaScript.
        """
        return self._challenge_observer.present()

    @classmethod
    def _puzzle_window(cls, state: Dict[str, Any] | None) -> Dict[str, float] | None:
        """Bounding window around the grid tiles that still counts as the puzzle."""
        if not isinstance(state, dict):
            return None
        lefts: List[float] = []
        tops: List[float] = []
        rights: List[float] = []
        bottoms: List[float] = []
        for mark in cls._grid_marks(state):
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
            lefts.append(x)
            tops.append(y)
            rights.append(x + width)
            bottoms.append(y + height)
        if not lefts:
            return None
        return {
            "left": min(lefts) - 40.0,
            "top": min(tops) - 12.0,
            "right": max(rights) + 40.0,
            "bottom": max(bottoms) + 200.0,
        }

    @classmethod
    def _submit_center(cls, state: Dict[str, Any]) -> Tuple[float, float] | None:
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
        point = (x + width / 2.0, y + height / 2.0)
        window = cls._puzzle_window(state)
        if window is not None and not (
            window["left"] <= point[0] <= window["right"]
            and window["top"] <= point[1] <= window["bottom"]
        ):
            return None
        return point

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
