from __future__ import annotations

import os

from cursor_path_provider import CursorPathProvider
from site_slider_adapter import SliderSiteAdapter
from slider_action_executor import SliderActionExecutor
import task_browser_worker_oopif as oopif_worker


def assert_seeded_bezier_replay() -> None:
    previous = os.environ.get("ARES_INTERACTION_SEED")
    try:
        os.environ["ARES_INTERACTION_SEED"] = "task-alpha"
        first = CursorPathProvider().plan((10.0, 20.0), (310.0, 90.0))
        second = CursorPathProvider().plan((10.0, 20.0), (310.0, 90.0))
        assert first["provider"] == "python-bezier"
        assert second["provider"] == "python-bezier"
        assert first["points"] == second["points"], "same task seed must replay the same motion"

        os.environ["ARES_INTERACTION_SEED"] = "task-beta"
        third = CursorPathProvider().plan((10.0, 20.0), (310.0, 90.0))
        assert third["points"] != first["points"], "different task seeds must get independent motion streams"
    finally:
        if previous is None:
            os.environ.pop("ARES_INTERACTION_SEED", None)
        else:
            os.environ["ARES_INTERACTION_SEED"] = previous


def assert_slider_geometry_contract() -> None:
    native = {
        "orientation": "horizontal",
        "nativeRange": True,
        "fraction": 0.25,
        "trackRect": {"x": 100.0, "y": 40.0, "width": 200.0, "height": 20.0},
        "handleRect": {"x": 140.0, "y": 40.0, "width": 20.0, "height": 20.0},
    }
    native_points = SliderActionExecutor._viewport_points(native, 0.75)
    assert native_points == ((150.0, 50.0), (250.0, 50.0)), "native range geometry must stay unchanged"

    custom = {
        "orientation": "horizontal",
        "nativeRange": False,
        "fraction": 0.25,
        "trackRect": {"x": 100.0, "y": 40.0, "width": 200.0, "height": 20.0},
        "handleRect": {"x": 140.0, "y": 40.0, "width": 20.0, "height": 20.0},
    }
    custom_points = SliderActionExecutor._viewport_points(custom, 0.75)
    assert custom_points == ((150.0, 50.0), (250.0, 50.0)), "custom slider command geometry must use the full track span"
    overshoot_points = SliderActionExecutor._viewport_points(custom, 1.02)
    assert overshoot_points == ((150.0, 50.0), (304.0, 50.0)), "custom slider must permit a small deterministic overshoot beyond the target/track end"


def assert_oopif_slider_projection() -> None:
    state = SliderSiteAdapter(FakeSliderSb()).poll()
    assert state["kind"] == "slider"
    assert state["scope"] == "oopif:iframe#cross"
    assert state["framePath"] == ["iframe#cross"]
    assert state["documentEpoch"] == 7
    assert state["sessionGeneration"] == 4
    assert state["handleRect"]["x"] == 310.0
    assert state["handleRect"]["y"] == 120.0
    assert state["trackRect"]["x"] == 310.0
    assert state["marks"][0]["visualBounds"]["x"] == 310.0


def assert_document_epoch_lifecycle() -> None:
    registry = oopif_worker.FlatCdpTargetRegistry("", autostart=False)
    assert registry.document_epoch("frame-1") == 0
    registry._handle_event({"method": "Page.frameAttached", "params": {"frameId": "frame-1"}})
    assert registry.document_epoch("frame-1") == 0
    registry._handle_event({"method": "Page.frameNavigated", "params": {"frame": {"id": "frame-1"}}})
    assert registry.document_epoch("frame-1") == 1
    registry._handle_event({"method": "Page.navigatedWithinDocument", "params": {"frameId": "frame-1"}})
    assert registry.document_epoch("frame-1") == 2
    registry._handle_event({"method": "Page.frameDetached", "params": {"frameId": "frame-1"}})
    assert registry.document_epoch("frame-1") == 0


class FakeSliderSb:
    def evaluate(self, _script: str):
        return {
            "kind": "none",
            "scope": "document",
            "score": 0,
            "rawMarks": [],
            "complete": False,
            "failed": False,
        }

    @staticmethod
    def ares_oopif_discover():
        return [{"path": ["iframe#cross"], "url": "https://frame.example.test/"}]

    @staticmethod
    def ares_oopif_evaluate(frame_path, _script, _args):
        assert frame_path == ["iframe#cross"]
        return {
            "value": {
                "kind": "slider",
                "scope": "document",
                "score": 88,
                "orientation": "horizontal",
                "fraction": 0.2,
                "min": 0,
                "max": 100,
                "value": 20,
                "instruction": "Move the slider to the target",
                "handleRect": {"x": 10, "y": 20, "width": 20, "height": 20},
                "trackRect": {"x": 10, "y": 20, "width": 200, "height": 20},
                "handleSelector": "#handle",
                "trackSelector": "#track",
                "nativeRange": False,
                "rawMarks": [
                    {
                        "role": "slider-handle",
                        "visualBounds": {"x": 10, "y": 20, "width": 20, "height": 20},
                        "confidence": 0.98,
                        "structuralKey": "handle",
                        "semanticSignature": "handle",
                    },
                    {
                        "role": "slider-track",
                        "visualBounds": {"x": 10, "y": 20, "width": 200, "height": 20},
                        "confidence": 0.96,
                        "structuralKey": "track",
                        "semanticSignature": "track",
                    },
                ],
                "viewport": {"width": 600, "height": 400, "devicePixelRatio": 1},
                "complete": False,
                "failed": False,
                "override": False,
            },
            "offsetX": 300,
            "offsetY": 100,
            "frameId": "frame-cross",
            "documentEpoch": 7,
            "sessionGeneration": 4,
        }


def main() -> int:
    assert_seeded_bezier_replay()
    assert_slider_geometry_contract()
    assert_oopif_slider_projection()
    assert_document_epoch_lifecycle()
    print("OOPIF_SEED_SLIDER_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
