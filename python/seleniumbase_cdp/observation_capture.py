from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, List

from interaction_policy import InteractionPolicy


class ObservationCapture:
    """Bounded screenshot archive for documentation and visual fallbacks."""

    def __init__(self, seleniumbase_cdp: Any, *, profile_dir: str | Path, policy: InteractionPolicy) -> None:
        self._sb = seleniumbase_cdp
        self._policy = policy
        self._root = Path(profile_dir).expanduser().resolve() / ".ares-observations"
        self._root.mkdir(parents=True, exist_ok=True)
        self._counter = 0
        self._last: Dict[str, Any] = {"captured": False, "reason": "not-run"}

    @property
    def root(self) -> Path:
        return self._root

    def capture(self, event: str, *, generation: int = 0, force: bool = False) -> Dict[str, Any]:
        event = str(event or "event").strip().lower() or "event"
        if not force and not self._policy.capture_enabled(event):
            self._last = {"captured": False, "reason": "policy-disabled", "event": event, "generation": generation}
            return dict(self._last)

        self._root.mkdir(parents=True, exist_ok=True)
        self._counter += 1
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        safe_event = re.sub(r"[^a-z0-9_-]+", "-", event).strip("-") or "event"
        filename = f"{stamp}-{self._counter:04d}-g{int(generation)}-{safe_event}.png"
        path = self._root / filename
        errors: List[str] = []
        provider = "seleniumbase-cdp"

        try:
            self._sb.save_screenshot(filename, folder=str(self._root))
        except Exception as exc:
            errors.append(f"seleniumbase-cdp: {exc}")
        captured = path.exists() and path.stat().st_size > 0

        # SeleniumBase's sync wrapper writes through its cached `page`. Manual
        # browser use can make that page stale while get_active_tab() is already
        # correct. Use SeleniumBase's documented async tab screenshot API as the
        # exact fallback, writing to the same absolute ARES path.
        if not captured:
            try:
                tab = self._sb.get_active_tab()
                loop = self._sb.get_event_loop()
                if tab is None or loop is None:
                    raise RuntimeError("active CDP tab/event loop unavailable")
                loop.run_until_complete(
                    tab.save_screenshot(filename=str(path), format="png", full_page=False)
                )
                provider = "active-tab-cdp"
            except Exception as exc:
                errors.append(f"active-tab-cdp: {exc}")
            captured = path.exists() and path.stat().st_size > 0

        if captured:
            self._rotate()
        self._last = {
            "captured": captured,
            "reason": "captured" if captured else ("capture-error" if errors else "missing-output"),
            "event": event,
            "generation": generation,
            "path": str(path),
            "provider": provider if captured else "none",
        }
        if not captured and errors:
            self._last["error"] = " | ".join(errors)[:2000]
        return dict(self._last)

    def status(self) -> Dict[str, Any]:
        return {
            "root": str(self._root),
            "maxSavedCaptures": self._policy.max_saved_captures,
            "last": dict(self._last),
        }

    def _rotate(self) -> None:
        try:
            files: List[Path] = sorted(
                (path for path in self._root.glob("*.png") if path.is_file()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        for path in files[self._policy.max_saved_captures :]:
            try:
                path.unlink()
            except OSError:
                pass
