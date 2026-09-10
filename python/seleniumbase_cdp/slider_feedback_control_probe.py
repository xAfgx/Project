from __future__ import annotations

from auto_interaction_controller import AutoInteractionController
from slider_action_executor import SliderActionExecutor


class FakeSb:
    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def get_gui_element_center(self, selector: str):
        if selector == "#track":
            return 50.0, 10.0
        fraction = float(self.adapter.fraction)
        return fraction * 100.0, 10.0


class FeedbackAdapter:
    def __init__(self, *, fraction: float, gain: float, target: float, epoch: int = 1) -> None:
        self.fraction = fraction
        self.gain = gain
        self.target = target
        self.epoch = epoch

    def poll(self):
        return {
            "kind": "slider",
            "scope": "document",
            "score": 90,
            "orientation": "horizontal",
            "fraction": self.fraction,
            "min": 0,
            "max": 100,
            "value": self.fraction * 100,
            "instruction": f"Ziehe den Regler auf {self.target:.2f}",
            "handleRect": {"x": self.fraction * 100.0 - 10.0, "y": 0, "width": 20, "height": 20},
            "trackRect": {"x": 0, "y": 0, "width": 100, "height": 20},
            "handleSelector": "#handle",
            "trackSelector": "#track",
            "nativeRange": False,
            "marks": [
                {"role": "slider-handle", "markId": "handle", "identitySignature": "handle-id"},
                {"role": "slider-track", "markId": "track", "identitySignature": "track-id"},
            ],
            "complete": False,
            "failed": False,
            "override": True,
            "documentEpoch": self.epoch,
            "sessionGeneration": 1,
            "signature": f"moving-{self.fraction:.6f}",
        }


class FeedbackPaths:
    def __init__(self, adapter: FeedbackAdapter) -> None:
        self.adapter = adapter
        self.commands = []

    def play_drag(
        self,
        _sb,
        start,
        end,
        *,
        preferred="ghost-cursor",
        gui_start=None,
        gui_end=None,
        end_hold_backtrack=False,
    ):
        assert preferred == "ghost-cursor"
        raw_command = float(end[0]) / 100.0
        applied_command = max(0.0, min(1.0, raw_command))
        before = self.adapter.fraction
        self.adapter.fraction = max(
            0.0,
            min(1.0, before + self.adapter.gain * (applied_command - before)),
        )
        self.commands.append({
            "before": before,
            "command": raw_command,
            "appliedCommand": applied_command,
            "after": self.adapter.fraction,
            "backtrack": bool(end_hold_backtrack),
        })
        return {
            "moved": True,
            "provider": "python-bezier:cdp",
            "pointCount": 24,
            "motionId": f"m{len(self.commands)}-seeded",
            "motionParameters": {},
            "endHoldBacktrack": bool(end_hold_backtrack),
        }


class GridNone:
    def poll(self):
        return {"kind": "none", "score": 0, "signature": "none"}


class Vision:
    def status(self):
        return {"ready": True}


class StrictSliderAdapter:
    def poll(self):
        return {
            "kind": "slider",
            "score": 90,
            "instruction": "Ziehe den Regler nach rechts",
            "fraction": 0.93,
            "complete": False,
            "failed": False,
            "override": True,
            "signature": "moving-state",
        }


class StrictSliderActions:
    def __init__(self) -> None:
        self.calls = 0

    def calibration_signature(self, _state):
        return "stable-calibration"

    def apply(self, target_fraction=0.96, *, state=None, force_fallback=False):
        self.calls += 1
        return {
            "moved": True,
            "verified": False,
            "targetFraction": target_fraction,
            "currentFraction": 0.93,
            "targetError": 0.03,
            "calibrationSignature": "stable-calibration",
            "state": {
                **(state or {}),
                "kind": "slider",
                "fraction": 0.93,
                "complete": False,
                "failed": False,
            },
        }


def run_feedback_case(*, fraction: float, gain: float, target: float):
    adapter = FeedbackAdapter(fraction=fraction, gain=gain, target=target)
    paths = FeedbackPaths(adapter)
    executor = SliderActionExecutor(FakeSb(adapter), adapter, paths)
    before = adapter.poll()
    signature = executor.calibration_signature(before)
    result = executor.apply(target, state=before)
    after = adapter.poll()

    assert result["verified"] is True, result
    assert abs(float(result["currentFraction"]) - target) <= 0.02, result
    assert 0 <= int(result["correctionDrags"]) <= 3, result
    assert str(result["mode"]).endswith(":cdp"), result
    assert len(paths.commands) <= 4, paths.commands
    assert executor.calibration_signature(after) == signature

    adapter.epoch += 1
    assert executor.calibration_signature(adapter.poll()) != signature
    return result, paths.commands


def main() -> int:
    under, under_commands = run_feedback_case(fraction=0.10, gain=0.72, target=0.70)
    assert len(under_commands) >= 2, under_commands
    assert under_commands[0]["command"] > 0.70
    assert under["gain"] is not None and 0.60 <= float(under["gain"]) <= 0.85

    over, over_commands = run_feedback_case(fraction=0.10, gain=1.25, target=0.60)
    assert len(over_commands) >= 2, over_commands
    assert over_commands[0]["after"] > 0.60
    assert over_commands[1]["command"] < over_commands[0]["after"], over_commands
    assert int(over["overshootDirectionChanges"]) >= 1, over

    end_result, end_commands = run_feedback_case(fraction=0.10, gain=1.0, target=0.96)
    assert len(end_commands) >= 2, end_commands
    assert abs(end_commands[0]["command"] - 1.02) < 1e-9, end_commands
    assert end_commands[0]["backtrack"] is True, end_commands
    assert end_commands[1]["command"] < end_commands[0]["after"], end_commands
    assert abs(float(end_result["currentFraction"]) - 0.96) <= 0.02, end_result

    custom = FeedbackAdapter(fraction=0.20, gain=1.0, target=0.90)
    points = SliderActionExecutor._viewport_points(custom.poll(), 0.90)
    assert points is not None
    _, end = points
    assert abs(end[0] - 90.0) < 1e-6, points
    overshoot = SliderActionExecutor._viewport_points(custom.poll(), 1.02)
    assert overshoot is not None and abs(overshoot[1][0] - 102.0) < 1e-6, overshoot

    strict_actions = StrictSliderActions()
    controller = AutoInteractionController(
        GridNone(),
        StrictSliderAdapter(),
        object(),
        strict_actions,
        Vision(),
    )
    first = controller.poll_and_act()
    assert first["acted"] is True
    assert first["verified"] is False, first
    assert first["verification"]["reason"] == "no-explicit-success-or-target-tolerance", first
    second = controller.poll_and_act()
    assert second["acted"] is False, second
    assert strict_actions.calls == 1

    print("Slider feedback control probe passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
