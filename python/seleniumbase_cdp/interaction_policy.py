from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


POLICY_FILENAME = ".ares-interaction-policy.json"

_DEFAULTS: Dict[str, Any] = {
    "watchdogIntervalMs": 800,
    "captureOnPageLoad": True,
    "captureOnNavigation": True,
    "captureOnGenerationChange": False,
    "captureOnIframe": True,
    "captureOnModal": True,
    "captureOnGrid": True,
    "captureOnSlider": True,
    "captureOnCanvas": True,
    "captureOnVerificationFailure": True,
    "maxSavedCaptures": 40,
    "gridClickDelayMs": 250,
    "gridSubmitDelayMs": 300,
    "textHints": [
        "verify", "verification", "challenge", "security", "secure", "gate", "check", "confirm",
        "prüfen", "bestätigen", "sicherheit", "verifizieren",
    ],
    "fingerprintSelectors": [
        "iframe",
        "dialog",
        "[role=dialog]",
        "canvas",
        "input[type=range]",
        "[role=slider]",
        "[aria-valuenow]",
        "[class*=slider i]",
        "[class*=grid i]",
        "[class*=tile i]",
        "[class*=modal i]",
        "[class*=overlay i]",
    ],
    "prioritySelectors": [],
}


class InteractionPolicy:
    """Small per-profile policy for ARES observation/capture behaviour."""

    def __init__(self, values: Dict[str, Any] | None = None) -> None:
        merged = dict(_DEFAULTS)
        if isinstance(values, dict):
            for key in _DEFAULTS:
                if key in values:
                    merged[key] = values[key]
        merged["watchdogIntervalMs"] = max(200, min(5000, int(merged["watchdogIntervalMs"])))
        merged["maxSavedCaptures"] = max(4, min(500, int(merged["maxSavedCaptures"])))
        merged["gridClickDelayMs"] = max(0, min(5000, int(merged["gridClickDelayMs"])))
        merged["gridSubmitDelayMs"] = max(0, min(5000, int(merged["gridSubmitDelayMs"])))
        for key in _DEFAULTS:
            if key.startswith("captureOn"):
                merged[key] = bool(merged[key])
        merged["textHints"] = self._strings(merged.get("textHints"), max_items=64, max_len=80)
        merged["fingerprintSelectors"] = self._strings(merged.get("fingerprintSelectors"), max_items=64, max_len=180)
        merged["prioritySelectors"] = self._strings(merged.get("prioritySelectors"), max_items=32, max_len=180)
        self._values = merged

    @classmethod
    def from_profile(cls, profile_dir: str | Path) -> "InteractionPolicy":
        path = Path(profile_dir).expanduser().resolve() / POLICY_FILENAME
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = {}
        except (OSError, json.JSONDecodeError):
            raw = {}
        return cls(raw if isinstance(raw, dict) else {})

    @property
    def watchdog_interval_seconds(self) -> float:
        return float(self._values["watchdogIntervalMs"]) / 1000.0

    @property
    def max_saved_captures(self) -> int:
        return int(self._values["maxSavedCaptures"])

    @property
    def grid_click_delay_seconds(self) -> float:
        return float(self._values["gridClickDelayMs"]) / 1000.0

    @property
    def grid_submit_delay_seconds(self) -> float:
        return float(self._values["gridSubmitDelayMs"]) / 1000.0

    @property
    def text_hints(self) -> List[str]:
        return list(self._values["textHints"])

    @property
    def fingerprint_selectors(self) -> List[str]:
        return list(self._values["fingerprintSelectors"])

    @property
    def priority_selectors(self) -> List[str]:
        return list(self._values["prioritySelectors"])

    def capture_enabled(self, event: str) -> bool:
        mapping = {
            "page-load": "captureOnPageLoad",
            "navigation": "captureOnNavigation",
            "layout-generation-changed": "captureOnGenerationChange",
            "iframe-added": "captureOnIframe",
            "modal-opened": "captureOnModal",
            "grid-candidate": "captureOnGrid",
            "slider-candidate": "captureOnSlider",
            "canvas-candidate": "captureOnCanvas",
            "verification-failure": "captureOnVerificationFailure",
        }
        key = mapping.get(str(event or ""))
        return bool(key and self._values.get(key))

    def status(self) -> Dict[str, Any]:
        return {**self._values, "filename": POLICY_FILENAME}

    @staticmethod
    def _strings(value: Any, *, max_items: int, max_len: int) -> List[str]:
        if not isinstance(value, list):
            return []
        result: List[str] = []
        seen = set()
        for item in value:
            text = str(item or "").strip()
            if not text or len(text) > max_len or text in seen:
                continue
            seen.add(text)
            result.append(text)
            if len(result) >= max_items:
                break
        return result
