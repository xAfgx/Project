from __future__ import annotations

import tempfile
from pathlib import Path

from interaction_policy import InteractionPolicy
from observation_capture import ObservationCapture
from page_observation_watchdog import PageObservationWatchdog


class FakeCdp:
    def __init__(self, states):
        self.states = list(states)
        self.index = 0

    def evaluate(self, _script):
        value = self.states[min(self.index, len(self.states) - 1)]
        self.index += 1
        return dict(value)

    def save_screenshot(self, filename, folder):
        path = Path(folder) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-png")


def _state(**changes):
    base = {
        "url": "https://example.test/a",
        "title": "Test",
        "readyState": "complete",
        "childCount": 3,
        "nodeCount": 20,
        "scrollHeight": 900,
        "interactive": 2,
        "inputs": 0,
        "buttons": 2,
        "iframes": 0,
        "modals": 0,
        "sliders": 0,
        "grids": 0,
        "canvas": 0,
        "selectorCounts": [],
        "priorityCounts": [],
        "textHints": [],
    }
    base.update(changes)
    return base


def main() -> int:
    policy = InteractionPolicy({
        "watchdogIntervalMs": 900,
        "maxSavedCaptures": 4,
        "textHints": ["customword"],
        "fingerprintSelectors": [".known-puzzle"],
        "prioritySelectors": ["#exam-widget"],
        "captureOnGenerationChange": False,
    })
    assert policy.watchdog_interval_seconds == 0.9
    assert policy.text_hints == ["customword"]
    assert policy.fingerprint_selectors == [".known-puzzle"]
    assert policy.priority_selectors == ["#exam-widget"]
    assert policy.capture_enabled("page-load") is True
    assert policy.capture_enabled("layout-generation-changed") is False

    cdp = FakeCdp([
        _state(),
        _state(),
        _state(iframes=1),
        _state(iframes=1, sliders=1),
    ])
    watchdog = PageObservationWatchdog(cdp, policy)
    first = watchdog.poll()
    assert first["changed"] is True and "page-load" in first["events"] and first["generation"] == 1, first
    second = watchdog.poll()
    assert second["changed"] is False and second["events"] == [] and second["generation"] == 1, second
    third = watchdog.poll()
    assert third["changed"] is True and "iframe-added" in third["events"] and third["generation"] == 2, third
    fourth = watchdog.poll()
    assert fourth["changed"] is True and "slider-candidate" in fourth["events"] and fourth["generation"] == 3, fourth

    with tempfile.TemporaryDirectory(prefix="ares-observation-probe-") as temporary:
        capture = ObservationCapture(cdp, profile_dir=temporary, policy=policy)
        for generation in range(1, 7):
            result = capture.capture("page-load", generation=generation)
            assert result["captured"] is True, result
        files = list((Path(temporary) / ".ares-observations").glob("*.png"))
        assert len(files) == 4, files
        disabled = capture.capture("layout-generation-changed", generation=7)
        assert disabled["captured"] is False and disabled["reason"] == "policy-disabled", disabled

    print("Event-driven observation policy/watchdog/capture probe passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
