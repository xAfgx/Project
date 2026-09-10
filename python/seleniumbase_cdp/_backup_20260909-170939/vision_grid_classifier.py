from __future__ import annotations

import base64
import io
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Tuple


PIPELINE_PROMPT_TEMPLATE = "This is a photo of {target}."
PROMPT_TEMPLATES: Tuple[str, ...] = (
    PIPELINE_PROMPT_TEMPLATE,
    "This image contains {target}.",
    "A photo containing {target}.",
)
DEFAULT_RAW_LOGIT_THRESHOLD = -3.892

# For "reference animal" captchas (e.g. "Find animals that live where the
# reference animal lives") the instruction references an image, not a text
# class. We map the reference and each tile onto a small set of habitat
# categories and select the tiles whose best-matching category equals the
# reference category. Text prompts are used because SigLIP2's image-text
# alignment discriminates habitats far better than raw image-to-image
# similarity (which just clusters "animal photos" together).
HABITAT_CATEGORIES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("water", ("a fish", "a sea animal", "an animal that lives in water")),
    ("forest", ("a deer", "a forest animal", "an animal that lives in the forest")),
    ("desert", ("a camel", "a desert animal", "an animal that lives in the desert")),
    ("savanna", ("a lion", "a savanna animal", "an animal that lives in the savanna")),
    ("arctic", ("a penguin", "a polar animal", "an animal that lives in snow or ice")),
    ("farm", ("a cow", "a farm animal", "livestock")),
    ("jungle", ("a monkey", "a jungle animal", "an animal that lives in the jungle")),
    ("bird", ("a bird", "an animal that flies")),
    ("pet", ("a cat", "a dog", "a domesticated pet animal")),
)


class VisionGridClassifier:
    """Lazy SigLIP2 classifier using the Hugging Face joint forward path."""

    def __init__(self, *, allow_remote: bool = True) -> None:
        self.model_name = os.environ.get(
            "ARES_VISION_MODEL",
            "google/siglip2-base-patch16-224",
        ).strip()
        self.raw_logit_threshold = self._raw_threshold(
            os.environ.get(
                "ARES_VISION_LOGIT_THRESHOLD",
                str(DEFAULT_RAW_LOGIT_THRESHOLD),
            )
        )
        self.threshold = self._sigmoid(self.raw_logit_threshold)
        self.reference_threshold = self._reference_threshold(
            os.environ.get("ARES_VISION_REFERENCE_THRESHOLD", "0.30")
        )
        self.offline = os.environ.get("ARES_VISION_OFFLINE", "0").strip() == "1"
        self.remote_url = (
            os.environ.get("ARES_VISION_SERVICE_URL", "").strip()
            if allow_remote
            else ""
        )
        self.remote_token = (
            os.environ.get("ARES_VISION_SERVICE_TOKEN", "").strip()
            if allow_remote
            else ""
        )

        self._processor: Any = None
        self._model: Any = None
        self._torch: Any = None
        self._image: Any = None
        self._device = "cpu"
        self._error = ""
        self._load_retry_at = 0.0
        self._remote_ready = False
        self._lock = threading.RLock()

    @property
    def ready(self) -> bool:
        if self.remote_url:
            return self._remote_health()
        return self._load()

    @property
    def error(self) -> str:
        return self._error

    def status(self) -> Dict[str, Any]:
        return {
            "ready": self._remote_ready if self.remote_url else self._model is not None,
            "model": self.model_name,
            "threshold": self.threshold,
            "rawLogitThreshold": self.raw_logit_threshold,
            "selectionPolicy": "hf-joint-forward-sigmoid",
            "promptTemplate": PIPELINE_PROMPT_TEMPLATE,
            "promptTemplates": list(PROMPT_TEMPLATES),
            "sharedService": bool(self.remote_url),
            "offline": self.offline,
            "device": "shared-service" if self.remote_url else self._device,
            "error": self._error,
        }

    def classify(self, instruction: str, sources: Iterable[str]) -> Dict[str, Any]:
        source_list = [str(source or "") for source in sources]
        if self.remote_url:
            return self._classify_remote(instruction, source_list)
        return self._classify_local(instruction, source_list)

    def classify_reference(self, reference_source: str, sources: Iterable[str]) -> Dict[str, Any]:
        """Select tiles by habitat matching against a reference image.

        Used for captchas such as "Find animals that live where the reference
        animal lives", where the instruction references an image instead of a
        concrete text class.
        """
        source_list = [str(source or "") for source in sources]
        if self.remote_url:
            return self._classify_reference_remote(reference_source, source_list)
        return self._classify_reference_local(reference_source, source_list)

    def _classify_reference_local(self, reference_source: str, source_list: List[str]) -> Dict[str, Any]:
        scores: List[float | None] = [None] * len(source_list)
        raw_logits: List[float | None] = [None] * len(source_list)
        if not self._load():
            return self._result([], scores, raw_logits, "reference-image", self._error)

        reference_image = self._read_image(reference_source)
        if reference_image is None:
            return self._result([], scores, raw_logits, "reference-image", "No readable reference image")

        loaded: List[Tuple[int, Any]] = []
        for index, source in enumerate(source_list):
            image = self._read_image(source)
            if image is not None:
                loaded.append((index, image))
        if not loaded:
            return self._result([], scores, raw_logits, "reference-image", "No readable grid images")

        prompts = [prompt for _, prompt_list in HABITAT_CATEGORIES for prompt in prompt_list]
        prompt_category = self._prompt_category_index(HABITAT_CATEGORIES)
        category_count = len(HABITAT_CATEGORIES)

        selected: List[int] = []
        reference_category: int | None = None
        reference_margin = 0.0
        try:
            with self._lock:
                inputs = self._processor(
                    text=prompts,
                    images=[reference_image] + [image for _, image in loaded],
                    padding="max_length",
                    max_length=64,
                    truncation=True,
                    return_tensors="pt",
                )
                inputs = self._to_device(inputs)
                with self._torch.inference_mode():
                    outputs = self._model(**inputs)

                logits_per_image = getattr(outputs, "logits_per_image", None)
                if logits_per_image is None or logits_per_image.ndim != 2 or logits_per_image.shape[1] != len(prompts):
                    raise ValueError("Unexpected SigLIP2 logits_per_image shape for reference matching")

                rows = logits_per_image.detach().float().cpu().tolist()
                reference_scores = self._category_scores(rows[0], prompt_category, category_count)
                reference_category = max(range(category_count), key=lambda c: reference_scores[c])
                ordered = sorted(reference_scores, reverse=True)
                reference_margin = ordered[0] - (ordered[1] if len(ordered) > 1 else ordered[0])

                for (source_index, _), row in zip(loaded, rows[1:]):
                    tile_scores = self._category_scores(row, prompt_category, category_count)
                    tile_category = max(range(category_count), key=lambda c: tile_scores[c])
                    raw_logits[source_index] = round(float(tile_scores[reference_category]), 6)
                    scores[source_index] = round(float(tile_scores[reference_category]), 6)
                    if tile_category == reference_category:
                        selected.append(source_index)

        except Exception as exc:
            return self._result(
                [],
                scores,
                raw_logits,
                "reference-image",
                f"Reference vision inference failed: {exc}",
            )

        result = self._result(selected, scores, raw_logits, "reference-image")
        result["selectionPolicy"] = "reference-habitat-match"
        result["referenceThreshold"] = self.reference_threshold
        result["referenceCategory"] = (
            HABITAT_CATEGORIES[reference_category][0] if reference_category is not None else None
        )
        result["referenceMargin"] = round(reference_margin, 6)
        return result

    @staticmethod
    def _prompt_category_index(categories: Tuple[Tuple[str, Tuple[str, ...]], ...]) -> List[int]:
        mapping: List[int] = []
        for category_index, (_, prompt_list) in enumerate(categories):
            mapping.extend([category_index] * len(prompt_list))
        return mapping

    @staticmethod
    def _category_scores(row: List[float], prompt_category: List[int], category_count: int) -> List[float]:
        sums = [0.0] * category_count
        counts = [0] * category_count
        for prompt_index, value in enumerate(row):
            category = prompt_category[prompt_index]
            sums[category] += float(value)
            counts[category] += 1
        return [sums[c] / max(1, counts[c]) for c in range(category_count)]

    def _classify_reference_remote(self, reference_source: str, source_list: List[str]) -> Dict[str, Any]:
        payload = __import__("json").dumps(
            {
                "referenceSource": str(reference_source or ""),
                "sources": source_list,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.remote_token:
            headers["Authorization"] = f"Bearer {self.remote_token}"
        request = urllib.request.Request(
            self.remote_url.rstrip("/") + "/classify-reference",
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = __import__("json").loads(
                    response.read(16 * 1024 * 1024).decode("utf-8")
                )
            if not isinstance(result, dict):
                raise TypeError("Vision service returned a non-object response")
            self._remote_ready = True
            self._error = ""
            result["sharedService"] = True
            return result
        except Exception as exc:
            self._remote_ready = False
            self._error = f"Shared vision service unavailable: {exc}"
            return {
                "selectedIndexes": [],
                "scores": [None] * len(source_list),
                "rawLogits": [None] * len(source_list),
                "model": self.model_name,
                "target": "reference-image",
                "referenceThreshold": self.reference_threshold,
                "selectionPolicy": "reference-image-similarity",
                "sharedService": True,
                "error": self._error,
            }

    def _classify_local(self, instruction: str, source_list: List[str]) -> Dict[str, Any]:
        if not self._load():
            return {
                "selectedIndexes": [],
                "scores": [],
                "rawLogits": [],
                "model": self.model_name,
                "error": self._error,
            }

        target = self._target_text(instruction).casefold().strip()
        prompts = [template.format(target=target) for template in PROMPT_TEMPLATES]

        loaded: List[Tuple[int, Any]] = []
        scores: List[float | None] = [None] * len(source_list)
        raw_logits: List[float | None] = [None] * len(source_list)

        for index, source in enumerate(source_list):
            image = self._read_image(source)
            if image is not None:
                loaded.append((index, image))

        if not loaded:
            return self._result(
                [],
                scores,
                raw_logits,
                target,
                "No readable grid images",
            )

        selected: List[int] = []
        try:
            with self._lock:
                inputs = self._processor(
                    text=prompts,
                    images=[image for _, image in loaded],
                    padding="max_length",
                    max_length=64,
                    truncation=True,
                    return_tensors="pt",
                )
                inputs = self._to_device(inputs)

                with self._torch.inference_mode():
                    outputs = self._model(**inputs)

                logits_per_image = getattr(outputs, "logits_per_image", None)
                if logits_per_image is None:
                    raise TypeError("SigLIP2 forward returned no logits_per_image")
                if logits_per_image.ndim != 2 or logits_per_image.shape[1] != len(prompts):
                    raise ValueError(
                        "Unexpected SigLIP2 logits_per_image shape "
                        f"{tuple(logits_per_image.shape)} for {len(prompts)} prompts"
                    )

                ensemble_logits = logits_per_image.mean(dim=1)
                probabilities = self._torch.sigmoid(ensemble_logits)
                logit_values = ensemble_logits.detach().float().cpu().tolist()
                probability_values = probabilities.detach().float().cpu().tolist()

            for (source_index, _), raw, probability in zip(
                loaded,
                logit_values,
                probability_values,
            ):
                raw_value = float(raw)
                probability_value = float(probability)
                raw_logits[source_index] = round(raw_value, 6)
                scores[source_index] = round(probability_value, 6)

            selected = self._select_top_k(loaded, scores, self.threshold)

        except Exception as exc:
            return self._result(
                [],
                scores,
                raw_logits,
                target,
                f"Vision inference failed: {exc}",
            )

        return self._result(selected, scores, raw_logits, target)

    def _classify_remote(self, instruction: str, sources: List[str]) -> Dict[str, Any]:
        payload = __import__("json").dumps(
            {"instruction": str(instruction or ""), "sources": sources},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.remote_token:
            headers["Authorization"] = f"Bearer {self.remote_token}"
        request = urllib.request.Request(
            self.remote_url.rstrip("/") + "/classify",
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = __import__("json").loads(
                    response.read(16 * 1024 * 1024).decode("utf-8")
                )
            if not isinstance(result, dict):
                raise TypeError("Vision service returned a non-object response")
            self._remote_ready = True
            self._error = ""
            result["sharedService"] = True
            return result
        except Exception as exc:
            self._remote_ready = False
            self._error = f"Shared vision service unavailable: {exc}"
            return {
                "selectedIndexes": [],
                "scores": [None] * len(sources),
                "rawLogits": [None] * len(sources),
                "model": self.model_name,
                "target": self._target_text(instruction).casefold().strip(),
                "threshold": self.threshold,
                "rawLogitThreshold": self.raw_logit_threshold,
                "selectionPolicy": "hf-joint-forward-sigmoid",
                "promptTemplate": PIPELINE_PROMPT_TEMPLATE,
                "promptTemplates": list(PROMPT_TEMPLATES),
                "sharedService": True,
                "error": self._error,
            }

    def _remote_health(self) -> bool:
        headers: Dict[str, str] = {}
        if self.remote_token:
            headers["Authorization"] = f"Bearer {self.remote_token}"
        request = urllib.request.Request(
            self.remote_url.rstrip("/") + "/health",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                value = __import__("json").loads(
                    response.read(64 * 1024).decode("utf-8")
                )
            ready = bool(isinstance(value, dict) and value.get("ready"))
            self._remote_ready = ready
            if ready:
                self._error = ""
            else:
                self._error = (
                    str(value.get("error") or "Shared vision service is not ready")
                    if isinstance(value, dict)
                    else "Shared vision service is not ready"
                )
            return ready
        except Exception as exc:
            self._remote_ready = False
            self._error = f"Shared vision service unavailable: {exc}"
            return False

    def _result(
        self,
        selected: List[int],
        scores: List[float | None],
        raw_logits: List[float | None],
        target: str,
        error: str = "",
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "selectedIndexes": selected,
            "scores": scores,
            "rawLogits": raw_logits,
            "model": self.model_name,
            "target": target,
            "threshold": self.threshold,
            "rawLogitThreshold": self.raw_logit_threshold,
            "selectionPolicy": "hf-joint-forward-sigmoid",
            "promptTemplate": PIPELINE_PROMPT_TEMPLATE,
            "promptTemplates": list(PROMPT_TEMPLATES),
            "device": self._device,
        }
        if error:
            result["error"] = error
        return result

    def _to_device(self, values: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: value.to(self._device) if hasattr(value, "to") else value
            for key, value in values.items()
        }

    def _load(self) -> bool:
        if self._model is not None:
            return True

        now = time.monotonic()
        if self._error and now < self._load_retry_at:
            return False

        with self._lock:
            if self._model is not None:
                return True

            now = time.monotonic()
            if self._error and now < self._load_retry_at:
                return False

            try:
                import torch
                from PIL import Image
                from transformers import AutoModel, AutoProcessor

                self._torch = torch
                self._image = Image
                self._device = "cuda" if torch.cuda.is_available() else "cpu"
                self._processor = AutoProcessor.from_pretrained(
                    self.model_name,
                    local_files_only=self.offline,
                )
                self._model = AutoModel.from_pretrained(
                    self.model_name,
                    local_files_only=self.offline,
                )
                self._model.to(self._device)
                self._model.eval()
                self._error = ""
                self._load_retry_at = 0.0
                return True

            except Exception as exc:
                self._model = None
                self._processor = None
                self._error = f"Vision model unavailable: {exc}"
                self._load_retry_at = time.monotonic() + 5.0
                return False

    def _read_image(self, source: str) -> Any:
        if not source:
            return None

        try:
            if source.startswith("data:"):
                _, payload = source.split(",", 1)
                metadata = source[: source.index(",")]
                raw = (
                    base64.b64decode(payload)
                    if ";base64" in metadata
                    else urllib.parse.unquote_to_bytes(payload)
                )
            elif source.startswith(("http://", "https://")):
                request = urllib.request.Request(
                    source,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                with urllib.request.urlopen(request, timeout=8) as response:
                    raw = response.read(8 * 1024 * 1024)
            else:
                return None

            return self._image.open(io.BytesIO(raw)).convert("RGB")

        except Exception:
            return None

    @staticmethod
    def _target_text(instruction: str) -> str:
        value = re.sub(r"\s+", " ", str(instruction or "")).strip()
        patterns = [
            r"(?i)^.*?(?:select|click|choose|mark|find)\s+(?:all\s+)?(?:images?|squares?|tiles?|pictures?)\s+(?:with|containing|of|showing|that\s+show)\s+",
            r"(?i)^.*?(?:wähle|wählen|klicke|anklicken|markiere|markieren|finde|finden|suche|suchen)\s+(?:alle\s+)?(?:bilder|felder|kacheln|tiere|fotos)?\s*(?:mit|von|auf denen|nach|die)\s+",
            r"(?i)^.*?(?:select|click|choose|mark|find)\s+(?:all\s+)?",
            r"(?i)^.*?(?:wähle|wählen|klicke|anklicken|markiere|markieren|finde|finden|suche|suchen)\s+(?:alle\s+)?(?:auf\s+)?",
        ]
        for pattern in patterns:
            cleaned = re.sub(pattern, "", value).strip(" .:;-")
            if cleaned and cleaned != value:
                cleaned = re.sub(r"(?i)\s+aus$", "", cleaned).strip(" .:;-")
                return cleaned[:240]
        return value[:240] or "the requested object"

    def _select_top_k(
        self,
        loaded: List[Tuple[int, Any]],
        scores: List[float | None],
        fallback_threshold: float,
    ) -> List[int]:
        """Pick the strongest tiles instead of a hard absolute threshold.

        Never returns empty for a non-empty grid: reCAPTCHA-style challenges
        always contain at least one positive tile, and "no click" just wastes a
        cycle. Strategy:
          1. k = max(1, ~sqrt(tileCount)) candidates (3 for 3x3, 4 for 4x4).
          2. If the top-k scores have a clear gap above the rest, take exactly
             those above the gap.
          3. Otherwise take the top-k as an educated guess (wrong answers only
             cost another round, never a ban).
        """
        ranked = sorted(
            ((index, float(score)) for index, score in enumerate(scores) if score is not None),
            key=lambda item: item[1],
            reverse=True,
        )
        if not ranked:
            return []

        n = len(ranked)
        k = max(1, int(round(n ** 0.5)))
        top_k = ranked[:k]

        if len(ranked) > k:
            cutoff = ranked[k][1]
            gap = top_k[-1][1] - cutoff
            strong = [index for index, score in top_k if score >= max(fallback_threshold, cutoff + 0.5 * gap)]
            if strong:
                return strong

        return [index for index, _ in top_k]

    @staticmethod
    def _raw_threshold(value: str) -> float:
        try:
            return max(-20.0, min(20.0, float(value)))
        except ValueError:
            return DEFAULT_RAW_LOGIT_THRESHOLD

    @staticmethod
    def _reference_threshold(value: str) -> float:
        try:
            return max(-1.0, min(1.0, float(value)))
        except ValueError:
            return 0.30

    @staticmethod
    def _sigmoid(value: float) -> float:
        if value >= 0:
            z = math.exp(-value)
            return 1.0 / (1.0 + z)
        z = math.exp(value)
        return z / (1.0 + z)
