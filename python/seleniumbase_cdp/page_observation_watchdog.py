from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from interaction_policy import InteractionPolicy


class PageObservationWatchdog:
    """Cheap DOM/URL heartbeat. It never captures screenshots or runs vision."""

    def __init__(self, seleniumbase_cdp: Any, policy: InteractionPolicy) -> None:
        self._sb = seleniumbase_cdp
        self._policy = policy
        self._last: Dict[str, Any] | None = None
        self._generation = 0

    def reset(self) -> None:
        self._last = None

    def poll(self) -> Dict[str, Any]:
        current = self._snapshot()
        previous = self._last
        events: List[str] = []

        if previous is None:
            events.append("page-load")
        else:
            if current.get("url") != previous.get("url"):
                events.append("navigation")
            if int(current.get("iframes") or 0) > int(previous.get("iframes") or 0):
                events.append("iframe-added")
            if int(current.get("modals") or 0) > int(previous.get("modals") or 0):
                events.append("modal-opened")
            if int(current.get("sliders") or 0) > int(previous.get("sliders") or 0):
                events.append("slider-candidate")
            if int(current.get("grids") or 0) > int(previous.get("grids") or 0):
                events.append("grid-candidate")
            if int(current.get("canvas") or 0) > int(previous.get("canvas") or 0):
                events.append("canvas-candidate")
            if current.get("fingerprint") != previous.get("fingerprint"):
                events.append("layout-generation-changed")

        changed = previous is None or current.get("actionFingerprint") != previous.get("actionFingerprint")
        if changed:
            self._generation += 1
        self._last = current
        return {
            **current,
            "events": sorted(set(events)),
            "changed": changed,
            "generation": self._generation,
            "textHints": list(current.get("textHints") or []),
        }

    def status(self) -> Dict[str, Any]:
        return {
            "generation": self._generation,
            "last": dict(self._last or {}),
            "intervalSeconds": self._policy.watchdog_interval_seconds,
        }

    def _snapshot(self) -> Dict[str, Any]:
        selectors = self._policy.fingerprint_selectors
        priorities = self._policy.priority_selectors
        hints = self._policy.text_hints
        script = f"""
        (() => {{
          const selectors = {json.dumps(selectors)};
          const priorities = {json.dumps(priorities)};
          const hints = {json.dumps(hints)};
          const safeCount = selector => {{
            try {{ return document.querySelectorAll(selector).length; }} catch (_) {{ return 0; }}
          }};
          const safeElements = selector => {{
            try {{ return [...document.querySelectorAll(selector)]; }} catch (_) {{ return []; }}
          }};
          const body = document.body;
          const text = (body?.innerText || '').toLowerCase().slice(0, 12000);
          const matches = hints.filter(hint => hint && text.includes(String(hint).toLowerCase())).slice(0, 16);
          const selectorCounts = selectors.map(selector => [selector, safeCount(selector)]);
          const priorityCounts = priorities.map(selector => [selector, safeCount(selector)]);
          const interactive = safeCount('input,button,select,textarea,[contenteditable=true],[role=button],[tabindex]');
          const inputs = safeCount('input,textarea,[contenteditable=true]');
          const buttons = safeCount('button,[role=button],input[type=submit]');
          const iframes = safeCount('iframe');
          const modals = safeCount('dialog,[role=dialog],[class*=modal i],[class*=overlay i]');
          const sliders = safeCount('input[type=range],[role=slider],[aria-valuenow],[class*=slider i]');
          const grids = safeCount('[class*=grid i],[class*=tile i],[class*=cell i]');
          const canvas = safeCount('canvas');

          const watched = [];
          const seen = new Set();
          const watchedSelectors = [...selectors, ...priorities, 'img', 'canvas', 'input[type=range]', '[role=slider]'];
          for (const selector of watchedSelectors) {{
            for (const el of safeElements(selector)) {{
              if (seen.has(el)) continue;
              seen.add(el);
              watched.push([
                el.tagName || '',
                el.id || '',
                typeof el.className === 'string' ? el.className.slice(0, 160) : '',
                el.getAttribute?.('role') || '',
                el.getAttribute?.('aria-label') || '',
                el.getAttribute?.('aria-valuenow') || '',
                el.getAttribute?.('data-state') || '',
                el.getAttribute?.('value') || '',
                el.currentSrc || el.getAttribute?.('src') || '',
                el.getAttribute?.('alt') || '',
              ]);
              if (watched.length >= 96) break;
            }}
            if (watched.length >= 96) break;
          }}

          return {{
            url: location.href,
            title: document.title || '',
            readyState: document.readyState || '',
            childCount: body?.childElementCount || 0,
            nodeCount: safeCount('body *'),
            scrollHeight: Math.round(document.documentElement?.scrollHeight || 0),
            interactive, inputs, buttons, iframes, modals, sliders, grids, canvas,
            selectorCounts, priorityCounts, textHints: matches, watched,
          }};
        }})()
        """
        try:
            value = self._evaluate(script)
        except Exception:
            value = {}
        if not isinstance(value, dict):
            value = {}

        # OOPIF children can be alive in Chromium's flattened CDP target tree
        # even when the top-level DOM heartbeat does not expose a useful iframe
        # count yet. The visual runtime is intentionally driven by a bounded
        # frame heartbeat, so fold discovered child frame paths into the
        # effective iframe count. FlatCdpTargetRegistry.discover() only returns
        # child paths (never the root page), therefore this does not create a
        # false heartbeat for ordinary single-frame pages.
        oopif_frames = self._oopif_frame_count()
        try:
            dom_iframes = int(value.get("iframes") or 0)
        except (TypeError, ValueError):
            dom_iframes = 0
        value["domIframes"] = dom_iframes
        value["oopifFrames"] = oopif_frames
        value["iframes"] = max(dom_iframes, oopif_frames)

        stable = {
            key: value.get(key)
            for key in (
                "url", "title", "readyState", "childCount", "nodeCount", "scrollHeight",
                "interactive", "inputs", "buttons", "iframes", "domIframes", "oopifFrames",
                "modals", "sliders", "grids", "canvas", "selectorCounts", "priorityCounts",
                "textHints", "watched",
            )
        }
        raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        stable["fingerprint"] = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()

        action_stable = {
            key: stable.get(key)
            for key in (
                "url", "readyState", "interactive", "inputs", "buttons", "iframes", "domIframes",
                "oopifFrames", "modals", "sliders", "grids", "canvas", "selectorCounts",
                "priorityCounts", "textHints", "watched",
            )
        }
        action_raw = json.dumps(action_stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        stable["actionFingerprint"] = hashlib.sha256(action_raw.encode("utf-8", errors="ignore")).hexdigest()
        return stable

    def _oopif_frame_count(self) -> int:
        discover = getattr(self._sb, "ares_oopif_discover", None)
        if not callable(discover):
            return 0
        try:
            entries = discover()
        except Exception:
            return 0
        if not isinstance(entries, list):
            return 0
        return sum(
            1
            for entry in entries
            if isinstance(entry, dict)
            and isinstance(entry.get("path"), list)
            and len(entry.get("path") or []) > 0
        )

    def _evaluate(self, script: str) -> Any:
        evaluator = getattr(self._sb, "evaluate", None)
        if callable(evaluator):
            return evaluator(script)
        executor = getattr(self._sb, "execute_script", None)
        if callable(executor):
            return executor(f"return {script};")
        raise RuntimeError("SeleniumBase CDP adapter has no script evaluation method")
