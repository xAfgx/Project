from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any, Dict, Tuple


_EXTRACT_SCRIPT = r"""
return (() => {
  const visible = el => {
    if (!el?.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width >= 12 && r.height >= 12 && s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
  };
  const identity = el => `${el.id || ''} ${typeof el.className === 'string' ? el.className : ''} ${el.getAttribute?.('aria-label') || ''}`.toLowerCase();
  const containers = [...(document.querySelectorAll?.('#captcha, #captcha__puzzle, [class*="puzzle" i], [class*="captcha" i], [class*="slide" i]') || [])];
  let nodes = [];
  for (const container of containers) {
    const found = [...(container.querySelectorAll?.('img,canvas') || [])].filter(visible);
    if (found.length >= 2) { nodes = found; break; }
  }
  if (nodes.length < 2) {
    nodes = [...(document.querySelectorAll?.('img,canvas') || [])].filter(visible);
  }
  if (nodes.length < 2) return null;
  const enriched = nodes.map((el, index) => {
    const r = el.getBoundingClientRect();
    const token = identity(el);
    const area = r.width * r.height;
    let bgScore = area;
    let pieceScore = 0;
    if (/background|\bbg\b|captcha|canvas|image/.test(token)) bgScore *= 1.35;
    if (/piece|puzzle|target|block|slider|slide/.test(token)) pieceScore += 1000000;
    pieceScore += 1 / Math.max(1, area) * 100000000;
    return {el, index, area, bgScore, pieceScore, width: r.width, height: r.height};
  });
  const background = [...enriched].sort((a, b) => b.bgScore - a.bgScore)[0];
  if (!background) return null;
  const pieces = enriched.filter(item => item.el !== background.el && item.area < background.area * 0.95 && item.width <= background.width);
  const piece = pieces.sort((a, b) => b.pieceScore - a.pieceScore)[0];
  if (!piece) return null;
  const toDataUrl = el => {
    try {
      if (el.tagName === 'CANVAS') return el.toDataURL('image/png');
      const canvas = document.createElement('canvas');
      canvas.width = el.naturalWidth || el.getBoundingClientRect().width;
      canvas.height = el.naturalHeight || el.getBoundingClientRect().height;
      canvas.getContext('2d').drawImage(el, 0, 0);
      return canvas.toDataURL('image/png');
    } catch (_) { return ''; }
  };
  const target = toDataUrl(piece.el);
  const backgroundUrl = toDataUrl(background.el);
  if (!target || !backgroundUrl) return null;
  return { target, background: backgroundUrl };
})();
"""


class DdddOcrSliderProvider:
    """Optional local slider-image matcher.

    The provider is deliberately lazy: ARES does not require ddddocr to start.
    When ddddocr is installed, matching is available automatically without a
    feature flag. SeleniumBase remains the only browser/screenshot executor.
    """

    def __init__(self, seleniumbase_cdp: Any, *, profile_dir: str | Path | None = None) -> None:
        self._sb = seleniumbase_cdp
        self._profile_dir = Path(profile_dir).expanduser().resolve() if profile_dir else None
        self._matcher: Any | None = None
        self._import_failed = False

    def available(self) -> bool:
        return self._load_matcher() is not None

    def ground(self, state: Dict[str, Any]) -> Dict[str, Any] | None:
        if state.get("kind") != "slider" or str(state.get("orientation") or "horizontal") == "vertical":
            return None
        matcher = self._load_matcher()
        if matcher is None:
            return None

        frame_path = [str(value) for value in state.get("framePath") or [] if str(value)]
        assets = self._run_extract(frame_path)
        if not isinstance(assets, dict):
            return None
        target_bytes = self._decode_data_url(str(assets.get("target") or ""))
        background_bytes = self._decode_data_url(str(assets.get("background") or ""))
        if not target_bytes or not background_bytes:
            return None

        try:
            dimensions = self._image_dimensions(target_bytes, background_bytes)
            if dimensions is None:
                return None
            target_width, background_width = dimensions
            result = self._match(matcher, target_bytes, background_bytes)
            if not isinstance(result, dict):
                return None
            box = result.get("target")
            if not isinstance(box, (list, tuple)) or len(box) < 4:
                return None
            x1 = float(box[0])
            x2 = float(box[2])
            matched_width = max(1.0, x2 - x1, float(target_width))
            travel = max(1.0, float(background_width) - matched_width)
            fraction = max(0.0, min(1.0, x1 / travel))
            return {
                "found": True,
                "grounded": True,
                "targetFraction": fraction,
                "confidence": 0.76,
                "source": "ddddocr-slide-match",
                "markId": "S3",
                "provider": "ddddocr",
                "matchBox": [float(value) for value in box[:4]],
            }
        except Exception:
            return None

    def status(self) -> Dict[str, Any]:
        # Status reads must stay side-effect free. Loading ddddocr constructs a
        # local model and can take seconds on a cold Windows runner; doing that
        # from a status/RPC probe can starve the task worker command loop.
        if self._matcher is not None:
            load_state = "ready"
        elif self._import_failed:
            load_state = "unavailable"
        else:
            load_state = "not-loaded"
        return {
            "provider": "ddddocr",
            "optional": True,
            "enabledByDefault": True,
            "available": self._matcher is not None,
            "loadState": load_state,
        }

    def _load_matcher(self) -> Any | None:
        if self._matcher is not None:
            return self._matcher
        if self._import_failed:
            return None
        try:
            import ddddocr  # type: ignore

            self._matcher = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)
            return self._matcher
        except Exception:
            self._import_failed = True
            return None

    def _run_extract(self, frame_path) -> Any:
        evaluate = getattr(self._sb, "ares_oopif_evaluate", None)
        if frame_path and callable(evaluate):
            try:
                evaluated = evaluate(frame_path, _EXTRACT_SCRIPT, [])
                if isinstance(evaluated, dict) and isinstance(evaluated.get("value"), dict):
                    return evaluated["value"]
            except Exception:
                pass
            return None
        try:
            result = self._sb.execute_script(_EXTRACT_SCRIPT)
        except Exception:
            return None
        return result if isinstance(result, dict) else None

    @staticmethod
    def _decode_data_url(url: str) -> bytes:
        if not url.startswith("data:"):
            return b""
        try:
            head, payload = url.split(",", 1)
            if ";base64" in head:
                return base64.b64decode(payload)
            import urllib.parse

            return urllib.parse.unquote_to_bytes(payload)
        except Exception:
            return b""

    @staticmethod
    def _image_dimensions(target_bytes: bytes, background_bytes: bytes) -> Tuple[int, int] | None:
        try:
            from PIL import Image

            with Image.open(io.BytesIO(target_bytes)) as target, Image.open(io.BytesIO(background_bytes)) as background:
                return int(target.width), int(background.width)
        except Exception:
            return None

    @staticmethod
    def _match(matcher: Any, target_bytes: bytes, background_bytes: bytes) -> Any:
        try:
            return matcher.slide_match(target_bytes, background_bytes)
        except Exception:
            return matcher.slide_match(target_bytes, background_bytes, simple_target=True)
