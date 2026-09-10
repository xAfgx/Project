from __future__ import annotations

import math
import re
import statistics
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple


_PERCENT = re.compile(r"(?<!\d)(\d{1,3}(?:[.,]\d+)?)\s*%")
_VALUE = re.compile(
    r"(?i)(?:to|at|value|position|target|goal|auf|bis|wert|position|ziel)\s*[:=]?\s*(\d+(?:[.,]\d+)?)"
)
_RIGHT = re.compile(r"(?i)(right|end|finish|complete|rechts|ende|fertig|abschlie)")
_LEFT = re.compile(r"(?i)(left|start|begin|links|anfang|beginn)")


class SliderTargetGrounder:
    """Ground a slider target from semantics, DOM marks, or rendered pixels."""

    def __init__(self, seleniumbase_cdp: Any, *, profile_dir: str | Path | None = None) -> None:
        self._sb = seleniumbase_cdp
        self._profile_dir = Path(profile_dir).expanduser().resolve() if profile_dir else None

    def ground(self, state: Dict[str, Any]) -> Dict[str, Any]:
        if state.get("kind") != "slider":
            return self._none("not-a-slider")

        instruction = str(state.get("instruction") or "")
        semantic = self._semantic_target(instruction, state)
        if semantic:
            return semantic

        dom = self._dom_target(state.get("targetCandidates") or [])
        if dom:
            return dom

        visual = self._visual_target(state)
        if visual:
            return visual

        # Directional fallback is intentionally NOT part of the primary
        # grounder. Puzzle sliders (DataDome) use an instruction like
        # "Nach rechts schieben" even though the real target is the puzzle gap,
        # so a bare "rechts"/"links" match must never shadow a ddddocr match.
        return self._none("no-target-grounded")

    def _semantic_target(self, instruction: str, state: Dict[str, Any]) -> Dict[str, Any] | None:
        match = _PERCENT.search(instruction)
        if match:
            value = self._number(match.group(1))
            if value is not None:
                return self._decision(value / 100.0, 0.98, "instruction-percent", "S3")

        match = _VALUE.search(instruction)
        if not match:
            return None
        value = self._number(match.group(1))
        minimum = self._number(state.get("min"))
        maximum = self._number(state.get("max"))
        if value is None or minimum is None or maximum is None or maximum <= minimum:
            return None
        fraction = (value - minimum) / (maximum - minimum)
        if 0.0 <= fraction <= 1.0:
            return self._decision(fraction, 0.95, "instruction-value", "S3")
        return None

    def _dom_target(self, candidates: Iterable[Dict[str, Any]]) -> Dict[str, Any] | None:
        clean = [candidate for candidate in candidates if isinstance(candidate, dict)]
        if not clean:
            return None
        best = max(clean, key=lambda item: float(item.get("score") or 0.0))
        fraction = self._number(best.get("fraction"))
        score = self._number(best.get("score")) or 0.0
        if fraction is None or not 0.0 <= fraction <= 1.0 or score < 50:
            return None
        confidence = max(0.55, min(0.94, 0.50 + score / 200.0))
        return {
            **self._decision(fraction, confidence, "dom-target", str(best.get("markId") or "S3")),
            "label": str(best.get("label") or ""),
            "rect": best.get("rect"),
        }

    def _visual_target(self, state: Dict[str, Any]) -> Dict[str, Any] | None:
        image = self._capture_track(state)
        if image is None:
            return None
        try:
            result = self.analyze_track_image(
                image,
                orientation=str(state.get("orientation") or "horizontal"),
                current_fraction=float(state.get("fraction") or 0.0),
            )
        finally:
            close = getattr(image, "close", None)
            if callable(close):
                close()
        if not result or float(result.get("confidence") or 0.0) < 0.45:
            return None
        return {
            **self._decision(
                float(result["targetFraction"]),
                float(result["confidence"]),
                "visual-region",
                "S3",
            ),
            "region": result.get("region"),
            "contrast": result.get("contrast"),
        }

    def _capture_track(self, state: Dict[str, Any]) -> Any:
        try:
            from PIL import Image
        except Exception:
            return None

        temp_root = self._profile_dir / ".ares-visual-cache" if self._profile_dir else Path(tempfile.gettempdir()) / "ares-visual-cache"
        try:
            temp_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        filename = "slider-observation.png"
        path = temp_root / filename
        selector = str(state.get("trackSelector") or "").strip()

        if selector and str(state.get("scope") or "document") == "document":
            try:
                self._sb.save_screenshot(filename, folder=str(temp_root), selector=selector)
                if path.exists():
                    return Image.open(path).convert("RGB")
            except Exception:
                pass

        try:
            self._sb.save_screenshot(filename, folder=str(temp_root))
            if not path.exists():
                return None
            image = Image.open(path).convert("RGB")
            rect = state.get("trackRect") if isinstance(state.get("trackRect"), dict) else None
            if not rect:
                return image
            metrics = self._viewport_metrics()
            cropped = self._crop_viewport_region(image, rect, metrics)
            image.close()
            return cropped
        except Exception:
            return None

    def _viewport_metrics(self) -> Dict[str, float]:
        script = """
        (() => ({
          width: window.innerWidth || document.documentElement.clientWidth || 0,
          height: window.innerHeight || document.documentElement.clientHeight || 0,
          scrollX: window.scrollX || 0,
          scrollY: window.scrollY || 0,
          dpr: window.devicePixelRatio || 1,
          pageHeight: Math.max(document.body?.scrollHeight || 0, document.documentElement?.scrollHeight || 0)
        }))()
        """
        try:
            value = self._evaluate(script)
        except Exception:
            value = {}
        if not isinstance(value, dict):
            value = {}
        return {key: float(value.get(key) or default) for key, default in {
            "width": 1.0, "height": 1.0, "scrollX": 0.0, "scrollY": 0.0, "dpr": 1.0, "pageHeight": 1.0
        }.items()}

    @staticmethod
    def _crop_viewport_region(image: Any, rect: Dict[str, Any], metrics: Dict[str, float]) -> Any:
        width = max(1.0, metrics.get("width", 1.0))
        height = max(1.0, metrics.get("height", 1.0))
        scale_x = image.width / width
        viewport_height_px = height * scale_x
        full_page = image.height > viewport_height_px * 1.25
        x = float(rect.get("x") or 0.0)
        y = float(rect.get("y") or 0.0) + (metrics.get("scrollY", 0.0) if full_page else 0.0)
        w = max(1.0, float(rect.get("width") or 1.0))
        h = max(1.0, float(rect.get("height") or 1.0))
        pad_x = max(3.0, w * 0.015)
        pad_y = max(5.0, h * 0.40)
        left = max(0, int(round((x - pad_x) * scale_x)))
        right = min(image.width, int(round((x + w + pad_x) * scale_x)))
        top = max(0, int(round((y - pad_y) * scale_x)))
        bottom = min(image.height, int(round((y + h + pad_y) * scale_x)))
        if right - left < 8 or bottom - top < 8:
            return image.copy()
        return image.crop((left, top, right, bottom))

    @classmethod
    def analyze_track_image(cls, image: Any, *, orientation: str, current_fraction: float) -> Dict[str, Any] | None:
        rgb = image.convert("RGB")
        horizontal = orientation != "vertical"
        axis_len = rgb.width if horizontal else rgb.height
        cross_len = rgb.height if horizontal else rgb.width
        if axis_len < 24 or cross_len < 3:
            return None

        band_half = max(1, int(round(cross_len * 0.14)))
        center = cross_len // 2
        band = range(max(0, center - band_half), min(cross_len, center + band_half + 1))
        vectors: List[Tuple[float, float, float]] = []
        pixels = rgb.load()
        for axis in range(axis_len):
            samples: List[Tuple[int, int, int]] = []
            source_axis = axis if horizontal else axis_len - 1 - axis
            for cross in band:
                x, y = (source_axis, cross) if horizontal else (cross, source_axis)
                samples.append(tuple(pixels[x, y]))
            vectors.append(tuple(float(statistics.median(channel)) for channel in zip(*samples)))

        vectors = cls._smooth_vectors(vectors)
        baseline = tuple(float(statistics.median(channel)) for channel in zip(*vectors))
        distances = [math.sqrt(sum((value[i] - baseline[i]) ** 2 for i in range(3))) for value in vectors]
        edges = [0.0]
        for index in range(1, axis_len):
            edges.append(math.sqrt(sum((vectors[index][i] - vectors[index - 1][i]) ** 2 for i in range(3))))
        scores = [distances[index] + edges[index] * 0.65 for index in range(axis_len)]

        edge_margin = max(1, int(axis_len * 0.015))
        handle_radius = max(3, int(axis_len * 0.045))
        handle_index = int(round(max(0.0, min(1.0, current_fraction)) * (axis_len - 1)))
        valid: List[float] = []
        for index, score in enumerate(scores):
            if index < edge_margin or index >= axis_len - edge_margin or abs(index - handle_index) <= handle_radius:
                scores[index] = 0.0
            else:
                valid.append(score)
        if not valid:
            return None

        median = float(statistics.median(valid))
        mad = float(statistics.median(abs(value - median) for value in valid))
        threshold = median + max(3.0, mad * 3.0)
        active = [score >= threshold for score in scores]
        cls._bridge_small_gaps(active, 2)
        segments = cls._segments(active)

        ranked: List[Tuple[float, int, int, float]] = []
        for start, end in segments:
            width = end - start + 1
            fraction_width = width / axis_len
            if fraction_width > 0.55:
                continue
            center_index = (start + end) / 2.0
            center_fraction = center_index / max(1, axis_len - 1)
            if abs(center_fraction - current_fraction) < 0.065:
                continue
            contrast = float(statistics.mean(scores[start : end + 1])) - median
            if contrast < 2.5:
                continue
            rank = contrast * math.sqrt(max(1.0, width))
            if start <= axis_len * 0.03 and abs((end / axis_len) - current_fraction) < 0.10:
                rank *= 0.25
            if width <= max(3, int(axis_len * 0.02)):
                rank *= 1.15
            ranked.append((rank, start, end, contrast))

        if not ranked:
            peak = max(range(axis_len), key=lambda idx: scores[idx])
            contrast = scores[peak] - median
            if contrast < max(7.0, mad * 4.0):
                return None
            ranked.append((contrast, peak, peak, contrast))

        _, start, end, contrast = max(ranked, key=lambda item: item[0])
        target = ((start + end) / 2.0) / max(1, axis_len - 1)
        confidence = max(0.45, min(0.93, 0.42 + contrast / 45.0))
        return {
            "targetFraction": round(target, 4),
            "confidence": round(confidence, 4),
            "contrast": round(contrast, 3),
            "region": {
                "startFraction": round(start / max(1, axis_len - 1), 4),
                "endFraction": round(end / max(1, axis_len - 1), 4),
            },
        }

    @staticmethod
    def _smooth_vectors(values: Sequence[Tuple[float, float, float]]) -> List[Tuple[float, float, float]]:
        result: List[Tuple[float, float, float]] = []
        for index in range(len(values)):
            sample = values[max(0, index - 1) : min(len(values), index + 2)]
            result.append(tuple(sum(item[channel] for item in sample) / len(sample) for channel in range(3)))
        return result

    @staticmethod
    def _bridge_small_gaps(values: List[bool], max_gap: int) -> None:
        index = 0
        while index < len(values):
            if values[index]:
                index += 1
                continue
            start = index
            while index < len(values) and not values[index]:
                index += 1
            end = index - 1
            if start > 0 and index < len(values) and end - start + 1 <= max_gap:
                for cursor in range(start, end + 1):
                    values[cursor] = True

    @staticmethod
    def _segments(values: Sequence[bool]) -> List[Tuple[int, int]]:
        segments: List[Tuple[int, int]] = []
        start: int | None = None
        for index, active in enumerate(values):
            if active and start is None:
                start = index
            elif not active and start is not None:
                segments.append((start, index - 1))
                start = None
        if start is not None:
            segments.append((start, len(values) - 1))
        return segments

    @staticmethod
    def _directional_target(instruction: str) -> Dict[str, Any] | None:
        if _RIGHT.search(instruction):
            return SliderTargetGrounder._decision(0.96, 0.40, "directional-fallback", "S3")
        if _LEFT.search(instruction):
            return SliderTargetGrounder._decision(0.04, 0.40, "directional-fallback", "S3")
        return None

    def _evaluate(self, script: str) -> Any:
        evaluator = getattr(self._sb, "evaluate", None)
        if callable(evaluator):
            return evaluator(script)
        return self._sb.execute_script(f"return {script};")

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            return float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _decision(fraction: float, confidence: float, source: str, mark_id: str) -> Dict[str, Any]:
        return {
            "grounded": True,
            "targetFraction": round(max(0.0, min(1.0, float(fraction))), 4),
            "confidence": round(max(0.0, min(1.0, float(confidence))), 4),
            "source": source,
            "markId": mark_id,
        }

    @staticmethod
    def _none(reason: str) -> Dict[str, Any]:
        return {"grounded": False, "targetFraction": None, "confidence": 0.0, "source": "none", "reason": reason}
