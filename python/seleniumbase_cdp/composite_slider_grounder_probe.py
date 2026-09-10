from __future__ import annotations

from typing import Any, Dict

from composite_slider_grounder import CompositeSliderGrounder


class PrimaryStub:
    def __init__(self, result: Dict[str, Any]) -> None:
        self.result = result
        self.calls = 0

    def ground(self, _state: Dict[str, Any]) -> Dict[str, Any]:
        self.calls += 1
        return dict(self.result)


class OptionalStub:
    def __init__(self, result: Dict[str, Any] | None) -> None:
        self.result = result
        self.calls = 0

    def ground(self, _state: Dict[str, Any]) -> Dict[str, Any] | None:
        self.calls += 1
        return dict(self.result) if self.result is not None else None

    def status(self) -> Dict[str, Any]:
        return {"provider": "ddddocr", "optional": True, "available": True}


def make_grounder(primary: PrimaryStub, optional: OptionalStub) -> CompositeSliderGrounder:
    grounder = CompositeSliderGrounder.__new__(CompositeSliderGrounder)
    grounder._primary = primary
    grounder._ddddocr = optional
    return grounder


def main() -> int:
    primary = PrimaryStub({
        "grounded": True,
        "targetFraction": 0.67,
        "confidence": 0.81,
        "source": "visual-region",
    })
    optional = OptionalStub({
        "found": True,
        "targetFraction": 0.31,
        "confidence": 0.76,
        "source": "ddddocr-slide-match",
    })
    result = make_grounder(primary, optional).ground({"kind": "slider"})
    assert result["source"] == "visual-region"
    assert result["targetFraction"] == 0.67
    assert optional.calls == 0, "ddddocr must not run when the primary grounder already succeeded"

    primary_miss = PrimaryStub({
        "grounded": False,
        "targetFraction": None,
        "confidence": 0.0,
        "reason": "no-target-grounded",
    })
    optional_hit = OptionalStub({
        "found": True,
        "targetFraction": 0.42,
        "confidence": 0.76,
        "source": "ddddocr-slide-match",
    })
    fallback = make_grounder(primary_miss, optional_hit).ground({"kind": "slider"})
    assert fallback["source"] == "ddddocr-slide-match"
    assert fallback["primaryReason"] == "no-target-grounded"
    assert optional_hit.calls == 1

    primary_miss_again = PrimaryStub({
        "grounded": False,
        "targetFraction": None,
        "confidence": 0.0,
        "reason": "no-target-grounded",
    })
    optional_miss = OptionalStub(None)
    miss = make_grounder(primary_miss_again, optional_miss).ground({"kind": "slider"})
    assert miss["grounded"] is False
    assert optional_miss.calls == 1

    print("composite slider grounder probe: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
