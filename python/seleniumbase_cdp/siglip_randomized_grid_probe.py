from __future__ import annotations

import os
import random
from typing import Any, Dict, List

from robust_vision_grid_classifier import RobustVisionGridClassifier
from siglip_randomized_grid_probe_legacy import (
    CATEGORIES,
    _feature_tensor,
    _make_grid,
    _seed,
    _separation,
)


def _rounds() -> int:
    return max(1, min(6, int(os.environ.get("ARES_SIGLIP_TEST_ROUNDS", "1"))))


def main() -> int:
    seed = _seed()
    rounds = _rounds()
    print(f"SIGLIP_RANDOM_SEED={seed} rounds={rounds}")

    classifier = RobustVisionGridClassifier()
    if not classifier._load():
        raise AssertionError(f"SigLIP2 unavailable: {classifier.error}")
    print(
        f"SIGLIP_PREFLIGHT model={classifier.model_name} device={classifier._device} "
        f"selectionPolicy={classifier.status().get('selectionPolicy')}"
    )

    failures: List[str] = []
    exact_cases = 0
    total_cases = 0
    minimum_margin: float | None = None

    for round_index in range(rounds):
        round_seed = seed ^ ((round_index + 1) * 1_000_003)
        rng = random.Random(round_seed)
        tiles, sources = _make_grid(rng, round_seed)

        for category_index, target in enumerate(CATEGORIES):
            expected = sorted(
                index for index, tile in enumerate(tiles)
                if int(tile["category"]) == category_index
            )
            runtime = classifier.classify(f"Select all images with {target}", sources)
            total_cases += 1
            if runtime.get("error"):
                failures.append(f"round={round_index} target={target}: {runtime.get('error')}")
                continue

            selected = sorted(int(value) for value in runtime.get("selectedIndexes") or [])
            scores = runtime.get("scores") or []
            try:
                separation: Dict[str, float] = _separation(scores, expected)
            except Exception as exc:
                failures.append(f"round={round_index} target={target}: incomplete joint-forward scores: {exc}")
                continue

            margin = float(separation["margin"])
            minimum_margin = margin if minimum_margin is None else min(minimum_margin, margin)
            exact = selected == expected
            exact_cases += int(exact)
            print(
                "SIGLIP_JOINT_FORWARD_DIAGNOSTIC "
                f"round={round_index} target={target!r} expected={expected} selected={selected} "
                f"minTarget={separation['minTarget']:.6f} "
                f"maxDistractor={separation['maxDistractor']:.6f} "
                f"margin={margin:.6f} exact={exact}"
            )

            if margin <= 0:
                failures.append(
                    f"round={round_index} target={target}: joint-forward ranking margin {margin:.6f} <= 0"
                )
            if not exact:
                failures.append(
                    f"round={round_index} target={target}: production selected {selected}, expected {expected}"
                )

    if failures:
        raise AssertionError(
            "SigLIP2 production joint-forward regression failures:\n- " + "\n- ".join(failures)
        )

    print(
        "PASS: production SigLIP2 joint-forward path ranked and selected every expected tile exactly "
        f"cases={exact_cases}/{total_cases} minMargin={(minimum_margin or 0.0):.6f}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CATEGORIES",
    "_feature_tensor",
    "_make_grid",
    "_seed",
    "_separation",
]
