from __future__ import annotations

from collections import deque
from typing import Any, Dict, Iterable

from scope_locked_grid_site_adapter import ScopeLockedGridSiteAdapter


def _marks(source_prefix: str) -> list[dict[str, Any]]:
    return [
        {
            "markId": f"GRID-{index}",
            "role": "grid-tile",
            "source": f"{source_prefix}-{index}",
            "semanticVisualSignature": f"sig-{source_prefix}-{index}",
        }
        for index in range(4)
    ]


def _grid(*, epoch: int = 1, source: str = "a") -> Dict[str, Any]:
    return {
        "kind": "image-grid",
        "scope": "oopif:iframe#challenge",
        "score": 90,
        "rows": 2,
        "columns": 2,
        "tileCount": 4,
        "instruction": "Select matching images",
        "sources": [f"{source}-{index}" for index in range(4)],
        "submitText": "Verify",
        "submitBounds": None,
        "complete": False,
        "failed": False,
        "override": False,
        "viewport": {"width": 800, "height": 600, "devicePixelRatio": 1},
        "marks": _marks(source),
        "framePath": ["iframe#challenge"],
        "frameId": "frame-challenge",
        "documentEpoch": epoch,
        "sessionGeneration": 3,
    }


def _terminal(*, complete: bool = False, failed: bool = False) -> Dict[str, Any]:
    return {
        "kind": "none",
        "scope": "oopif:iframe#challenge",
        "score": 0,
        "rows": 0,
        "columns": 0,
        "tileCount": 0,
        "instruction": "",
        "sources": [],
        "submitText": "",
        "submitBounds": None,
        "complete": complete,
        "failed": failed,
        "override": False,
        "viewport": {},
        "marks": [],
        "framePath": ["iframe#challenge"],
        "frameId": "frame-challenge",
        "documentEpoch": 1,
        "sessionGeneration": 3,
    }


class FakeScopeAdapter(ScopeLockedGridSiteAdapter):
    def __init__(self, discoveries: Iterable[Dict[str, Any]], local_states: Iterable[Dict[str, Any]]) -> None:
        self._sb = object()
        self._overrides = {}
        self._generation = 0
        self._last_signature = ""
        self._scope_lock = None
        self._discoveries = deque(dict(value) for value in discoveries)
        self._locals = deque(dict(value) for value in local_states)
        self.global_calls = 0
        self.local_calls = 0

    def _discover_global(self) -> Dict[str, Any]:
        self.global_calls += 1
        if not self._discoveries:
            raise AssertionError("unexpected global rediscovery")
        return self._with_generation(self._discoveries.popleft())

    def _prime_scope(self, discovered: Dict[str, Any]) -> Dict[str, Any] | None:
        return dict(discovered)

    def _revalidate_locked_scope(self) -> Dict[str, Any]:
        self.local_calls += 1
        if not self._locals:
            raise AssertionError("unexpected local revalidation")
        return dict(self._locals.popleft())

    @staticmethod
    def _candidate_is_plausible(snapshot: Dict[str, Any]) -> bool:
        return snapshot.get("kind") == "image-grid" and int(snapshot.get("tileCount") or 0) == 4


def assert_local_revalidation_without_global_rescan() -> None:
    adapter = FakeScopeAdapter([_grid(epoch=1)], [_grid(epoch=1)])
    first = adapter.poll()
    assert first["scopeLocked"] is True
    assert adapter.global_calls == 1 and adapter.local_calls == 0

    second = adapter.poll()
    assert second["scopeLocked"] is True
    assert adapter.global_calls == 1, "unchanged locked scope must not globally rediscover"
    assert adapter.local_calls == 1


def assert_document_epoch_invalidates_lock() -> None:
    adapter = FakeScopeAdapter(
        [_grid(epoch=1), _grid(epoch=2)],
        [_grid(epoch=2)],
    )
    adapter.poll()
    refreshed = adapter.poll()
    assert adapter.global_calls == 2, "new document epoch must force global rediscovery"
    assert refreshed["documentEpoch"] == 2
    assert refreshed["scopeLocked"] is True


def assert_visual_identity_invalidates_lock() -> None:
    adapter = FakeScopeAdapter(
        [_grid(epoch=1, source="a"), _grid(epoch=1, source="b")],
        [_grid(epoch=1, source="b")],
    )
    adapter.poll()
    refreshed = adapter.poll()
    assert adapter.global_calls == 2, "changed tile identity must force global rediscovery"
    assert refreshed["sources"][0] == "b-0"


def assert_terminal_outcome_releases_lock() -> None:
    adapter = FakeScopeAdapter([_grid(epoch=1)], [_terminal(complete=True)])
    adapter.poll()
    terminal = adapter.poll()
    assert terminal["complete"] is True
    assert adapter._scope_lock is None
    assert adapter.global_calls == 1


def main() -> int:
    assert_local_revalidation_without_global_rescan()
    assert_document_epoch_invalidates_lock()
    assert_visual_identity_invalidates_lock()
    assert_terminal_outcome_releases_lock()
    print("GRID_SCOPE_LIFECYCLE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
