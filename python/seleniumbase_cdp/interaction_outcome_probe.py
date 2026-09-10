from interaction_outcome import from_semantic_result, from_visual_result


def main() -> None:
    semantic_ok = from_semantic_result({
        "results": [{"fieldId": "f1", "verified": True, "intent": "email"}]
    })
    assert semantic_ok["success"] is True
    assert semantic_ok["verified"] is True
    assert semantic_ok["fallbackNeeded"] is False

    semantic_fail = from_semantic_result({
        "results": [{"fieldId": "f2", "verified": False, "reason": "field-not-found"}]
    })
    assert semantic_fail["success"] is False
    assert semantic_fail["fallbackNeeded"] is True
    assert semantic_fail["outcomes"][0]["stage"] == "failed"

    visual_ok = from_visual_result({"acted": True, "verified": True, "kind": "slider"})
    assert visual_ok["success"] is True
    assert visual_ok["verified"] is True
    assert visual_ok["outcomes"][0]["kind"] == "slider"

    visual_idle = from_visual_result({"acted": False, "kind": "none"})
    assert visual_idle["fallbackNeeded"] is False

    visual_fail = from_visual_result({"acted": True, "verified": False, "kind": "grid", "reason": "no-change"})
    assert visual_fail["success"] is False
    assert visual_fail["fallbackNeeded"] is True

    print("unified interaction outcome probe: ok")


if __name__ == "__main__":
    main()
