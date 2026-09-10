from __future__ import annotations

import asyncio
import math

from cursor_path_provider import CursorPathProvider
from slider_target_grounder import SliderTargetGrounder
from stable_marks import build_stable_marks


class FakeImage:
    def __init__(self, width: int, height: int, base=(120, 120, 120)) -> None:
        self.width = width
        self.height = height
        self._pixels = [[tuple(base) for _ in range(width)] for _ in range(height)]

    def fill_rect(self, x1: int, y1: int, x2: int, y2: int, color) -> None:
        for y in range(max(0, y1), min(self.height, y2)):
            for x in range(max(0, x1), min(self.width, x2)):
                self._pixels[y][x] = tuple(color)

    def convert(self, _mode: str):
        return self

    def load(self):
        image = self

        class Pixels:
            def __getitem__(self, key):
                x, y = key
                return image._pixels[y][x]

        return Pixels()


class NoScreenshotSb:
    def evaluate(self, _script: str):
        return {}


class FakeTab:
    def __init__(self) -> None:
        self.events = []

    async def send(self, command):
        self.events.append(command)
        return None


class FakeCdpSb:
    def __init__(self) -> None:
        self.tab = FakeTab()
        self.loop = asyncio.new_event_loop()

    def get_active_tab(self):
        return self.tab

    def get_event_loop(self):
        return self.loop

    def close(self) -> None:
        self.loop.close()


def main() -> int:
    first_mark = build_stable_marks([
        {
            "role": "slider-target",
            "structuralKey": "#target-zone",
            "semanticSignature": "target|grey|goal",
            "visualBounds": {"x": 200, "y": 40, "width": 24, "height": 18},
            "confidence": 0.91,
        }
    ], scope="document", viewport={"width": 1280, "height": 720}, observed_at=100.0)[0]
    shifted_mark = build_stable_marks([
        {
            "role": "slider-target",
            "structuralKey": "#target-zone",
            "semanticSignature": "target|grey|goal",
            "visualBounds": {"x": 228, "y": 52, "width": 24, "height": 18},
            "confidence": 0.91,
        }
    ], scope="document", viewport={"width": 1365, "height": 768}, observed_at=101.0)[0]
    changed_visual = build_stable_marks([
        {
            "role": "slider-target",
            "structuralKey": "#target-zone",
            "semanticSignature": "target|blue|goal",
            "visualBounds": {"x": 228, "y": 52, "width": 24, "height": 18},
            "confidence": 0.91,
        }
    ], scope="document", viewport={"width": 1365, "height": 768}, observed_at=102.0)[0]
    assert first_mark["markId"] == shifted_mark["markId"] == changed_visual["markId"]
    assert first_mark["semanticVisualSignature"] == shifted_mark["semanticVisualSignature"]
    assert changed_visual["semanticVisualSignature"] != shifted_mark["semanticVisualSignature"]
    assert first_mark["visualBounds"] != shifted_mark["visualBounds"]
    assert shifted_mark["frameViewport"]["viewport"]["width"] == 1365.0
    assert shifted_mark["observedAt"] == 101.0

    grounder = SliderTargetGrounder(NoScreenshotSb())

    percent = grounder.ground({
        "kind": "slider",
        "instruction": "Bewege den Regler auf 63 %",
        "min": 0,
        "max": 100,
        "targetCandidates": [],
    })
    assert percent["grounded"] is True
    assert abs(percent["targetFraction"] - 0.63) < 0.001
    assert percent["source"] == "instruction-percent"

    dom = grounder.ground({
        "kind": "slider",
        "instruction": "Bewege den Regler zur Zielmarkierung",
        "min": 0,
        "max": 100,
        "targetCandidates": [
            {"markId": first_mark["markId"], "fraction": 0.71, "score": 82, "label": "Ziel", "rect": first_mark["visualBounds"]}
        ],
    })
    assert dom["grounded"] is True
    assert abs(dom["targetFraction"] - 0.71) < 0.001
    assert dom["source"] == "dom-target" and dom["markId"] == first_mark["markId"]

    image = FakeImage(240, 28)
    image.fill_rect(150, 8, 174, 20, (166, 166, 166))
    visual = SliderTargetGrounder.analyze_track_image(image, orientation="horizontal", current_fraction=0.18)
    assert visual is not None
    assert 0.62 <= visual["targetFraction"] <= 0.72, visual
    assert visual["confidence"] >= 0.45

    provider = CursorPathProvider()
    plan = provider.plan((30.0, 40.0), (360.0, 180.0))
    points = plan["points"]
    assert len(points) >= 2
    assert math.hypot(points[0][0] - 30.0, points[0][1] - 40.0) <= 1.0
    assert math.hypot(points[-1][0] - 360.0, points[-1][1] - 180.0) <= 1.0
    assert plan["provider"] in {"ghost-cursor", "bezier-mouse-js", "internal-bezier", "python-bezier"}

    cdp = FakeCdpSb()
    try:
        motion = provider._motion_parameters(end_hold_backtrack=False)
        assert 0.026 <= motion["prePress"] <= 0.064
        assert 0.038 <= motion["postPress"] <= 0.082
        assert provider._play_cdp_drag(
            cdp,
            [(10.0, 20.0), (20.0, 30.0), (30.0, 40.0)],
            motion=motion,
            end_hold_backtrack=False,
        ) is True
        assert len(cdp.tab.events) == 5
    finally:
        cdp.close()

    print(f"Visual grounding probe passed. cursorProvider={plan['provider']} target={visual['targetFraction']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
