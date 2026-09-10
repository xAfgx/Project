from __future__ import annotations

import random


_STREET_VARIANTS = [
    (("straße",), ("str.",)),
    (("strasse",), ("str.",)),
    (("str.",), ("straße",)),
    (("street",), ("st.",)),
    (("avenue",), ("ave.",)),
    (("boulevard",), ("blvd.",)),
]
_SEPARATORS = [" ", "-", "", "  ", " - "]
_NUMBER_SPACING = ["", " ", "-"]
_SUFFIXES = ["", "a", "b", "c"]


def fuzz_address_line(line: str, seed: int | None = None) -> str:
    """Return a slightly varied version of one address line for duplicate-check fuzzing.

    Deterministic per seed so the same task always produces the same variation,
    while different tasks get distinct ones. Keeps the change small and valid so
    the target's address validation still accepts it.
    """
    rng = random.Random(seed if seed is not None else 0)
    value = " ".join(str(line or "").strip().split())
    if not value:
        return value

    # 1. Street-name abbreviation/expansion.
    for source_group, target_group in _STREET_VARIANTS:
        lower = value.lower()
        for source in source_group:
            if source in lower and rng.random() < 0.35:
                index = lower.index(source)
                value = value[:index] + rng.choice(target_group) + value[index + len(source):]
                break

    # 2. Light separator variation around the house number.
    tokens = value.split()
    if len(tokens) >= 2:
        try:
            int(tokens[-1].rstrip("aAbBcC"))
            tokens[-2] = tokens[-2] + (rng.choice(_SEPARATORS) if rng.random() < 0.3 else " ")
            value = " ".join(tokens)
        except ValueError:
            pass

    # 3. Optional suffix on the house number (small, valid variation).
    if " " in value and rng.random() < 0.25:
        parts = value.rsplit(" ", 1)
        try:
            int(parts[-1].rstrip("aAbBcC"))
            value = f"{parts[0]} {parts[-1]}{rng.choice(_SUFFIXES)}"
        except ValueError:
            pass

    return " ".join(value.split())


def fuzz_address(profile: dict, seed: int | None = None) -> dict:
    """Fuzz the address lines of a profile dict (shipping/billing).

    Modifies only the free-text street lines; keeps postal code, city and
    country untouched so the address stays valid.
    """
    result = dict(profile)
    key = seed if seed is not None else 0
    for section in ("shippingAddress", "billingAddress"):
        address = result.get(section)
        if not isinstance(address, dict):
            continue
        if isinstance(address.get("address1"), str) and address["address1"].strip():
            address["address1"] = fuzz_address_line(address["address1"], seed=(key + 1) if seed is not None else 1)
        if isinstance(address.get("street"), str) and address["street"].strip():
            address["street"] = fuzz_address_line(address["street"], seed=(key + 2) if seed is not None else 2)
    return result
