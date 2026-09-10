from visual_event_trigger import VisualEventTrigger


def main() -> int:
    trigger = VisualEventTrigger()

    words_only = trigger.evaluate([], visible_text="security verify challenge gate")
    assert words_only["triggered"] is False, words_only

    slider = trigger.evaluate(["slider-candidate"], visible_text="")
    assert slider["triggered"] is True, slider
    assert slider["structuralScore"] == 4, slider

    iframe = trigger.evaluate(["iframe-added"], visible_text="verify")
    assert iframe["triggered"] is True, iframe
    assert iframe["score"] >= 4, iframe

    weak_canvas = trigger.evaluate(["canvas-candidate"], visible_text="")
    assert weak_canvas["triggered"] is False, weak_canvas

    canvas_with_context = trigger.evaluate(["canvas-candidate"], visible_text="verification")
    assert canvas_with_context["triggered"] is True, canvas_with_context

    print("Visual event trigger keeps text weak and structural/geometric events authoritative.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
