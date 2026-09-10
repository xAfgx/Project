from __future__ import annotations

from stable_marks import build_stable_marks, stable_mark_digest


def main() -> int:
    raw = [
        {
            "role": "slider-target",
            "visualBounds": {"x": 240, "y": 100, "width": 18, "height": 32},
            "confidence": 0.86,
            "structuralKey": "slider|target|slot:1",
            "semanticSignature": "slider-target|grey-zone|63%",
            "fraction": 0.63,
            "label": "grey target zone",
        }
    ]
    viewport = {"width": 1280, "height": 720, "scrollX": 0, "scrollY": 140, "devicePixelRatio": 1.25}
    first = build_stable_marks(raw, scope="document/iframe:0", viewport=viewport, observed_at=123.0)
    moved = build_stable_marks(
        [{**raw[0], "visualBounds": {"x": 244, "y": 101, "width": 18, "height": 32}}],
        scope="document/iframe:0",
        viewport=viewport,
        observed_at=124.0,
    )

    assert len(first) == 1
    mark = first[0]
    for key in (
        "markId",
        "visualBounds",
        "confidence",
        "frame",
        "viewport",
        "observedAt",
        "identitySignature",
        "semanticVisualSignature",
        "visualSignature",
    ):
        assert key in mark, key
    assert mark["visualBounds"]["x"] == 240.0
    assert mark["frame"] == "document/iframe:0"
    assert mark["viewport"]["scrollY"] == 140.0
    assert mark["observedAt"] == 123.0
    assert mark["markId"] == moved[0]["markId"]
    assert mark["identitySignature"] == moved[0]["identitySignature"]
    assert mark["visualSignature"] != moved[0]["visualSignature"]
    assert stable_mark_digest(first) == stable_mark_digest(moved)

    print("Stable visual marks contract probe passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
