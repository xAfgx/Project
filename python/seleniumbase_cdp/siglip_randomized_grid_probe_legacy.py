from __future__ import annotations

import base64
import io
import os
import random
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw

from robust_vision_grid_classifier import RobustVisionGridClassifier


CATEGORIES: Tuple[str, ...] = (
    "crosswalk",
    "traffic light",
    "bicycle",
    "car",
)


def _seed() -> int:
    configured = str(os.environ.get("ARES_SIGLIP_TEST_SEED") or "").strip()
    if configured:
        return int(configured, 0)
    return int.from_bytes(os.urandom(8), "big")


def _scene_base(rng: random.Random, variant: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (224, 224), (176, 205, 226))
    draw = ImageDraw.Draw(image)
    horizon = 70 + ((variant * 7) % 15)
    draw.rectangle((0, horizon, 224, 224), fill=(92, 97, 101))
    draw.rectangle((0, horizon - 12, 224, horizon), fill=(94, 143, 79))
    draw.polygon(((32, 224), (88, horizon), (136, horizon), (196, 224)), fill=(68, 71, 74))
    lane_shift = (-6, 0, 5)[variant % 3]
    draw.polygon(((109 + lane_shift, 224), (111, horizon + 8), (114, horizon + 8), (117 + lane_shift, 224)), fill=(226, 216, 132))
    for _ in range(5):
        x = rng.randint(4, 210)
        y = rng.randint(8, max(12, horizon - 18))
        radius = rng.randint(2, 5)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(245, 245, 240))
    return image, draw


def _draw_crosswalk(draw: ImageDraw.ImageDraw, variant: int) -> None:
    y0 = 128 + (-5, 0, 5)[variant % 3]
    for stripe in range(6):
        y = y0 + stripe * 11
        left = 48 + stripe * 4
        right = 176 - stripe * 4
        draw.polygon(((left, y), (right, y), (right - 3, y + 7), (left + 3, y + 7)), fill=(239, 239, 232))


def _draw_traffic_light(draw: ImageDraw.ImageDraw, variant: int) -> None:
    x = 54 + (-8, 2, 9)[variant % 3]
    draw.rectangle((x + 17, 75, x + 22, 174), fill=(48, 49, 49))
    draw.rounded_rectangle((x, 58, x + 40, 116), radius=7, fill=(34, 36, 36), outline=(10, 10, 10), width=2)
    for index, colour in enumerate(((208, 45, 43), (229, 181, 51), (48, 167, 86))):
        cy = 70 + index * 18
        draw.ellipse((x + 12, cy - 6, x + 28, cy + 10), fill=colour, outline=(12, 12, 12))


def _draw_bicycle(draw: ImageDraw.ImageDraw, variant: int) -> None:
    shift = (-8, 1, 8)[variant % 3]
    y = 154
    left_x, right_x = 76 + shift, 146 + shift
    radius = 25
    for x in (left_x, right_x):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=(24, 24, 24), width=5)
    crank = (111 + shift, 151)
    draw.line((left_x, y, crank[0], crank[1], right_x, y), fill=(36, 49, 58), width=5)
    draw.line((crank[0], crank[1], 98 + shift, 122, 126 + shift, 122, right_x, y), fill=(36, 49, 58), width=5)
    draw.line((98 + shift, 122, 92 + shift, 111), fill=(24, 24, 24), width=4)
    draw.line((126 + shift, 122, 135 + shift, 108), fill=(24, 24, 24), width=4)


def _draw_car(draw: ImageDraw.ImageDraw, variant: int) -> None:
    shift = (-8, 0, 7)[variant % 3]
    body = (55 + shift, 125, 171 + shift, 170)
    draw.rounded_rectangle(body, radius=12, fill=(45, 93, 168), outline=(20, 30, 42), width=3)
    draw.polygon(((76 + shift, 126), (95 + shift, 101), (137 + shift, 101), (155 + shift, 126)), fill=(70, 115, 175), outline=(20, 30, 42))
    draw.polygon(((94 + shift, 105), (104 + shift, 105), (104 + shift, 124), (81 + shift, 124)), fill=(171, 208, 224))
    draw.polygon(((109 + shift, 105), (134 + shift, 105), (149 + shift, 124), (109 + shift, 124)), fill=(171, 208, 224))
    for x in (80 + shift, 148 + shift):
        draw.ellipse((x - 14, 157, x + 14, 185), fill=(22, 22, 22))
        draw.ellipse((x - 6, 165, x + 6, 177), fill=(122, 126, 129))


def _tile_png(category: str, variant: int, seed: int) -> str:
    rng = random.Random((seed << 8) ^ (variant * 7919) ^ sum(ord(ch) for ch in category))
    image, draw = _scene_base(rng, variant)
    if category == "crosswalk":
        _draw_crosswalk(draw, variant)
    elif category == "traffic light":
        _draw_traffic_light(draw, variant)
    elif category == "bicycle":
        _draw_bicycle(draw, variant)
    elif category == "car":
        _draw_car(draw, variant)
    else:
        raise ValueError(f"Unsupported test category: {category}")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _feature_tensor(value: Any, *, name: str) -> Any:
    if hasattr(value, "float") and hasattr(value, "shape"):
        return value
    for attr in ("pooler_output", "image_embeds", "text_embeds", "embeds"):
        candidate = getattr(value, attr, None)
        if candidate is not None and hasattr(candidate, "float") and hasattr(candidate, "shape"):
            return candidate
    if isinstance(value, (tuple, list)):
        for candidate in value:
            if hasattr(candidate, "float") and hasattr(candidate, "shape"):
                return candidate
    raise TypeError(f"{name} returned unsupported type {type(value).__name__}")


def _separation(scores: List[float | None], expected: List[int]) -> Dict[str, float]:
    expected_set = set(expected)
    target_scores = [float(scores[index]) for index in expected if scores[index] is not None]
    distractor_scores = [
        float(value)
        for index, value in enumerate(scores)
        if index not in expected_set and value is not None
    ]
    if len(target_scores) != len(expected) or not distractor_scores:
        raise AssertionError(f"Incomplete score vector: expected={expected}, scores={scores}")
    minimum_target = min(target_scores)
    maximum_distractor = max(distractor_scores)
    return {
        "minTarget": minimum_target,
        "maxDistractor": maximum_distractor,
        "margin": minimum_target - maximum_distractor,
        "safeThreshold": (minimum_target + maximum_distractor) / 2.0,
    }


def _embedding_scores(classifier: RobustVisionGridClassifier, target: str, sources: List[str]) -> List[float | None]:
    loaded: List[Tuple[int, Any]] = []
    for index, source in enumerate(sources):
        image = classifier._read_image(source)
        if image is not None:
            loaded.append((index, image))
    if len(loaded) != len(sources):
        raise AssertionError(f"Only {len(loaded)}/{len(sources)} images decoded")

    processor = classifier._processor
    model = classifier._model
    torch = classifier._torch
    device = classifier._device
    image_inputs = processor(images=[image for _, image in loaded], return_tensors="pt")
    text_inputs = processor(
        text=[f"This is a photo of {target}."],
        padding="max_length",
        max_length=64,
        truncation=True,
        return_tensors="pt",
    )
    image_inputs = {key: value.to(device) if hasattr(value, "to") else value for key, value in image_inputs.items()}
    text_inputs = {key: value.to(device) if hasattr(value, "to") else value for key, value in text_inputs.items()}
    image_kwargs = {key: value for key, value in image_inputs.items() if key in {"pixel_values", "pixel_attention_mask", "spatial_shapes"}}
    text_kwargs = {key: value for key, value in text_inputs.items() if key in {"input_ids", "attention_mask"}}

    with torch.inference_mode():
        image_features = _feature_tensor(model.get_image_features(**image_kwargs), name="get_image_features").float()
        text_features = _feature_tensor(model.get_text_features(**text_kwargs), name="get_text_features").float()
    if image_features.ndim != 2 or image_features.shape[0] != len(loaded):
        raise AssertionError(f"Unexpected image feature shape: {tuple(image_features.shape)}")
    if text_features.ndim != 2 or text_features.shape[0] != 1:
        raise AssertionError(f"Unexpected text feature shape: {tuple(text_features.shape)}")

    image_features = torch.nn.functional.normalize(image_features, p=2, dim=-1)
    text_features = torch.nn.functional.normalize(text_features, p=2, dim=-1)
    similarities = (image_features @ text_features.T)[:, 0].detach().cpu().tolist()

    result: List[float | None] = [None] * len(sources)
    for (source_index, _), similarity in zip(loaded, similarities):
        result[source_index] = round(float(similarity), 6)
    return result


def _make_grid(rng: random.Random, seed: int) -> tuple[List[Dict[str, Any]], List[str]]:
    tiles: List[Dict[str, Any]] = []
    for category_index, label in enumerate(CATEGORIES):
        for variant in range(3):
            tiles.append({"category": category_index, "source": _tile_png(label, variant, seed)})
    rng.shuffle(tiles)
    return tiles, [str(tile["source"]) for tile in tiles]


def main() -> int:
    seed = _seed()
    rng = random.Random(seed)
    print(f"SIGLIP_RANDOM_SEED={seed}")

    classifier = RobustVisionGridClassifier()
    if not classifier._load():
        raise AssertionError(f"SigLIP2 unavailable: {classifier.error}")
    print(
        f"SIGLIP_PREFLIGHT model={classifier.model_name} device={classifier._device} "
        f"currentThreshold={classifier.threshold}"
    )

    tiles, sources = _make_grid(rng, seed)
    diagnostics: List[Dict[str, Any]] = []
    ranking_failures: List[str] = []

    for category_index, target in enumerate(CATEGORIES):
        expected = sorted(index for index, tile in enumerate(tiles) if tile["category"] == category_index)
        runtime = classifier.classify(f"Select all images with {target}", sources)
        if runtime.get("error"):
            ranking_failures.append(f"{target}: runtime inference error: {runtime.get('error')}")
            continue

        logits_scores = runtime.get("scores") or []
        logits = _separation(logits_scores, expected)
        selected = sorted(int(value) for value in runtime.get("selectedIndexes") or [])

        try:
            cosine_scores = _embedding_scores(classifier, target, sources)
            cosine = _separation(cosine_scores, expected)
        except Exception as exc:
            ranking_failures.append(f"{target}: embedding diagnostic error: {exc}")
            continue

        diagnostic = {
            "target": target,
            "expected": expected,
            "selected": selected,
            "currentExact": selected == expected,
            "logits": logits,
            "cosine": cosine,
        }
        diagnostics.append(diagnostic)
        print(
            "SIGLIP_DIAGNOSTIC "
            f"target={target!r} expected={expected} selectedAt{classifier.threshold}={selected} "
            f"logitsMinTarget={logits['minTarget']:.6f} logitsMaxDistractor={logits['maxDistractor']:.6f} "
            f"logitsMargin={logits['margin']:.6f} safeThreshold={logits['safeThreshold']:.6f} "
            f"cosineMargin={cosine['margin']:.6f}"
        )
        if logits["margin"] <= 0:
            ranking_failures.append(f"{target}: logits ranking margin {logits['margin']:.6f} <= 0")
        if cosine["margin"] <= 0:
            ranking_failures.append(f"{target}: cosine ranking margin {cosine['margin']:.6f} <= 0")

    if not diagnostics:
        raise AssertionError("SigLIP2 produced no usable diagnostics")

    global_low = max(item["logits"]["maxDistractor"] for item in diagnostics)
    global_high = min(item["logits"]["minTarget"] for item in diagnostics)
    fixed_threshold_possible = global_low < global_high
    current_exact = sum(1 for item in diagnostics if item["currentExact"])
    print(
        "SIGLIP_THRESHOLD_SUMMARY "
        f"currentExact={current_exact}/{len(diagnostics)} globalSafeLow={global_low:.6f} "
        f"globalSafeHigh={global_high:.6f} fixedThresholdPossible={fixed_threshold_possible}"
    )

    if ranking_failures:
        raise AssertionError("SigLIP2 semantic ranking failures:\n- " + "\n- ".join(ranking_failures))

    print(
        "PASS: all street-grid target classes were semantically ranked above distractors. "
        "Current fixed-threshold exactness is reported as calibration diagnostics, not hidden by early exit."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
