from __future__ import annotations

import hashlib
import math
import time
from typing import Any, Dict, List


_CHALLENGE_HINTS = (
    "captcha",
    "recaptcha",
    "hcaptcha",
    "turnstile",
    "challenge",
)
_GRID_SIZES = {9: (3, 3), 16: (4, 4)}


class ChallengeStateTracker:
    """Observe challenge structure without solving or submitting it.

    The tracker is intentionally URL-agnostic. It inspects iframe structure and
    image-grid state through SeleniumBase's existing CDP objects, fingerprints
    visible grid images, and reports generations/changed indexes. It does not
    call any solver, click any challenge, inject tokens, or submit responses.
    """

    def __init__(self, seleniumbase_cdp: Any) -> None:
        self._sb = seleniumbase_cdp
        self._generation = 0
        self._last_signature = ""
        self._last_fingerprints: List[str] = []
        self._last_state: Dict[str, Any] = self._empty_state()

    @property
    def state(self) -> Dict[str, Any]:
        return dict(self._last_state)

    def poll(self) -> Dict[str, Any]:
        state = self._snapshot()
        signature = self._signature(state)
        fingerprints = list(state.get("tileFingerprints") or [])

        if signature != self._last_signature:
            self._generation += 1
            state["changedIndexes"] = self._changed_indexes(
                self._last_fingerprints,
                fingerprints,
            )
            self._last_signature = signature
            self._last_fingerprints = fingerprints
        else:
            state["changedIndexes"] = []

        state["generation"] = self._generation
        self._last_state = state
        return dict(state)

    def wait_for_stable_challenge(
        self,
        *,
        timeout: float = 2.0,
        poll_interval: float = 0.10,
        stable_samples: int = 2,
    ) -> Dict[str, Any]:
        """Wait only while a detected challenge is still changing.

        Pages with no challenge return immediately. This replaces the old fixed
        post-navigation sleep with state-based observation.
        """
        first = self.poll()
        if first.get("kind") == "none":
            return first

        deadline = time.monotonic() + max(0.0, timeout)
        previous = self._signature(first)
        stable = 0
        latest = first
        while time.monotonic() < deadline:
            time.sleep(max(0.01, poll_interval))
            latest = self.poll()
            current = self._signature(latest)
            if current == previous:
                stable += 1
                if stable >= max(1, stable_samples):
                    return latest
            else:
                stable = 0
                previous = current
        return latest

    def _snapshot(self) -> Dict[str, Any]:
        try:
            url = str(self._sb.get_current_url() or "")
        except Exception:
            url = ""

        try:
            frames = list(self._sb.find_elements("iframe") or [])
        except Exception:
            frames = []

        challenge_frames = 0
        best_fingerprints: List[str] = []
        best_rows = 0
        best_columns = 0

        for frame in frames:
            attrs = self._attrs(frame)
            descriptor = " ".join(
                str(attrs.get(key) or "")
                for key in ("id", "name", "title", "src", "class")
            ).lower()
            hinted = any(hint in descriptor for hint in _CHALLENGE_HINTS)

            images = self._nested_images(frame)
            dimensions = _GRID_SIZES.get(len(images))
            if hinted or dimensions:
                challenge_frames += 1

            if dimensions and len(images) > len(best_fingerprints):
                best_rows, best_columns = dimensions
                best_fingerprints = [self._fingerprint(image) for image in images]

        if best_fingerprints:
            kind = "image-grid"
        elif challenge_frames:
            kind = "challenge-frame"
        else:
            kind = "none"

        return {
            "kind": kind,
            "url": url,
            "frameCount": len(frames),
            "challengeFrameCount": challenge_frames,
            "rows": best_rows,
            "columns": best_columns,
            "tileCount": len(best_fingerprints),
            "tileFingerprints": best_fingerprints,
        }

    @staticmethod
    def _attrs(element: Any) -> Dict[str, Any]:
        try:
            attrs = getattr(element, "attrs", {}) or {}
            return dict(attrs) if isinstance(attrs, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _nested_images(frame: Any) -> List[Any]:
        try:
            images = list(frame.query_selector_all("img") or [])
        except Exception:
            return []
        # Only treat square grid-sized image sets as a challenge grid. This is
        # structural detection, not a provider/URL allowlist.
        side = int(math.sqrt(len(images))) if images else 0
        return images if side * side == len(images) and len(images) in _GRID_SIZES else []

    @classmethod
    def _fingerprint(cls, element: Any) -> str:
        attrs = cls._attrs(element)
        try:
            html = str(element.get_html() or "")
        except Exception:
            html = ""
        material = "\n".join(
            [
                str(attrs.get("src") or ""),
                str(attrs.get("style") or ""),
                str(attrs.get("class") or ""),
                html,
            ]
        )
        return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()

    @staticmethod
    def _signature(state: Dict[str, Any]) -> str:
        material = repr(
            (
                state.get("kind"),
                state.get("url"),
                state.get("frameCount"),
                state.get("challengeFrameCount"),
                state.get("rows"),
                state.get("columns"),
                tuple(state.get("tileFingerprints") or []),
            )
        )
        return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()

    @staticmethod
    def _changed_indexes(previous: List[str], current: List[str]) -> List[int]:
        length = max(len(previous), len(current))
        return [
            index
            for index in range(length)
            if (previous[index] if index < len(previous) else None)
            != (current[index] if index < len(current) else None)
        ]

    @staticmethod
    def _empty_state() -> Dict[str, Any]:
        return {
            "kind": "none",
            "url": "",
            "frameCount": 0,
            "challengeFrameCount": 0,
            "rows": 0,
            "columns": 0,
            "tileCount": 0,
            "tileFingerprints": [],
            "changedIndexes": [],
            "generation": 0,
        }
