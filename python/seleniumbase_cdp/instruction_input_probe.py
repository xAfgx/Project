from __future__ import annotations

from instruction_input_runtime import InstructionInputRuntime


class FakeSb:
    def __init__(self) -> None:
        self.calls = []

    def execute_script(self, script, *args):
        self.calls.append((script, args))
        if "document.querySelectorAll(selector)" in script:
            return {
                "pageText": "Gib 42 ein.",
                "controls": [
                    {
                        "fieldId": "ares-instruction-0",
                        "index": 0,
                        "tagName": "input",
                        "inputType": "number",
                        "placeholder": "",
                        "ariaLabel": "",
                        "value": "",
                    }
                ],
            }
        return {"acted": True, "verified": True, "reason": "", "observedValue": str(args[1])}


def control(field_id: str, input_type: str = "number"):
    return [{
        "fieldId": field_id,
        "index": 0,
        "tagName": "input",
        "inputType": input_type,
        "placeholder": "",
        "ariaLabel": "",
        "value": "",
    }]


def main() -> int:
    sb = FakeSb()
    runtime = InstructionInputRuntime(sb)

    decision = runtime.infer()
    assert decision["matched"] is True
    assert decision["value"] == "42"
    assert decision["fieldId"] == "ares-instruction-0"

    result = runtime.apply()
    assert result["acted"] is True
    assert result["verified"] is True
    assert result["requestedValue"] == "42"
    assert result["observedValue"] == "42"

    runtime.observe = lambda: {
        "pageText": "Tippe TEST123.",
        "controls": control("ares-instruction-1", "text"),
    }
    text_decision = runtime.infer()
    assert text_decision["matched"] is True
    assert text_decision["value"] == "TEST123"

    arithmetic_cases = (
        ("Was ist 14 + 7?", "21"),
        ("Berechne 8 * 5.", "40"),
        ("Berechne -3 - 9.", "-12"),
        ("What is 7 / 2?", "3.5"),
    )
    for index, (page_text, expected) in enumerate(arithmetic_cases, start=2):
        runtime.observe = lambda page_text=page_text, index=index: {
            "pageText": page_text,
            "controls": control(f"ares-instruction-{index}"),
        }
        arithmetic = runtime.infer()
        assert arithmetic["matched"] is True
        assert arithmetic["value"] == expected

    runtime.observe = lambda: {
        "pageText": "Berechne 5 / 0.",
        "controls": control("ares-instruction-zero"),
    }
    divide_by_zero = runtime.infer()
    assert divide_by_zero["matched"] is False
    assert divide_by_zero["reason"] == "no-simple-instruction"

    runtime.observe = lambda: {
        "pageText": "Willkommen auf der Testseite.",
        "controls": control("ares-instruction-last", "text"),
    }
    no_match = runtime.infer()
    assert no_match["matched"] is False
    assert no_match["reason"] == "no-simple-instruction"

    print("instruction input runtime probe: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
