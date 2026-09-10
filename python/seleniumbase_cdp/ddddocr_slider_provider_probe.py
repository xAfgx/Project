from __future__ import annotations

import base64
import tempfile
from pathlib import Path

from ddddocr_slider_provider import DdddOcrSliderProvider


class FakeMatcher:
    def slide_match(self, target: bytes, background: bytes, simple_target: bool = False):
        assert target == b"target"
        assert background == b"background"
        return {"target": [60, 4, 80, 24]}


class FakeSb:
    pass


def data_url(raw: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        provider = DdddOcrSliderProvider(FakeSb(), profile_dir=root)
        provider._matcher = FakeMatcher()
        provider._run_extract = lambda frame_path: {
            "target": data_url(b"target"),
            "background": data_url(b"background"),
        }
        provider._image_dimensions = lambda target_bytes, background_bytes: (20, 200)

        result = provider.ground({"kind": "slider", "orientation": "horizontal"})
        assert result is not None
        assert result["found"] is True
        assert result["grounded"] is True
        assert result["provider"] == "ddddocr"
        assert result["source"] == "ddddocr-slide-match"
        assert abs(float(result["targetFraction"]) - (60.0 / 180.0)) < 1e-9

        unavailable = DdddOcrSliderProvider(FakeSb(), profile_dir=root)
        unavailable._import_failed = True
        assert unavailable.available() is False
        assert unavailable.ground({"kind": "slider", "orientation": "horizontal"}) is None

    print("ddddocr slider provider probe: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
