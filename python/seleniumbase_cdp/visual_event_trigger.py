from __future__ import annotations

from typing import Any, Dict, Iterable


class VisualEventTrigger:
    """Score structural page events before requesting expensive visual capture.

    Structural/geometric changes carry the decision. Text hints may increase
    confidence, but can never trigger a capture on their own.
    """

    THRESHOLD = 3
    STRUCTURAL_WEIGHTS = {
        "page-load": 3,
        "navigation": 3,
        "iframe-added": 3,
        "shadow-root-added": 3,
        "overlay-opened": 3,
        "modal-opened": 3,
        "slider-candidate": 4,
        "grid-candidate": 4,
        "canvas-candidate": 2,
        "unknown-interactive-region": 2,
        "layout-generation-changed": 3,
        "target-invalidated": 3,
        "action-verification-needed": 3,
        "structured-chain-miss": 3,
    }
    TEXT_HINTS = ("verify", "verification", "challenge", "security", "secure", "gate", "check", "confirm")

    def evaluate(self, events: Iterable[str], *, visible_text: str = "") -> Dict[str, Any]:
        normalized = {str(event).strip().lower() for event in events if str(event).strip()}
        structural_score = sum(self.STRUCTURAL_WEIGHTS.get(event, 0) for event in normalized)
        text = str(visible_text or "").lower()
        hints = [hint for hint in self.TEXT_HINTS if hint in text]
        text_score = min(len(hints), 2)
        score = structural_score + text_score
        # Words alone are deliberately insufficient: at least one structural
        # event must exist before visual capture is requested.
        triggered = structural_score > 0 and score >= self.THRESHOLD
        return {
            "triggered": triggered,
            "score": score,
            "structuralScore": structural_score,
            "textScore": text_score,
            "events": sorted(normalized),
            "textHints": hints,
            "threshold": self.THRESHOLD,
        }
