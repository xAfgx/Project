from __future__ import annotations

from interaction_orchestrator import InteractionOrchestrator


def main() -> int:
    orchestrator = InteractionOrchestrator()
    calls: list[str] = []

    first = orchestrator.run_cycle(
        lambda: calls.append("visual") or {"acted": True, "verified": True, "kind": "grid"},
        lambda: calls.append("instruction") or {"acted": True, "verified": True, "kind": "instruction-input"},
    )
    assert calls == ["visual"]
    assert first["kind"] == "grid"

    calls.clear()
    second = orchestrator.run_cycle(
        lambda: calls.append("visual") or {"acted": False, "kind": "none"},
        lambda: calls.append("instruction") or {"acted": True, "verified": True, "kind": "instruction-input"},
    )
    assert calls == ["visual", "instruction"]
    assert second["kind"] == "instruction-input"

    semantic = orchestrator.run_action(
        "semantic",
        lambda: {"acted": True, "verified": True, "kind": "semantic"},
    )
    assert semantic["kind"] == "semantic"

    status = orchestrator.status()
    assert status["state"] == "idle"
    assert status["owner"] == "none"
    assert status["singleOwner"] is True
    assert int(status["generation"]) >= 3

    print("interaction orchestrator probe: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
