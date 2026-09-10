from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict


class InteractionTrace:
    """Append compact observation/decision/action records for later inspection."""

    def __init__(self, profile_dir: str | Path) -> None:
        root = Path(profile_dir).expanduser().resolve()
        self._path = root / ".ares-visual-trace.jsonl"
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.touch(exist_ok=True)
        except OSError:
            pass
        self.append("session-start", {"state": {"kind": "runtime", "status": "initialized"}})

    @property
    def path(self) -> Path:
        return self._path

    def append(self, phase: str, payload: Dict[str, Any]) -> None:
        record = {
            "ts": time.time(),
            "phase": str(phase),
            **self._compact(payload),
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            pass

    def _compact(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        clean: Dict[str, Any] = {}
        for key, value in payload.items():
            if key in {"sources"}:
                clean[key] = [self._short(item, 160) for item in (value or [])[:30]]
            elif key in {"state", "result", "decision", "target"} and isinstance(value, dict):
                clean[key] = self._compact_dict(value)
            else:
                clean[key] = self._safe(value)
        return clean

    def _compact_dict(self, value: Dict[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, item in value.items():
            if key == "sources":
                result[key] = [self._short(source, 160) for source in (item or [])[:30]]
            elif key == "scores" and isinstance(item, list):
                result[key] = item[:30]
            elif key in {"marks", "targetCandidates"} and isinstance(item, list):
                result[key] = [self._safe(entry) for entry in item[:30]]
            elif key in {"error", "instruction", "label"}:
                result[key] = self._short(item, 600)
            else:
                result[key] = self._safe(item)
        return result

    @classmethod
    def _safe(cls, value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return cls._short(value, 1000)
        if isinstance(value, list):
            return [cls._safe(item) for item in value[:50]]
        if isinstance(value, dict):
            return {str(key): cls._safe(item) for key, item in list(value.items())[:60]}
        return cls._short(str(value), 1000)

    @staticmethod
    def _short(value: Any, limit: int) -> str:
        text = str(value or "")
        return text if len(text) <= limit else text[:limit] + "…"
