from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any, Dict, List, Tuple


class ScreenshotGridTileProvider:
    """Build per-tile image sources from one viewport screenshot and Stable Marks."""

    def sources(self, screenshot_path: str | Path, state: Dict[str, Any]) -> Dict[str, Any]:
        path = Path(screenshot_path).expanduser().resolve()
        if not path.exists():
            return {"sources": [], "error": "screenshot-missing", "path": str(path)}

        marks = [
            mark for mark in state.get("marks") or []
            if isinstance(mark, dict) and mark.get("role") == "grid-tile"
        ]
        expected = int(state.get("tileCount") or 0)
        if not marks:
            return {"sources": [], "error": "grid-marks-missing", "path": str(path)}
        if expected <= 0 or len(marks) != expected:
            return {
                "sources": [],
                "error": "grid-mark-count-mismatch",
                "path": str(path),
                "tileCount": expected,
                "markCount": len(marks),
            }

        try:
            from PIL import Image
        except Exception as exc:
            return {"sources": [], "error": f"Pillow unavailable: {exc}", "path": str(path)}

        try:
            image = Image.open(path).convert("RGB")
        except Exception as exc:
            return {"sources": [], "error": f"screenshot-open-failed: {exc}", "path": str(path)}

        boxes = self.crop_boxes(image.size, marks)
        if len(boxes) != len(marks):
            return {
                "sources": [],
                "error": "invalid-grid-bounds",
                "path": str(path),
                "tileCount": expected,
                "markCount": len(marks),
                "cropCount": len(boxes),
            }

        sources: List[str] = []
        for box in boxes:
            try:
                crop = image.crop(box)
                if crop.width < 2 or crop.height < 2:
                    sources.append("")
                    continue
                buffer = io.BytesIO()
                crop.save(buffer, format="PNG", optimize=False)
                encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
                sources.append(f"data:image/png;base64,{encoded}")
            except Exception:
                sources.append("")

        readable = sum(1 for source in sources if source)

        reference_source = self._reference_crop(image, state)
        return {
            "sources": sources,
            "readable": readable,
            "tileCount": expected,
            "markCount": len(marks),
            "cropCount": len(boxes),
            "cropBoxes": [list(box) for box in boxes],
            "referenceSource": reference_source,
            "scope": str(state.get("scope") or ""),
            "path": str(path),
            "error": "" if readable == expected else "incomplete-grid-crops",
        }

    @classmethod
    def _reference_crop(cls, image: Any, state: Dict[str, Any]) -> str:
        bounds = state.get("referenceBounds")
        if not isinstance(bounds, dict):
            return ""
        try:
            x = float(bounds.get("x") or 0.0)
            y = float(bounds.get("y") or 0.0)
            width = float(bounds.get("width") or 0.0)
            height = float(bounds.get("height") or 0.0)
        except (TypeError, ValueError):
            return ""
        if width <= 0 or height <= 0:
            return ""

        image_width, image_height = int(image.width), int(image.height)
        viewport = state.get("viewport")
        if not isinstance(viewport, dict):
            viewport = {}
        try:
            viewport_width = float(viewport.get("width") or 0.0)
            viewport_height = float(viewport.get("height") or 0.0)
        except (TypeError, ValueError):
            viewport_width = viewport_height = 0.0

        if viewport_width > 1 and viewport_height > 1:
            scale_x = image_width / viewport_width
            scale_y = image_height / viewport_height
        else:
            scale_x = scale_y = 1.0

        left = max(0, min(image_width, int(round(x * scale_x))))
        top = max(0, min(image_height, int(round(y * scale_y))))
        right = max(left + 1, min(image_width, int(round((x + width) * scale_x))))
        bottom = max(top + 1, min(image_height, int(round((y + height) * scale_y))))
        if left >= image_width or top >= image_height or right <= left or bottom <= top:
            return ""
        try:
            crop = image.crop((left, top, right, bottom))
            if crop.width < 2 or crop.height < 2:
                return ""
            buffer = io.BytesIO()
            crop.save(buffer, format="PNG", optimize=False)
            return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception:
            return ""

    @staticmethod
    def crop_boxes(image_size: Tuple[int, int], marks: List[Dict[str, Any]]) -> List[Tuple[int, int, int, int]]:
        image_width, image_height = int(image_size[0]), int(image_size[1])
        if image_width <= 0 or image_height <= 0 or not marks:
            return []

        viewport = marks[0].get("viewport") if isinstance(marks[0], dict) else None
        if not isinstance(viewport, dict):
            viewport = {}
        try:
            viewport_width = float(viewport.get("width") or 0.0)
            viewport_height = float(viewport.get("height") or 0.0)
        except (TypeError, ValueError):
            viewport_width = viewport_height = 0.0

        if viewport_width > 1 and viewport_height > 1:
            scale_x = image_width / viewport_width
            scale_y = image_height / viewport_height
        else:
            scale_x = scale_y = 1.0

        result: List[Tuple[int, int, int, int]] = []
        for mark in marks:
            bounds = mark.get("visualBounds") if isinstance(mark, dict) else None
            if not isinstance(bounds, dict):
                return []
            try:
                x = float(bounds.get("x") or 0.0)
                y = float(bounds.get("y") or 0.0)
                width = float(bounds.get("width") or 0.0)
                height = float(bounds.get("height") or 0.0)
            except (TypeError, ValueError):
                return []
            if width <= 0 or height <= 0:
                return []

            left = max(0, min(image_width, int(round(x * scale_x))))
            top = max(0, min(image_height, int(round(y * scale_y))))
            right = max(left + 1, min(image_width, int(round((x + width) * scale_x))))
            bottom = max(top + 1, min(image_height, int(round((y + height) * scale_y))))
            if left >= image_width or top >= image_height or right <= left or bottom <= top:
                return []
            result.append((left, top, right, bottom))
        return result
