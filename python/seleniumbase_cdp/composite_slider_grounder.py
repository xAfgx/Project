from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

from ddddocr_slider_provider import DdddOcrSliderProvider
from slider_target_grounder import SliderTargetGrounder


_RIGHT_END = re.compile(r"(?i)(right|to\s+the\s+end|until\s+the\s+end|end|finish|complete|rechts|bis\s+zum\s+ende|zum\s+ende|ende|fertig|abschlie)")
_LEFT_END = re.compile(r"(?i)(left|to\s+the\s+start|until\s+the\s+start|start|begin|links|bis\s+zum\s+anfang|zum\s+anfang|anfang|beginn)")
# Explicit "all the way to the end" copy. Unlike the generic right/left terms
# this is an unambiguous endpoint and may override a weak visual-region guess.
_EXPLICIT_END = re.compile(r"(?i)(bis\s+zum\s+ende|zum\s+ende|to\s+the\s+end|until\s+the\s+end|to\s+the\s+start|bis\s+zum\s+anfang)")


class CompositeSliderGrounder:
    """Ground sliders through the existing semantic/DOM/visual target path.

    Falls back to the local ddddocr slide-match provider when the primary
    grounder cannot locate a target, so real puzzle sliders are still solved.
    """

    def __init__(self, seleniumbase_cdp: Any, *, profile_dir: str | Path | None = None) -> None:
        self._primary = SliderTargetGrounder(seleniumbase_cdp, profile_dir=profile_dir)
        self._ddddocr = DdddOcrSliderProvider(seleniumbase_cdp, profile_dir=profile_dir)

    def ground(self, state: Dict[str, Any]) -> Dict[str, Any]:
        instruction = str(state.get("instruction") or "")
        primary = self._primary.ground(state)
        if bool(primary.get("grounded")):
            # An explicit "bis zum Ende"/"to the end" instruction is
            # authoritative over the weak visual-region heuristic, which can
            # latch onto the handle edge on simple sliders. Generic puzzle copy
            # like "Nach rechts schieben" is unaffected.
            if _EXPLICIT_END.search(instruction) and str(primary.get("source") or "") == "visual-region":
                if _RIGHT_END.search(instruction):
                    return {
                        "grounded": True,
                        "targetFraction": 0.985,
                        "confidence": 0.98,
                        "source": "instruction-end-right",
                        "markId": "S3",
                    }
                return {
                    "grounded": True,
                    "targetFraction": 0.015,
                    "confidence": 0.98,
                    "source": "instruction-end-left",
                    "markId": "S3",
                }
            return primary

        fallback = self._ddddocr.ground(state)
        if fallback and (bool(fallback.get("found")) or bool(fallback.get("grounded"))):
            return {**fallback, "primaryReason": primary.get("reason")}

        # Directional endpoint is only a last resort. Puzzle sliders (DataDome)
        # use "Nach rechts schieben" as generic copy while the real target is
        # the puzzle gap, so the directional match must not run before ddddocr.
        if str(state.get("orientation") or "horizontal") != "vertical":
            if _RIGHT_END.search(instruction):
                return {
                    "grounded": True,
                    "targetFraction": 0.985,
                    "confidence": 0.98,
                    "source": "instruction-end-right",
                    "markId": "S3",
                }
            if _LEFT_END.search(instruction):
                return {
                    "grounded": True,
                    "targetFraction": 0.015,
                    "confidence": 0.98,
                    "source": "instruction-end-left",
                    "markId": "S3",
                }

        return primary

    def status(self) -> Dict[str, Any]:
        return {
            "provider": "slider-target-grounder",
            "sideEffectFree": True,
            "ddddocr": self._ddddocr.status(),
        }
