from __future__ import annotations

from authorized_grid_action_executor import AuthorizedGridActionExecutor


class FakeAdapter:
    def __init__(self) -> None:
        self._overrides = {"tiles": ".tile", "submit": ".submit"}
        self.calls = 0

    def poll(self):
        self.calls += 1
        return {
            "kind": "image-grid",
            "scope": "document",
            "tileCount": 9,
            "generation": self.calls,
            "signature": f"sig-{self.calls}",
        }


class FakeCdp:
    def __init__(self) -> None:
        self.scripts = []

    def evaluate(self, script):
        self.scripts.append(script)
        return {"clicked": [1, 3, 8], "submitted": True}


def main() -> int:
    cdp = FakeCdp()
    adapter = FakeAdapter()
    executor = AuthorizedGridActionExecutor(cdp, adapter)

    result = executor.apply([8, 3, 1, 3, -1, 99], submit=True)
    assert result["clickedIndexes"] == [1, 3, 8]
    assert result["submitted"] is True
    assert result["state"]["generation"] == 2
    assert "const selected = [1, 3, 8]" in cdp.scripts[0]
    assert '"tiles": ".tile"' in cdp.scripts[0]
    assert adapter.calls == 2

    empty = AuthorizedGridActionExecutor(cdp, adapter)._indexes([], 9)
    assert empty == []
    print("Authorized grid action executor probe passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
