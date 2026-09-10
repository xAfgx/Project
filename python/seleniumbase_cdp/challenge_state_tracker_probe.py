from __future__ import annotations

from challenge_state_tracker import ChallengeStateTracker


class FakeElement:
    def __init__(self, attrs=None, images=None, html=""):
        self.attrs = attrs or {}
        self._images = images or []
        self._html = html

    def query_selector_all(self, selector):
        if selector == "img":
            return list(self._images)
        return []

    def get_html(self):
        return self._html


class FakeSb:
    def __init__(self):
        self.url = "https://demo.invalid/test"
        self.frames = []

    def get_current_url(self):
        return self.url

    def find_elements(self, selector):
        return list(self.frames) if selector == "iframe" else []


def image(index: int, generation: int = 1) -> FakeElement:
    src = f"https://assets.invalid/g{generation}/tile-{index}.jpg"
    return FakeElement(attrs={"src": src}, html=f'<img src="{src}">')


def main() -> int:
    sb = FakeSb()
    tracker = ChallengeStateTracker(sb)

    empty = tracker.poll()
    assert empty["kind"] == "none"
    assert empty["generation"] == 1

    sb.frames = [
        FakeElement(
            attrs={"title": "visual challenge"},
            images=[image(i) for i in range(9)],
        )
    ]
    first_grid = tracker.poll()
    assert first_grid["kind"] == "image-grid"
    assert first_grid["rows"] == 3 and first_grid["columns"] == 3
    assert first_grid["tileCount"] == 9
    assert first_grid["changedIndexes"] == list(range(9))

    stable = tracker.poll()
    assert stable["changedIndexes"] == []
    stable_generation = stable["generation"]

    changed = [image(i) for i in range(9)]
    changed[4] = image(4, generation=2)
    sb.frames = [FakeElement(attrs={"title": "visual challenge"}, images=changed)]
    updated = tracker.poll()
    assert updated["changedIndexes"] == [4]
    assert updated["generation"] == stable_generation + 1

    sb.url = "https://demo.invalid/after-reload"
    after_reload = tracker.poll()
    assert after_reload["generation"] == updated["generation"] + 1

    print("PASS: SeleniumBase challenge state tracker detects grid generations and changed tiles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
