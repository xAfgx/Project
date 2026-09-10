from __future__ import annotations

from semantic_interaction_runtime import SemanticInteractionRuntime


class FakeSb:
    def __init__(self) -> None:
        self.calls = []

    def execute_script(self, script, *args):
        self.calls.append((script, args))
        if "return controls.map" in script:
            return [
                {
                    "fieldId": "ares-semantic-0",
                    "index": 0,
                    "tagName": "input",
                    "inputType": "email",
                    "name": "email",
                    "id": "email",
                    "autocomplete": "shipping email",
                    "placeholder": "",
                    "ariaLabel": "",
                    "label": "E-Mail",
                    "nearbyText": "E-Mail",
                }
            ]
        if "field-not-found" in script:
            return {"verified": True, "reason": "", "observedValue": str(args[1])}
        raise AssertionError("unexpected script")


def main() -> int:
    runtime = SemanticInteractionRuntime(FakeSb())
    fields = runtime.observe_fields()
    assert fields[0]["fieldId"] == "ares-semantic-0"
    assert fields[0]["autocomplete"] == "shipping email"

    result = runtime.execute_plan([
        {
            "fieldId": "ares-semantic-0",
            "intent": "email",
            "context": "shipping",
            "confidence": 1.0,
            "value": "student@example.test",
        }
    ])
    assert result["planned"] == 1
    assert result["applied"] == 1
    assert result["verified"] is True
    assert result["fallbackNeeded"] == []
    print("Semantic interaction runtime probe passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
