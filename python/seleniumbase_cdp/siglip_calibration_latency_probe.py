from __future__ import annotations

import math
import os
import random
import statistics
import time
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from robust_vision_grid_classifier import RobustVisionGridClassifier
from siglip_randomized_grid_probe import CATEGORIES, _feature_tensor, _make_grid, _seed

# OpenAI CLIP-style prompt ensembling, kept intentionally small because text
# embeddings can be cached once while image embeddings remain the hot path.
PROMPT_TEMPLATES: Tuple[str, ...] = (
    "This is a photo of {target}.",
    "a photo of a {target}.",
    "a photo of the {target}.",
    "a close-up photo of a {target}.",
)
SINGLE_TEMPLATE: Tuple[str, ...] = (PROMPT_TEMPLATES[0],)


Case = Dict[str, Any]


def _rounds(name: str, default: int) -> int:
    return max(1, min(6, int(os.environ.get(name, str(default)))))


def _to_device(values: Dict[str, Any], device: str) -> Dict[str, Any]:
    return {
        key: value.to(device) if hasattr(value, "to") else value
        for key, value in values.items()
    }


def _text_bank(
    classifier: RobustVisionGridClassifier,
    templates: Sequence[str],
) -> Tuple[Any, float]:
    processor = classifier._processor
    model = classifier._model
    torch = classifier._torch
    device = classifier._device

    prompts = [
        template.format(target=target)
        for target in CATEGORIES
        for template in templates
    ]
    started = time.perf_counter()
    inputs = processor(
        text=prompts,
        padding="max_length",
        max_length=64,
        truncation=True,
        return_tensors="pt",
    )
    inputs = _to_device(inputs, device)
    text_kwargs = {
        key: value
        for key, value in inputs.items()
        if key in {"input_ids", "attention_mask"}
    }
    with torch.inference_mode():
        features = _feature_tensor(
            model.get_text_features(**text_kwargs),
            name="get_text_features",
        ).float()
    features = torch.nn.functional.normalize(features, p=2, dim=-1)
    features = features.reshape(len(CATEGORIES), len(templates), -1)
    # Standard prompt ensemble: average normalized prompt embeddings per class,
    # then renormalize the resulting class prototype.
    features = features.mean(dim=1)
    features = torch.nn.functional.normalize(features, p=2, dim=-1)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return features, elapsed_ms


def _shared_logits(
    classifier: RobustVisionGridClassifier,
    sources: Sequence[str],
    text_features: Any,
) -> Tuple[List[List[float]], float]:
    processor = classifier._processor
    model = classifier._model
    torch = classifier._torch
    device = classifier._device

    # Start before decoding so this latency is directly comparable to the
    # current production classify() path, which includes tile decoding too.
    started = time.perf_counter()
    images = [classifier._read_image(source) for source in sources]
    if any(image is None for image in images):
        raise AssertionError("Shared SigLIP path could not decode every grid tile")

    inputs = processor(images=images, return_tensors="pt")
    inputs = _to_device(inputs, device)
    image_kwargs = {
        key: value
        for key, value in inputs.items()
        if key in {"pixel_values", "pixel_attention_mask", "spatial_shapes"}
    }
    with torch.inference_mode():
        image_features = _feature_tensor(
            model.get_image_features(**image_kwargs),
            name="get_image_features",
        ).float()
    image_features = torch.nn.functional.normalize(image_features, p=2, dim=-1)

    # Match Hugging Face SigLIP2.forward exactly after the encoders:
    # normalized cosine similarity * exp(logit_scale) + logit_bias.
    logits = image_features @ text_features.T
    logits = logits * model.logit_scale.to(device).exp() + model.logit_bias.to(device)
    values = logits.detach().cpu().tolist()
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return [[float(value) for value in row] for row in values], elapsed_ms


def _cases_from_matrix(tiles: Sequence[Dict[str, Any]], matrix: Sequence[Sequence[float]]) -> List[Case]:
    cases: List[Case] = []
    for category_index, target in enumerate(CATEGORIES):
        expected = sorted(
            index
            for index, tile in enumerate(tiles)
            if int(tile["category"]) == category_index
        )
        scores = [float(row[category_index]) for row in matrix]
        cases.append({"target": target, "expected": expected, "scores": scores})
    return cases


def _production_cases(
    classifier: RobustVisionGridClassifier,
    tiles: Sequence[Dict[str, Any]],
    sources: Sequence[str],
) -> Tuple[List[Case], float]:
    started = time.perf_counter()
    cases: List[Case] = []
    for category_index, target in enumerate(CATEGORIES):
        expected = sorted(
            index
            for index, tile in enumerate(tiles)
            if int(tile["category"]) == category_index
        )
        result = classifier.classify(f"Select all images with {target}", sources)
        if result.get("error"):
            raise AssertionError(f"Production SigLIP path failed for {target}: {result.get('error')}")
        cases.append(
            {
                "target": target,
                "expected": expected,
                "selected": sorted(int(value) for value in result.get("selectedIndexes") or []),
                "scores": [None if value is None else float(value) for value in result.get("scores") or []],
            }
        )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return cases, elapsed_ms


def _threshold_metrics(cases: Iterable[Case], threshold: float) -> Dict[str, float]:
    exact = 0
    count = 0
    tp = fp = fn = tn = 0
    for case in cases:
        expected = set(int(value) for value in case["expected"])
        scores = [float(value) for value in case["scores"]]
        selected = {index for index, score in enumerate(scores) if score >= threshold}
        exact += int(selected == expected)
        count += 1
        for index in range(len(scores)):
            wanted = index in expected
            chosen = index in selected
            if wanted and chosen:
                tp += 1
            elif wanted and not chosen:
                fn += 1
            elif not wanted and chosen:
                fp += 1
            else:
                tn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
    balanced = 0.5 * (
        (tp / (tp + fn) if tp + fn else 0.0)
        + (tn / (tn + fp) if tn + fp else 0.0)
    )
    return {
        "exact": float(exact),
        "cases": float(count),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "balancedAccuracy": balanced,
    }


def _fit_threshold(cases: Sequence[Case]) -> Tuple[float, Dict[str, float]]:
    values = sorted({float(score) for case in cases for score in case["scores"]})
    if len(values) < 2:
        raise AssertionError("Not enough distinct logits to calibrate a threshold")
    epsilon = max(1e-6, (values[-1] - values[0]) * 1e-6)
    candidates = [values[0] - epsilon]
    candidates.extend((left + right) / 2.0 for left, right in zip(values, values[1:]))
    candidates.append(values[-1] + epsilon)

    best_threshold = candidates[0]
    best_metrics = _threshold_metrics(cases, best_threshold)
    best_key = (
        best_metrics["exact"],
        best_metrics["f1"],
        best_metrics["balancedAccuracy"],
    )
    for threshold in candidates[1:]:
        metrics = _threshold_metrics(cases, threshold)
        key = (metrics["exact"], metrics["f1"], metrics["balancedAccuracy"])
        if key > best_key:
            best_threshold = threshold
            best_metrics = metrics
            best_key = key
    return float(best_threshold), best_metrics


def _ranking_summary(cases: Sequence[Case]) -> Dict[str, float]:
    margins: List[float] = []
    separable = 0
    for case in cases:
        expected = set(int(value) for value in case["expected"])
        target_scores = [float(score) for index, score in enumerate(case["scores"]) if index in expected]
        distractors = [float(score) for index, score in enumerate(case["scores"]) if index not in expected]
        margin = min(target_scores) - max(distractors)
        margins.append(margin)
        separable += int(margin > 0)
    return {
        "separable": float(separable),
        "cases": float(len(cases)),
        "meanMargin": statistics.fmean(margins) if margins else 0.0,
        "minMargin": min(margins) if margins else 0.0,
    }


def _latency(values: Sequence[float]) -> Dict[str, float]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {"mean": 0.0, "median": 0.0, "p95": 0.0}
    p95_index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1))
    return {
        "mean": statistics.fmean(ordered),
        "median": statistics.median(ordered),
        "p95": ordered[p95_index],
    }


def _grid(seed: int, round_index: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    grid_seed = seed ^ ((round_index + 1) * 1_000_003)
    rng = random.Random(grid_seed ^ 0xA5A5A5A5)
    return _make_grid(rng, grid_seed)


def main() -> int:
    seed = _seed()
    calibration_rounds = _rounds("ARES_SIGLIP_CALIBRATION_ROUNDS", 3)
    heldout_rounds = _rounds("ARES_SIGLIP_HELDOUT_ROUNDS", 3)
    print(
        f"SIGLIP_CALIBRATION_SEED={seed} calibrationRounds={calibration_rounds} "
        f"heldoutRounds={heldout_rounds}"
    )

    classifier = RobustVisionGridClassifier()
    load_started = time.perf_counter()
    if not classifier._load():
        raise AssertionError(f"SigLIP2 unavailable: {classifier.error}")
    model_load_ms = (time.perf_counter() - load_started) * 1000.0

    single_text, single_text_ms = _text_bank(classifier, SINGLE_TEMPLATE)
    ensemble_text, ensemble_text_ms = _text_bank(classifier, PROMPT_TEMPLATES)

    calibration_single: List[Case] = []
    calibration_ensemble: List[Case] = []
    calibration_seed = seed ^ 0xC411B4A7
    for round_index in range(calibration_rounds):
        tiles, sources = _grid(calibration_seed, round_index)
        single_matrix, _ = _shared_logits(classifier, sources, single_text)
        ensemble_matrix, _ = _shared_logits(classifier, sources, ensemble_text)
        calibration_single.extend(_cases_from_matrix(tiles, single_matrix))
        calibration_ensemble.extend(_cases_from_matrix(tiles, ensemble_matrix))

    single_threshold, single_fit = _fit_threshold(calibration_single)
    ensemble_threshold, ensemble_fit = _fit_threshold(calibration_ensemble)

    current_probability_threshold = float(classifier.threshold)
    current_logit_threshold = math.log(current_probability_threshold / (1.0 - current_probability_threshold))

    heldout_baseline_exact = 0
    heldout_baseline_cases = 0
    heldout_single: List[Case] = []
    heldout_ensemble: List[Case] = []
    baseline_latency_ms: List[float] = []
    single_latency_ms: List[float] = []
    ensemble_latency_ms: List[float] = []

    heldout_seed = seed ^ 0x5EEDBEEF
    for round_index in range(heldout_rounds):
        tiles, sources = _grid(heldout_seed, round_index)

        baseline_cases, baseline_ms = _production_cases(classifier, tiles, sources)
        baseline_latency_ms.append(baseline_ms)
        heldout_baseline_cases += len(baseline_cases)
        heldout_baseline_exact += sum(
            1 for case in baseline_cases if list(case["selected"]) == list(case["expected"])
        )

        single_matrix, single_ms = _shared_logits(classifier, sources, single_text)
        single_latency_ms.append(single_ms)
        heldout_single.extend(_cases_from_matrix(tiles, single_matrix))

        ensemble_matrix, ensemble_ms = _shared_logits(classifier, sources, ensemble_text)
        ensemble_latency_ms.append(ensemble_ms)
        heldout_ensemble.extend(_cases_from_matrix(tiles, ensemble_matrix))

    single_current = _threshold_metrics(heldout_single, current_logit_threshold)
    single_calibrated = _threshold_metrics(heldout_single, single_threshold)
    ensemble_calibrated = _threshold_metrics(heldout_ensemble, ensemble_threshold)
    single_ranking = _ranking_summary(heldout_single)
    ensemble_ranking = _ranking_summary(heldout_ensemble)

    baseline_latency = _latency(baseline_latency_ms)
    single_latency = _latency(single_latency_ms)
    ensemble_latency = _latency(ensemble_latency_ms)
    single_speedup = baseline_latency["mean"] / single_latency["mean"] if single_latency["mean"] > 0 else 0.0
    ensemble_speedup = baseline_latency["mean"] / ensemble_latency["mean"] if ensemble_latency["mean"] > 0 else 0.0
    ensemble_overhead = ensemble_latency["mean"] - single_latency["mean"]

    print(
        "SIGLIP_CALIBRATION_FIT "
        f"singleThreshold={single_threshold:.6f} singleCalibrationExact={int(single_fit['exact'])}/{int(single_fit['cases'])} "
        f"ensembleThreshold={ensemble_threshold:.6f} ensembleCalibrationExact={int(ensemble_fit['exact'])}/{int(ensemble_fit['cases'])}"
    )
    print(
        "SIGLIP_HELDOUT_ACCURACY "
        f"baselineCurrent={heldout_baseline_exact}/{heldout_baseline_cases} "
        f"sharedSingleAtCurrent={int(single_current['exact'])}/{int(single_current['cases'])} "
        f"sharedSingleCalibrated={int(single_calibrated['exact'])}/{int(single_calibrated['cases'])} "
        f"promptEnsembleCalibrated={int(ensemble_calibrated['exact'])}/{int(ensemble_calibrated['cases'])} "
        f"singleF1={single_calibrated['f1']:.4f} ensembleF1={ensemble_calibrated['f1']:.4f}"
    )
    print(
        "SIGLIP_HELDOUT_RANKING "
        f"singleSeparable={int(single_ranking['separable'])}/{int(single_ranking['cases'])} "
        f"singleMeanMargin={single_ranking['meanMargin']:.6f} singleMinMargin={single_ranking['minMargin']:.6f} "
        f"ensembleSeparable={int(ensemble_ranking['separable'])}/{int(ensemble_ranking['cases'])} "
        f"ensembleMeanMargin={ensemble_ranking['meanMargin']:.6f} ensembleMinMargin={ensemble_ranking['minMargin']:.6f}"
    )
    print(
        "SIGLIP_LATENCY "
        f"modelLoadMs={model_load_ms:.2f} singleTextCacheMs={single_text_ms:.2f} ensembleTextCacheMs={ensemble_text_ms:.2f} "
        f"baselineMeanMs={baseline_latency['mean']:.2f} baselineP95Ms={baseline_latency['p95']:.2f} "
        f"sharedSingleMeanMs={single_latency['mean']:.2f} sharedSingleP95Ms={single_latency['p95']:.2f} "
        f"sharedSingleSpeedup={single_speedup:.2f}x "
        f"ensembleMeanMs={ensemble_latency['mean']:.2f} ensembleP95Ms={ensemble_latency['p95']:.2f} "
        f"ensembleSpeedup={ensemble_speedup:.2f}x ensembleOverheadVsSingleMs={ensemble_overhead:.2f}"
    )

    if not heldout_single or not heldout_ensemble:
        raise AssertionError("Held-out calibration probe produced no cases")

    print(
        "PASS: held-out threshold calibration and prompt ensembling were measured separately from production. "
        "Production threshold/selection policy was not changed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
